"""PZ-06: disclosure field alignment (SS-11 §3.1).

bundle_meta (proof bundle, excluded from evidence_hash), the four disclosure
fields in the compliance report and the monitoring security posture, and the
chain_anchoring state in the audit _backend_disclosure — all computed from
live state and mutually consistent.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.models.audit_log import AuditLog
from app.models.data_product import DataProduct, DataProductStatus
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services.audit_service import AuditService
from app.services.compliance_report import compliance_report_service, disclosure_fields


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


DISCLOSURE_KEYS = {"signing_backend", "aead_algorithm", "trust_root", "chain_anchoring", "sandbox_engine"}


class TestDisclosureFields:
    def test_derived_from_live_state(self, monkeypatch):
        # Inputs are class/module-level live state other tests may have left
        # dirty (e.g. audit signing initialized _hsm_key_id against a mocked
        # HSM). Pin the known test-environment values so the assertion is
        # deterministic regardless of suite order.
        import app.services.remote_attestation as remote_attestation
        monkeypatch.setattr(AuditService, "_hsm_key_id", None, raising=False)
        monkeypatch.delattr(remote_attestation, "EXTERNAL_ATTESTATION_ROOT", raising=False)

        fields = disclosure_fields()
        assert set(fields) == DISCLOSURE_KEYS
        # HSM not active in the test environment (software keypair fallback)
        assert fields["signing_backend"] == "software"
        assert fields["aead_algorithm"] == "aes_fallback"
        assert fields["trust_root"] == "local-simulated"
        assert fields["chain_anchoring"] == "pg-log"

    def test_signing_backend_flips_to_hsm(self, monkeypatch):
        monkeypatch.setattr(AuditService, "_hsm_key_id", "hsm-key-001")
        assert disclosure_fields()["signing_backend"] == "hsm"

    def test_signing_backend_software_marker(self, monkeypatch):
        monkeypatch.setattr(AuditService, "_hsm_key_id", "__software__")
        assert disclosure_fields()["signing_backend"] == "software"


class TestDisclosureConsistency:
    @pytest.mark.asyncio
    async def test_anchor_compliance_and_posture_agree(self, client: AsyncClient, operator_headers: dict, db_session):
        log = AuditLog(
            user_id=None,
            action="sandbox.provision",
            resource_type="sandbox_session",
            resource_id=str(uuid.uuid4()),
            detail={},
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(log)
        await db_session.flush()
        await db_session.refresh(log)

        anchor_resp = await client.post(
            "/api/v1/audit/anchor",
            json={"record_ids": [str(log.id)]},
            headers=operator_headers,
        )
        assert anchor_resp.status_code == 200
        anchor_state = anchor_resp.json()["backend"]["chain_anchoring"]

        audit_report_resp = await client.get(
            "/api/v1/audit/compliance-report?days=30", headers=operator_headers,
        )
        assert audit_report_resp.status_code == 200
        audit_state = audit_report_resp.json()["anchoring_backend"]["chain_anchoring"]

        end = datetime.now(timezone.utc)
        service_report = await compliance_report_service.generate_report(
            db_session, end - timedelta(days=30), end,
        )
        for key in DISCLOSURE_KEYS:
            assert key in service_report, f"compliance report missing {key}"

        posture_resp = await client.get("/api/v1/monitoring/security-posture", headers=operator_headers)
        assert posture_resp.status_code == 200
        posture = posture_resp.json()
        for key in DISCLOSURE_KEYS:
            assert key in posture, f"security posture missing {key}"

        # chain_anchoring must agree across anchor, audit compliance, service
        # report and monitoring posture
        assert anchor_state == "pg-log"
        assert audit_state == anchor_state
        assert service_report["chain_anchoring"] == anchor_state
        assert posture["chain_anchoring"] == anchor_state
        # the other three fields must agree across the two disclosure surfaces
        assert service_report["signing_backend"] == posture["signing_backend"]
        assert service_report["aead_algorithm"] == posture["aead_algorithm"]
        assert service_report["trust_root"] == posture["trust_root"]

    @pytest.mark.asyncio
    async def test_service_report_carries_disclosure(self, db_session):
        from tests.conftest import create_user_with_role
        await create_user_with_role(db_session, "admin", "pz06")
        end = datetime.now(timezone.utc)
        report = await compliance_report_service.generate_report(
            db_session, end - timedelta(days=1), end,
        )
        for key in DISCLOSURE_KEYS:
            assert key in report
        assert report["chain_anchoring"] == "pg-log"
        assert report["anchoring"]["backend"] == "pg_append_only"


class TestProofBundleMeta:
    @pytest.mark.asyncio
    async def test_bundle_meta_present_and_hash_inputs_unchanged(self, client: AsyncClient, db_session):
        from tests.conftest import create_user_with_role
        headers, user_id = await create_user_with_role(db_session, "data_provider", "pz06")

        product = DataProduct(
            provider_id=uuid.UUID(user_id),
            name="PZ06 Product",
            product_type="structured",
            status=DataProductStatus.PUBLISHED.value,
        )
        db_session.add(product)
        await db_session.flush()
        await db_session.refresh(product)
        session = SandboxSession(
            user_id=uuid.UUID(user_id),
            data_product_id=product.id,
            sandbox_level="L3",
            status=SessionStatus.RUNNING.value,
        )
        db_session.add(session)
        await db_session.flush()
        await db_session.refresh(session)

        resp = await client.get(
            f"/api/v1/sandbox-sessions/{session.id}/proof-bundle", headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()

        meta = body["bundle_meta"]
        assert set(meta) == DISCLOSURE_KEYS | {"dp_applied", "is_simulation"}
        assert meta["aead_algorithm"] == "aes_fallback"
        assert meta["trust_root"] == "local-simulated"
        assert meta["chain_anchoring"] == "pg-log"
        assert meta["signing_backend"] in {"software", "hsm"}
        assert meta["dp_applied"] is None  # no inspection event for this session

        # evidence_hash must equal the hash of the payload WITHOUT bundle_meta
        # (the old input set) — proving bundle_meta is not an evidence input.
        evidence_inputs = {k: v for k, v in body.items() if k not in {"generated_at", "integrity", "bundle_meta"}}
        assert _sha(evidence_inputs) == body["integrity"]["evidence_hash"]
        # bundle_meta must not be inside the integrity block
        assert "bundle_meta" not in body["integrity"]

    @pytest.mark.asyncio
    async def test_dp_applied_from_latest_inspection(self, client: AsyncClient, db_session):
        from tests.conftest import create_user_with_role
        headers, user_id = await create_user_with_role(db_session, "data_provider", "pz06b")

        product = DataProduct(
            provider_id=uuid.UUID(user_id),
            name="PZ06 Product B",
            product_type="structured",
            status=DataProductStatus.PUBLISHED.value,
        )
        db_session.add(product)
        await db_session.flush()
        await db_session.refresh(product)
        session = SandboxSession(
            user_id=uuid.UUID(user_id),
            data_product_id=product.id,
            sandbox_level="L3",
            status=SessionStatus.RUNNING.value,
        )
        db_session.add(session)
        await db_session.flush()
        await db_session.refresh(session)

        inspection = AuditLog(
            user_id=uuid.UUID(user_id),
            session_id=session.id,
            action="sandbox.output_inspected",
            resource_type="sandbox_session",
            resource_id=str(session.id),
            detail={"passed": True, "dp_applied": True, "findings_count": 0},
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(inspection)
        await db_session.flush()

        resp = await client.get(
            f"/api/v1/sandbox-sessions/{session.id}/proof-bundle", headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["bundle_meta"]["dp_applied"] is True
