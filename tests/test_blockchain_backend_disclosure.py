"""T8/F1 — Honest anchoring-backend disclosure.

When the active anchoring backend is the local PG append-only tamper-evident
log (the default without a real consortium chain), every anchor / verify /
merkle-proof / compliance response must disclose the backend and never imply a
real "上链" (on-chain) anchor. `is_consortium_chain` distinguishes the two.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.audit_log import AuditLog
from app.services.compliance_report import compliance_report_service


@pytest.fixture
async def anchored_record(client, operator_headers, db_session):
    """Create an audit record and anchor it via the API."""
    log = AuditLog(
        user_id=None,
        action="sandbox.provision",
        resource_type="sandbox_session",
        resource_id=str(uuid.uuid4()),
        detail={"level": "L3"},
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(log)
    await db_session.flush()
    await db_session.refresh(log)

    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(log.id)]},
        headers=operator_headers,
    )
    assert resp.status_code == 200
    return log, resp.json()


class TestAnchorDisclosure:
    @pytest.mark.asyncio
    async def test_anchor_response_discloses_pg_backend(self, anchored_record):
        log, body = anchored_record
        assert body["backend"] is not None
        assert body["backend"]["backend"] == "pg_append_only"
        assert body["backend"]["is_consortium_chain"] is False
        assert "非联盟链" in body["backend"]["backend_label"]

    @pytest.mark.asyncio
    async def test_verify_discloses_backend_and_note(self, client, operator_headers, anchored_record):
        log, _ = anchored_record
        resp = await client.get(f"/api/v1/audit/verify/{log.id}", headers=operator_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["verified"] is True
        assert body["backend"]["backend"] == "pg_append_only"
        assert "本地防篡改" in body["verification_note"]
        assert "非联盟链" in body["verification_note"]

    @pytest.mark.asyncio
    async def test_merkle_proof_discloses_backend(self, client, operator_headers, anchored_record):
        log, _ = anchored_record
        resp = await client.get(f"/api/v1/audit/merkle-proof/{log.id}", headers=operator_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["backend"]["backend"] == "pg_append_only"
        assert body["backend"]["is_consortium_chain"] is False

    @pytest.mark.asyncio
    async def test_anchor_never_claims_consortium_with_pg_backend(self, client, operator_headers, db_session):
        """The compliance endpoint (audit API) must disclose pg backend too."""
        log = AuditLog(
            user_id=None,
            action="data.policy.denied",
            resource_type="contract",
            resource_id=str(uuid.uuid4()),
            detail={},
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(log)
        await db_session.flush()
        await db_session.refresh(log)
        resp = await client.get("/api/v1/audit/compliance-report?days=7", headers=operator_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["anchoring_backend"]["backend"] == "pg_append_only"
        assert body["anchoring_backend"]["is_consortium_chain"] is False


class TestComplianceServiceDisclosure:
    @pytest.mark.asyncio
    async def test_compliance_report_discloses_anchoring(self, db_session):
        end = datetime.now()
        start = end - timedelta(days=7)
        report = await compliance_report_service.generate_report(db_session, start, end)
        assert "anchoring" in report
        assert report["anchoring"]["backend"] == "pg_append_only"
        assert report["anchoring"]["is_consortium_chain"] is False
        assert "非联盟链" in report["anchoring"]["backend_label"]
