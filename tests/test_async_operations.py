"""W11: async operation model — 202 + operation_id + polling + exclusion."""
import asyncio
import json
import uuid

import pytest
import pytest_asyncio

from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.session_operation import SessionOperation
from app.models.user import User
from app.services import session_operations as ops


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "files").mkdir(parents=True)
    (tmp_path / "input").mkdir()
    (tmp_path / "files" / "a.txt").write_bytes(b"AAAA")
    (tmp_path / "input" / "data.enc").write_bytes(b"\x00\x01\x02ENCRYPTED")
    return tmp_path


@pytest.fixture(autouse=True)
def _patch_workspace(monkeypatch, ws):
    monkeypatch.setattr(
        "app.api.sandbox_sessions._session_workspace_or_400", lambda s: ws
    )


@pytest_asyncio.fixture
async def owner_and_headers(db_session):
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"ops_{unique}",
        email=f"ops_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=user.id,
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
        container_id="bwrap-deadbeef",
    )
    db_session.add(session)
    await db_session.flush()

    from app.services.auth_service import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}
    return session, headers


@pytest.mark.asyncio
async def test_sync_snapshot_unchanged_shape_plus_operation_record(
    client, db_session, owner_and_headers
):
    session, headers = owner_and_headers
    resp = await client.post(f"/api/v1/sandbox-sessions/{session.id}/snapshots", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot_id"]
    assert body["operation_id"]

    op = await ops.get_operation(db_session, uuid.UUID(body["operation_id"]))
    assert op.status == "succeeded"
    assert op.result_ref["snapshot_id"] == body["snapshot_id"]


@pytest.mark.asyncio
async def test_async_snapshot_returns_202_and_completes(
    client, db_session, owner_and_headers
):
    session, headers = owner_and_headers
    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session.id}/snapshots",
        params={"async": "true"},
        headers=headers,
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["operation_id"]
    assert body["status"] in ("pending", "running")

    await ops.flush_background_operations()

    poll = await client.get(
        f"/api/v1/sandbox-sessions/operations/{body['operation_id']}", headers=headers
    )
    assert poll.status_code == 200
    payload = poll.json()
    assert payload["status"] == "succeeded"
    assert payload["result_ref"]["snapshot_id"]

    listing = await client.get(f"/api/v1/sandbox-sessions/{session.id}/snapshots", headers=headers)
    snapshot_ids = [s["snapshot_id"] for s in listing.json()["snapshots"]]
    assert payload["result_ref"]["snapshot_id"] in snapshot_ids


@pytest.mark.asyncio
async def test_failed_operation_records_error(
    client, db_session, owner_and_headers, monkeypatch
):
    from app.services import session_snapshots as ss

    def _boom(workspace, session_id):
        raise ss.SnapshotError("workspace vanished")

    monkeypatch.setattr(ss, "create_snapshot", _boom)
    session, headers = owner_and_headers

    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session.id}/snapshots",
        params={"async": "true"},
        headers=headers,
    )
    assert resp.status_code == 202
    await ops.flush_background_operations()

    poll = await client.get(
        f"/api/v1/sandbox-sessions/operations/{resp.json()['operation_id']}", headers=headers
    )
    assert poll.status_code == 200
    payload = poll.json()
    assert payload["status"] == "failed"
    assert "workspace vanished" in payload["error"]


@pytest.mark.asyncio
async def test_concurrent_rollback_locked(client, db_session, owner_and_headers):
    session, headers = owner_and_headers
    db_session.add(SessionOperation(
        op_id=uuid.uuid4(),
        session_id=session.id,
        op_type="rollback",
        status="running",
        progress=0.5,
    ))
    await db_session.flush()

    # need an existing snapshot to pass the executor; the lock check fires first
    resp = await client.post(
        f"/api/v1/sandbox-sessions/{session.id}/snapshots", headers=headers
    )
    assert resp.status_code == 200
    snapshot_id = resp.json()["snapshot_id"]

    from app.core.config import get_settings
    get_settings().RETENTION_JANITOR_DRY_RUN = True

    rb = await client.post(
        f"/api/v1/sandbox-sessions/{session.id}/snapshots/{snapshot_id}/rollback",
        headers=headers,
    )
    assert rb.status_code == 503
    assert rb.headers.get("Retry-After") == "2"
    assert rb.json()["detail"]["code"] == "OPERATION_LOCKED"


@pytest.mark.asyncio
async def test_operation_ownership_enforced(client, db_session, owner_and_headers):
    session, headers = owner_and_headers
    op = SessionOperation(
        op_id=uuid.uuid4(),
        session_id=session.id,
        op_type="snapshot",
        status="succeeded",
        progress=1.0,
        result_ref={"snapshot_id": "s1"},
    )
    db_session.add(op)
    await db_session.flush()

    from app.models.user import User as UserM
    from app.services.auth_service import create_access_token

    stranger = UserM(
        username=f"stranger_{uuid.uuid4().hex[:6]}",
        email=f"stranger_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(stranger)
    await db_session.flush()

    other = await client.get(
        f"/api/v1/sandbox-sessions/operations/{op.op_id}",
        headers={"Authorization": f"Bearer {create_access_token(stranger.id, stranger.role)}"},
    )
    assert other.status_code == 404


def test_sdk_wait_for_operation():
    """SDK wait_for_operation polls until terminal (MockTransport)."""
    import sys
    from pathlib import Path

    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
    from cds_sdk import CDSClient

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"status": "running", "progress": 0.4})
        return httpx.Response(200, json={
            "status": "succeeded", "progress": 1.0, "result_ref": {"snapshot_id": "s-1"},
        })

    with CDSClient("http://test", token="t", transport=httpx.MockTransport(handler)) as c:
        op = c.wait_for_operation("op-1", timeout=5, interval=0.01)
    assert op["status"] == "succeeded"
    assert calls["n"] == 2
