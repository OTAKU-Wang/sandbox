import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.core.errors import CDSError
from app.models.compute_run import ComputeRun, ComputeRunStatus, ComputeTrigger
from app.models.contract import Contract
from app.models.user import User
from app.schemas.flow import ComputeRunCreate
from app.services import compute_scheduler
from app.services.audit_service import audit_service

router = APIRouter()

_EDITABLE_STATUSES = (ComputeRunStatus.PENDING.value, ComputeRunStatus.RUNNING.value)


def _is_party(contract: Contract, user: User) -> bool:
    return contract.buyer_id == user.id or contract.provider_id == user.id or getattr(user, "role", None) in ("admin", "platform_admin")


async def _load_contract(db: AsyncSession, contract_id: uuid.UUID, user: User) -> Contract:
    res = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = res.scalar_one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    if not _is_party(contract, user):
        raise HTTPException(status_code=403, detail="not a party of this contract")
    return contract


async def _run_dict(db: AsyncSession, run: ComputeRun) -> dict:
    return {
        "id": str(run.id),
        "contract_id": str(run.contract_id),
        "workload_id": str(run.workload_id) if run.workload_id else None,
        "trigger": run.trigger,
        "scheduled_for": run.scheduled_for,
        "status": run.status,
        "attempt": run.attempt,
        "session_id": str(run.session_id) if run.session_id else None,
        "output_files": run.output_files,
        "output_rows": run.output_rows,
        "resource_usage": run.resource_usage,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
    }


@router.get("/schedule")
async def get_schedule(
    contract_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    stmt = select(Contract)
    if getattr(current_user, "role", None) not in ("admin", "platform_admin"):
        stmt = stmt.where(or_(Contract.buyer_id == current_user.id, Contract.provider_id == current_user.id))
    if contract_id is not None:
        stmt = stmt.where(Contract.id == contract_id)
    res = await db.execute(stmt.order_by(Contract.next_compute_at.asc().nulls_last()))
    items = []
    for contract in res.scalars().all():
        items.append(
            {
                "contract_id": str(contract.id),
                "contract_no": contract.contract_no,
                "contract_status": contract.status,
                "auto_compute": contract.auto_compute,
                "compute_frequency": contract.compute_frequency,
                "compute_minute": contract.compute_minute,
                "compute_hour": contract.compute_hour,
                "compute_tz": contract.compute_tz,
                "next_compute_at": contract.next_compute_at,
                "last_computed_at": contract.last_computed_at,
                "delivery_methods": contract.delivery_methods or [],
                "sandbox_resource_spec": contract.sandbox_resource_spec,
            }
        )
    return {"items": items, "total": len(items), "server_time": datetime.now(timezone.utc)}


@router.post("/runs")
async def trigger_manual_run(
    body: ComputeRunCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    contract = await _load_contract(db, body.contract_id, current_user)
    run = await compute_scheduler.claim_run(db, contract, trigger=ComputeTrigger.MANUAL.value, scheduled_for=None, requested_by=current_user.id)
    if run is None:
        raise CDSError("该合约已有计算运行在进行中，请稍后再试", code="COMPUTE_RUN_ACTIVE", http_status=409)
    await audit_service.log(
        db, action="compute.run.requested", resource_type="contract",
        user_id=current_user.id, resource_id=str(contract.id), detail={"run_id": str(run.id), "trigger": ComputeTrigger.MANUAL.value},
    )
    await db.commit()
    await compute_scheduler.execute_run(db, run, contract)
    contract.last_computed_at = run.finished_at
    await db.commit()
    return await _run_dict(db, run)


@router.get("/runs")
async def list_runs(
    contract_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    stmt = select(ComputeRun).join(Contract, Contract.id == ComputeRun.contract_id)
    if getattr(current_user, "role", None) not in ("admin", "platform_admin"):
        stmt = stmt.where(or_(Contract.buyer_id == current_user.id, Contract.provider_id == current_user.id))
    if contract_id is not None:
        stmt = stmt.where(ComputeRun.contract_id == contract_id)
    if status_filter:
        stmt = stmt.where(ComputeRun.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    res = await db.execute(stmt.order_by(ComputeRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    runs = list(res.scalars().all())
    return {"items": [await _run_dict(db, r) for r in runs], "total": total, "page": page, "page_size": page_size}


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    res = await db.execute(select(ComputeRun).where(ComputeRun.id == run_id))
    run = res.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="compute run not found")
    await _load_contract(db, run.contract_id, current_user)
    return await _run_dict(db, run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_business_user)):
    res = await db.execute(select(ComputeRun).where(ComputeRun.id == run_id))
    run = res.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="compute run not found")
    await _load_contract(db, run.contract_id, current_user)
    if run.status not in _EDITABLE_STATUSES:
        raise CDSError(f"运行已处于终态 {run.status}，无法取消", code="COMPUTE_RUN_TERMINAL", http_status=409)
    if run.status == ComputeRunStatus.RUNNING.value:
        raise CDSError(
            "运行中的沙箱不能被静默中止，请终止其会话以停止执行",
            code="COMPUTE_RUN_RUNNING",
            http_status=409,
            detail={"session_id": str(run.session_id) if run.session_id else None},
        )
    run.status = ComputeRunStatus.CANCELLED.value
    run.finished_at = datetime.now(timezone.utc)
    await audit_service.log(db, action="compute.run.cancelled", resource_type="contract", user_id=current_user.id, resource_id=str(run.contract_id), detail={"run_id": str(run.id)})
    await db.commit()
    return await _run_dict(db, run)
