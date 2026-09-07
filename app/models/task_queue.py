"""W16: multi-replica task queue (CubeSandbox C5 alignment).

Durable, DB-backed queue so background work survives restarts and can be
claimed by any replica. Claiming is portable: PostgreSQL uses
FOR UPDATE SKIP LOCKED, SQLite relies on an optimistic conditional UPDATE
(single-writer semantics make that race-free).
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, func, select, update
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base
from sqlalchemy.orm import Mapped, mapped_column


class TaskQueueItem(Base):
    __tablename__ = "task_queue"
    __table_args__ = (
        Index("ix_task_queue_status_type_created", "status", "task_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # pending | claimed | done | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # Earliest time the item becomes claimable again (delay / retry backoff).
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue(
    db: AsyncSession,
    task_type: str,
    payload: dict,
    *,
    delay_s: float = 0,
    max_attempts: int = 3,
) -> TaskQueueItem:
    """Persist a new queue item (pending)."""
    item = TaskQueueItem(
        task_type=task_type,
        payload=payload,
        max_attempts=max_attempts,
        run_after=_utcnow() + timedelta(seconds=delay_s) if delay_s else None,
    )
    db.add(item)
    await db.flush()
    return item


async def claim_next(
    db: AsyncSession,
    worker_id: str,
    *,
    task_types: list[str] | None = None,
    visibility_s: int = 300,
    batch: int = 1,
) -> list[TaskQueueItem]:
    """Atomically claim up to `batch` pending items for one worker.

    Returns the claimed items (status=claimed, claimed_by/claimed_at set).
    Racy claimers lose via the conditional UPDATE rowcount check; on
    PostgreSQL the candidate scan additionally uses SKIP LOCKED.
    """
    now = _utcnow()
    query = (
        select(TaskQueueItem)
        .where(
            TaskQueueItem.status == "pending",
            (TaskQueueItem.run_after.is_(None)) | (TaskQueueItem.run_after <= now),
        )
        .order_by(TaskQueueItem.created_at.asc())
        .limit(batch * 4)
    )
    if task_types:
        query = query.where(TaskQueueItem.task_type.in_(task_types))
    if db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)

    result = await db.execute(query)
    claimed: list[TaskQueueItem] = []
    for item in result.scalars().all():
        if len(claimed) >= batch:
            break
        stmt = (
            update(TaskQueueItem)
            .where(TaskQueueItem.id == item.id, TaskQueueItem.status == "pending")
            .values(status="claimed", claimed_by=worker_id, claimed_at=now)
        )
        claim_result = await db.execute(stmt)
        if claim_result.rowcount == 1:
            item.status = "claimed"
            item.claimed_by = worker_id
            item.claimed_at = now
            claimed.append(item)
    return claimed


async def complete(db: AsyncSession, item: TaskQueueItem) -> None:
    item.status = "done"
    await db.flush()


async def fail(db: AsyncSession, item: TaskQueueItem, error: str, *, backoff_s: int = 30) -> None:
    """Record a failure: retry with backoff until max_attempts, then failed."""
    item.attempts += 1
    item.last_error = error[:2000]
    if item.attempts >= item.max_attempts:
        item.status = "failed"
    else:
        item.status = "pending"
        item.claimed_by = None
        item.claimed_at = None
        item.run_after = _utcnow() + timedelta(seconds=backoff_s * item.attempts)
    await db.flush()


async def requeue_stale(db: AsyncSession, *, visibility_s: int = 300) -> int:
    """Return claims whose visibility window expired back to pending.

    Handles worker crashes mid-task: the claim is not lost, the item retries.
    """
    cutoff = _utcnow() - timedelta(seconds=visibility_s)
    result = await db.execute(
        select(TaskQueueItem).where(
            TaskQueueItem.status == "claimed",
            TaskQueueItem.claimed_at.is_not(None),
            TaskQueueItem.claimed_at <= cutoff,
        )
    )
    requeued = 0
    for item in result.scalars().all():
        item.status = "pending"
        item.claimed_by = None
        item.claimed_at = None
        requeued += 1
    if requeued:
        await db.flush()
    return requeued
