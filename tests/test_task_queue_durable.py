"""Task Queue tests — Redis-backed async scheduling."""
import asyncio
import pytest


# ─── In-Memory Queue Tests ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enqueue_and_dequeue():
    """Unit test: enqueue a task and dequeue it."""
    from app.services.task_queue import TaskQueue, TaskPriority
    queue = TaskQueue()
    await queue.connect()

    task = await queue.enqueue("sandbox_run", {"session_id": "s1"}, priority=TaskPriority.NORMAL)
    assert task.task_id is not None
    assert task.status.value == "pending"

    dequeued = await queue.dequeue()
    assert dequeued is not None
    assert dequeued.task_id == task.task_id
    assert dequeued.status.value == "claimed"


@pytest.mark.asyncio
async def test_priority_ordering():
    """Unit test: higher priority tasks dequeue first."""
    from app.services.task_queue import TaskQueue, TaskPriority
    queue = TaskQueue()
    await queue.connect()

    low = await queue.enqueue("type_a", {}, priority=TaskPriority.LOW)
    high = await queue.enqueue("type_b", {}, priority=TaskPriority.HIGH)
    critical = await queue.enqueue("type_c", {}, priority=TaskPriority.CRITICAL)
    normal = await queue.enqueue("type_d", {}, priority=TaskPriority.NORMAL)

    first = await queue.dequeue()
    assert first.task_id == critical.task_id

    second = await queue.dequeue()
    assert second.task_id == high.task_id

    third = await queue.dequeue()
    assert third.task_id == normal.task_id

    fourth = await queue.dequeue()
    assert fourth.task_id == low.task_id


@pytest.mark.asyncio
async def test_complete_task():
    """Unit test: complete a task records result."""
    from app.services.task_queue import TaskQueue, QueueTaskStatus
    queue = TaskQueue()
    await queue.connect()

    task = await queue.enqueue("sandbox_run", {"session_id": "s1"})
    await queue.dequeue()
    ok = await queue.complete(task.task_id, result={"output": "done"})

    assert ok is True
    retrieved = await queue.get_task(task.task_id)
    assert retrieved.status == QueueTaskStatus.COMPLETED
    assert retrieved.result == {"output": "done"}


@pytest.mark.asyncio
async def test_fail_and_retry():
    """Unit test: failed task retries up to max_retries."""
    from app.services.task_queue import TaskQueue, QueueTaskStatus
    queue = TaskQueue()
    await queue.connect()

    task = await queue.enqueue("sandbox_run", {}, max_retries=2)
    await queue.dequeue()

    # First fail — should retry
    await queue.fail(task.task_id, "error 1")
    retrieved = await queue.get_task(task.task_id)
    assert retrieved.retry_count == 1
    assert retrieved.status == QueueTaskStatus.RETRYING

    # Dequeue again (retry)
    retried = await queue.dequeue()
    assert retried.task_id == task.task_id

    # Second fail — should go dead
    await queue.fail(task.task_id, "error 2")
    retrieved = await queue.get_task(task.task_id)
    assert retrieved.retry_count == 2
    assert retrieved.status == QueueTaskStatus.DEAD


@pytest.mark.asyncio
async def test_dequeue_empty():
    """Unit test: dequeue from empty queue returns None."""
    from app.services.task_queue import TaskQueue
    queue = TaskQueue()
    await queue.connect()

    result = await queue.dequeue()
    assert result is None


