import asyncio
import threading
import uuid
from concurrent.futures import Future

import sqlite3

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

# Register UUID adapter for SQLite so UUID columns work with aiosqlite
sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)
sqlite3.register_converter("UUID", lambda b: uuid.UUID(b.decode()))

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
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session_factory = None

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
            self._session_factory = async_sessionmaker(
                bridge_engine, class_=AsyncSession, expire_on_commit=False
            )
            ready = Future()

            def _warm() -> None:
                asyncio.set_event_loop(loop)
                self._loop = loop

                async def _ping() -> None:
                    try:
                        async with bridge_engine.connect() as conn:
                            from sqlalchemy import text

                            await conn.execute(text("SELECT 1"))
                    finally:
                        ready.set_result(None)

                asyncio.ensure_future(_ping())
                loop.run_forever()

            thread = threading.Thread(target=_warm, daemon=True, name="cds-sync-db-bridge")
            thread.start()
            self._thread = thread
            ready.result(timeout=30)

    def run(self, coro, timeout: float = 30.0):
        """Run an async coroutine on the bridge loop from a sync caller.

        The coroutine must use `sync_bridge_session()`, never the shared
        `async_session` (that one belongs to the main event loop).
        """
        self._ensure()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self) -> None:
        with self._lock:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop = None


sync_db_bridge = SyncDbBridge()


def sync_bridge_session() -> AsyncSession:
    """Session for bridge-loop coroutines: `async with sync_bridge_session() as db:`."""
    sync_db_bridge._ensure()
    assert sync_db_bridge._session_factory is not None
    return sync_db_bridge._session_factory()
