"""N10: sandbox-internal agent execution API (ADMIN/OPERATOR/DATA_PROVIDER).

Creates an AGENT task: the platform generates a self-contained runner over
user-selected (platform-fixed) tools with step/token budgets, stores it
encrypted as code_content, and submits it through the same pipeline as any
other task (code-scanning → runner materialization → execution → metering).
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.sandbox_task import SandboxTask
from app.models.user import User, UserRole
from app.services.agent_service import build_agent_runner
from app.services.agent_tools import tool_registry
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)

router = APIRouter()


class AgentTaskCreate(BaseModel):
    session_id: uuid.UUID
    prompt: str = Field(..., min_length=1, max_length=4000)
    tool_ids: list[str] = Field(..., min_length=1)
    step_budget: int | None = Field(None, ge=1, le=100)
    token_budget: int | None = Field(None, ge=1)
    generative_model_id: str | None = None
    inference_model_id: str | None = None


def _enforce_tool_roles(user: User, tool_ids: list[str]) -> None:
    for tool_id in tool_ids:
        tool = tool_registry.get(tool_id)
        if tool is None:
            raise HTTPException(status_code=422, detail=f"unknown tool {tool_id!r}")
        if tool.roles and user.role not in tool.roles:
            raise HTTPException(
                status_code=403,
                detail=f"tool {tool_id!r} is not allowed for role {user.role}",
            )


@router.post("/tasks", status_code=201)
async def create_agent_task(
    body: AgentTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER, UserRole.BUYER)),
):
    from app.core.config import get_settings

    settings = get_settings()
    session = await db.get(SandboxSession, body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id and current_user.role not in (UserRole.ADMIN, UserRole.OPERATOR):
        raise HTTPException(status_code=403, detail="Not your session")
    if session.status not in (SessionStatus.READY.value, SessionStatus.RUNNING.value):
        raise HTTPException(status_code=400, detail=f"Session is {session.status}")

    tool_err = tool_registry.validate_run(body.tool_ids)
    if tool_err:
        raise HTTPException(status_code=422, detail=tool_err)
    _enforce_tool_roles(current_user, body.tool_ids)

    if len(body.tool_ids) > settings.AGENT_MAX_TOOLS:
        raise HTTPException(status_code=422, detail=f"too many tools (max {settings.AGENT_MAX_TOOLS})")

    rag_index_file = "input/rag_index.json" if "rag_retrieve" in body.tool_ids else None
    generative_model_id = body.generative_model_id if "llm_reason" in body.tool_ids else None
    inference_model_id = body.inference_model_id if "inference" in body.tool_ids else None
    if "inference" in body.tool_ids and not inference_model_id:
        raise HTTPException(
            status_code=422,
            detail="tool 'inference' requires an inference_model_id (registered model)",
        )

    try:
        code = build_agent_runner(
            body.prompt,
            body.tool_ids,
            step_budget=body.step_budget,
            token_budget=body.token_budget,
            rag_index_file=rag_index_file,
            generative_model_id=generative_model_id,
            inference_model_id=inference_model_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    from app.utils.crypto import sm3_hash

    task_id = f"task-{uuid.uuid4().hex[:12]}"
    from app.services.task_code_security import encrypt_task_code

    encrypted_code = encrypt_task_code(code, scope=task_id)
    task = SandboxTask(
        task_id=task_id,
        session_id=session.id,
        user_id=current_user.id,
        task_type="agent",
        code_hash=sm3_hash(code.encode()),
        code_content=encrypted_code,
        language="python",
        timeout_seconds=session.timeout_seconds,
        purpose=None,
    )
    db.add(task)
    if session.status == SessionStatus.READY.value:
        session.status = SessionStatus.RUNNING.value
    await db.flush()

    from app.services.task_pipeline import task_pipeline

    await task_pipeline.submit(
        task_type="sandbox_execute",
        session_id=str(session.id),
        payload={
            "task_id": task_id,
            "code": code,
            "language": "python",
            "sandbox_mode": getattr(session, "sandbox_mode", None) or "structured_query",
            "timeout_seconds": task.timeout_seconds,
            "agent": True,
            "agent_tool_ids": body.tool_ids,
            "agent_generative_model_id": generative_model_id,
            "agent_inference_model_id": inference_model_id,
        },
        timeout=task.timeout_seconds,
    )

    await audit_service.log(
        db, action="agent.task_create", resource_type="sandbox_task",
        user_id=current_user.id, session_id=session.id, resource_id=task_id,
        detail={
            "tool_ids": body.tool_ids,
            "step_budget": _step_budget(body.step_budget),
            "token_budget": _token_budget(body.token_budget),
            "generative_model_id": generative_model_id,
            "inference_model_id": inference_model_id,
        },
    )
    await db.commit()

    return {
        "task_id": task_id,
        "session_id": str(session.id),
        "task_type": "agent",
        "status": task.status,
        "tool_ids": body.tool_ids,
        "prompt_chars": len(body.prompt),
    }


def _step_budget(v: int | None) -> int:
    from app.core.config import get_settings
    return int(get_settings().AGENT_STEP_BUDGET if v is None else v)


def _token_budget(v: int | None) -> int:
    from app.core.config import get_settings
    return int(get_settings().AGENT_TOKEN_BUDGET if v is None else v)
