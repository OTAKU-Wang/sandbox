"""Session exec / logs / usage endpoint tests (Round 40 usability).

The exec endpoint's runtime is replaced with a fake SandboxRuntime whose
``execute`` returns canned results — no bwrap needed. POSIX-only import
chain (sandbox_security via session_files) skips on Windows.
"""
import sys
import uuid

import pytest
import pytest_asyncio

_win_skip = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only 'resource' import chain; covered on Linux",
)


class _FakeRuntime:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    async def execute(self, container_id, code, language, **kwargs):
        self.calls.append(
            {"container_id": container_id, "code": code, "language": language, **kwargs}
        )
        return dict(self.result)


@pytest.fixture
def sandbox_workspace(tmp_path):
    ws = tmp_path / "bwrap-it"
    ws.mkdir()
    (ws / "files").mkdir()
    (ws / "files" / "seed.txt").write_bytes(b"seed")
    return ws


@pytest.fixture
def fake_session_id():
    return uuid.uuid4()


@pytest_asyncio.fixture
async def session_row(db_session, fake_session_id):
    """Insert a RUNNING, provisioned session owned by a fresh user."""
    from app.models.sandbox_session import SandboxSession, SandboxLevel, SandboxMode
    from app.models.user import User
    from app.services.auth_service import hash_password

    user = User(username=f"exec_{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@e2e.local",
                hashed_password=hash_password("testpass123"), role="data_provider")
    db_session.add(user)
    await db_session.flush()
    from app.models.data_product import DataProduct
    product = DataProduct(name="ExecTest", provider_id=user.id, product_type="structured")
    db_session.add(product)
    await db_session.flush()
    session = SandboxSession(
        id=fake_session_id,
        user_id=user.id,
        data_product_id=product.id,
        sandbox_level=SandboxLevel.L3.value,
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        status="running",
        container_id=f"bwrap-{fake_session_id}",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def owner_headers(db_session, session_row):
    from app.services.auth_service import create_access_token
    token = create_access_token(session_row.user_id, "data_provider")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def session_client(client, db_session, session_row, sandbox_workspace, monkeypatch):
    from app.services.sandbox_runtime import sandbox_runtime

    monkeypatch.setattr(
        sandbox_runtime, "get_workspace",
        lambda container_id, level: sandbox_workspace,
    )
    return client


@pytest.fixture
def fake_exec(monkeypatch):
    """Patch get_sandbox_manager in the API module to return the fake runtime."""
    from app.api import sandbox_sessions as api_mod

    runtime = _FakeRuntime({"output": "hello from sandbox\n", "exit_code": 0})
    async def _fake_manager():
        return runtime

    monkeypatch.setattr(api_mod, "get_sandbox_manager", _fake_manager)
    return runtime


# ─── exec ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@_win_skip
async def test_exec_runs_command_and_returns_output(
    session_client, owner_headers, fake_session_id, fake_exec
):
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/exec",
        json={"command": "ls -la files/"},
        headers=owner_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["exit_code"] == 0
    assert "hello from sandbox" in body["output"]
    assert body["output_blocked"] is False
    assert body["command"] == "ls -la files/"
    # command reached the runtime as a bash execution
    assert fake_exec.calls[0]["language"] == "bash"


@pytest.mark.asyncio
@_win_skip
async def test_exec_output_blocked_on_critical_dlp(
    session_client, owner_headers, fake_session_id, monkeypatch
):
    from app.api import sandbox_sessions as api

    pii_runtime = _FakeRuntime({"output": "身份证 11010119900307867X", "exit_code": 0})
    async def _fake_manager():
        return pii_runtime

    monkeypatch.setattr(api, "get_sandbox_manager", _fake_manager)
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/exec",
        json={"command": "cat files/pii.txt"},
        headers=owner_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["output_blocked"] is True
    assert body["output"] == ""
    assert body["blocked_reason"] == "output_inspection_blocked"
    assert body["security_report"]["blocked"] is True


@pytest.mark.asyncio
@_win_skip
async def test_exec_requires_running_session(session_client, owner_headers, fake_session_id, db_session, session_row):
    session_row.status = "suspended"
    db_session.add(session_row)
    await db_session.flush()

    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/exec",
        json={"command": "ls"},
        headers=owner_headers,
    )
    assert resp.status_code == 400
    assert "not running" in resp.json()["detail"]