@pytest.mark.asyncio
async def test_get_nonexistent_task():
    """Unit test: get_task for nonexistent ID returns None."""
    from app.services.task_queue import TaskQueue
    queue = TaskQueue()
    await queue.connect()

    result = await queue.get_task("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_stats():
    """Unit test: stats returns correct counts."""
    from app.services.task_queue import TaskQueue, TaskPriority
    queue = TaskQueue()
    await queue.connect()

    await queue.enqueue("a", {}, priority=TaskPriority.LOW)
    await queue.enqueue("b", {}, priority=TaskPriority.HIGH)
    task = await queue.enqueue("c", {})
    await queue.dequeue()  # one moves to processing

    stats = await queue.stats()
    assert stats["pending"] == 2
    assert stats["processing"] == 1
    assert stats["backend"] == "memory"


@pytest.mark.asyncio
async def test_timeout_detection():
    """Unit test: timed-out tasks are detected and re-enqueued."""
    from app.services.task_queue import TaskQueue, QueueTaskStatus
    queue = TaskQueue()
    await queue.connect()

    # Enqueue with 0-second timeout
    task = await queue.enqueue("sandbox_run", {}, timeout_seconds=0)
    await queue.dequeue()

    # Check timeouts immediately
    import asyncio
    await asyncio.sleep(0.01)
    timed_out = await queue.check_timeouts()
    assert len(timed_out) == 1
    assert timed_out[0].task_id == task.task_id


@pytest.mark.asyncio
async def test_task_serialization():
    """Unit test: QueueTask to_dict/from_dict roundtrip."""
    from app.services.task_queue import QueueTask, TaskPriority, QueueTaskStatus
    original = QueueTask(
        task_id="test-123",
        task_type="sandbox_run",
        payload={"key": "value"},
        priority=TaskPriority.HIGH,
        status=QueueTaskStatus.PENDING,
        session_id="session-1",
        max_retries=5,
        timeout_seconds=600,
    )

    data = original.to_dict()
    restored = QueueTask.from_dict(data)

    assert restored.task_id == original.task_id
    assert restored.task_type == original.task_type
    assert restored.payload == original.payload
    assert restored.priority == original.priority
    assert restored.max_retries == original.max_retries
    assert restored.timeout_seconds == original.timeout_seconds


@pytest.mark.asyncio
async def test_fail_nonexistent_task():
    """Unit test: fail on nonexistent task returns False."""
    from app.services.task_queue import TaskQueue
    queue = TaskQueue()
    await queue.connect()

    ok = await queue.fail("nonexistent", "error")
    assert ok is False


@pytest.mark.asyncio
async def test_complete_nonexistent_task():
    """Unit test: complete on nonexistent task returns False."""
    from app.services.task_queue import TaskQueue
    queue = TaskQueue()
    await queue.connect()

    ok = await queue.complete("nonexistent")
    assert ok is False


@pytest.mark.asyncio
async def test_multiple_enqueue_dequeue():
    """Unit test: multiple enqueue/dequeue cycles work correctly."""
    from app.services.task_queue import TaskQueue, TaskPriority
    queue = TaskQueue()
    await queue.connect()

    tasks = []
    for i in range(10):
        t = await queue.enqueue(f"type_{i}", {"index": i})
        tasks.append(t)

    dequeued = []
    for _ in range(10):
        t = await queue.dequeue()
        if t:
            dequeued.append(t)

    assert len(dequeued) == 10
    assert len(set(t.task_id for t in dequeued)) == 10  # all unique


@pytest.mark.asyncio
async def test_priority_enum_values():
    """Unit test: priority enum values are correct."""
    from app.services.task_queue import TaskPriority
    assert TaskPriority.LOW == 0
    assert TaskPriority.NORMAL == 1
    assert TaskPriority.HIGH == 2
    assert TaskPriority.CRITICAL == 3


@pytest.mark.asyncio
async def test_queue_task_status_enum():
    """Unit test: all queue task statuses."""
    from app.services.task_queue import QueueTaskStatus
    assert QueueTaskStatus.PENDING.value == "pending"
    assert QueueTaskStatus.CLAIMED.value == "claimed"
    assert QueueTaskStatus.PROCESSING.value == "processing"
    assert QueueTaskStatus.COMPLETED.value == "completed"
    assert QueueTaskStatus.FAILED.value == "failed"
    assert QueueTaskStatus.RETRYING.value == "retrying"
    assert QueueTaskStatus.DEAD.value == "dead"


@pytest.mark.asyncio
async def test_close_idempotent():
    """Unit test: close() is safe to call multiple times."""
    from app.services.task_queue import TaskQueue
    queue = TaskQueue()
    await queue.connect()
    await queue.close()
    await queue.close()  # Should not raise
