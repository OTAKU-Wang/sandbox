"""W14: node operations — list, isolate/unisolate, health observation.

CubeSandbox C7 alignment (isolate ≈ cordon). Health detection is honest and
read-only: stale heartbeats mark nodes and raise alerts, but no automatic
recovery is attempted (W18 differentiator stays out of scope here).
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.config import get_settings
from app.models.sandbox_node import SandboxNode, NodeStatus
from app.models.user import User
from app.services.audit_service import audit_service
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")


def _node_payload(node: SandboxNode, running_sessions: int) -> dict:
    return {
        "id": str(node.id),
        "node_id": node.node_id,
        "hostname": node.hostname,
        "ip_address": node.ip_address,
        "status": node.status,
        "region": node.region,
        "capabilities": node.capabilities,
        "cpu_cores": node.cpu_cores,
        "memory_mb": node.memory_mb,
        "active_tasks": node.active_tasks,
        "max_tasks": node.max_tasks,
        "running_sessions": running_sessions,
        "scheduling_disabled": node.scheduling_disabled,
        "health_state": node.health_state,
        "last_heartbeat": node.last_heartbeat.isoformat() if node.last_heartbeat else None,
        "error_rate": node.error_rate,
    }


@router.get("")
async def list_nodes(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List sandbox nodes with operational health state (admin)."""
    _require_admin(current_user)
    result = await db.execute(select(SandboxNode).order_by(SandboxNode.created_at.desc()))
    nodes = list(result.scalars().all())
    return {
        "nodes": [_node_payload(n, n.active_tasks or 0) for n in nodes],
        "total": len(nodes),
    }


async def _load_node(db: AsyncSession, node_id: str) -> SandboxNode:
    result = await db.execute(select(SandboxNode).where(SandboxNode.node_id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return node


@router.post("/{node_id}/isolate")
async def isolate_node(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cordon a node: excluded from future scheduling, running sessions untouched."""
    _require_admin(current_user)
    node = await _load_node(db, node_id)
    node.scheduling_disabled = True
    node.health_state = "isolated"
    await db.flush()
    await audit_service.log(
        db, action="node.isolate", resource_type="sandbox_node",
        user_id=current_user.id, resource_id=str(node.id),
        detail={"node_id": node.node_id},
    )
    await db.flush()
    return {"node_id": node.node_id, "scheduling_disabled": True, "health_state": node.health_state}


@router.post("/{node_id}/unisolate")
async def unisolate_node(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Restore a node to scheduling."""
    _require_admin(current_user)
    node = await _load_node(db, node_id)
    node.scheduling_disabled = False
    node.health_state = "healthy" if node.status == NodeStatus.ONLINE.value else "unknown"
    await db.flush()
    await audit_service.log(
        db, action="node.unisolate", resource_type="sandbox_node",
        user_id=current_user.id, resource_id=str(node.id),
        detail={"node_id": node.node_id},
    )
    await db.flush()
    return {"node_id": node.node_id, "scheduling_disabled": False, "health_state": node.health_state}


async def detect_stale_nodes(db: AsyncSession) -> int:
    """Mark ONLINE nodes with an overdue heartbeat as stale + raise alerts.

    Called from the session cleanup loop. Alert-only — recovery (W18) is a
    separate capability and is NOT attempted here.
    """
    settings = get_settings()
    cutoff = datetime.now(timezone.utc).timestamp() - settings.NODE_STALE_SECONDS
    result = await db.execute(
        select(SandboxNode).where(SandboxNode.status == NodeStatus.ONLINE.value)
    )
    stale_count = 0
    for node in result.scalars().all():
        if not node.last_heartbeat:
            continue
        hb = node.last_heartbeat
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        if hb.timestamp() > cutoff:
            continue
        if node.health_state == "stale":
            continue
        node.health_state = "stale"
        stale_count += 1

        from app.models.alert import AlertRecord

        db.add(AlertRecord(
            dedup_key=f"node-stale-{node.node_id}",
            alert_type="node_stale",
            severity="warning",
            status="open",
            message=f"Node {node.node_id} heartbeat overdue (> {settings.NODE_STALE_SECONDS}s)",
            resource_type="sandbox_node",
            resource_id=str(node.id),
        ))
        await audit_service.log(
            db, action="node.stale_detected", resource_type="sandbox_node",
            resource_id=str(node.id),
            detail={"node_id": node.node_id, "stale_seconds": settings.NODE_STALE_SECONDS},
        )
    if stale_count:
        await db.flush()
        logger.warning("[NodeOps] %d nodes marked stale", stale_count)
    return stale_count


async def recover_stale_nodes(db: AsyncSession) -> int:
    """W18: take stale nodes offline once their heartbeat is hopelessly old.

    A node still stale after a full extra grace window (heartbeat older than
    2 x NODE_STALE_SECONDS) is flipped to OFFLINE so scheduling stops
    relying on it; an alert + audit record mark the transition. Recovery of
    workloads (rescheduling) is out of scope here — sessions are tracked by
    their own lifecycle, nodes are capacity only.
    """
    settings = get_settings()
    hard_cutoff = datetime.now(timezone.utc).timestamp() - 2 * settings.NODE_STALE_SECONDS
    result = await db.execute(
        select(SandboxNode).where(SandboxNode.status == NodeStatus.ONLINE.value)
    )
    offline_count = 0
    for node in result.scalars().all():
        if not node.last_heartbeat:
            continue
        hb = node.last_heartbeat
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        if hb.timestamp() > hard_cutoff:
            continue
        node.status = NodeStatus.OFFLINE.value
        node.health_state = "offline"
        offline_count += 1

        from app.models.alert import AlertRecord

        db.add(AlertRecord(
            dedup_key=f"node-offline-{node.node_id}",
            alert_type="node_offline",
            severity="critical",
            status="open",
            message=f"Node {node.node_id} automatically taken offline (heartbeat silent > {2 * settings.NODE_STALE_SECONDS}s)",
            resource_type="sandbox_node",
            resource_id=str(node.id),
        ))
        await audit_service.log(
            db, action="node.auto_offline", resource_type="sandbox_node",
            resource_id=str(node.id),
            detail={"node_id": node.node_id, "grace_seconds": 2 * settings.NODE_STALE_SECONDS},
        )
    if offline_count:
        await db.flush()
        logger.warning("[NodeOps] %d stale nodes taken offline", offline_count)
    return offline_count
