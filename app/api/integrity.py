"""P2-5（D-e2e-18）一致性巡检 API：只读查询 + OPERATOR+ 手动触发.

挂载见 main.py（audit 组，与审计面同群）；OPERATOR+ 在路由级
require_roles 收紧。手动触发不受调度双门（INTEGRITY_PATROL_ENABLED）约束——
那两门管的是「后台要不要自己跑」，人工点一下是显式意图，直接跑并留审计。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.data_resource import DataResource, ResourceStatus
from app.models.user import User, UserRole
from app.services.integrity_patrol import (
    INTEGRITY_STATES,
    integrity_summary,
    patrol_cycle,
)

router = APIRouter()


@router.get("/data-resources")
async def list_resource_integrity(
    integrity_status: str | None = Query(None, description="ok|missing|uncheckable"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
):
    """ready 资源的巡检台账（只读，零副作用）。信封 {items,total,page,page_size}。"""
    if integrity_status is not None and integrity_status not in INTEGRITY_STATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"integrity_status 必须是 {'/'.join(INTEGRITY_STATES)} 之一",
        )
    from sqlalchemy import func as _func

    base = select(DataResource).where(DataResource.status == ResourceStatus.READY.value)
    if integrity_status:
        base = base.where(DataResource.integrity_status == integrity_status)
    total = (
        await db.execute(
            select(_func.count()).select_from(base.subquery())
        )
    ).scalar_one()
    rows = (
        await db.execute(
            base.order_by(DataResource.integrity_checked_at.asc().nulls_first())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).scalars().all()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": await integrity_summary(db),
        "items": [
            {
                "id": str(r.id),
                "name": r.name,
                "status": r.status,
                "integrity_status": r.integrity_status,
                "integrity_checked_at": r.integrity_checked_at.isoformat() if r.integrity_checked_at else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/patrol")
async def run_patrol(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
):
    """手动触发一轮 head 对账（OPERATOR+）。结果同时进审计（integrity.patrol）。
    本轮只处理一个批次（CDS_INTEGRITY_PATROL_BATCH_SIZE，未巡检者优先），
    返回里 checked=batch 上限即提示还有存量。"""
    return await patrol_cycle(db, triggered_by=f"user:{current_user.id}", user_id=current_user.id)
