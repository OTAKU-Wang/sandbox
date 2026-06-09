"""Task Scheduler — background task scheduling for async pipeline jobs.

Provides priority-based task scheduling with concurrency control.
Integrates with PipelineTask model for persistent task tracking.
"""
import asyncio
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SchedulerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass
class SchedulerConfig:
    """Configuration for the task scheduler."""
    max_concurrent_tasks: int = 4
    poll_interval_seconds: float = 5.0
    max_retries: int = 3
    task_timeout_seconds: int = 3600


class TaskScheduler:
    """Background task scheduler with priority queue and concurrency control."""

    def __init__(self, config: SchedulerConfig | None = None):
        self.config = config or SchedulerConfig()
        self._state = SchedulerState.IDLE
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._task_handlers: dict[str, callable] = {}

    def register_handler(self, task_type: str, handler: callable):
        """Register a handler for a task type."""
        self._task_handlers[task_type] = handler

    @property
    def state(self) -> SchedulerState:
        return self._state

    @property
    def active_count(self) -> int:
        return len(self._running_tasks)

    async def start(self):
        """Start the scheduler loop."""
        if self._state == SchedulerState.RUNNING:
            return
        self._state = SchedulerState.RUNNING
        logger.info("Task scheduler started (max_concurrent=%d)", self.config.max_concurrent_tasks)

    async def stop(self):
        """Stop the scheduler and cancel running tasks."""
        self._state = SchedulerState.STOPPED
        for task_id, task in self._running_tasks.items():
            task.cancel()
        self._running_tasks.clear()
        logger.info("Task scheduler stopped")

    async def submit(self, task_type: str, task_id: str, payload: dict) -> bool:
        """Submit a task for async execution.

        Returns True if task was accepted, False if at capacity.
        """
        if len(self._running_tasks) >= self.config.max_concurrent_tasks:
            logger.warning("Scheduler at capacity (%d/%d)", len(self._running_tasks), self.config.max_concurrent_tasks)
            return False

        handler = self._task_handlers.get(task_type)
        if not handler:
            logger.error("No handler registered for task type: %s", task_type)
            return False

        async def _run():
            try:
                await handler(task_id, payload)
            except Exception as e:
                logger.error("Task %s failed: %s", task_id, e)
            finally:
                self._running_tasks.pop(task_id, None)

        task = asyncio.create_task(_run())
        self._running_tasks[task_id] = task
        return True

    def get_status(self) -> dict:
        """Get scheduler status."""
        return {
            "state": self._state.value,
            "active_tasks": len(self._running_tasks),
            "max_concurrent": self.config.max_concurrent_tasks,
            "registered_handlers": list(self._task_handlers.keys()),
        }


# Singleton
task_scheduler = TaskScheduler()
