"""Network Policy API — CRUD for sandbox network access control.

Zero-trust networking: default deny, explicit allowlist per session.
Supports IP CIDR ranges, domain names, and port restrictions.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.sandbox_session import SandboxSession
from app.models.user import User, UserRole
from app.models.network_policy import NetworkPolicy
from app.schemas.network_policy import (
    NetworkPolicyCreate,
    NetworkPolicyUpdate,
    NetworkPolicyResponse,
)
from app.services.audit_service import audit_service
from app.services.network_policy import network_policy_engine

router = APIRouter()


def _can_view_all_network_policies(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)


def _can_operate_network_policies(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.ADMIN)


async def _get_authorized_session(
    db: AsyncSession,
    session_id: str,
    user: User,
    *,
    write: bool,
) -> SandboxSession:
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session ID")
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_uuid))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id == user.id:
        return session
    if write and _can_operate_network_policies(user):
        return session
    if not write and _can_view_all_network_policies(user):
        return session
    raise HTTPException(status_code=403, detail="Not your session")


@router.post("", response_model=NetworkPolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_network_policy(
    body: NetworkPolicyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a network policy for a sandbox session."""
    session = await _get_authorized_session(db, body.session_id, current_user, write=True)
    # Check if policy already exists for this session
    existing = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == body.session_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Network policy already exists for session {body.session_id}",
        )

    policy = NetworkPolicy(
        session_id=body.session_id,
        user_id=str(session.user_id),
        mode=body.mode,
        allowed_ips=body.allowed_ips,
        allowed_domains=body.allowed_domains,
        allowed_ports=body.allowed_ports,
        dns_proxy_enabled=body.dns_proxy_enabled,
        max_connections_per_second=body.max_connections_per_second,
        max_bandwidth_bytes_per_second=body.max_bandwidth_bytes_per_second,
    )
    db.add(policy)
    await db.flush()
    await db.refresh(policy)

    # Enforce policy via iptables/DNS proxy
    from app.services.network_policy import NetworkPolicyConfig
    config = NetworkPolicyConfig(
        mode=policy.mode,
        allowed_ips=policy.allowed_ips or [],
        allowed_domains=policy.allowed_domains or [],
        allowed_ports=policy.allowed_ports or [],
        dns_proxy_enabled=policy.dns_proxy_enabled,
        max_connections_per_second=policy.max_connections_per_second,
        max_bandwidth_bytes_per_second=policy.max_bandwidth_bytes_per_second,
    )
    await network_policy_engine.create_policy(body.session_id, config)

    await audit_service.log(
        db,
        action="network_policy.create",
        resource_type="network_policy",
        user_id=current_user.id,
        session_id=body.session_id,
        detail={"mode": body.mode, "allowed_ips": body.allowed_ips, "allowed_domains": body.allowed_domains},
    )

    return NetworkPolicyResponse.model_validate(policy)


@router.get("", response_model=dict)
async def list_network_policies(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List network policies for the current user."""
    base = select(NetworkPolicy).where(NetworkPolicy.user_id == str(current_user.id))
    count_base = select(func.count()).select_from(NetworkPolicy).where(
        NetworkPolicy.user_id == str(current_user.id)
    )

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    result = await db.execute(base.offset(skip).limit(limit))
    items = [NetworkPolicyResponse.model_validate(p) for p in result.scalars().all()]
    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/session/{session_id}", response_model=NetworkPolicyResponse)
async def get_policy_by_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get network policy for a specific sandbox session."""
    await _get_authorized_session(db, session_id, current_user, write=False)
    result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == session_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="No network policy for this session")
    return NetworkPolicyResponse.model_validate(policy)


@router.get("/session/{session_id}/egress-audit")
async def get_session_egress_audit(
    session_id: str,
    limit: int = Query(50, ge=1, le=500),
    since: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Tail the W13 egress audit JSONL for one session (newest first)."""
    from app.services.egress_audit import read_egress_records

    await _get_authorized_session(db, session_id, current_user, write=False)
    records = read_egress_records(session_id, limit=limit, since=since)
    return {"session_id": session_id, "records": records, "count": len(records)}


@router.get("/{policy_id}", response_model=NetworkPolicyResponse)
async def get_network_policy(
    policy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific network policy."""
    result = await db.execute(select(NetworkPolicy).where(NetworkPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Network policy not found")
    if policy.user_id != str(current_user.id) and not _can_view_all_network_policies(current_user):
        raise HTTPException(status_code=403, detail="Not your policy")
    return NetworkPolicyResponse.model_validate(policy)


@router.patch("/{policy_id}", response_model=NetworkPolicyResponse)
async def update_network_policy(
    policy_id: uuid.UUID,
    body: NetworkPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a network policy (hot-reload enforcement)."""
    result = await db.execute(select(NetworkPolicy).where(NetworkPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Network policy not found")
    if policy.user_id != str(current_user.id) and not _can_operate_network_policies(current_user):
        raise HTTPException(status_code=403, detail="Not your policy")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(policy, field, value)

    await db.flush()
    await db.refresh(policy)

    # Hot-reload: remove old policy, create new one
    await network_policy_engine.remove_policy(policy.session_id)
    if policy.active:
        from app.services.network_policy import NetworkPolicyConfig
        config = NetworkPolicyConfig(
            mode=policy.mode,
            allowed_ips=policy.allowed_ips or [],
            allowed_domains=policy.allowed_domains or [],
            allowed_ports=policy.allowed_ports or [],
            dns_proxy_enabled=policy.dns_proxy_enabled,
            max_connections_per_second=policy.max_connections_per_second,
            max_bandwidth_bytes_per_second=policy.max_bandwidth_bytes_per_second,
        )
        await network_policy_engine.create_policy(policy.session_id, config)

    return NetworkPolicyResponse.model_validate(policy)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_network_policy(
    policy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a network policy (removes enforcement)."""
    result = await db.execute(select(NetworkPolicy).where(NetworkPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Network policy not found")
    if policy.user_id != str(current_user.id) and not _can_operate_network_policies(current_user):
        raise HTTPException(status_code=403, detail="Not your policy")

    # Remove enforcement
    await network_policy_engine.remove_policy(policy.session_id)

    await db.delete(policy)
    await audit_service.log(
        db,
        action="network_policy.delete",
        resource_type="network_policy",
        user_id=current_user.id,
        session_id=policy.session_id,
    )
