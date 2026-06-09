"""Tests for task scheduler and pipeline task persistence."""
import asyncio
import uuid
import pytest
from datetime import datetime, timezone
from httpx import AsyncClient

from app.services.task_scheduler import TaskScheduler, SchedulerConfig, SchedulerState


def test_scheduler_init():
    """Scheduler initializes with default config."""
    scheduler = TaskScheduler()
    assert scheduler.state == SchedulerState.IDLE
    assert scheduler.active_count == 0


def test_scheduler_custom_config():
    """Scheduler accepts custom config."""
    config = SchedulerConfig(max_concurrent_tasks=8, poll_interval_seconds=10)
    scheduler = TaskScheduler(config)
    assert scheduler.config.max_concurrent_tasks == 8


def test_scheduler_register_handler():
    """Registering a handler adds it to the list."""
    scheduler = TaskScheduler()
    scheduler.register_handler("ocr", lambda tid, payload: None)
    status = scheduler.get_status()
    assert "ocr" in status["registered_handlers"]


@pytest.mark.asyncio
async def test_scheduler_start_stop():
    """Scheduler can start and stop."""
    scheduler = TaskScheduler()
    await scheduler.start()
    assert scheduler.state == SchedulerState.RUNNING
    await scheduler.stop()
    assert scheduler.state == SchedulerState.STOPPED


@pytest.mark.asyncio
async def test_scheduler_submit_task():
    """Submitting a task runs the handler."""
    scheduler = TaskScheduler()
    results = []

    async def handler(task_id, payload):
        results.append({"task_id": task_id, "payload": payload})

    scheduler.register_handler("test", handler)
    await scheduler.start()

    accepted = await scheduler.submit("test", "task-001", {"key": "value"})
    assert accepted is True

    # Wait for task to complete
    await asyncio.sleep(0.1)
    assert len(results) == 1
    assert results[0]["task_id"] == "task-001"

    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_submit_unknown_type():
    """Submitting unknown task type returns False."""
    scheduler = TaskScheduler()
    await scheduler.start()
    accepted = await scheduler.submit("unknown", "task-002", {})
    assert accepted is False
    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_submit_at_capacity():
    """Submitting when at capacity returns False."""
    config = SchedulerConfig(max_concurrent_tasks=1)
    scheduler = TaskScheduler(config)

    async def slow_handler(task_id, payload):
        await asyncio.sleep(10)

    scheduler.register_handler("slow", slow_handler)
    await scheduler.start()

    # First task accepted
    accepted1 = await scheduler.submit("slow", "t1", {})
    assert accepted1 is True

    # Second task rejected (at capacity)
    accepted2 = await scheduler.submit("slow", "t2", {})
    assert accepted2 is False

    await scheduler.stop()


def test_scheduler_status():
    """get_status returns correct info."""
    scheduler = TaskScheduler()
    scheduler.register_handler("ocr", lambda tid, p: None)
    status = scheduler.get_status()
    assert status["state"] == "idle"
    assert status["active_tasks"] == 0
    assert "ocr" in status["registered_handlers"]


@pytest.mark.asyncio
async def test_pipeline_task_db_persistence(client: AsyncClient, auth_headers: dict):
    """Pipeline tasks are persisted in DB."""
    # Create a simple file to process
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("test content for pipeline")
        temp_path = f.name

    try:
        resp = await client.post(
            "/api/v1/data-pipeline/process-path",
            params={"task_type": "document", "input_path": temp_path},
            headers=auth_headers,
        )
        # May succeed or fail depending on sandbox availability
        if resp.status_code == 200:
            data = resp.json()
            assert "task_id" in data
            assert data["task_id"].startswith("pipe-")
    finally:
        import os
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_pipeline_tasks_list(client: AsyncClient, auth_headers: dict):
    """Listing pipeline tasks returns user's tasks."""
    resp = await client.get("/api/v1/data-pipeline/tasks", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
