import asyncio
import time
import uuid

import pytest

from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus

BLOCK_SECONDS = 0.4
TICK_SECONDS = 0.02


async def _mk_running_session(db_session, make_user, prefix="blk"):
    _, user_id = await make_user("data_provider", prefix)
    product = DataProduct(provider_id=uuid.UUID(user_id), name=f"p-{prefix}", status="published")
    db_session.add(product)
    await db_session.flush()
    session = SandboxSession(
        user_id=uuid.UUID(user_id),
        data_product_id=product.id,
        sandbox_level="L3",
        status=SessionStatus.RUNNING.value,
        timeout_seconds=3600,
        container_id="l2c-blockprobe",
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


class _SlowRuntime:
    def __init__(self):
        self.terminated = []

    def terminate(self, container_id):
        time.sleep(BLOCK_SECONDS)
        self.terminated.append(container_id)


async def test_terminate_session_does_not_block_the_event_loop(db_session, make_user, monkeypatch):
    import app.services.session_lifecycle as lc

    session = await _mk_running_session(db_session, make_user)
    runtime = _SlowRuntime()

    async def fake_manager():
        return runtime

    monkeypatch.setattr(lc, "get_sandbox_manager", fake_manager)

    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(TICK_SECONDS)

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(TICK_SECONDS * 2)
    before = ticks
    ok = await lc.terminate_session(session, db_session, reason="loop-blocking probe")
    during = ticks - before
    ticker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ticker

    assert ok is True
    assert runtime.terminated == ["l2c-blockprobe"]
    assert session.status == SessionStatus.TERMINATED.value
    assert during >= 3
