"""A64 — the federation chain wrote zero audit rows and GET /audit lied.

`grep audit_service app/api/federation.py` was empty on HEAD: `POST /trust`,
`POST /trusts/{id}/revoke`, `POST /trusts/{id}/suspend` and `POST /request`
(cross-space data egress) never entered the `audit_service` funnel, and
`GET /audit` served only the connector's in-process `list` — empty after a
restart, invisible across workers — while `_persist_audit_entry`'s
`FederationAuditRecord` rows were written but read by nobody.

Invariants pinned here:
* every federation mutation appends an `AuditLog` row through the funnel;
* `GET /api/v1/federation/audit` returns persisted `FederationAuditRecord`
  rows, not just current-process memory.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.federation_trust import FederationAuditRecord
from app.services.federation_connector import FederationConnector, GatewayResponse

SPACE = "cds-remote-a64"


async def _establish(client, headers, space_id: str = SPACE) -> str:
    resp = await client.post("/api/v1/federation/trust", json={
        "space_id": space_id,
        "space_name": "A64 Remote",
        "endpoint": f"https://{space_id}.example.com",
        "trust_level": "basic",
    }, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["trust_id"]


async def _audit_rows(db_session, action: str) -> list[AuditLog]:
    res = await db_session.execute(select(AuditLog).where(AuditLog.action == action))
    return list(res.scalars().all())


@pytest.mark.asyncio
async def test_trust_lifecycle_mutations_are_audited(client, db_session, operator_headers):
    trust_id = await _establish(client, operator_headers)
    est = await _audit_rows(db_session, "federation.trust.established")
    assert est, "POST /trust wrote no audit row"
    assert est[-1].detail and est[-1].detail.get("remote_space") == SPACE

    rev = await client.post(f"/api/v1/federation/trusts/{trust_id}/revoke", headers=operator_headers)
    assert rev.status_code == 200, rev.text
    rows = await _audit_rows(db_session, "federation.trust.revoked")
    assert rows, "POST /revoke wrote no audit row"
    assert rows[-1].resource_id == trust_id

    trust_id2 = await _establish(client, operator_headers, space_id="cds-remote-a64b")
    sus = await client.post(f"/api/v1/federation/trusts/{trust_id2}/suspend", headers=operator_headers)
    assert sus.status_code == 200, sus.text
    rows = await _audit_rows(db_session, "federation.trust.suspended")
    assert rows, "POST /suspend wrote no audit row"
    assert rows[-1].resource_id == trust_id2


@pytest.mark.asyncio
async def test_cross_space_request_is_audited(client, db_session, operator_headers):
    trust_id = await _establish(client, operator_headers, space_id="cds-remote-a64c")

    async def _fake_send(self, trust, operation, resource, payload=None, user_id=None):
        return GatewayResponse(request_id="req-a64", status_code=200, data={"ok": True})

    with patch.object(FederationConnector, "send_request_async", _fake_send):
        resp = await client.post("/api/v1/federation/request", json={
            "trust_id": trust_id, "operation": "read", "resource": "/data/x",
        }, headers=operator_headers)
    assert resp.status_code == 200, resp.text

    rows = await _audit_rows(db_session, "federation.request.sent")
    assert rows, "POST /request (cross-space egress) wrote no audit row"
    detail = rows[-1].detail or {}
    assert detail["operation"] == "read"
    assert detail["status_code"] == 200


@pytest.mark.asyncio
async def test_get_audit_reads_persisted_records(client, db_session, operator_headers):
    """A persisted record from a previous worker/restart must be visible."""
    space = f"db-only-{uuid.uuid4().hex[:8]}"
    db_session.add(FederationAuditRecord(
        entry_id=f"a64-{uuid.uuid4().hex}",
        request_id="req-db-only",
        source_space=space,
        target_space="cds-local",
        operation="read_catalog",
        resource="/catalog",
        status="success",
        user_id=None,
        details_json=None,
        timestamp=datetime.now(timezone.utc),
    ))
    await db_session.flush()

    resp = await client.get(f"/api/v1/federation/audit?space_id={space}", headers=operator_headers)
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    assert entries, "GET /audit served only process memory — persisted rows are invisible"
    assert entries[0]["source_space"] == space
    assert entries[0]["operation"] == "read_catalog"
