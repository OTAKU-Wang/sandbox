"""Task Pipeline — unified orchestrator for sandbox task lifecycle.

Replaces the disconnected TaskScheduler/Queue/Worker with a single
pipeline that uses TaskStateMachine as the canonical status source.

Flow:
  submit() → QUEUED → CODE_SCANNING → PREPARING → RUNNING → OUTPUT_INSPECTING → COMPLETED

Each stage is a handler registered with the pipeline. The pipeline
polls the queue, validates state transitions, and executes handlers
with concurrency control.
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.task_state_machine import (
    TaskStateMachine, TaskStatus, ACTIVE_STATES, TERMINAL_STATES,
)
from app.services.task_queue import TaskQueue, TaskPriority, QueueTask

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Pipeline configuration."""
    max_concurrent: int = 4
    poll_interval: float = 2.0
    default_timeout: int = 600


@dataclass
class PipelineTask:
    """A task tracked by the pipeline."""
    task_id: str
    session_id: str
    task_type: str
    payload: dict
    status: TaskStatus = TaskStatus.QUEUED
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    result: dict | None = None
    retry_count: int = 0
    max_retries: int = 3


class TaskPipeline:
    """Unified task lifecycle orchestrator.

    Integrates queue, scheduler, and state machine into a single flow.
    Each pipeline stage has a handler that performs the work and
    transitions the task to the next state.
    """

    def __init__(self, config: PipelineConfig | None = None):
        self.config = config or PipelineConfig()
        self._sm = TaskStateMachine()
        self._queue = TaskQueue()
        self._tasks: dict[str, PipelineTask] = {}
        self._handlers: dict[TaskStatus, Any] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._poll_task: asyncio.Task | None = None
        self._running_flag = False

    def register_handler(self, stage: TaskStatus, handler):
        """Register a handler for a pipeline stage.

        Handler signature: async def handler(task: PipelineTask) -> tuple[TaskStatus, dict | None]
        Returns (next_status, result) where result is None for intermediate stages.
        """
        self._handlers[stage] = handler

    async def start(self):
        """Start the pipeline polling loop."""
        if self._running_flag:
            return
        await self._queue.connect()
        self._running_flag = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("[Pipeline] Started (max_concurrent=%d)", self.config.max_concurrent)

    async def stop(self):
        """Stop the pipeline and cancel running tasks."""
        self._running_flag = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        for task in self._running.values():
            task.cancel()
        self._running.clear()
        await self._queue.close()
        logger.info("[Pipeline] Stopped")

    async def submit(self, task_type: str, session_id: str, payload: dict,
                     priority: TaskPriority = TaskPriority.NORMAL,
                     max_retries: int = 3, timeout: int = 600) -> PipelineTask:
        """Submit a new task to the pipeline.

        Returns immediately with the task in QUEUED state.
        """
        queue_task = await self._queue.enqueue(
            task_type=task_type,
            payload=payload,
            priority=priority,
            session_id=session_id,
            max_retries=max_retries,
            timeout_seconds=timeout,
        )

        task = PipelineTask(
            task_id=queue_task.task_id,
            session_id=session_id,
            task_type=task_type,
            payload=payload,
            priority=priority,
            max_retries=max_retries,
        )
        self._tasks[task.task_id] = task
        logger.info("[Pipeline] Task %s submitted (type=%s, priority=%s)",
                     task.task_id, task_type, priority.name)
        return task

    def get_task(self, task_id: str) -> PipelineTask | None:
        """Get task by ID."""
        return self._tasks.get(task_id)

    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        status_counts = {}
        for task in self._tasks.values():
            s = task.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        return {
            "total_tasks": len(self._tasks),
            "running": len(self._running),
            "max_concurrent": self.config.max_concurrent,
            "status_counts": status_counts,
            "registered_stages": [s.value for s in self._handlers],
        }

    async def _poll_loop(self):
        """Main loop: poll queue, dispatch to stage handlers."""
        while self._running_flag:
            try:
                # Check capacity
                if len(self._running) >= self.config.max_concurrent:
                    await asyncio.sleep(self.config.poll_interval)
                    continue

                # Dequeue next task
                queue_task = await self._queue.dequeue(timeout=1)
                if not queue_task:
                    await asyncio.sleep(self.config.poll_interval)
                    continue

                task = self._tasks.get(queue_task.task_id)
                if not task:
                    # Reconstruct from queue task
                    task = PipelineTask(
                        task_id=queue_task.task_id,
                        session_id=queue_task.session_id or "",
                        task_type=queue_task.task_type,
                        payload=queue_task.payload,
                        priority=queue_task.priority,
                        max_retries=queue_task.max_retries,
                    )
                    self._tasks[task.task_id] = task

                # Start pipeline execution from current stage
                asyncio_task = asyncio.create_task(self._execute_pipeline(task))
                self._running[task.task_id] = asyncio_task

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[Pipeline] Poll error: %s", e)
                await asyncio.sleep(self.config.poll_interval)

    async def _execute_pipeline(self, task: PipelineTask):
        """Execute a task through all pipeline stages."""
        try:
            # Stage: CODE_SCANNING
            result = await self._run_stage(task, TaskStatus.CODE_SCANNING)
            if result is None:
                return

            # Stage: PREPARING
            result = await self._run_stage(task, TaskStatus.PREPARING)
            if result is None:
                return

            # Stage: RUNNING
            result = await self._run_stage(task, TaskStatus.RUNNING)
            if result is None:
                return

            # Stage: OUTPUT_INSPECTING
            result = await self._run_stage(task, TaskStatus.OUTPUT_INSPECTING)
            if result is None:
                return

            # Mark completed
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
            await self._queue.complete(task.task_id, task.result)
            logger.info("[Pipeline] Task %s completed", task.task_id)

        except Exception as e:
            logger.error("[Pipeline] Task %s failed: %s", task.task_id, e)
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = datetime.now(timezone.utc)
            await self._queue.fail(task.task_id, str(e))
        finally:
            self._running.pop(task.task_id, None)

    async def _run_stage(self, task: PipelineTask, stage: TaskStatus) -> dict | None:
        """Run a single pipeline stage. Returns result dict or None if task failed/was rejected."""
        # Validate transition
        result = self._sm.validate_transition(task.status, stage)
        if not result.success:
            logger.error("[Pipeline] Invalid transition %s → %s for task %s",
                         task.status.value, stage.value, task.task_id)
            task.status = TaskStatus.FAILED
            task.error = result.error
            task.completed_at = datetime.now(timezone.utc)
            await self._queue.fail(task.task_id, result.error)
            return None

        # Transition
        task.status = stage
        if stage == TaskStatus.RUNNING and not task.started_at:
            task.started_at = datetime.now(timezone.utc)

        handler = self._handlers.get(stage)
        if not handler:
            task.status = TaskStatus.FAILED
            task.error = f"No handler registered for stage {stage.value}"
            task.completed_at = datetime.now(timezone.utc)
            await self._queue.fail(task.task_id, task.error)
            logger.error("[Pipeline] %s for task %s", task.error, task.task_id)
            return None

        try:
            stage_result = await handler(task)
            if stage_result is None:
                # Handler returned None → task failed at this stage
                task.status = TaskStatus.FAILED
                task.error = f"Handler for {stage.value} returned failure"
                task.completed_at = datetime.now(timezone.utc)
                await self._queue.fail(task.task_id, task.error)
                return None

            next_status, result_data = stage_result
            if next_status in TERMINAL_STATES and next_status != TaskStatus.COMPLETED:
                # Handler wants to terminate (REJECTED, FAILED, CANCELLED)
                task.status = next_status
                task.completed_at = datetime.now(timezone.utc)
                if next_status in (TaskStatus.REJECTED, TaskStatus.FAILED):
                    task.error = result_data.get("reason", "Rejected") if result_data else "Rejected"
                await self._queue.fail(task.task_id, task.error or "Terminal state")
                return None

            if result_data:
                task.result = {**(task.result or {}), **result_data}
            return result_data

        except Exception as e:
            logger.error("[Pipeline] Stage %s failed for task %s: %s",
                         stage.value, task.task_id, e)
            raise


