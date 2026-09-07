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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session_operation import SessionOperation

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {"succeeded", "failed"}
# W11 mutual exclusion: only one rollback may run per session at a time.
_EXCLUSIVE_OP_TYPES = {"rollback"}

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
    await db.flush()

    if not run_async:
        await _execute(db, op, executor)
        return op

    # Persist the operation row BEFORE the response returns: the background
    # runner uses its own session and must see it committed. Refresh after
    # the commit so the response payload reads loaded attributes only
    # (lazy loads are illegal in async ORM attribute access).
    op.status = "running"
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
    await db.flush()


async def get_operation(db: AsyncSession, op_id: uuid.UUID) -> SessionOperation | None:
    return await db.get(SessionOperation, op_id)


def operation_payload(op: SessionOperation) -> dict:
    return {
        "operation_id": str(op.op_id),
        "session_id": str(op.session_id),
        "op_type": op.op_type,
        "status": op.status,
        "progress": op.progress,
        "error": op.error,
        "result_ref": op.result_ref,
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "updated_at": op.updated_at.isoformat() if op.updated_at else None,
    }
