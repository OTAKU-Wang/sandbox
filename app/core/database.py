import asyncio
import threading
import uuid
from concurrent.futures import Future

import logging
import sqlite3

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

# Register UUID adapter for SQLite so UUID columns work with aiosqlite
sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)
sqlite3.register_converter("UUID", lambda b: uuid.UUID(b.decode()))

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,  # Recycle connections after 1 hour
    pool_pre_ping=True,  # Verify connection health before use
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# SS-14 RT-07 (2026-10-14): 同步桥 —— 同步适配器代码（cube adapter 的
# provision/terminate 等, 运行在 to_thread worker 线程）需要 DB 访问, 但共享
# 的 asyncpg 引擎绑定主事件循环; 旧 _run_sync 每次新开线程+新 loop 复用共享
# 引擎 → asyncpg 跨 loop（实测: RuntimeError "attached to a different loop",
# 以及 native 空指针段错误）。桥拥有独立线程+独立 loop+独立引擎, 与主循环
# 零共享, 跨 loop 面结构性消失。
class SyncDbBridge:
    """Bridge a coroutine onto a private event loop + engine (SS-14 RT-07).

    The bridge owns its engine, so its connections are never shared with the
    main loop's asyncpg pool. A42 (2026-10-18) removed three ways this used to
    fail while looking healthy:

    * a failed ``SELECT 1`` resolved ``ready`` from a ``finally`` block, so an
      unreachable DB looked "verified"; the error died unretrieved inside the
      ``ensure_future`` task and surfaced on the first real query instead;
    * ``_warm`` wrote ``self._loop`` before the ping, so after a warm-up
      timeout every later ``_ensure()`` short-circuited on
      ``_loop is not None`` — the bridge stayed permanently unverified;
    * ``close()`` stopped the loop without disposing the engine, without
      joining the thread and without clearing the session factory, so the next
      ``_ensure()`` built a second loop+engine and leaked the first pool.

    Now ``ready`` resolves only on a successful ping; ``_loop`` /
    ``_session_factory`` / ``_engine`` are published by the *calling* thread
    only after verification; and every teardown path disposes, stops, joins and
    clears state so the next ``_ensure()`` builds a clean bridge.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session_factory = None
        self._engine = None
        # Warm-up ping budget / teardown budget as state (not literals) so
        # tests and a future readiness probe can drive them without patching.
        self._warm_timeout: float = 30.0
        self._shutdown_timeout: float = 10.0

    def _ensure(self) -> None:
        with self._lock:
            if self._loop is not None:
                return
            loop = asyncio.new_event_loop()
            bridge_engine = create_async_engine(
                settings.DATABASE_URL,
                echo=settings.DATABASE_ECHO,
                pool_size=2,
                max_overflow=1,
                pool_recycle=3600,
                pool_pre_ping=True,
            )
            session_factory = async_sessionmaker(
                bridge_engine, class_=AsyncSession, expire_on_commit=False
            )
            ready: Future = Future()

            async def _ping() -> None:
                async with bridge_engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))

            def _teardown() -> None:
                """Dispose the pool and close the loop, from the loop's thread."""
                try:
                    for task in asyncio.all_tasks(loop):
                        task.cancel()
                    loop.run_until_complete(bridge_engine.dispose())
                except BaseException as exc:  # teardown must not kill the thread
                    logger.warning("sync DB bridge engine dispose failed: %s", exc)
                finally:
                    try:
                        if not loop.is_closed():
                            loop.close()
                    except BaseException as exc:
                        logger.warning("sync DB bridge loop close failed: %s", exc)

            def _warm() -> None:
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(_ping())
                except BaseException as exc:
                    # 健康检查失败（或被 close()/超时叫停）：当场报给 _ensure()，
                    # 绝不进 run_forever() 装作一把可用的桥。
                    _resolve(ready, error=exc)
                    _teardown()
                    return
                _resolve(ready, result=None)  # ← 只有真 ping 通才算就绪
                loop.run_forever()
                _teardown()  # close() 路径的兜底（dispose 幂等）

            thread = threading.Thread(target=_warm, daemon=True, name="cds-sync-db-bridge")
            thread.start()
            try:
                ready.result(timeout=self._warm_timeout)
            except BaseException:
                # 失败/超时：叫线程收摊并等它退出，然后把状态清空 —— 下一次
                # _ensure() 必须重建，而不是把半初始化当成"已就绪"。
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
                thread.join(timeout=self._shutdown_timeout)
                if thread.is_alive():
                    logger.warning(
                        "sync DB bridge warm-up thread survived %.1fs shutdown; "
                        "its engine pool is left to the abandoned loop",
                        self._shutdown_timeout,
                    )
                self._loop = None
                self._thread = None
                self._session_factory = None
                self._engine = None
                raise
            # 只有验证通过的桥才对外可见
            self._loop = loop
            self._thread = thread
            self._session_factory = session_factory
            self._engine = bridge_engine

    def run(self, coro, timeout: float = 30.0):
        """Run an async coroutine on the bridge loop from a sync caller.

        The coroutine must use sync_bridge_session(), never the shared
        async_session (that one belongs to the main event loop).
        """
        self._ensure()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self) -> None:
        """Dispose the pool, stop the loop, join the thread, forget everything."""
        with self._lock:
            loop, thread, engine = self._loop, self._thread, self._engine
            self._loop = None
            self._thread = None
            self._session_factory = None
            self._engine = None

        if loop is None or loop.is_closed():
            return

        if engine is not None:
            try:
                asyncio.run_coroutine_threadsafe(engine.dispose(), loop).result(
                    timeout=self._shutdown_timeout
                )
            except BaseException as exc:
                logger.warning("sync DB bridge dispose failed: %s", exc)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError as exc:
            logger.warning("sync DB bridge loop stop failed: %s", exc)

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._shutdown_timeout)
            if thread.is_alive():
                logger.warning(
                    "sync DB bridge thread did not exit within %.1fs",
                    self._shutdown_timeout,
                )


def _resolve(future: Future, *, result=None, error: BaseException | None = None) -> None:
    """Resolve `ready` from the warm thread, tolerating a caller that gave up.

    ``ready.result(timeout=...)`` may already have timed out, so a late
    resolution must not raise InvalidStateError inside the thread.
    """
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(result)

sync_db_bridge = SyncDbBridge()


def sync_bridge_session() -> AsyncSession:
    """Session for bridge-loop coroutines: `async with sync_bridge_session() as db:`."""
    sync_db_bridge._ensure()
    assert sync_db_bridge._session_factory is not None
    return sync_db_bridge._session_factory()
