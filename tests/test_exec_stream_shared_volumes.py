"""A24 + A25: 流式执行通道的共享卷挂载与一次性票据消费。

A24（critical）：`_stream_loop._run` 里的 `resolve_binds(db, …)` 里的 `db` 从来不是
闭包自由变量，而是 GLOBAL 查找 —— 模块根本没有 `db`（真正的 db 只在 `exec_stream`
的 `async with async_session() as db` 里，且在 `_stream_loop` 之前就已关闭），于是
`NameError` 被紧随其后的 `except Exception: binds = []` 无日志吞掉：同一个会话走
非流式 exec 会正常挂共享卷，走 WS 流式则**永远不挂**，且不留任何痕迹（违反 honest
degradation 铁律）。tests/test_exec_stream.py 只测协议/DLP，tests/test_shared_volumes.py
只直接调服务层 —— 两条线从未接起来，所以这条路径上没有一条会失败的断言。

A25（medium）：票据消费是 `get` + `delete` 两条命令，窗口内两个并发连接可双双通过。

本文件的用例：
1. 真实 SharedVolume + attachment 落库 → WS start → 断言 runtime 收到的 extra_binds
   就是服务层会给出的那组 bind（把两条线接起来）；
2. 卷解析失败 ⇒ 显式 error 帧披露 + 拒绝执行（绝不带着 binds=[] 假装在跑）；
3. get→delete 窗口里塞一个确定性让出点 ⇒ 恰好一个并发消费者拿到会话。

WS 端点跑在 TestClient 的 portal loop 上，不能与 pytest loop 共用 StaticPool 连接，
所以这里与 tests/test_exec_stream.py 一样用文件型 SQLite 支撑 handler。
"""
import asyncio
import uuid

import fakeredis
import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from app.api.session_stream import issue_ws_ticket
from app.core.database import Base
from app.main import app
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.shared_volume import SharedVolume, SharedVolumeAttachment
from app.models.user import User


def _fake_redis_factory(server):
    async def _fake_get_redis():
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    return _fake_get_redis


@pytest_asyncio.fixture(autouse=True)
async def _fake_redis(monkeypatch):
    """Shared FakeServer with a per-call client (portal loop vs pytest loop)."""
    server = fakeredis.FakeServer()
    import app.core.redis as redis_module
    import app.api.session_stream as stream_module

    fake = _fake_redis_factory(server)
    monkeypatch.setattr(redis_module, "get_redis", fake)
    monkeypatch.setattr(stream_module, "get_redis", fake)


@pytest_asyncio.fixture
async def ws_db(tmp_path, monkeypatch):
    """Dedicated file-backed engine for the WS portal loop (same as test_exec_stream)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr("app.core.database.async_session", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_runtime(monkeypatch):
    """Runtime stand-in — pins the execute_streaming call without touching bwrap.

    本机无 bwrap（L0 诚实拒跑），所以这里替换掉 runtime：本文件要钉的是「binds
    有没有真的解析并传下去」，不是 L0 能不能跑命令。签名写成调用点的确切形状，
    调用契约一变就红。
    """
    calls: list[dict] = []

    class _FakeRuntime:
        async def execute_streaming(self, container_id, command, language, *,
                                    env_vars, timeout, extra_binds, on_line):
            calls.append({
                "container_id": container_id,
                "command": command,
                "extra_binds": extra_binds,
            })
            await on_line("stdout", "streamed")
            return {"exit_code": 0, "duration_ms": 5}

    async def _get_runtime():
        return _FakeRuntime()

    monkeypatch.setattr("app.services.sandbox_manager.get_sandbox_manager", _get_runtime)
    return calls


async def _mk_running_session(factory, *, level: str = "L0") -> tuple[User, SandboxSession]:
    async with factory() as db:
        unique = uuid.uuid4().hex[:8]
        user = User(
            username=f"volsvc_{unique}",
            email=f"volsvc_{unique}@example.com",
            hashed_password="x",
            role="data_provider",
        )
        db.add(user)
        await db.flush()
        session = SandboxSession(
            id=uuid.uuid4(),
            user_id=user.id,
            data_product_id=uuid.uuid4(),
            sandbox_level=level,
            sandbox_mode="structured_query",
            status=SessionStatus.RUNNING.value,
            container_id=f"l0-{uuid.uuid4().hex[:12]}",
        )
        db.add(session)
        await db.flush()
        await db.commit()
        return user, session


async def _attach_volume(factory, session: SandboxSession, user: User, *, name: str, read_only: bool):
    from app.services.shared_volumes import volume_dir

    async with factory() as db:
        volume = SharedVolume(owner_id=user.id, name=name, read_only=read_only)
        db.add(volume)
        await db.flush()
        db.add(SharedVolumeAttachment(
            session_id=session.id, volume_id=volume.id, read_only=read_only
        ))
        await db.commit()
        return volume, volume_dir(volume.id)


async def _ticket(user: User) -> str:
    mint = await issue_ws_ticket(str(user.id), user.role)
    assert "ticket" in mint, mint
    return mint["ticket"]


def _frames_until_close(ws, limit: int = 20) -> list[dict]:
    frames: list[dict] = []
    while len(frames) < limit:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("exit", "blocked", "error"):
            break
    return frames


@pytest.mark.asyncio
async def test_ws_stream_mounts_attached_shared_volumes(client, ws_db, fake_runtime):
    """A24 主断言：会话挂的共享卷必须真的出现在 execute_streaming 的 extra_binds 里。

    这里的 resolve_binds 不打桩 —— 走的就是服务层真实现（另一个 DB 连接），
    正是旧实现必然抱 NameError 的那一句。
    """
    user, session = await _mk_running_session(ws_db)
    volume, host_dir = await _attach_volume(ws_db, session, user, name="handoff", read_only=True)
    ticket = await _ticket(user)

    tc = TestClient(app)
    with tc.websocket_connect(
        f"/api/v1/sandbox-sessions/{session.id}/exec/stream?ticket={ticket}"
    ) as ws:
        ws.send_json({"type": "start", "command": "ls /workspace/shared", "timeout": 30})
        frames = _frames_until_close(ws)

    assert frames[-1]["type"] == "exit", frames
    assert len(fake_runtime) == 1, fake_runtime
    binds = fake_runtime[0]["extra_binds"]
    assert binds is not None, "shared volume was silently dropped from the streaming exec"
    assert [(str(b[0]), b[1], b[2]) for b in binds] == [
        (str(host_dir), f"/workspace/shared/{volume.name}", True)
    ]
    assert fake_runtime[0]["container_id"] == session.container_id


@pytest.mark.asyncio
async def test_ws_stream_without_volume_sends_no_binds(client, ws_db, fake_runtime):
    """反向用例：没挂卷的会话仍须正常流式执行（不能被修复修成“永远拒跑”）。"""
    user, session = await _mk_running_session(ws_db)
    ticket = await _ticket(user)

    tc = TestClient(app)
    with tc.websocket_connect(
        f"/api/v1/sandbox-sessions/{session.id}/exec/stream?ticket={ticket}"
    ) as ws:
        ws.send_json({"type": "start", "command": "echo hi", "timeout": 30})
        frames = _frames_until_close(ws)

    assert [f["type"] for f in frames] == ["stdout", "exit"], frames
    assert len(fake_runtime) == 1, fake_runtime
    assert fake_runtime[0]["extra_binds"] is None


@pytest.mark.asyncio
async def test_ws_stream_discloses_and_refuses_when_volume_resolution_fails(
    client, ws_db, fake_runtime, monkeypatch
):
    """A24 铁律面：解析不了共享卷必须“拒绝 + 披露”，不得带着 binds=[] 假装在跑。"""
    user, session = await _mk_running_session(ws_db)
    await _attach_volume(ws_db, session, user, name="handoff", read_only=True)

    async def _boom(db, session_id):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr("app.services.shared_volumes.resolve_binds", _boom)
    ticket = await _ticket(user)

    tc = TestClient(app)
    with tc.websocket_connect(
        f"/api/v1/sandbox-sessions/{session.id}/exec/stream?ticket={ticket}"
    ) as ws:
        ws.send_json({"type": "start", "command": "ls /workspace/shared", "timeout": 30})
        frames = _frames_until_close(ws)

    codes = [f.get("code") for f in frames if f["type"] == "error"]
    assert "RUNTIME_UNAVAILABLE" in codes, frames
    assert not [f for f in frames if f["type"] == "exit"], frames
    assert fake_runtime == [], "exec must not run without its declared mounts"


@pytest.mark.asyncio
async def test_ticket_consume_is_atomic_under_concurrent_use(monkeypatch):
    """A25：get 与 delete 之间的窗口必须消失 —— 两个并发消费者只能过一个。

    包装器在 `get` 返回后插一个 `asyncio.sleep(0)`，把「RTT 已完成、下一条命令
    未发出」这个竞态窗口放大成必然交错（确定性的调度点，不是赌时序）。
    get→delete 写法下两个调用方双双拿到 payload；GETDEL 后只剩一个。
    """
    import app.api.session_stream as stream_module
    import fakeredis.aioredis

    server = fakeredis.FakeServer()
    inner = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    class _RaceWindowRedis:
        def __init__(self, target):
            self._target = target

        async def get(self, key):
            value = await self._target.get(key)
            await asyncio.sleep(0)  # exactly the get→delete window A25 names
            return value

        def __getattr__(self, name):
            return getattr(self._target, name)

    async def _get_redis():
        return _RaceWindowRedis(inner)

    monkeypatch.setattr(stream_module, "get_redis", _get_redis)

    ticket = (await stream_module.issue_ws_ticket(str(uuid.uuid4()), "data_provider"))["ticket"]
    first, second = await asyncio.gather(
        stream_module._consume_ticket(ticket), stream_module._consume_ticket(ticket)
    )

    winners = [p for p in (first, second) if p is not None]
    assert len(winners) == 1, f"single-use ticket consumed {len(winners)} times (A25 race)"
    # 第三次无论如何都不能过
    assert await stream_module._consume_ticket(ticket) is None
