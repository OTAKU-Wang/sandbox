"""Monitoring API — system health and statistics."""
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import UserRole
from app.core.cache import cache_get, cache_set
from app.models.user import User
from app.models.contract import Contract
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession
from app.models.audit_log import AuditLog

router = APIRouter()

STATS_CACHE_KEY = "monitoring:stats"
STATS_CACHE_TTL = 30  # seconds


@router.get("/stats")
async def get_monitoring_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get system monitoring statistics. Cached for 30s to reduce DB load."""
    cached = await cache_get(STATS_CACHE_KEY)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Parallel count queries via single round-trip
    active_q = select(func.count()).select_from(SandboxSession).where(SandboxSession.status == "running")
    contracts_q = select(func.count()).select_from(Contract).where(Contract.created_at >= today_start)
    products_q = select(func.count()).select_from(DataProduct)
    audit_q = select(func.count()).select_from(AuditLog)
    anchored_q = select(func.count()).select_from(AuditLog).where(AuditLog.blockchain_tx_hash.isnot(None))
    insp_total_q = select(func.count()).select_from(AuditLog).where(AuditLog.action.like("output_inspection%"))
    insp_pass_q = select(func.count()).select_from(AuditLog).where(AuditLog.action == "output_inspection_pass")

    results = await asyncio.gather(
        db.execute(active_q),
        db.execute(contracts_q),
        db.execute(products_q),
        db.execute(audit_q),
        db.execute(anchored_q),
        db.execute(insp_total_q),
        db.execute(insp_pass_q),
    )

    active_sessions = results[0].scalar() or 0
    today_contracts = results[1].scalar() or 0
    total_products = results[2].scalar() or 0
    total_audit_records = results[3].scalar() or 0
    total_anchored = results[4].scalar() or 0
    total_inspections = results[5].scalar() or 0
    passed_inspections = results[6].scalar() or 0
    pass_rate = (passed_inspections / total_inspections * 100) if total_inspections > 0 else 100.0

    result = {
        "active_sessions": active_sessions,
        "today_contracts": today_contracts,
        "total_products": total_products,
        "total_audit_records": total_audit_records,
        "total_anchored": total_anchored,
        "inspection_pass_rate": round(pass_rate, 1),
    }

    await cache_set(STATS_CACHE_KEY, result, STATS_CACHE_TTL)
    return result


@router.get("/alerts")
async def list_alerts(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """List security alerts from audit logs."""
    # Security-relevant audit actions
    security_actions = [
        "output_inspection_fail",
        "unauthorized_access",
        "dp_budget_exceeded",
        "contract_violation",
        "sandbox_escape_attempt",
    ]

    query = select(AuditLog).where(AuditLog.action.in_(security_actions))
    count_query = select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(security_actions))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)

    items = []
    for log in result.scalars().all():
        items.append({
            "id": str(log.id),
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/task-trend")
async def get_task_trend(
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get task execution trend for the last N days."""
    cache_key = f"monitoring:task_trend:{days}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Group audit events by date for task-related actions
    task_actions = [
        "sandbox.create", "sandbox.start", "sandbox.stop",
        "task.execute", "task.complete", "task.fail",
    ]

    query = (
        select(
            cast(AuditLog.created_at, Date).label("date"),
            func.count().label("count"),
        )
        .where(AuditLog.created_at >= start_date)
        .where(AuditLog.action.in_(task_actions))
        .group_by(cast(AuditLog.created_at, Date))
        .order_by(cast(AuditLog.created_at, Date))
    )

    result = await db.execute(query)
    rows = result.all()

    # Fill in missing dates with 0
    trend = []
    current = start_date.date()
    end = now.date()
    row_dict = {str(r.date): r.count for r in rows}

    while current <= end:
        date_str = current.isoformat()
        trend.append({"date": date_str, "count": row_dict.get(date_str, 0)})
        current += timedelta(days=1)

    await cache_set(cache_key, trend, 60)
    return trend


@router.get("/policy-rejection")
async def get_policy_rejection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get policy rejection rate breakdown."""
    cache_key = "monitoring:policy_rejection"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count total and rejected queries
    total_q = select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= today_start)
    rejected_q = (
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.created_at >= today_start)
        .where(AuditLog.action.in_(["policy.deny", "output_inspection_fail", "contract_violation"]))
    )

    # Rejection by category
    category_q = (
        select(
            AuditLog.action.label("category"),
            func.count().label("count"),
        )
        .where(AuditLog.created_at >= today_start)
        .where(AuditLog.action.in_(["policy.deny", "output_inspection_fail", "contract_violation"]))
        .group_by(AuditLog.action)
    )

    total_r, rejected_r, category_r = await asyncio.gather(
        db.execute(total_q),
        db.execute(rejected_q),
        db.execute(category_q),
    )

    total = total_r.scalar() or 0
    rejected = rejected_r.scalar() or 0
    categories = [{"category": r.category, "count": r.count} for r in category_r.all()]

    result = {
        "total": total,
        "rejected": rejected,
        "pass_rate": round(((total - rejected) / total * 100) if total > 0 else 100.0, 1),
        "categories": categories,
    }

    await cache_set(cache_key, result, 60)
    return result


@router.get("/security-distribution")
async def get_security_distribution(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get security level distribution of data products."""
    cache_key = "monitoring:security_distribution"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    query = (
        select(
            DataProduct.security_level.label("level"),
            func.count().label("count"),
        )
        .group_by(DataProduct.security_level)
    )

    result = await db.execute(query)
    rows = result.all()

    # Normalize to L1/L2/L3
    distribution = []
    for r in rows:
        level = r.level or "L1"
        distribution.append({"level": level, "count": r.count})

    await cache_set(cache_key, distribution, 60)
    return distribution


@router.get("/recent-events")
async def get_recent_events(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get recent audit events for real-time event stream."""
    query = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )

    result = await db.execute(query)
    events = []

    for log in result.scalars().all():
        events.append({
            "id": str(log.id),
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "user_id": str(log.user_id) if log.user_id else None,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return events
