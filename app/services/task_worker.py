"""Task Worker — executes sandbox tasks asynchronously.

Picks up pending tasks, runs code in the sandbox runtime,
and updates task status with results. Uses Redis for task queuing
with in-memory fallback for single-worker deployments.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models.sandbox_task import SandboxTask, TaskStatus
from app.models.sandbox_session import SandboxSession
from app.services.sandbox_runtime import SandboxRuntime
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)

_REDIS_QUEUE_KEY = "task_worker:queue"
_worker_running = False
_worker_task: asyncio.Task | None = None


class TaskWorker:
    """Executes sandbox tasks in the background."""

    def __init__(self):
        self.runtime = SandboxRuntime()

    async def submit_to_queue(self, task_id: str) -> None:
        """Add a task to the execution queue."""
        try:
            from app.core.redis import get_redis
            redis = await get_redis()
            await redis.rpush(_REDIS_QUEUE_KEY, task_id)
            logger.info(f"Task {task_id} queued for execution")
        except Exception:
            # Fallback: execute directly in background
            asyncio.create_task(self._execute_task_direct(task_id))

    async def _execute_task_direct(self, task_id: str) -> None:
        """Execute a task directly (fallback when Redis is unavailable)."""
        async with async_session() as db:
            await self._execute_task(db, task_id)

    async def _execute_task(self, db: AsyncSession, task_id: str) -> None:
        """Execute a single task."""
        result = await db.execute(
            select(SandboxTask).where(SandboxTask.task_id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            logger.warning(f"Task {task_id} not found")
            return

        if task.status not in (TaskStatus.PENDING.value, TaskStatus.CODE_SCANNING.value):
            logger.warning(f"Task {task_id} not executable (status: {task.status})")
            return

        # Get the session
        session_result = await db.execute(
            select(SandboxSession).where(SandboxSession.id == task.session_id)
        )
        session = session_result.scalar_one_or_none()
        if not session:
            task.status = TaskStatus.FAILED.value
            task.error_message = "Sandbox session not found"
            task.completed_at = datetime.now(timezone.utc)
            await db.commit()
            return

        # Code scanning before execution (P0-5)
        from app.services.code_scanner import code_scanner
        sandbox_mode = getattr(session, "sandbox_mode", None) or "structured_query"
        from app.services.task_code_security import decrypt_task_code
        code_content = decrypt_task_code(task.code_content)
        scan_result = code_scanner.scan(
            code_content,
            task.language or "python",
            sandbox_mode,
        )
        if not scan_result.passed:
            task.status = TaskStatus.FAILED.value
            issue_msgs = [f"[{i.severity.value}] {i.code}: {i.message}" for i in scan_result.issues]
            task.error_message = f"Code scanning failed: {'; '.join(issue_msgs)}"
            task.completed_at = datetime.now(timezone.utc)
            await audit_service.log(
                db, action="sandbox_task.code_scanning", resource_type="sandbox_task",
                user_id=task.user_id, resource_id=task.task_id,
                detail={"passed": False, "issues": issue_msgs},
            )
            await db.commit()
            logger.info(f"Task {task_id} rejected by code scanner: {len(scan_result.issues)} issue(s)")
            return

        await audit_service.log(
            db, action="sandbox_task.code_scanning", resource_type="sandbox_task",
            user_id=task.user_id, resource_id=task.task_id,
            detail={"passed": True, "issue_count": len(scan_result.issues)},
        )

        # Transition to running
        task.status = TaskStatus.RUNNING.value
        task.started_at = datetime.now(timezone.utc)
        await db.commit()

        # Get session key if available
        session_key = None
        try:
            from app.services.kms_service import KMSService
            kms = KMSService()
            key_data = kms.generate_data_key(str(task.session_id))
            session_key = key_data.get("key_bytes", b"").hex()[:32]
        except Exception:
            pass

        # Execute in sandbox
        try:
            container_id = session.container_id or f"bwrap-{task.session_id}"
            exec_result = await self.runtime.execute(
                container_id=container_id,
                code=code_content,
                language=task.language or "python",
                session_key=session_key,
                timeout=task.timeout_seconds,
            )

            # Update task with results
            if exec_result.get("exit_code", -1) == 0:
                from app.services.output_security import inspect_text_output, inspection_to_report

                output = exec_result.get("output", "") or ""
                if output:
                    inspection = inspect_text_output(
                        output,
                        user_id=str(task.user_id),
                        session_id=str(task.session_id),
                        sandbox_mode=sandbox_mode,
                    )
                    report = inspection_to_report(inspection)
                    passed = inspection.passed
                    redacted_output = inspection.redacted_output or ""
                else:
                    report = {
                        "passed": True,
                        "blocked": False,
                        "stage_results": {"no_output": True},
                        "findings": [],
                        "findings_count": 0,
                    }
                    passed = True
                    redacted_output = ""

                task.status = TaskStatus.COMPLETED.value if passed else TaskStatus.FAILED.value
                if not passed:
                    task.error_message = "Output inspection failed"
            else:
                task.status = TaskStatus.FAILED.value
                task.error_message = exec_result.get("output", "")[:2000]
                report = None
                redacted_output = ""

            task.output_rows = len(exec_result.get("output", "").splitlines())
            task.completed_at = datetime.now(timezone.utc)
            task.resource_usage = {
                "duration_ms": exec_result.get("duration_ms", 0),
                "sandbox_level": exec_result.get("sandbox_level", "L3"),
                "output_truncated": exec_result.get("output_truncated", False),
                "output_security": {
                    "inspection_report": report,
                    "redacted_output": redacted_output,
                } if exec_result.get("exit_code", -1) == 0 else None,
            }

            # Audit
            await audit_service.log(
                db, action="sandbox_task.execute", resource_type="sandbox_task",
                user_id=task.user_id, resource_id=task.task_id,
                detail={
                    "session_id": str(task.session_id),
                    "exit_code": exec_result.get("exit_code"),
                    "duration_ms": exec_result.get("duration_ms"),
                },
            )
            if exec_result.get("exit_code", -1) == 0:
                await audit_service.log(
                    db,
                    action="output_inspection_pass" if task.status == TaskStatus.COMPLETED.value else "output_inspection_fail",
                    resource_type="sandbox_task",
                    user_id=task.user_id,
                    resource_id=task.task_id,
                    detail=report or {},
                )

        except Exception as e:
            logger.error(f"Task {task_id} execution failed: {e}")
            task.status = TaskStatus.FAILED.value
            task.error_message = str(e)[:2000]
            task.completed_at = datetime.now(timezone.utc)

        await db.commit()
        logger.info(f"Task {task_id} completed with status: {task.status}")

    async def get_task_result(self, task_id: str) -> dict | None:
        """Get the result of a completed task."""
        async with async_session() as db:
            result = await db.execute(
                select(SandboxTask).where(SandboxTask.task_id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                return None
            return {
                "task_id": task.task_id,
                "status": task.status,
                "output_rows": task.output_rows,
                "resource_usage": task.resource_usage,
                "error_message": task.error_message,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            }


# Singleton
task_worker = TaskWorker()


async def start_worker() -> None:
    """Start the background task worker loop."""
    global _worker_running, _worker_task
    if _worker_running:
        return
    _worker_running = True
    _worker_task = asyncio.create_task(_worker_loop())
    logger.info("Task worker started")


async def stop_worker() -> None:
    """Stop the background task worker."""
    global _worker_running, _worker_task
    _worker_running = False
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    logger.info("Task worker stopped")


async def _worker_loop() -> None:
    """Main worker loop — polls Redis queue for pending tasks."""
    while _worker_running:
        try:
            from app.core.redis import get_redis
            redis = await get_redis()
            # Block pop with 1s timeout
            result = await redis.blpop(_REDIS_QUEUE_KEY, timeout=1)
            if result:
                _, task_id_bytes = result
                task_id = task_id_bytes.decode() if isinstance(task_id_bytes, bytes) else task_id_bytes
                async with async_session() as db:
                    await task_worker._execute_task(db, task_id)
        except ConnectionError:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(1)