# Singleton
task_pipeline = TaskPipeline()


async def _default_running_handler(task: PipelineTask) -> tuple[TaskStatus, dict | None]:
    """Default RUNNING handler — bridges to TaskWorker execution logic."""
    from app.core.database import async_session
    from app.models.sandbox_task import SandboxTask, TaskStatus as ORMTaskStatus
    from app.models.sandbox_session import SandboxSession
    from app.services.sandbox_manager import get_sandbox_manager
    from app.services.sandbox_runtime import SceneRuntimeFactory
    from app.services.kms_service import kms_service
    from app.services.audit_service import audit_service
    from sqlalchemy import select

    task_id = task.payload.get("task_id")
    if not task_id:
        return TaskStatus.FAILED, {"error": "No task_id in payload"}

    async with async_session() as db:
        result = await db.execute(select(SandboxTask).where(SandboxTask.task_id == task_id))
        orm_task = result.scalar_one_or_none()
        if not orm_task:
            return TaskStatus.FAILED, {"error": f"Task {task_id} not found"}

        session_result = await db.execute(
            select(SandboxSession).where(SandboxSession.id == orm_task.session_id)
        )
        session = session_result.scalar_one_or_none()
        if not session:
            orm_task.status = ORMTaskStatus.FAILED.value
            orm_task.error_message = "Sandbox session not found"
            await db.commit()
            return TaskStatus.FAILED, {"error": "Session not found"}

        if not session.container_id:
            orm_task.status = ORMTaskStatus.FAILED.value
            orm_task.error_message = "Sandbox session has no container"
            await db.commit()
            return TaskStatus.FAILED, {"error": "Session has no container"}

        runtime = await get_sandbox_manager()
        container_id = session.container_id

        # Gap T11: materialize the session's RAG corpus into the sandbox
        # workspace input (re-encrypted with the session DEK, exactly like
        # provision) before executing the runner. Missing corpus → the runner
        # fails closed with a clear missing-index error.
        rag_corpus = None
        if task.payload.get("rag_query"):
            try:
                from app.services.rag_service import prepare_corpus_for_task
                workspace = runtime.get_workspace(container_id, session.sandbox_level)
                if workspace:
                    rag_corpus = await prepare_corpus_for_task(db, session, workspace)
                if not rag_corpus:
                    logger.warning("[Pipeline] RAG corpus unavailable for task %s", task_id)
            except Exception as e:
                logger.warning("[Pipeline] RAG corpus materialization failed for task %s: %s", task_id, e)

        # N6: for generative RAG tasks, ship the provider ONNX bundle into the
        # workspace before executing (fail-closed without it).
        rag_generative = None
        if (
            task.payload.get("rag_query")
            and task.payload.get("rag_answer_mode") == "generative"
            and task.payload.get("rag_generative_model_id")
        ):
            try:
                from app.services.rag_generative_service import prepare_rag_generative_model
                workspace = runtime.get_workspace(container_id, session.sandbox_level)
                if workspace:
                    rag_generative = await prepare_rag_generative_model(
                        db, session, workspace,
                        uuid.UUID(str(task.payload["rag_generative_model_id"])),
                    )
                if not rag_generative:
                    logger.warning("[Pipeline] RAG generative model unavailable for task %s", task_id)
            except Exception as e:
                logger.warning("[Pipeline] RAG generative materialization failed for task %s: %s", task_id, e)

        # N5: materialize the registered model + invoke input into the sandbox
        # workspace (re-encrypted with the session DEK) before executing the
        # inference runner. Missing/corrupt model → the runner fails closed.
        inference_model = None
        if task.payload.get("inference_model_id"):
            try:
                from app.services.inference_service import prepare_model_for_task
                workspace = runtime.get_workspace(container_id, session.sandbox_level)
                if workspace:
                    inference_model = await prepare_model_for_task(
                        db, session, workspace,
                        uuid.UUID(str(task.payload["inference_model_id"])),
                        task.payload.get("inference_input") or {},
                    )
                if not inference_model:
                    logger.warning("[Pipeline] Inference model unavailable for task %s", task_id)
            except Exception as e:
                logger.warning("[Pipeline] Inference model materialization failed for task %s: %s", task_id, e)

        # N10: agent tasks materialize dependencies selected by their tools
        # (corpus for rag_retrieve, generative bundle for llm_reason, model
        # for inference) — same prepare_* helpers the RAG/INFERENCE paths use.
        agent_deps = None
        if task.payload.get("agent"):
            agent_deps = {}
            tool_ids = set(task.payload.get("agent_tool_ids") or [])
            workspace = runtime.get_workspace(container_id, session.sandbox_level)
            if workspace:
                if "rag_retrieve" in tool_ids:
                    try:
                        from app.services.rag_service import prepare_corpus_for_task
                        agent_deps["corpus"] = await prepare_corpus_for_task(db, session, workspace)
                    except Exception as e:
                        logger.warning("[Pipeline] Agent corpus materialization failed: %s", e)
                if "llm_reason" in tool_ids and task.payload.get("agent_generative_model_id"):
                    try:
                        from app.services.rag_generative_service import prepare_rag_generative_model
                        agent_deps["generative"] = await prepare_rag_generative_model(
                            db, session, workspace,
                            uuid.UUID(str(task.payload["agent_generative_model_id"])),
                        )
                    except Exception as e:
                        logger.warning("[Pipeline] Agent generative materialization failed: %s", e)
                if "inference" in tool_ids and task.payload.get("agent_inference_model_id"):
                    try:
                        from app.services.inference_service import prepare_model_for_task
                        agent_deps["inference"] = await prepare_model_for_task(
                            db, session, workspace,
                            uuid.UUID(str(task.payload["agent_inference_model_id"])), {},
                        )
                    except Exception as e:
                        logger.warning("[Pipeline] Agent inference materialization failed: %s", e)

        try:
            from app.services.task_code_security import decrypt_task_code
            code_content = decrypt_task_code(orm_task.code_content)
            if not code_content.strip():
                orm_task.status = ORMTaskStatus.FAILED.value
                orm_task.error_message = "code cannot be empty"
                await db.commit()
                return TaskStatus.FAILED, {"error": "code cannot be empty"}

            session_key = None
            if session.session_key_id:
                # Gap B1: pass the stored quote whenever present (not only
                # TEE levels); L1/L2 without a quote still hard-fail.
                from app.services.sandbox_manager import (
                    attestation_from_session,
                    attestation_required_for_level,
                )
                attestation = attestation_from_session(session)
                if attestation_required_for_level(session.sandbox_level) and not attestation:
                    orm_task.status = ORMTaskStatus.FAILED.value
                    orm_task.error_message = "Sandbox attestation quote missing; key distribution denied"
                    await db.commit()
                    return TaskStatus.FAILED, {"error": orm_task.error_message}
                session_key = kms_service.distribute_key(
                    session.session_key_id,
                    str(session.id),
                    attestation=attestation,
                )
                if not session_key:
                    orm_task.status = ORMTaskStatus.FAILED.value
                    orm_task.error_message = "Session key distribution failed"
                    await db.commit()
                    return TaskStatus.FAILED, {"error": "Session key distribution failed"}

            language = (orm_task.language or "python").lower()
            sandbox_mode = getattr(session, "sandbox_mode", None) or task.payload.get("sandbox_mode") or "structured_query"
            scene_runtime = SceneRuntimeFactory.create(sandbox_mode)
            scene_context = {"language": language, "max_output_rows": 10000}
            code_content = await scene_runtime.pre_execute(code_content, scene_context)

            exec_result = await runtime.execute(
                container_id=container_id,
                code=code_content,
                language=language,
                session_key=session_key,
                env_vars={
                    "CDS_SESSION_ID": str(session.id),
                    "CDS_SANDBOX_LEVEL": session.sandbox_level,
                    "CDS_SANDBOX_MODE": sandbox_mode,
                    "CDS_DATA_PRODUCT_ID": str(session.data_product_id),
                    "CDS_USER_ID": str(session.user_id),
                },
                timeout=orm_task.timeout_seconds,
            )
            exec_result = await scene_runtime.post_execute(exec_result, scene_context)
            output = exec_result.get("output", "") or ""
            if exec_result.get("exit_code", -1) == 0:
                orm_task.status = ORMTaskStatus.OUTPUT_REVIEW.value
            else:
                orm_task.status = ORMTaskStatus.FAILED.value
                orm_task.error_message = output[:2000]
                orm_task.completed_at = datetime.now(timezone.utc)
            orm_task.output_rows = len(output.splitlines())

            # N5: inference metering — record output rows + success on completion.
            if task.payload.get("inference_model_id"):
                try:
                    from app.services.inference_service import inference_service as _inference_service
                    await _inference_service.update_usage_on_completion(
                        db, task_id,
                        output_rows=len(output.splitlines()),
                        succeeded=exec_result.get("exit_code", -1) == 0,
                    )
                except Exception as e:
                    logger.warning("[Pipeline] Inference usage update failed: %s", e)
            orm_task.resource_usage = {
                "duration_ms": exec_result.get("duration_ms", 0),
                "sandbox_level": exec_result.get("sandbox_level", "L3"),
                "output_truncated": exec_result.get("output_truncated", False),
            }
            # N6: RAG retrieval metering — record retrieved chunks, answer
            # mode and embedding engine from the runner's rag_meta.
            if task.payload.get("rag_query"):
                try:
                    from app.services.rag_service import _extract_rag_metrics
                    metrics = _extract_rag_metrics(output)
                    if metrics:
                        orm_task.resource_usage = {
                            **(orm_task.resource_usage or {}),
                            **{k: v for k, v in metrics.items() if v is not None},
                        }
                except Exception as e:
                    logger.warning("[Pipeline] RAG metering extract failed: %s", e)
            # N10: agent metering — parse agent_trace, verify step budget was
            # honored (fail-closed on mismatch), audit every step.
            if task.payload.get("agent"):
                try:
                    from app.services.agent_service import extract_agent_metrics
                    from app.core.config import get_settings as _get_agent_settings
                    metrics = extract_agent_metrics(output)
                    budget = int(_get_agent_settings().AGENT_STEP_BUDGET)
                    if metrics.get("traces"):
                        orm_task.resource_usage = {
                            **(orm_task.resource_usage or {}),
                            "agent_steps_used": metrics["steps_used"],
                            "agent_tokens_used": metrics["tokens_used"],
                            "agent_tools_used": metrics["tools_used"],
                        }
                        for trace in metrics["traces"]:
                            await audit_service.log(
                                db, action="agent.step", resource_type="sandbox_task",
                                user_id=orm_task.user_id, session_id=orm_task.session_id,
                                detail={"step": trace.get("step"), "tool": trace.get("tool"),
                                        "args_hash": trace.get("args_hash"), "status": trace.get("status"),
                                        "tokens": trace.get("tokens"), "ms": trace.get("ms")},
                            )
                        if metrics["steps_used"] > budget:
                            orm_task.status = ORMTaskStatus.FAILED.value
                            orm_task.error_message = (
                                f"agent step budget exceeded (used {metrics['steps_used']} > {budget})"
                            )
                            await db.commit()
                            return TaskStatus.FAILED, {"error": orm_task.error_message}
                except Exception as e:
                    logger.warning("[Pipeline] Agent metering extract failed: %s", e)
            await audit_service.log(
                db, action="sandbox_task.execute", resource_type="sandbox_task",
                user_id=orm_task.user_id, resource_id=task_id,
                detail={"exit_code": exec_result.get("exit_code"), "duration_ms": exec_result.get("duration_ms")},
            )
            await db.commit()
            if exec_result.get("exit_code", -1) != 0:
                return TaskStatus.FAILED, {"reason": orm_task.error_message or "Sandbox execution failed"}
            return TaskStatus.RUNNING, {
                "exit_code": exec_result.get("exit_code"),
                "output": output,
                "output_rows": orm_task.output_rows,
                "output_truncated": exec_result.get("output_truncated", False),
                "duration_ms": exec_result.get("duration_ms", 0),
                "sandbox_level": exec_result.get("sandbox_level", "L3"),
                "orm_task_id": orm_task.task_id,
                "user_id": str(orm_task.user_id),
                "sandbox_session_id": str(orm_task.session_id),
                "sandbox_mode": getattr(session, "sandbox_mode", None),
                "rag_corpus": rag_corpus,
            }
        except Exception as e:
            orm_task.status = ORMTaskStatus.FAILED.value
            orm_task.error_message = str(e)[:2000]
            await db.commit()
            return TaskStatus.FAILED, {"error": str(e)}


