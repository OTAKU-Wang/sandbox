"""Redis-based Task Queue — SS-03 异步任务调度。

Provides async task scheduling with priority queues, retry logic,
and integration with TaskStateMachine for status transitions.
Falls back to in-memory queue when Redis is unavailable.
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    logger.warning("redis not installed — TaskQueue will use in-memory fallback")


class TaskPriority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class QueueTaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"


@dataclass
class QueueTask:
    """A task in the queue."""
    task_id: str
    task_type: str  # e.g. "sandbox_run", "code_scan", "output_inspect"
    payload: dict
    priority: TaskPriority = TaskPriority.NORMAL
    status: QueueTaskStatus = QueueTaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    claimed_at: float | None = None
    completed_at: float | None = None
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None
    result: dict | None = None
    session_id: str | None = None
    timeout_seconds: int = 300

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "payload": self.payload,
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "claimed_at": self.claimed_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error": self.error,
            "result": self.result,
            "session_id": self.session_id,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "QueueTask":
        return cls(
            task_id=data["task_id"],
            task_type=data["task_type"],
            payload=data["payload"],
            priority=TaskPriority(data.get("priority", 1)),
            status=QueueTaskStatus(data.get("status", "pending")),
            created_at=data.get("created_at", 0),
            claimed_at=data.get("claimed_at"),
            completed_at=data.get("completed_at"),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            error=data.get("error"),
            result=data.get("result"),
            session_id=data.get("session_id"),
            timeout_seconds=data.get("timeout_seconds", 300),
        )


class TaskQueue:
    """Redis-based async task queue with in-memory fallback.

    Features:
    - Priority queues (LOW/NORMAL/HIGH/CRITICAL)
    - Automatic retry with exponential backoff
    - Dead letter queue for permanently failed tasks
    - Task timeout detection
    - Integration with TaskStateMachine
    """

    def __init__(self, redis_url: str | None = None, prefix: str = "cds:queue"):
        self._prefix = prefix
        self._redis: Any = None
        self._redis_url = redis_url
        self._memory_queue: list[QueueTask] = []  # fallback
        self._memory_processing: dict[str, QueueTask] = {}
        self._memory_completed: dict[str, QueueTask] = {}
        self._memory_dead: dict[str, QueueTask] = {}
        self._connected = False

    async def connect(self, redis_url: str | None = None) -> bool:
        """Connect to Redis. Returns True if successful."""
        url = redis_url or self._redis_url
        if not url or not _HAS_REDIS:
            logger.info("TaskQueue using in-memory fallback (no Redis)")
            self._connected = True
            return False

        try:
            self._redis = aioredis.from_url(url, decode_responses=True)
            await self._redis.ping()
            self._connected = True
            logger.info(f"TaskQueue connected to Redis: {url}")
            return True
        except Exception as e:
            logger.warning(f"TaskQueue Redis connection failed: {e}, using in-memory fallback")
            self._redis = None
            self._connected = True
            return False

    async def enqueue(self, task_type: str, payload: dict,
                      priority: TaskPriority = TaskPriority.NORMAL,
                      session_id: str | None = None,
                      max_retries: int = 3,
                      timeout_seconds: int = 300) -> QueueTask:
        """Enqueue a new task."""
        task = QueueTask(
            task_id=str(uuid.uuid4())[:12],
            task_type=task_type,
            payload=payload,
            priority=priority,
            session_id=session_id,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
        )

        if self._redis:
            await self._redis.hset(
                f"{self._prefix}:tasks",
                task.task_id,
                json.dumps(task.to_dict()),
            )
            # Push to priority queue (score = priority * 1e12 + timestamp for FIFO within priority)
            score = priority.value * 1e12 + task.created_at
            await self._redis.zadd(f"{self._prefix}:pending", {task.task_id: score})
        else:
            self._memory_queue.append(task)
            self._memory_queue.sort(key=lambda t: (-t.priority.value, t.created_at))

        return task

    async def dequeue(self, timeout: int = 0) -> QueueTask | None:
        """Dequeue the highest-priority pending task."""
        if self._redis:
            # Atomic pop from sorted set
            result = await self._redis.zpopmin(f"{self._prefix}:pending")
            if not result:
                if timeout > 0:
                    await asyncio.sleep(min(timeout, 1))
                return None

            task_id = result[0][0]
            task_data = await self._redis.hget(f"{self._prefix}:tasks", task_id)
            if not task_data:
                return None

            task = QueueTask.from_dict(json.loads(task_data))
            task.status = QueueTaskStatus.CLAIMED
            task.claimed_at = time.time()
            await self._redis.hset(
                f"{self._prefix}:tasks",
                task.task_id,
                json.dumps(task.to_dict()),
            )
            await self._redis.hset(f"{self._prefix}:processing", task.task_id, str(time.time()))
            return task
        else:
            if not self._memory_queue:
                if timeout > 0:
                    await asyncio.sleep(min(timeout, 1))
                return None
            task = self._memory_queue.pop(0)
            task.status = QueueTaskStatus.CLAIMED
            task.claimed_at = time.time()
            self._memory_processing[task.task_id] = task
            return task

    async def complete(self, task_id: str, result: dict | None = None) -> bool:
        """Mark a task as completed."""
        if self._redis:
            task_data = await self._redis.hget(f"{self._prefix}:tasks", task_id)
            if not task_data:
                return False
            task = QueueTask.from_dict(json.loads(task_data))
            task.status = QueueTaskStatus.COMPLETED
            task.completed_at = time.time()
            task.result = result
            await self._redis.hset(
                f"{self._prefix}:tasks",
                task_id,
                json.dumps(task.to_dict()),
            )
            await self._redis.hdel(f"{self._prefix}:processing", task_id)
            await self._redis.hset(f"{self._prefix}:completed", task_id, str(time.time()))
            return True
        else:
            task = self._memory_processing.pop(task_id, None)
            if not task:
                return False
            task.status = QueueTaskStatus.COMPLETED
            task.completed_at = time.time()
            task.result = result
            self._memory_completed[task_id] = task
            return True

    async def fail(self, task_id: str, error: str) -> bool:
        """Mark a task as failed. Retries if under max_retries."""
        if self._redis:
            task_data = await self._redis.hget(f"{self._prefix}:tasks", task_id)
            if not task_data:
                return False
            task = QueueTask.from_dict(json.loads(task_data))
            task.retry_count += 1
            task.error = error

            if task.retry_count < task.max_retries:
                task.status = QueueTaskStatus.RETRYING
                # Re-enqueue with backoff delay
                backoff = min(2 ** task.retry_count, 60)
                score = task.priority.value * 1e12 + time.time() + backoff
                await self._redis.zadd(f"{self._prefix}:pending", {task_id: score})
            else:
                task.status = QueueTaskStatus.DEAD
                await self._redis.hset(f"{self._prefix}:dead", task_id, json.dumps(task.to_dict()))

            await self._redis.hset(f"{self._prefix}:tasks", task_id, json.dumps(task.to_dict()))
            await self._redis.hdel(f"{self._prefix}:processing", task_id)
            return True
        else:
            task = self._memory_processing.pop(task_id, None)
            if not task:
                return False
            task.retry_count += 1
            task.error = error

            if task.retry_count < task.max_retries:
                task.status = QueueTaskStatus.RETRYING
                self._memory_queue.append(task)
                self._memory_queue.sort(key=lambda t: (-t.priority.value, t.created_at))
            else:
                task.status = QueueTaskStatus.DEAD
                self._memory_dead[task_id] = task
            return True

    async def get_task(self, task_id: str) -> QueueTask | None:
        """Get task by ID."""
        if self._redis:
            task_data = await self._redis.hget(f"{self._prefix}:tasks", task_id)
            if task_data:
                return QueueTask.from_dict(json.loads(task_data))
            return None
        else:
            for task in self._memory_queue:
                if task.task_id == task_id:
                    return task
            for d in [self._memory_processing, self._memory_completed, self._memory_dead]:
                if task_id in d:
                    return d[task_id]
            return None

    async def check_timeouts(self) -> list[QueueTask]:
        """Check for timed-out tasks and re-enqueue them."""
        timed_out = []
        now = time.time()

        if self._redis:
            processing = await self._redis.hgetall(f"{self._prefix}:processing")
            for task_id, claimed_str in processing.items():
                claimed = float(claimed_str)
                task_data = await self._redis.hget(f"{self._prefix}:tasks", task_id)
                if task_data:
                    task = QueueTask.from_dict(json.loads(task_data))
                    if now - claimed > task.timeout_seconds:
                        await self.fail(task_id, "Task timed out")
                        timed_out.append(task)
        else:
            for task_id, task in list(self._memory_processing.items()):
                if task.claimed_at and now - task.claimed_at > task.timeout_seconds:
                    await self.fail(task_id, "Task timed out")
                    timed_out.append(task)

        return timed_out

    async def stats(self) -> dict:
        """Get queue statistics."""
        if self._redis:
            pending = await self._redis.zcard(f"{self._prefix}:pending")
            processing = await self._redis.hlen(f"{self._prefix}:processing")
            completed = await self._redis.hlen(f"{self._prefix}:completed")
            dead = await self._redis.hlen(f"{self._prefix}:dead")
            return {
                "backend": "redis",
                "pending": pending,
                "processing": processing,
                "completed": completed,
                "dead": dead,
            }
        else:
            return {
                "backend": "memory",
                "pending": len(self._memory_queue),
                "processing": len(self._memory_processing),
                "completed": len(self._memory_completed),
                "dead": len(self._memory_dead),
            }

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
        self._connected = False
