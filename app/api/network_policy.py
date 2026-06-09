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
from app.models.user import User
from app.models.network_policy import NetworkPolicy
from app.schemas.network_policy import (
    NetworkPolicyCreate,
    NetworkPolicyUpdate,
    NetworkPolicyResponse,
)
from app.services.audit_service import audit_service
from app.services.network_policy import network_policy_engine

router = APIRouter()


@router.post("", response_model=NetworkPolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_network_policy(
    body: NetworkPolicyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a network policy for a sandbox session."""
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
        user_id=str(current_user.id),
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
    network_policy_engine.create_policy(body.session_id, config)

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
    if policy.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your policy")
    return NetworkPolicyResponse.model_validate(policy)


@router.get("/session/{session_id}", response_model=NetworkPolicyResponse)
async def get_policy_by_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get network policy for a specific sandbox session."""
    result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == session_id)
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="No network policy for this session")
    if policy.user_id != str(current_user.id):
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
    if policy.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your policy")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(policy, field, value)

    await db.flush()
    await db.refresh(policy)

    # Hot-reload: remove old policy, create new one
    network_policy_engine.remove_policy(policy.session_id)
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
        network_policy_engine.create_policy(policy.session_id, config)

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
    if policy.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your policy")

    # Remove enforcement
    network_policy_engine.remove_policy(policy.session_id)

    await db.delete(policy)
    await audit_service.log(
        db,
        action="network_policy.delete",
        resource_type="network_policy",
        user_id=current_user.id,
        session_id=policy.session_id,
    )