async def _code_scanning_handler(task: PipelineTask) -> tuple[TaskStatus, dict | None]:
    """CODE_SCANNING handler — validates code before execution.

    Gap T11: system-generated RAG runners are validated byte-exactly against
    the trusted template (``validate_rag_runner``) instead of the general
    user-code import whitelist — the whitelist is never widened.
    """
    from app.services.code_scanner import CodeScanner
    from app.core.database import async_session
    from app.services.audit_service import audit_service

    code = task.payload.get("code", "")
    language = task.payload.get("language", "python")
    sandbox_mode = task.payload.get("sandbox_mode", "structured_query")
    if not code:
        return TaskStatus.CODE_SCANNING, {"scan": "skipped", "reason": "no code"}

    if task.payload.get("rag_query"):
        from app.services.rag_service import validate_rag_runner
        try:
            passed, reason, _query = validate_rag_runner(code)
            async with async_session() as db:
                await audit_service.log(
                    db, action="sandbox_task.code_scanning", resource_type="sandbox_task",
                    user_id=task.session_id, resource_id=task.task_id,
                    detail={
                        "rag_runner": True,
                        "passed": passed,
                        "reason": reason,
                    },
                )
                await db.commit()
            if not passed:
                return TaskStatus.REJECTED, {
                    "reason": f"RAG runner validation failed: {reason}",
                    "rag_runner": True,
                }
            return TaskStatus.CODE_SCANNING, {"scan": "rag_runner_verified", "reason": reason}
        except Exception as e:
            logger.warning("[Pipeline] RAG runner validation error: %s", e)
            return TaskStatus.FAILED, {"reason": f"RAG runner validation error: {e}"}

    # N5: system-generated INFERENCE runners are validated byte-exactly
    # against the trusted template (never the widened user-code whitelist).
    if task.payload.get("inference_model_id"):
        from app.services.inference_service import validate_inference_runner
        try:
            import uuid as _uuid
            model_id = _uuid.UUID(str(task.payload["inference_model_id"]))
            passed, reason = validate_inference_runner(code, model_id)
            async with async_session() as db:
                await audit_service.log(
                    db, action="sandbox_task.code_scanning", resource_type="sandbox_task",
                    user_id=task.session_id, resource_id=task.task_id,
                    detail={"inference_runner": True, "passed": passed, "reason": reason},
                )
                await db.commit()
            if not passed:
                return TaskStatus.REJECTED, {
                    "reason": f"Inference runner validation failed: {reason}",
                    "inference_runner": True,
                }
            return TaskStatus.CODE_SCANNING, {"scan": "inference_runner_verified", "reason": reason}
        except Exception as e:
            logger.warning("[Pipeline] Inference runner validation error: %s", e)
            return TaskStatus.FAILED, {"reason": f"Inference runner validation error: {e}"}

    # N10: system-generated AGENT runners are validated byte-exactly against
    # the trusted template (never the widened user-code whitelist).
    if task.payload.get("agent"):
        from app.services.agent_service import validate_agent_runner
        try:
            passed, reason = validate_agent_runner(code, task.payload.get("agent_tool_ids"))
            try:
                async with async_session() as db:
                    await audit_service.log(
                        db, action="sandbox_task.code_scanning", resource_type="sandbox_task",
                        user_id=task.session_id, resource_id=task.task_id,
                        detail={"agent_runner": True, "passed": passed, "reason": reason,
                                "tool_ids": task.payload.get("agent_tool_ids")},
                    )
                    await db.commit()
            except Exception as audit_err:
                logger.warning("[Pipeline] Agent scan audit write failed: %s", audit_err)
            if not passed:
                return TaskStatus.REJECTED, {
                    "reason": f"Agent runner validation failed: {reason}",
                    "agent_runner": True,
                }
            return TaskStatus.CODE_SCANNING, {"scan": "agent_runner_verified", "reason": reason}
        except Exception as e:
            logger.warning("[Pipeline] Agent runner validation error: %s", e)
            return TaskStatus.FAILED, {"reason": f"Agent runner validation error: {e}"}

    scanner = CodeScanner()
    try:
        result = scanner.scan(code, language, sandbox_mode=sandbox_mode)
        # Write scan result to audit log
        try:
            async with async_session() as db:
                issues_summary = [{"code": i.code, "message": i.message, "severity": i.severity.value}
                                  for i in result.issues]
                await audit_service.log(
                    db, action="sandbox_task.code_scanning", resource_type="sandbox_task",
                    user_id=task.session_id, resource_id=task.task_id,
                    detail={"passed": result.passed, "issue_count": len(result.issues), "issues": issues_summary},
                )
                await db.commit()
        except Exception as audit_err:
            logger.warning("[Pipeline] Audit log write failed: %s", audit_err)

        if not result.passed:
            issues = [{"code": i.code, "message": i.message, "severity": i.severity.value}
                      for i in result.issues]
            return TaskStatus.REJECTED, {"reason": "Code scan failed", "issues": issues}
        return TaskStatus.CODE_SCANNING, {"scan": "passed", "issue_count": len(result.issues)}
    except Exception as e:
        logger.warning("[Pipeline] Code scanning error: %s", e)
        return TaskStatus.FAILED, {"reason": f"Code scanning error: {e}"}


