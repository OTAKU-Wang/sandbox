"""W16: durable multi-replica task queue — claim exclusivity, retry, stale requeue."""
import pytest

from app.models.task_queue import claim_next, complete, enqueue, fail, requeue_stale
from sqlalchemy import select


@pytest.mark.asyncio
async def test_enqueue_and_claim_fifo(db_session):
    for i in range(3):
        await enqueue(db_session, "retention_sweep", {"n": i})
    claimed = await claim_next(db_session, "worker-1", batch=2)
    assert [c.payload["n"] for c in claimed] == [0, 1]
    assert all(c.status == "claimed" and c.claimed_by == "worker-1" for c in claimed)

    again = await claim_next(db_session, "worker-2")
    assert again[0].payload["n"] == 2
    assert again[0].claimed_by == "worker-2"


@pytest.mark.asyncio
async def test_claim_exclusive_no_double_claim(db_session):
    item = await enqueue(db_session, "job", {"x": 1})
    first = await claim_next(db_session, "w1")
    second = await claim_next(db_session, "w2")
    assert len(first) == 1 and len(second) == 0
    await db_session.refresh(item)
    assert item.claimed_by == "w1"


@pytest.mark.asyncio
async def test_task_type_filter(db_session):
    await enqueue(db_session, "type_a", {"v": 1})
    await enqueue(db_session, "type_b", {"v": 2})
    claimed = await claim_next(db_session, "w", task_types=["type_b"])
    assert len(claimed) == 1 and claimed[0].task_type == "type_b"


@pytest.mark.asyncio
async def test_fail_retries_with_backoff_then_failed(db_session):
    item = await enqueue(db_session, "flaky", {}, max_attempts=2)
    (claimed,) = await claim_next(db_session, "w")
    await fail(db_session, claimed, "boom", backoff_s=0)
    await db_session.refresh(item)
    assert item.status == "pending" and item.attempts == 1

    (claimed2,) = await claim_next(db_session, "w")
    await fail(db_session, claimed2, "boom again", backoff_s=0)
    await db_session.refresh(item)
    assert item.status == "failed" and item.attempts == 2
    assert item.last_error == "boom again"


@pytest.mark.asyncio
async def test_complete_and_delayed_items_not_claimable(db_session):
    item = await enqueue(db_session, "ok", {})
    (claimed,) = await claim_next(db_session, "w")
    await complete(db_session, claimed)
    assert await claim_next(db_session, "w") == []

    delayed = await enqueue(db_session, "later", {}, delay_s=3600)
    assert await claim_next(db_session, "w") == []
    await db_session.refresh(delayed)
    assert delayed.status == "pending"


@pytest.mark.asyncio
async def test_requeue_stale_claims(db_session):
    from app.models.task_queue import TaskQueueItem

    item = await enqueue(db_session, "crashed", {})
    await claim_next(db_session, "dead-worker", visibility_s=0)
    requeued = await requeue_stale(db_session, visibility_s=0)
    assert requeued == 1
    await db_session.refresh(item)
    assert item.status == "pending" and item.claimed_by is None

    (reclaimed,) = await claim_next(db_session, "live-worker")
    assert reclaimed.id == item.id
    result = await db_session.execute(
        select(TaskQueueItem).where(TaskQueueItem.status == "claimed")
    )
    assert len(result.scalars().all()) == 1