@pytest.mark.asyncio
@_win_skip
async def test_exec_timeout_capped_by_settings(
    session_client, owner_headers, fake_session_id, fake_exec, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "SESSION_EXEC_TIMEOUT_SECONDS", 7)
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/exec",
        json={"command": "sleep 999", "timeout_seconds": 500},
        headers=owner_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timeout_seconds"] == 7
    assert fake_exec.calls[0]["timeout"] == 7


@pytest.mark.asyncio
@_win_skip
async def test_exec_command_validation(session_client, owner_headers, fake_session_id):
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/exec",
        json={"command": "   "},
        headers=owner_headers,
    )
    assert resp.status_code == 422
    resp = await session_client.post(
        f"/api/v1/sandbox-sessions/{fake_session_id}/exec",
        json={"command": "ls", "timeout_seconds": 0},
        headers=owner_headers,
    )
    assert resp.status_code == 422


# ─── logs ──────────────────────────────────────────────────────

@pytest.mark.asyncio
@_win_skip
async def test_logs_returns_session_audit_trail(
    session_client, owner_headers, fake_session_id, db_session, session_row
):
    from app.models.audit_log import AuditLog

    db_session.add(AuditLog(session_id=session_row.id, user_id=session_row.user_id,
                            action="sandbox.create", resource_type="sandbox_session",
                            detail={"a": 1}))
    db_session.add(AuditLog(session_id=session_row.id, user_id=session_row.user_id,
                            action="sandbox.exec", resource_type="sandbox_session",
                            detail={"b": 2}))
    await db_session.flush()

    resp = await session_client.get(
        f"/api/v1/sandbox-sessions/{fake_session_id}/logs", headers=owner_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    actions = [entry["action"] for entry in body["logs"]]
    assert "sandbox.create" in actions and "sandbox.exec" in actions
    assert body["latest_created_at"] is not None

    resp = await session_client.get(
        f"/api/v1/sandbox-sessions/{fake_session_id}/logs",
        params={"action": "sandbox.exec"},
        headers=owner_headers,
    )
    actions = [entry["action"] for entry in resp.json()["logs"]]
    assert set(actions) == {"sandbox.exec"}

    resp = await session_client.get(
        f"/api/v1/sandbox-sessions/{fake_session_id}/logs",
        params={"since": "not-a-date"},
        headers=owner_headers,
    )
    assert resp.status_code == 400


# ─── usage ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@_win_skip
async def test_usage_reports_workspace_files_snapshots(
    session_client, owner_headers, fake_session_id
):
    resp = await session_client.get(
        f"/api/v1/sandbox-sessions/{fake_session_id}/usage", headers=owner_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace"]["files"] >= 1
    assert body["workspace"]["bytes"] >= 4
    assert body["snapshots"]["count"] == 0
    assert body["timeout"]["timeout_seconds"] == 3600
    assert body["status"] == "running"


# ─── templates list endpoint (registered BEFORE /{session_id}) ──

@pytest.mark.asyncio
async def test_session_templates_route_not_shadowed_by_uuid(client):
    """GET /session-templates must reach the literal route, not 401/422 via
    the /{session_id} UUID match."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"tpl_{unique}", "email": f"tpl_{unique}@example.com",
        "password": "testpass123", "role": "buyer",
    })
    assert resp.status_code in (200, 201), resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = await client.get("/api/v1/sandbox-sessions/session-templates", headers=headers)
    assert resp.status_code == 200, resp.text
    names = {t["name"] for t in resp.json()["templates"]}
    assert "python-analysis" in names