async def _preparing_handler(task: PipelineTask) -> tuple[TaskStatus, dict | None]:
    """PREPARING handler — validates execution payload before sandbox run."""
    if not isinstance(task.payload, dict):
        return TaskStatus.FAILED, {"error": "Task payload must be a dictionary"}

    task_id = task.payload.get("task_id")
    if not task_id:
        return TaskStatus.FAILED, {"error": "No task_id in payload"}

    language = str(task.payload.get("language") or "python").lower()
    task.payload["language"] = language

    timeout = task.payload.get("timeout_seconds", task.payload.get("timeout"))
    if timeout is not None:
        try:
            timeout_value = int(timeout)
        except (TypeError, ValueError):
            return TaskStatus.FAILED, {"error": "timeout_seconds must be an integer"}
        if timeout_value <= 0:
            return TaskStatus.FAILED, {"error": "timeout_seconds must be positive"}
        task.payload["timeout_seconds"] = timeout_value

    return TaskStatus.PREPARING, {
        "prepared": True,
        "task_id": str(task_id),
        "language": language,
        "has_code": bool(task.payload.get("code")),
        "timeout_seconds": task.payload.get("timeout_seconds"),
    }


async def _output_inspecting_handler(task: PipelineTask) -> tuple[TaskStatus, dict | None]:
    """OUTPUT_INSPECTING handler — inspects execution output for sensitive data.

    Gap E1: output is additionally clamped against the contract's OutputPolicy
    (max_output_rows / allowed_output_formats) resolved via
    app.services.output_policy, and the applied policy is recorded in the
    persisted inspection report.
    """
    output = task.result.get("output", "") if task.result else ""
    task_result = task.result or {}
    if task.result is not None:
        task.result.pop("output", None)
    orm_task_id = task_result.get("orm_task_id") or task.payload.get("task_id")
    user_id = task_result.get("user_id") or task.payload.get("user_id") or ""
    session_id = task_result.get("sandbox_session_id") or task.session_id

    try:
        from app.services.output_security import inspect_text_output, inspection_to_report

        if output:
            inspection = inspect_text_output(
                output,
                user_id=user_id,
                session_id=session_id,
                sandbox_mode=task_result.get("sandbox_mode") or task.payload.get("sandbox_mode"),
                dp_epsilon=task_result.get("dp_epsilon_used") or task.payload.get("dp_epsilon_used"),
            )
            report = inspection_to_report(inspection)
            redacted_output = inspection.redacted_output or ""
            passed = inspection.passed
        else:
            report = {
                "passed": True,
                "blocked": False,
                "stage_results": {"no_output": True},
                "findings": [],
                "findings_count": 0,
                "severities": [],
                "dp_applied": False,
                "watermark": None,
                "signature": None,
            }
            redacted_output = ""
            passed = True

        # Gap E1: enforce the contract's output policy (row limit) on the
        # redacted output and annotate the report with the applied policy.
        output_rows = int(task_result.get("output_rows", 0) or 0)
        try:
            from app.core.database import async_session as _policy_db_session
            from app.services.output_policy import enforce_text_output_policy
            async with _policy_db_session() as policy_db:
                redacted_output, report = await enforce_text_output_policy(
                    policy_db, session_id, redacted_output, report
                )
                if report.get("policy_row_limit_truncated"):
                    output_rows = min(output_rows, int(report["policy_row_limit"]))
        except Exception as policy_err:
            logger.warning("[Pipeline] Output policy enforcement failed (task=%s): %s", task.task_id, policy_err)

        await _finalize_orm_output_inspection(
            orm_task_id,
            passed=passed,
            report=report,
            redacted_output=redacted_output,
            output_rows=output_rows,
            resource_base={
                "duration_ms": task_result.get("duration_ms", 0),
                "sandbox_level": task_result.get("sandbox_level", "L3"),
                "output_truncated": task_result.get("output_truncated", False),
            },
        )

        result_data = {
            "inspection": "passed" if passed else "failed",
            "inspection_report": report,
            "redacted_output": redacted_output,
        }
        if not passed:
            return TaskStatus.REJECTED, {
                **result_data,
                "reason": "Output inspection failed",
            }
        return TaskStatus.OUTPUT_INSPECTING, result_data
    except Exception as e:
        logger.warning("[Pipeline] Output inspection error: %s", e)
        await _finalize_orm_output_inspection(
            orm_task_id,
            passed=False,
            report={"passed": False, "blocked": True, "error": str(e), "findings": []},
            redacted_output="",
            output_rows=0,
            resource_base={},
        )
        return TaskStatus.FAILED, {"reason": f"Output inspection error: {e}"}


