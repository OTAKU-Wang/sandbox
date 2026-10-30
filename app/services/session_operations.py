"""W11: session operation service — submit/track long-running workspace ops.

Sync path (default) executes inline and returns the terminal-state operation
so existing callers see unchanged behavior plus an audit record. Async path
returns a 202-able pending operation and drives execution in a background
task (``asyncio.create_task``); multi-replica offload to an external queue is
W16 scope.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session_operation import SessionOperation

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"succeeded", "failed"}
# W11 mutual exclusion: only one rollback may run per session at a time.
_EXCLUSIVE_OP_TYPES = {"rollback"}

# A82 lease: a `running` row is only provably stale against `started_at`. A row
# left `running` by a crashed/restarted process has no executor anywhere — the
# only writer of `running` sets `started_at` in the same flush — so once the
# lease expires a boot-time sweep may reclaim it. Without that sweep the
# rollback exclusivity gate refuses the session forever.
OPERATION_LEASE_SECONDS = 3600

_background_tasks: set[asyncio.Task] = set()


async def flush_background_operations() -> None:
    """Await all pending async operation tasks (determinism for tests)."""
    if _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)


def _new_operation(db: AsyncSession, session_id: uuid.UUID, op_type: str) -> SessionOperation:
    op = SessionOperation(session_id=session_id, op_type=op_type, status="pending", progress=0.0)
    db.add(op)
    return op


async def _has_running_exclusive(db: AsyncSession, session_id: uuid.UUID, op_type: str) -> bool:
    if op_type not in _EXCLUSIVE_OP_TYPES:
        return False
    result = await db.execute(
        select(SessionOperation).where(
            SessionOperation.session_id == session_id,
            SessionOperation.op_type == op_type,
            SessionOperation.status.in_(["pending", "running"]),
        )
    )
    return result.scalar_one_or_none() is not None


async def submit_operation(
    db: AsyncSession,
    session_id: uuid.UUID,
    op_type: str,
    *,
    run_async: bool,
    executor,
) -> SessionOperation:
    """Create (and optionally start) a session operation.

    Args:
        executor: ``async def (op: SessionOperation) -> dict`` running the
            actual work and returning the ``result_ref`` payload. It may
            raise — the wrapper records status=failed.
        run_async: False → execute inline, return the op in a terminal
            state (sync callers unchanged). True → return the pending op
            and run in a background task.
    """
    if await _has_running_exclusive(db, session_id, op_type):
        from app.core.errors import OperationLocked

        raise OperationLocked(f"A {op_type} operation is already in progress for this session")

    op = _new_operation(db, session_id, op_type)
    try:
        await db.flush()
    except IntegrityError as exc:
        # A82: the gate above is SELECT-then-INSERT; the partial UNIQUE index
        # uq_session_ops_rollback_inflight (app/models/session_operation.py) is
        # what makes the losing racer fail closed at the database. Surface the
        # collision as the same 423 the pre-check raises — callers already map
        # OperationLocked; it must never escape as a 500. The request session
        # is deliberately NOT rolled back here (async SQLAlchemy lesson: a
        # mid-request rollback expires every loaded ORM object; get_db's
        # except-branch rolls back when the 423 propagates).
        from app.core.errors import OperationLocked

        raise OperationLocked(
            f"A {op_type} operation is already in progress for this session"
        ) from exc

    if not run_async:
        await _execute(db, op, executor)
        return op

    # Persist the operation row BEFORE the response returns: the background
    # runner uses its own session and must see it committed. Refresh after
    # the commit so the response payload reads loaded attributes only
    # (lazy loads are illegal in async ORM attribute access).
    op.status = "running"
    op.started_at = datetime.now(timezone.utc)  # A82 lease heartbeat (async path)
    await db.commit()
    await db.refresh(op)

    async def _runner() -> None:
        try:
            from app.core.database import async_session

            async with async_session() as task_db:
                reloaded = await task_db.get(SessionOperation, op.op_id)
                if reloaded is None:
                    return
                await _execute(task_db, reloaded, executor)
                await task_db.commit()
        except Exception as e:
            logger.error("[SessionOps] Async operation %s crashed: %s", op.op_id, e)

    task = asyncio.get_running_loop().create_task(_runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return op


async def _execute(db: AsyncSession, op: SessionOperation, executor) -> None:
    op.status = "running"
    # A82 lease heartbeat: every writer of `running` stamps started_at, so a
    # later `running` row without a live heartbeat is provably an orphan.
    op.started_at = op.started_at or datetime.now(timezone.utc)
    op.progress = 0.1
    await db.flush()
    try:
        result_ref = await executor(op)
        op.result_ref = result_ref or {}
        op.progress = 1.0
        op.status = "succeeded"
    except Exception as e:
        logger.error("[SessionOps] Operation %s failed: %s", op.op_id, e)
        op.status = "failed"
        op.error = str(e)
        try:
            from app.services.audit_service import audit_service

            await audit_service.log(
                db, action="operation.failed", resource_type="session_operation",
                resource_id=str(op.op_id), session_id=op.session_id,
                detail={"op_type": op.op_type, "error": str(e)},
            )
        except Exception:
            pass
    finally:
        op.finished_at = datetime.now(timezone.utc)  # A82 lease release
    await db.flush()


async def get_operation(db: AsyncSession, op_id: uuid.UUID) -> SessionOperation | None:
    return await db.get(SessionOperation, op_id)


async def reclaim_stale_operations(db: AsyncSession, *, lease_seconds: int = OPERATION_LEASE_SECONDS) -> int:
    """A82 boot sweep: mark operations orphaned by a process restart as failed.

    A `running` row is reclaimed when its lease expired (`started_at` older than
    `lease_seconds`) or when it predates the lease columns (`started_at IS NULL`
    — the pre-A82 writer set `running` without any heartbeat, so such a row can
    never be proven alive). This runs at boot, before the first request is
    served; a genuinely-running operation on another replica carries a fresh
    `started_at` and is untouched. A `pending` row cannot be committed-orphaned:
    the async path commits only as `running`, and the sync path's `pending` row
    rolls back with its request transaction.

    Without this sweep a wedged `running` rollback keeps
    `_has_running_exclusive` true forever and refuses every later rollback of
    that session — a permanent lock, not a slow one.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)
    rows = (await db.execute(
        select(SessionOperation).where(
            SessionOperation.status == "running",
            or_(SessionOperation.started_at.is_(None), SessionOperation.started_at < cutoff),
        )
    )).scalars().all()
    for op in rows:
        op.status = "failed"
        op.error = (
            f"reclaimed at startup: no lease heartbeat within {lease_seconds}s "
            "(orphaned by a process restart)"
        )[:2000]
        op.finished_at = datetime.now(timezone.utc)
    if rows:
        await db.flush()
    return len(rows)


def operation_payload(op: SessionOperation) -> dict:
    return {
        "operation_id": str(op.op_id),
        "session_id": str(op.session_id),
        "op_type": op.op_type,
        "status": op.status,
        "progress": op.progress,
        "error": op.error,
        "result_ref": op.result_ref,
        # A82 lease timestamps — clients/ops can tell an actively running op
        # from one the boot sweep considers stale.
        "started_at": op.started_at.isoformat() if op.started_at else None,
        "finished_at": op.finished_at.isoformat() if op.finished_at else None,
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "updated_at": op.updated_at.isoformat() if op.updated_at else None,
    }
