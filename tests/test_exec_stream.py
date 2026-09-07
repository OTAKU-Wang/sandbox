"""W10: WebSocket exec stream — ticket auth, framing, line-level DLP.

The WS endpoint runs on the TestClient portal loop, which cannot share the
pytest loop's StaticPool connection — these tests back the handler with a
dedicated file-based SQLite engine (same file, two connections).
"""
import uuid

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from app.core.database import Base
from app.main import app
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services.auth_service import create_access_token


@pytest_asyncio.fixture(autouse=True)
async def _fake_redis(monkeypatch):
    """Shared FakeServer with a per-call client: the WS portal loop and the
    pytest loop must not reuse one loop-bound async connection."""
    server = fakeredis.FakeServer()
    import app.core.redis as redis_module

    async def _fake_get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(redis_module, "get_redis", _fake_get_redis)
    # session_stream binds the symbol at import time — patch its namespace too
    import app.api.session_stream as stream_module

    monkeypatch.setattr(stream_module, "get_redis", _fake_get_redis)


@pytest_asyncio.fixture
async def ws_db(tmp_path, monkeypatch):
    """Dedicated file-backed engine for the WS portal loop."""
    ws_dir = tmp_path / "l0-ws"
    for sub in ("tmp", "input", "output", "files"):
        (ws_dir / sub).mkdir(parents=True)
    monkeypatch.setattr(
        "app.services.sandbox_runtime.ProcessAdapter._resolve_workspace",
        lambda self, container_id: ws_dir,
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr("app.core.database.async_session", factory)
    yield factory
    await engine.dispose()


async def _mk_running_session(factory) -> tuple[User, SandboxSession, str]:
    async with factory() as db:
        unique = uuid.uuid4().hex[:8]
        user = User(
            username=f"ws_{unique}",
            email=f"ws_{unique}@example.com",
            hashed_password="x",
            role="data_provider",
        )
        db.add(user)
        await db.flush()
        session = SandboxSession(
            id=uuid.uuid4(),
            user_id=user.id,
            data_product_id=uuid.uuid4(),
            sandbox_level="L0",
            sandbox_mode="structured_query",
            status=SessionStatus.RUNNING.value,
            container_id=f"l0-{uuid.uuid4().hex[:12]}",
        )
        db.add(session)
        await db.flush()
        await db.commit()
        return user, session, create_access_token(user.id, user.role)


def _drain(ws, limit: int = 50) -> list[dict]:
    frames: list[dict] = []
    while len(frames) < limit:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("exit", "blocked", "error"):
            break
    return frames


@pytest.mark.asyncio
async def test_bad_ticket_rejected(client, ws_db):
    _, session, _ = await _mk_running_session(ws_db)
    tc = TestClient(app)
    with pytest.raises(Exception):
        with tc.websocket_connect(
            f"/api/v1/sandbox-sessions/{session.id}/exec/stream?ticket=garbage"
        ) as ws:
            ws.receive_json()


@pytest.mark.asyncio
async def test_ticket_single_use_and_protocol_frames(client, ws_db):
    user, session, token = await _mk_running_session(ws_db)
    from app.api.session_stream import issue_ws_ticket

    mint = await issue_ws_ticket(str(user.id), user.role)
    assert "ticket" in mint
    ticket = mint["ticket"]

    tc = TestClient(app)
    with tc.websocket_connect(
        f"/api/v1/sandbox-sessions/{session.id}/exec/stream?ticket={ticket}"
    ) as ws:
        ws.send_json({"type": "start", "command": "echo hello-stream", "timeout": 30})
        frames = _drain(ws)

    types = [f["type"] for f in frames]
    assert "stdout" in types, frames
    assert frames[-1]["type"] == "exit", frames
    stdout_text = "".join(f["data"] for f in frames if f["type"] == "stdout")
    assert "hello-stream" in stdout_text

    with pytest.raises(Exception):
        with tc.websocket_connect(
            f"/api/v1/sandbox-sessions/{session.id}/exec/stream?ticket={ticket}"
        ) as ws:
            ws.receive_json()


@pytest.mark.asyncio
async def test_unknown_session_rejected(client, ws_db):
    from app.api.session_stream import issue_ws_ticket

    user, _, _ = await _mk_running_session(ws_db)
    ticket = (await issue_ws_ticket(str(user.id), user.role))["ticket"]

    tc = TestClient(app)
    stranger_id = uuid.uuid4()
    with pytest.raises(Exception):
        with tc.websocket_connect(
            f"/api/v1/sandbox-sessions/{stranger_id}/exec/stream?ticket={ticket}"
        ) as ws:
            ws.receive_json()


@pytest.mark.asyncio
async def test_critical_pii_blocks_stream(client, ws_db):
    from app.api.session_stream import issue_ws_ticket

    user, session, _ = await _mk_running_session(ws_db)
    ticket = (await issue_ws_ticket(str(user.id), user.role))["ticket"]

    tc = TestClient(app)
    with tc.websocket_connect(
        f"/api/v1/sandbox-sessions/{session.id}/exec/stream?ticket={ticket}"
    ) as ws:
        ws.send_json({"type": "start", "command": "echo 110101199003077758", "timeout": 30})
        frames = _drain(ws)

    types = [f["type"] for f in frames]
    assert "blocked" in types, frames


@pytest.mark.asyncio
async def test_ws_ticket_endpoint_requires_auth_and_mints(client):
    """HTTP mint path: 401 without auth, 200 with a valid user (in-memory DB)."""
    resp = await client.post("/api/v1/auth/ws-ticket")
    assert resp.status_code in (401, 403)
    token = await _token_for(client)
    mint = await client.post("/api/v1/auth/ws-ticket", headers={"Authorization": f"Bearer {token}"})
    assert mint.status_code == 200
    assert mint.json()["expires_in"] == 30


async def _token_for(client) -> str:
    import uuid as _uuid

    unique = _uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"wsmint_{unique}",
        "email": f"wsmint_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    return resp.json()["access_token"]