async def _finalize_orm_output_inspection(
    orm_task_id: str | None,
    *,
    passed: bool,
    report: dict,
    redacted_output: str,
    output_rows: int,
    resource_base: dict,
) -> None:
    """Persist inspection outcome for ORM-backed sandbox tasks."""
    if not orm_task_id:
        return

    from app.core.database import async_session
    from app.models.sandbox_task import SandboxTask, TaskStatus as ORMTaskStatus
    from app.services.audit_service import audit_service
    from sqlalchemy import select

    async with async_session() as db:
        result = await db.execute(select(SandboxTask).where(SandboxTask.task_id == orm_task_id))
        orm_task = result.scalar_one_or_none()
        if not orm_task:
            return

        orm_task.status = ORMTaskStatus.COMPLETED.value if passed else ORMTaskStatus.FAILED.value
        orm_task.completed_at = datetime.now(timezone.utc)
        orm_task.output_rows = int(output_rows or 0)
        if not passed:
            orm_task.error_message = "Output inspection failed"
        orm_task.resource_usage = {
            **(resource_base or {}),
            "output_security": {
                "inspection_report": report,
                "redacted_output": redacted_output,
            },
        }
        await audit_service.log(
            db,
            action="output_inspection_pass" if passed else "output_inspection_fail",
            resource_type="sandbox_task",
            user_id=orm_task.user_id,
            resource_id=orm_task.task_id,
            detail=report,
        )
        await db.commit()


# Register default handlers
task_pipeline.register_handler(TaskStatus.CODE_SCANNING, _code_scanning_handler)
task_pipeline.register_handler(TaskStatus.PREPARING, _preparing_handler)
task_pipeline.register_handler(TaskStatus.RUNNING, _default_running_handler)
task_pipeline.register_handler(TaskStatus.OUTPUT_INSPECTING, _output_inspecting_handler)
