"""SS-12 L1 — API key audit integration: key calls produce actor_type=api_key
rows with non-empty SM2 signature, all 5 admin actions are logged, and the
activity endpoint reflects the audit table (resource_type/resource_id path).
"""
import uuid

import pytest
from sqlalchemy import select

from app.core import scopes as ss12_scopes
from app.models.audit_log import AuditLog
from app.models.user import UserRole


@pytest.fixture(autouse=True)
def _clean_ss12_state():
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()
    yield
    ss12_scopes.reset_auth_counters()
    ss12_scopes.reset_rate_limits()


async def _create_key(client, headers, **overrides) -> dict:
    payload = {"name": "ss12-audit-key", "preset": "custom", "scopes": ["meta:read"]}
    payload.update(overrides)
    resp = await client.post("/api/v1/api-keys", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _key_calls(client, body, n=2):
    """n authenticated key calls — pass the meta gate and resolve the issuer
    identity → 200 (D-e2e-09); auth succeeded either way."""
    headers = {"Authorization": f"Bearer {body['key']}"}
    for _ in range(n):
        resp = await client.get("/api/v1/meta/capability-matrix", headers=headers)
        assert resp.status_code == 200


class TestKeyCallAudit:
    @pytest.mark.asyncio
    async def test_successful_key_call_logs_api_key_actor_with_signature(
        self, client, db_session, admin_headers
    ):
        body = await _create_key(client, admin_headers)
        await _key_calls(client, body)

        rows = (await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "api_key",
                AuditLog.resource_id == str(body["id"]),
                AuditLog.action == "api_key.call",
            )
        )).scalars().all()
        assert len(rows) == 2
        for row in rows:
            assert row.detail["actor_type"] == "api_key"
            assert row.detail["actor_id"] == str(body["id"])
            assert row.detail["path"] == "/api/v1/meta/capability-matrix"
            assert row.detail["method"] == "GET"
            assert row.user_id is None  # key calls are not attributed to a user
            assert row.detail.get("_sm2_signature")  # SM2 signature non-empty

    @pytest.mark.asyncio
    async def test_denied_attempts_are_logged(self, client, db_session, admin_headers):
        await _create_key(client, admin_headers)
        # unknown key
        await client.get("/api/v1/meta/capability-matrix",
                         headers={"Authorization": "Bearer cds_ak_" + "q" * 43})
        denied = (await db_session.execute(
            select(AuditLog).where(AuditLog.action == "auth.api_key.denied")
        )).scalars().all()
        assert len(denied) == 1
        assert denied[0].detail["denied_reason"] == "not_found"
        assert denied[0].detail["actor_type"] == "api_key"
        assert denied[0].detail["status_code"] == 401
        assert denied[0].detail.get("_sm2_signature")


class TestAdminActionAudit:
    @pytest.mark.asyncio
    async def test_all_five_admin_actions_logged(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers)
        await client.patch(f"/api/v1/api-keys/{body['id']}", json={"status": "disabled"},
                           headers=admin_headers)
        await client.patch(f"/api/v1/api-keys/{body['id']}", json={"status": "active"},
                           headers=admin_headers)
        rotated = (await client.post(f"/api/v1/api-keys/{body['id']}/rotate",
                                     headers=admin_headers)).json()
        await client.delete(f"/api/v1/api-keys/{rotated['id']}", headers=admin_headers)

        rows = (await db_session.execute(
            select(AuditLog).where(AuditLog.resource_type == "api_key")
        )).scalars().all()
        actions = sorted(row.action for row in rows)
        assert actions == sorted(["api_key.created", "api_key.disabled", "api_key.enabled",
                                  "api_key.rotated", "api_key.revoked"])
        for row in rows:
            assert row.user_id is not None  # attributed to the admin user
            assert row.detail["actor_type"] == "user"
            assert row.detail.get("_sm2_signature")
        rotate_row = next(r for r in rows if r.action == "api_key.rotated")
        assert rotate_row.detail["new_id"] == str(rotated["id"])


class TestActivityEndpoint:
    @pytest.mark.asyncio
    async def test_activity_matches_audit_rows(self, client, db_session, admin_headers):
        body = await _create_key(client, admin_headers)
        await _key_calls(client, body, n=3)
        await client.get("/api/v1/meta/capability-matrix",
                         headers={"Authorization": "Bearer cds_ak_" + "w" * 43})

        resp = await client.get(f"/api/v1/api-keys/{body['id']}/activity", headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()
        # 3 api_key.call rows for this key (+ the create row) — the denied row
        # belongs to the unknown key's prefix, not this key's id
        assert len(items) == 4
        for item in items:
            assert item["time"] is not None
            assert "status_code" in item and "ip" in item
        call_items = [i for i in items if i["path"] == "/api/v1/meta/capability-matrix"]
        assert len(call_items) == 3

        # matches the audit table exactly (same paths, newest-first)
        rows = (await db_session.execute(
            select(AuditLog).where(
                AuditLog.resource_type == "api_key",
                AuditLog.resource_id == str(body["id"]),
            ).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(50)
        )).scalars().all()
        assert [i["path"] for i in items] == [(r.detail or {}).get("path") for r in rows]
        assert len(items) == len(rows) == min(50, len(rows))

    @pytest.mark.asyncio
    async def test_activity_404_unknown_key(self, client, db_session, admin_headers):
        resp = await client.get(f"/api/v1/api-keys/{uuid.uuid4()}/activity", headers=admin_headers)
        assert resp.status_code == 404


class TestAuditLossIsDisclosed:
    """B2 (CR 2026-10-15): the key-call audit write was wrapped in a bare
    `except Exception: rollback` — an audit-store outage silently erased every
    machine call from the tamper-evidence trail."""

    @pytest.mark.asyncio
    async def test_failed_audit_write_is_counted_and_logged(self, client, db_session, admin_headers,
                                                           monkeypatch, caplog):
        import logging

        from app.core.metrics import AUDIT_WRITE_FAILURES
        from app.services.audit_service import audit_service

        body = await _create_key(client, admin_headers)

        async def _audit_store_down(*args, **kwargs):
            raise RuntimeError("audit store down")

        counter = AUDIT_WRITE_FAILURES.labels(op="api_key.call")
        before = counter._value.get()
        monkeypatch.setattr(audit_service, "log", _audit_store_down)

        with caplog.at_level(logging.ERROR, logger="app.core.deps"):
            resp = await client.get("/api/v1/meta/capability-matrix",
                                    headers={"Authorization": f"Bearer {body['key']}"})

        assert resp.status_code == 200  # availability kept: the call is not blocked
        assert counter._value.get() == before + 1
        assert [r for r in caplog.records if "AUDIT-LOSS" in r.getMessage()]
