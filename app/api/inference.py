"""N5: Inference service sandbox API (spec N5).

Model registry (provider uploads encrypted artifacts) + buyer invoke:
contract + purpose gate → reuses the buyer's active session for the model's
product → submits an INFERENCE task whose system-generated runner executes the
model inside the sandbox → output flows through the standard output gateway →
metering is recorded.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.contract import Contract, ContractStatus
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.sandbox_task import SandboxTask, TaskStatus, TaskType
from app.models.user import User, UserRole
from app.services.audit_service import audit_service
from app.services.inference_service import (
    _estimate_input_tokens,
    build_inference_runner,
    inference_service,
)
from app.services.task_code_security import encrypt_task_code
from app.services.task_pipeline import task_pipeline
from app.utils.crypto import sm3_hash

router = APIRouter()

_INFERENCE_TASK_TIMEOUT = 300


class InvokeRequest(BaseModel):
    # Structured inputs: {onnx_input_name: nested-list-array}. Numeric arrays
    # keyed by the ONNX model's input names — the v1 structured IO contract.
    inputs: dict = Field(default_factory=dict)
    purpose: str | None = None


@router.post("/models", status_code=201)
async def register_model(
    file: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=255),
    format: str = Form(..., pattern="^(onnx|pickle|safetensors)$"),
    product_id: uuid.UUID | None = Form(None),
    task_id: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Register a model artifact (encrypted at rest)."""
    artifact = await file.read()
    try:
        model = await inference_service.register_model(
            db,
            owner_id=current_user.id,
            name=name,
            fmt=format,
            artifact_bytes=artifact,
            product_id=product_id,
            task_id=task_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="inference.model.register", resource_type="inference_model",
        user_id=current_user.id, resource_id=str(model.model_id),
        detail={"name": model.name, "format": model.format, "size_bytes": model.size_bytes},
    )

    return {
        "model_id": str(model.model_id),
        "name": model.name,
        "format": model.format,
        "product_id": str(model.product_id) if model.product_id else None,
        "status": model.status,
        "size_bytes": model.size_bytes,
    }


@router.get("/models")
async def list_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List models: providers see their own; buyers see models for products
    covered by their active contracts."""
    if current_user.role in (UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER):
        models = await inference_service.list_models(db, owner_id=current_user.id)
        return [_model_view(m) for m in models]

    result = await db.execute(
        select(Contract).where(
            Contract.buyer_id == current_user.id,
            Contract.status.in_([ContractStatus.ACTIVE.value, ContractStatus.SIGNED.value]),
        )
    )
    product_ids = {
        pid for c in result.scalars().all() for pid in (c.product_ids or [])
    }
    seen: dict[str, dict] = {}
    for pid in product_ids:
        try:
            pid_uuid = uuid.UUID(str(pid))
        except (TypeError, ValueError):
            continue
        for m in await inference_service.list_models(db, product_id=pid_uuid):
            seen[str(m.model_id)] = _model_view(m)
    return list(seen.values())


@router.post("/{model_id}/invoke", status_code=201)
async def invoke_model(
    model_id: uuid.UUID,
    body: InvokeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Invoke a registered model inside the buyer's sandbox session."""
    model = await inference_service.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    if model.status != "registered":
        raise HTTPException(status_code=409, detail=f"Model is {model.status}")

    session, contract = await _resolve_inference_session(db, model, current_user)
    if not session:
        raise HTTPException(
            status_code=409,
            detail="No active sandbox session for this model's product/contract; create one via POST /sandbox-sessions",
        )

    if contract:
        try:
            inference_service.check_purpose(contract, body.purpose)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    task_id = f"task-{uuid.uuid4().hex[:12]}"
    runner = build_inference_runner(model.model_id)
    timeout = min(_INFERENCE_TASK_TIMEOUT, session.timeout_seconds)
    encrypted_code = encrypt_task_code(runner, scope=task_id)

    task = SandboxTask(
        task_id=task_id,
        session_id=session.id,
        user_id=current_user.id,
        task_type=TaskType.INFERENCE.value,
        code_hash=sm3_hash(runner.encode()),
        code_content=encrypted_code,
        language="python",
        timeout_seconds=timeout,
        purpose=body.purpose,
        status=TaskStatus.CODE_SCANNING.value,
        started_at=datetime.now(timezone.utc),
    )
    db.add(task)
    await db.flush()

    await task_pipeline.submit(
        task_type=TaskType.INFERENCE.value,
        session_id=str(session.id),
        payload={
            "task_id": task_id,
            "code": runner,
            "language": "python",
            "sandbox_mode": session.sandbox_mode,
            "timeout_seconds": timeout,
            "inference_model_id": str(model.model_id),
            "inference_input": body.inputs,
        },
        timeout=timeout,
    )

    await audit_service.log(
        db, action="inference.model.invoke", resource_type="inference_model",
        user_id=current_user.id, resource_id=str(model.model_id),
        detail={"task_id": task_id, "session_id": str(session.id), "purpose": body.purpose},
    )
    await db.commit()

    await inference_service.record_usage(
        db,
        model_id=model.model_id,
        user_id=current_user.id,
        contract_id=session.contract_id,
        task_id=task_id,
        input_tokens=_estimate_input_tokens(body.inputs),
    )

    return {
        "task_id": task_id,
        "session_id": str(session.id),
        "status": task.status,
        "model_id": str(model.model_id),
        "message": "Inference task queued; poll GET /sandbox-tasks/{session_id}/{task_id}/result",
    }


