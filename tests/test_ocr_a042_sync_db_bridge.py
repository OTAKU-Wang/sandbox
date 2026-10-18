"""A42: `SyncDbBridge` 的三条失效路径（本机 3.12 门口径）。

1. `_ping()` 把 ready 的落点放在 `finally` ⇒ 库不可达时健康检查照样「成功」，
   故障被推迟到第一次真 DB 调用，异常留在没人取回的 task 里。
2. `ready.result(timeout=30)` 超时后 `_loop` 已被线程写过 ⇒ 之后每次 `_ensure()`
   都在 `if self._loop is not None: return` 直接返回 ⇒ 桥永久停在未验证态。
3. `close()` 不 dispose 引擎（池内连接不关）、不 join、不清 session_factory
   ⇒ 再 `_ensure()` 会新建一套 loop+engine，旧的池就泄了。
"""
import asyncio
import os
import threading
import time

import pytest
from sqlalchemy import text

import app.core.database as db_module
from app.core.config import get_settings
from app.core.database import SyncDbBridge


class _FakeConn:
    def __init__(self, engine):
        self._engine = engine

    async def __aenter__(self):
        if self._engine.connect_error is not None:
            raise self._engine.connect_error
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return None


class _FakeEngine:
    """只替掉 `connect()`/`dispose()`：桥用到的就是这两下。"""

    def __init__(self, connect_error: Exception | None = None, connect_delay: float = 0.0):
        self.connect_error = connect_error
        self.connect_delay = connect_delay
        self.disposed = False
        self.connects = 0

    def connect(self):
        self.connects += 1
        if self.connect_delay:
            async def _slow():
                await asyncio.sleep(self.connect_delay)
                raise OSError("dial never completed")
            return _SlowCtx(self, _slow)
        return _FakeConn(self)

    async def dispose(self):
        self.disposed = True


class _SlowCtx:
    def __init__(self, engine, coro_factory):
        self._engine = engine
        self._coro_factory = coro_factory

    async def __aenter__(self):
        await self._coro_factory()
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return None


@pytest.fixture
def factory_spy(monkeypatch):
    """把 create_async_engine 换成可控假引擎，并记录每次构造。"""
    created: list[_FakeEngine] = []

    def _install(engine: _FakeEngine):
        def _factory(*args, **kwargs):
            created.append(engine)
            return engine

        monkeypatch.setattr(db_module, "create_async_engine", _factory)
        return created

    return _install


def test_ping_failure_is_reported_not_swallowed(factory_spy):
    engine = _FakeEngine(connect_error=OSError("database is unreachable"))
    created = factory_spy(engine)
    bridge = SyncDbBridge()
    try:
        # 1) 失败必须当场抛出，而不是「桥可用」
        with pytest.raises(OSError, match="unreachable"):
            bridge._ensure()
        # 2) 不许停在「看起来已就绪」的状态
        assert bridge._loop is None
        assert bridge._session_factory is None
        # 3) 必须可重试：下一次 _ensure() 重新建一套，而不是静默 return
        with pytest.raises(OSError, match="unreachable"):
            bridge._ensure()
        assert len(created) == 2
        # 4) 失败路径也要把池收掉
        assert engine.disposed is True
        assert bridge._thread is None or not bridge._thread.is_alive()
    finally:
        bridge.close()


def test_run_does_not_pretend_when_bridge_cannot_start(factory_spy):
    engine = _FakeEngine(connect_error=OSError("nope"))
    factory_spy(engine)
    bridge = SyncDbBridge()
    ran = []

    async def _work():
        ran.append(True)
        return 1

    try:
        with pytest.raises(OSError):
            bridge.run(_work())
        assert ran == [], "桥没起来就不许把协程丢进 loop"
    finally:
        bridge.close()


def test_warm_timeout_leaves_retryable_state(factory_spy, monkeypatch):
    engine = _FakeEngine(connect_delay=5.0)
    created = factory_spy(engine)
    bridge = SyncDbBridge()
    bridge._warm_timeout = 0.4  # 别把门跑成 30s
    try:
        with pytest.raises(Exception):
            bridge._ensure()
        # 超时后桥必须回到「未初始化」，而不是永久未验证态
        assert bridge._loop is None
        assert bridge._session_factory is None
        thread = bridge._thread
        assert thread is None or not thread.is_alive()
        with pytest.raises(Exception):
            bridge._ensure()
        assert len(created) == 2
    finally:
        bridge.close()


# --------------------------------------------------------------------------- #
# 真引擎（sqlite 文件）：close 必须关池、停线程、可重建
# --------------------------------------------------------------------------- #
@pytest.fixture
def file_db_url(tmp_path):
    path = tmp_path / "bridge.db"
    url = f"sqlite+aiosqlite:///{path}"
    from sqlalchemy.ext.asyncio import create_async_engine as real_create

    def _factory(*args, **kwargs):
        kwargs.pop("pool_size", None)
        kwargs.pop("max_overflow", None)
        return real_create(*args, **kwargs)

    return url, _factory, path


@pytest.mark.asyncio
async def test_close_disposes_pool_stops_thread_and_allows_rebuild(file_db_url, monkeypatch):
    url, factory, path = file_db_url
    monkeypatch.setattr(db_module.settings, "DATABASE_URL", url)
    monkeypatch.setattr(db_module, "create_async_engine", factory)

    bridge = SyncDbBridge()

    async def _select_one():
        from app.core.database import sync_bridge_session

        async with sync_bridge_session() as db:
            return (await db.execute(text("SELECT 1"))).scalar_one()

    try:
        assert bridge.run(_select_one(), timeout=30) == 1
        engine = bridge._engine
        loop = bridge._loop
        thread = bridge._thread
        assert engine is not None and loop is not None and thread is not None
        assert thread.is_alive()
        assert loop.is_running()

        bridge.close()

        # 线程退出 + loop 收干净 + 状态清空
        assert not thread.is_alive()
        assert loop.is_closed()
        assert bridge._loop is None
        assert bridge._session_factory is None
        assert bridge._engine is None
    finally:
        # 池没关的话，Windows 上这个 sqlite 文件删不掉（句柄还被占着）
        try:
            os.remove(path)
        except PermissionError:
            pytest.fail("close() 之后 sqlite 文件仍被占用 ⇒ 连接池没 dispose")
        bridge.close()

    # 重建：close 之后再用，必须是一套干净的 loop+engine，且真能查
    assert bridge.run(_select_one(), timeout=30) == 1
    assert bridge._loop is not None and bridge._loop is not loop
    assert bridge._engine is not engine
    bridge.close()


def test_close_is_safe_before_start_and_twice():
    bridge = SyncDbBridge()
    bridge.close()
    bridge.close()
    assert bridge._loop is None


@pytest.mark.asyncio
async def test_bridge_runs_on_its_own_loop_not_the_callers(file_db_url, monkeypatch):
    """跨 loop 是这套桥存在的理由：桥协程必须跑在桥线程上。"""
    url, factory, _ = file_db_url
    monkeypatch.setattr(db_module.settings, "DATABASE_URL", url)
    monkeypatch.setattr(db_module, "create_async_engine", factory)

    bridge = SyncDbBridge()
    caller_thread = threading.current_thread()

    async def _where():
        return threading.current_thread().name, asyncio.get_running_loop()

    try:
        name, loop = bridge.run(_where(), timeout=30)
        assert name != caller_thread.name
        assert "cds-sync-db-bridge" in name
        assert loop is not asyncio.get_running_loop()
    finally:
        bridge.close()
