"""Sandbox Tasks API — task lifecycle within sandbox sessions."""
import uuid
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.sandbox_task import SandboxTask, TaskStatus, TaskType
from app.services.audit_service import audit_service
from app.services.dp_budget import dp_budget_ledger
from app.services.sandbox_manager import get_resource_limits

router = APIRouter()


async def _enforce_task_purpose(db: AsyncSession, session: SandboxSession, purpose: str | None) -> None:
    """Gap A4: validate a task's declared purpose against the session contract.

    - No contract / contract without purpose limitation → unrestricted.
    - Contract has purpose_scope → task purpose must be one of them.
    - Contract has a single purpose → task purpose must match exactly.
    - Contract limits purpose but task declares none → rejected.
    """
    if not session.contract_id:
        return
    from app.models.contract import Contract
    try:
        cid = uuid.UUID(str(session.contract_id))
    except (TypeError, ValueError):
        return
    result = await db.execute(select(Contract).where(Contract.id == cid))
    contract = result.scalar_one_or_none()
    if not contract or not contract.purpose:
        return  # no purpose limitation → unrestricted

    if not purpose:
        raise HTTPException(
            status_code=400,
            detail="Task must declare a purpose (the governing contract has a purpose limitation)",
        )

    scope = [str(p) for p in (contract.purpose_scope or [])]
    if scope:
        if purpose not in scope:
            raise HTTPException(
                status_code=400,
                detail=f"Purpose '{purpose}' is not within contract purpose_scope {scope}",
            )
        return
    if purpose != contract.purpose:
        raise HTTPException(
            status_code=400,
            detail=f"Purpose '{purpose}' does not match contract purpose '{contract.purpose}'",
        )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    session_id: uuid.UUID,
    task_type: str = "query",
    code: str | None = None,
    language: str | None = None,
    timeout_seconds: int = 3600,
    purpose: str | None = None,
    rag_query: str | None = None,
    answer_mode: str | None = None,
    generative_model_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new sandbox task.

    Gap T11: ``task_type="rag_query"`` takes a natural-language ``rag_query``
    instead of user ``code`` — the RAG service generates a self-contained
    runner that retrieves from the session's encrypted corpus INSIDE the
    sandbox and answers extractively. N6: ``answer_mode="generative"`` (with a
    registered ``generative_model_id``) answers via a provider ONNX bundle.
    It flows through the same pipeline (code scan → sandbox run → output
    gateway) as any other task.
    """
    # Validate session
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this session")
    if session.status not in (SessionStatus.READY.value, SessionStatus.RUNNING.value):
        raise HTTPException(status_code=400, detail=f"Session not ready for tasks (status: {session.status})")
    if not session.container_id:
        raise HTTPException(status_code=400, detail="Session has no container")

    # Gap T11: RAG tasks carry a natural-language query instead of code.
    is_rag = task_type == TaskType.RAG_QUERY.value
    if is_rag:
        if code and code.strip():
            raise HTTPException(status_code=400, detail="code must not be set for task_type=rag_query")
        if not rag_query or not rag_query.strip():
            raise HTTPException(status_code=422, detail="rag_query cannot be empty")
        from app.core.config import get_settings as _get_rag_settings
        rag_query = rag_query.strip()
        if len(rag_query) > _get_rag_settings().RAG_QUERY_MAX_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"rag_query exceeds max length ({_get_rag_settings().RAG_QUERY_MAX_CHARS} chars)",
            )
        language = "python"
        # N6: generative answer mode requires a registered ONNX bundle.
        mode = (answer_mode or "extractive_retrieval").lower()
        if mode not in ("extractive_retrieval", "generative"):
            raise HTTPException(status_code=422, detail=f"Unknown answer_mode {mode!r}")
        if mode == "generative" and not generative_model_id:
            raise HTTPException(
                status_code=400,
                detail="answer_mode=generative requires a generative_model_id "
                       "(register a bundle via POST /api/v1/rag/generative-model)",
            )
    else:
        if not code or not code.strip():
            raise HTTPException(status_code=422, detail="code cannot be empty")

    if timeout_seconds <= 0:
        raise HTTPException(status_code=422, detail="timeout_seconds must be positive")
    if timeout_seconds > session.timeout_seconds:
        raise HTTPException(status_code=400, detail="Task timeout exceeds session timeout")
    level_max = get_resource_limits(session.sandbox_level)["max_timeout_seconds"]
    if timeout_seconds > level_max:
        raise HTTPException(status_code=400, detail=f"Task timeout exceeds sandbox level max ({level_max}s)")
    language = (language or "python").lower()

    # Gap A4: enforce the contract purpose limitation for this session.
    await _enforce_task_purpose(db, session, purpose)

    # Resolve the code to store/scan: user code for regular tasks, a generated
    # self-contained RAG runner for RAG_QUERY tasks.
    from app.utils.crypto import sm3_hash
    if is_rag:
        from app.services.rag_service import build_rag_runner
        code = build_rag_runner(
            rag_query,
            answer_mode=(answer_mode or "extractive_retrieval").lower(),
            generative_model_id=generative_model_id,
        )
    code_hash = sm3_hash(code.encode())

    task_id = f"task-{uuid.uuid4().hex[:12]}"
    from app.services.task_code_security import encrypt_task_code

    encrypted_code = encrypt_task_code(code, scope=task_id)
    task = SandboxTask(
        task_id=task_id,
        session_id=session_id,
        user_id=current_user.id,
        task_type=task_type,
        code_hash=code_hash,
        code_content=encrypted_code,
        language=language,
        timeout_seconds=timeout_seconds,
        purpose=purpose,
        rag_answer_mode=(answer_mode or "extractive_retrieval").lower() if is_rag else None,
        rag_generative_model_id=generative_model_id if is_rag else None,
    )
    db.add(task)

    # Update session status
    if session.status == SessionStatus.READY.value:
        session.status = SessionStatus.RUNNING.value

    await db.flush()
    await db.refresh(task)

    await audit_service.log(
        db, action="sandbox_task.create", resource_type="sandbox_task",
        user_id=current_user.id, resource_id=task_id,
        detail={
            "session_id": str(session_id),
            "task_type": task_type,
            "purpose": purpose,
            "rag_query_chars": len(rag_query) if is_rag else None,
        },
    )

    return {
        "task_id": task.task_id,
        "session_id": str(session_id),
        "status": task.status,
        "task_type": task.task_type,
        "purpose": task.purpose,
        "created_at": task.created_at.isoformat(),
    }


@router.get("")
async def list_tasks(
    session_id: uuid.UUID,
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List tasks for a session."""
    # Verify session access
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this session")

    query = select(SandboxTask).where(SandboxTask.session_id == session_id)
    if status_filter:
        query = query.where(SandboxTask.status == status_filter)
    query = query.order_by(SandboxTask.created_at.desc())

    result = await db.execute(query)
    tasks = result.scalars().all()
    return {
        "items": [
            {
                "task_id": t.task_id, "status": t.status, "task_type": t.task_type,
                "language": t.language, "output_rows": t.output_rows,
                "dp_epsilon_used": float(t.dp_epsilon_used) if t.dp_epsilon_used else None,
                "created_at": t.created_at.isoformat(),
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks
        ]
    }


@router.get("/{task_id}")
async def get_task(
    session_id: uuid.UUID,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get task details."""
    result = await db.execute(
        select(SandboxTask).where(
            SandboxTask.task_id == task_id,
            SandboxTask.session_id == session_id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this task")

    return {
        "task_id": task.task_id,
        "session_id": str(task.session_id),
        "status": task.status,
        "task_type": task.task_type,
        "language": task.language,
        "code_hash": task.code_hash,
        "resource_usage": task.resource_usage,
        "dp_epsilon_used": float(task.dp_epsilon_used) if task.dp_epsilon_used else None,
        "output_rows": task.output_rows,
        "timeout_seconds": task.timeout_seconds,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@router.post("/{task_id}/submit")
async def submit_task(
    session_id: uuid.UUID,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit a task for execution (transition from pending to queued, then async execution)."""
    result = await db.execute(
        select(SandboxTask).where(
            SandboxTask.task_id == task_id,
            SandboxTask.session_id == session_id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this task")
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(status_code=400, detail=f"Can only submit pending tasks (current: {task.status})")
    session_result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this session")
    if session.status not in (SessionStatus.READY.value, SessionStatus.RUNNING.value):
        raise HTTPException(status_code=400, detail=f"Session not ready for tasks (status: {session.status})")
    if not session.container_id:
        raise HTTPException(status_code=400, detail="Session has no container")

    task.status = TaskStatus.CODE_SCANNING.value
    task.started_at = datetime.now(timezone.utc)
    await db.commit()

    # Queue for async execution via unified pipeline (P1-2)
    from app.services.task_pipeline import task_pipeline
    from app.services.task_code_security import decrypt_task_code
    code_for_scan = decrypt_task_code(task.code_content)
    if not code_for_scan.strip():
        task.status = TaskStatus.FAILED.value
        task.error_message = "code cannot be empty"
        await db.commit()
        raise HTTPException(status_code=422, detail="code cannot be empty")
    await task_pipeline.submit(
        task_type="sandbox_execute",
        session_id=str(session_id),
        payload={
            "task_id": task_id,
            "code": code_for_scan,
            "language": task.language or "python",
            "sandbox_mode": session.sandbox_mode,
            "timeout_seconds": task.timeout_seconds,
            # Gap T11: system-generated RAG runners are validated against the
            # trusted template instead of the general user-code import whitelist.
            "rag_query": task.task_type == "rag_query",
            # N6: answer mode + generative bundle for RAG phase-2 tasks.
            "rag_answer_mode": task.rag_answer_mode,
            "rag_generative_model_id": task.rag_generative_model_id,
        },
        timeout=task.timeout_seconds,
    )

    return {"task_id": task.task_id, "status": task.status, "message": "Task queued for execution"}


@router.get("/{task_id}/result")
async def get_task_result(
    session_id: uuid.UUID,
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the execution result of a task.

    Gap E1: completed results are gated on a persisted PASSING output
    inspection verdict — previously the endpoint returned whatever the
    worker had written, trusting a status flag set out-of-band.
    """
    result = await db.execute(
        select(SandboxTask).where(
            SandboxTask.task_id == task_id,
            SandboxTask.session_id == session_id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this task")

    if task.status not in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value):
        return {
            "task_id": task.task_id,
            "status": task.status,
            "message": f"Task is still {task.status}",
        }

    inspection_summary = None
    if task.status == TaskStatus.COMPLETED.value:
        usage = task.resource_usage if isinstance(task.resource_usage, dict) else {}
        output_security = usage.get("output_security") if isinstance(usage, dict) else None
        output_security = output_security if isinstance(output_security, dict) else {}
        report = output_security.get("inspection_report")
        report = report if isinstance(report, dict) else None
        verdict = report.get("passed") if report is not None else None
        if verdict is not True:
            raise HTTPException(
                status_code=409,
                detail="Output inspection verdict missing or failed — result withheld",
            )
        inspection_summary = {
            "passed": True,
            "findings_count": report.get("findings_count", 0),
            "watermark": report.get("watermark"),
            "signature": report.get("signature"),
            "policy": report.get("policy"),
            "row_limit_truncated": bool(report.get("policy_row_limit_truncated")),
            "redacted_output": output_security.get("redacted_output"),
        }

    return {
        "task_id": task.task_id,
        "status": task.status,
        "output_rows": task.output_rows,
        "resource_usage": task.resource_usage,
        "error_message": task.error_message,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "output_inspection": inspection_summary,
    }


@router.post("/{task_id}/complete")
async def complete_task(
    session_id: uuid.UUID,
    task_id: str,
    output_rows: int = 0,
    dp_epsilon_used: float | None = None,
    resource_usage: dict | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a task as completed (called after output review).

    When dp_epsilon_used is provided and the session has a contract_id,
    automatically deducts from the contract's DP budget ledger.
    """
    result = await db.execute(
        select(SandboxTask).where(
            SandboxTask.task_id == task_id,
            SandboxTask.session_id == session_id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Task completion is restricted to operators")
    if task.session_id != session_id:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (TaskStatus.OUTPUT_REVIEW.value, TaskStatus.RUNNING.value):
        raise HTTPException(status_code=409, detail=f"Task is not awaiting output review (status: {task.status})")

    inspection_report = (resource_usage or {}).get("output_security", {}).get("inspection_report", {})
    if not inspection_report.get("passed"):
        raise HTTPException(status_code=400, detail="Passed output inspection report is required")

    # Deduct DP budget from contract ledger
    if dp_epsilon_used and dp_epsilon_used > 0:
        session_result = await db.execute(
            select(SandboxSession).where(SandboxSession.id == session_id)
        )
        session = session_result.scalar_one_or_none()
        if session and session.contract_id:
            deducted = await dp_budget_ledger.consume(
                db, str(session.contract_id), str(session_id),
                dp_epsilon_used, operation=f"task:{task_id}",
            )
            if not deducted:
                raise HTTPException(
                    status_code=409,
                    detail=f"DP budget insufficient for contract {session.contract_id}",
                )

    task.status = TaskStatus.COMPLETED.value
    task.completed_at = datetime.now(timezone.utc)
    task.output_rows = output_rows
    task.dp_epsilon_used = dp_epsilon_used
    task.resource_usage = resource_usage

    await db.flush()
    await db.refresh(task)

    return {"task_id": task.task_id, "status": task.status}