@router.post("/{model_id}/revoke")
async def revoke_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Revoke a model — no further invokes accepted."""
    model = await inference_service.get_model(db, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    if current_user.id != model.owner_id and current_user.role not in (UserRole.ADMIN, UserRole.OPERATOR):
        raise HTTPException(status_code=403, detail="Only the owner or an operator can revoke this model")

    model = await inference_service.revoke_model(db, model_id)
    await audit_service.log(
        db, action="inference.model.revoke", resource_type="inference_model",
        user_id=current_user.id, resource_id=str(model_id), detail={"name": model.name},
    )
    return {"model_id": str(model_id), "status": model.status, "revoked_at": model.revoked_at.isoformat() if model.revoked_at else None}


@router.get("/metering")
async def get_metering(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Inference metering: providers see their own models; operators see all."""
    if current_user.role in (UserRole.ADMIN, UserRole.OPERATOR):
        usage = await inference_service.usage_stats(db)
    else:
        models = await inference_service.list_models(db, owner_id=current_user.id)
        model_ids = [m.model_id for m in models]
        usage = []
        for mid in model_ids:
            usage.extend(await inference_service.usage_stats(db, model_id=mid))
    return {
        "total": len(usage),
        "records": [
            {
                "id": str(u.id),
                "model_id": str(u.model_id),
                "contract_id": u.contract_id,
                "task_id": u.task_id,
                "input_tokens": u.input_tokens,
                "output_rows": u.output_rows,
                "status": u.status,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in usage
        ],
    }


async def _resolve_inference_session(
    db: AsyncSession, model, current_user: User
) -> tuple[SandboxSession | None, Contract | None]:
    """The buyer's active session for the model's product, plus its contract."""
    if model.product_id is None:
        if current_user.id != model.owner_id and current_user.role not in (UserRole.ADMIN, UserRole.OPERATOR):
            return None, None
        result = await db.execute(
            select(SandboxSession).where(
                SandboxSession.user_id == current_user.id,
                SandboxSession.status.in_([SessionStatus.READY.value, SessionStatus.RUNNING.value]),
            )
        )
        session = result.scalars().first()
        return session, None

    result = await db.execute(
        select(SandboxSession).where(
            SandboxSession.user_id == current_user.id,
            SandboxSession.data_product_id == model.product_id,
            SandboxSession.status.in_([SessionStatus.READY.value, SessionStatus.RUNNING.value]),
        )
    )
    for session in result.scalars().all():
        if not session.contract_id:
            continue
        try:
            cid = uuid.UUID(str(session.contract_id))
        except (TypeError, ValueError):
            continue
        contract = await db.get(Contract, cid)
        if not contract or contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SIGNED.value):
            continue
        if str(model.product_id) not in [str(pid) for pid in (contract.product_ids or [])]:
            continue
        return session, contract
    return None, None


def _model_view(m) -> dict:
    return {
        "model_id": str(m.model_id),
        "name": m.name,
        "format": m.format,
        "product_id": str(m.product_id) if m.product_id else None,
        "owner_id": str(m.owner_id),
        "task_id": m.task_id,
        "size_bytes": m.size_bytes,
        "status": m.status,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }
