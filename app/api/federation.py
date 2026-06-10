"""Federation API — Cross-space trust and request proxying."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.data_product import DataProduct
from app.models.user import User, UserRole
from app.services.catalog_sync import SyncMode, catalog_sync
from app.services.federation_connector import (
    federation_connector,
    SpaceIdentity,
    TrustLevel,
    FederationStatus,
)

router = APIRouter()


class EstablishTrustRequest(BaseModel):
    space_id: str = Field(..., min_length=1, max_length=64)
    space_name: str = Field(..., min_length=1, max_length=128)
    endpoint: str = Field(..., min_length=1)
    public_key: str = ""
    certificate: str = ""
    trust_level: str = Field("basic", pattern="^(none|basic|verified|full)$")
    allowed_operations: list[str] | None = None
    policy_sync: bool = False


class SendRequestInput(BaseModel):
    trust_id: str
    operation: str = Field(..., min_length=1, max_length=64)
    resource: str = Field(..., min_length=1, max_length=512)
    payload: dict | None = None


def _ensure_local_space():
    """Ensure local space is configured."""
    if not federation_connector._local_space:
        federation_connector.set_local_space(SpaceIdentity(
            space_id="cds-local",
            space_name="Local CDS",
            endpoint="https://local.cds.example.com",
        ))


def _trust_payload(trust) -> dict:
    return {
        "trust_id": trust.trust_id,
        "remote_space": trust.remote_space.space_id,
        "remote_space_name": trust.remote_space.space_name,
        "endpoint": trust.remote_space.endpoint,
        "trust_level": trust.trust_level.value,
        "status": trust.status.value,
        "allowed_operations": trust.allowed_operations,
        "policy_sync_enabled": trust.policy_sync_enabled,
        "created_at": trust.created_at.isoformat(),
        "expires_at": trust.expires_at.isoformat() if trust.expires_at else None,
        "last_sync": trust.last_sync.isoformat() if trust.last_sync else None,
    }


@router.post("/trust")
async def establish_trust(
    body: EstablishTrustRequest,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Establish a trust relationship with a remote space."""
    _ensure_local_space()

    remote = SpaceIdentity(
        space_id=body.space_id,
        space_name=body.space_name,
        endpoint=body.endpoint,
        public_key=body.public_key,
        certificate=body.certificate,
    )

    trust_level = TrustLevel(body.trust_level)
    trust = federation_connector.establish_trust(
        remote, trust_level,
        allowed_operations=body.allowed_operations,
        policy_sync=body.policy_sync,
    )

    return _trust_payload(trust)


@router.get("/trusts")
async def list_trusts(
    status: str | None = None,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """List all trust relationships."""
    _ensure_local_space()
    status_filter = FederationStatus(status) if status else None
    trusts = federation_connector.list_trusts(status=status_filter)
    return [_trust_payload(t) for t in trusts]


@router.get("/trusts/{space_or_trust_id}/score")
async def get_trust_score(
    space_or_trust_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Calculate an operational trust score for a trust or remote space."""
    _ensure_local_space()
    trust = (
        federation_connector.get_trust(space_or_trust_id)
        or federation_connector.get_trust_by_space(space_or_trust_id)
    )
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")

    level_base = {
        TrustLevel.NONE: 10,
        TrustLevel.BASIC: 45,
        TrustLevel.VERIFIED: 72,
        TrustLevel.FULL: 88,
    }[trust.trust_level]
    status_penalty = {
        FederationStatus.ACTIVE: 0,
        FederationStatus.PENDING: 12,
        FederationStatus.SUSPENDED: 35,
        FederationStatus.REVOKED: 85,
    }[trust.status]

    audit_entries = federation_connector.get_audit_log(space_id=trust.remote_space.space_id, limit=500)
    failed = sum(1 for entry in audit_entries if entry.status not in {"success", "ok", "200"})
    failure_rate = failed / len(audit_entries) if audit_entries else 0.0
    reputation = max(0, min(100, 100 - int(failure_rate * 100)))

    catalog_count_result = await db.execute(
        select(func.count()).select_from(DataProduct).where(DataProduct.name.like(f"[remote:{trust.remote_space.space_id}]%"))
    )
    catalog_count = catalog_count_result.scalar() or 0
    data_quality = min(100, 60 + catalog_count * 4) if catalog_count else 50

    compliance = 70
    if trust.policy_sync_enabled:
        compliance += 15
    if "verify_audit" in trust.allowed_operations:
        compliance += 10
    if trust.status != FederationStatus.ACTIVE:
        compliance -= 30
    compliance = max(0, min(100, compliance))

    security = level_base
    if trust.remote_space.certificate:
        security += 8
    if trust.remote_space.public_key:
        security += 4
    if "write_data" in trust.allowed_operations and trust.trust_level != TrustLevel.FULL:
        security -= 15
    security = max(0, min(100, security - status_penalty))

    score = round((security * 0.35) + (compliance * 0.25) + (reputation * 0.25) + (data_quality * 0.15))
    return {
        "level": trust.trust_level.value,
        "score": max(0, min(100, score)),
        "data_quality": data_quality,
        "compliance": compliance,
        "reputation": reputation,
        "security": security,
    }


@router.get("/trusts/{trust_id}")
async def get_trust(
    trust_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Get one trust relationship."""
    _ensure_local_space()
    trust = federation_connector.get_trust(trust_id)
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")
    return _trust_payload(trust)


@router.post("/trusts/{trust_id}/revoke")
async def revoke_trust(
    trust_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Revoke a trust relationship."""
    if not federation_connector.revoke_trust(trust_id):
        raise HTTPException(status_code=404, detail="Trust not found")
    trust = federation_connector.get_trust(trust_id)
    return _trust_payload(trust)


@router.post("/trusts/{trust_id}/suspend")
async def suspend_trust(
    trust_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Suspend a trust relationship."""
    if not federation_connector.suspend_trust(trust_id):
        raise HTTPException(status_code=404, detail="Trust not found")
    trust = federation_connector.get_trust(trust_id)
    return _trust_payload(trust)


@router.get("/catalog/sync-status")
async def get_catalog_sync_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Get catalog sync status for all trusted spaces."""
    _ensure_local_space()
    trusts = federation_connector.list_trusts()
    items = []
    for trust in trusts:
        space_id = trust.remote_space.space_id
        history = catalog_sync.get_sync_history(space_id)
        latest = history[-1] if history else None
        count_result = await db.execute(
            select(func.count()).select_from(DataProduct).where(DataProduct.name.like(f"[remote:{space_id}]%"))
        )
        entry_count = count_result.scalar() or 0
        items.append({
            "space_id": space_id,
            "space_name": trust.remote_space.space_name,
            "last_sync_at": latest.completed_at.isoformat() if latest and latest.completed_at else None,
            "entry_count": entry_count,
            "status": latest.status.value if latest else "never_synced",
        })
    return items


@router.post("/catalog/sync/{space_id}")
async def trigger_catalog_sync(
    space_id: str,
    mode: str = Query(SyncMode.INCREMENTAL.value, pattern="^(full|incremental)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Trigger catalog synchronization from a trusted remote space."""
    _ensure_local_space()
    result = await catalog_sync.sync_space(db, space_id, SyncMode(mode))
    return {
        "sync_id": result.sync_id,
        "space_id": result.space_id,
        "mode": result.mode.value,
        "status": result.status.value,
        "synced": result.entries_synced,
        "added": result.entries_added,
        "updated": result.entries_updated,
        "removed": result.entries_removed,
        "errors": result.errors,
    }


@router.get("/catalog/entries")
async def list_synced_catalog_entries(
    space_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """List locally mirrored remote catalog entries."""
    query = select(DataProduct).where(DataProduct.name.like("[remote:%"))
    if space_id:
        query = query.where(DataProduct.name.like(f"[remote:{space_id}]%"))
    query = query.order_by(DataProduct.updated_at.desc()).limit(limit)
    result = await db.execute(query)
    entries = []
    for product in result.scalars().all():
        remote_meta = (product.data_schema or {}).get("remote", {}) if isinstance(product.data_schema, dict) else {}
        entries.append({
            "id": str(product.id),
            "remote_space_id": remote_meta.get("space_id") or space_id or "",
            "product_name": product.name,
            "product_id": remote_meta.get("remote_id") or str(product.id),
            "synced_at": product.updated_at.isoformat() if product.updated_at else product.created_at.isoformat(),
        })
    return entries


@router.post("/request")
async def send_federation_request(
    body: SendRequestInput,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Send a proxied request to a remote space."""
    trust = federation_connector.get_trust(body.trust_id)
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")

    response = federation_connector.send_request(
        trust, body.operation, body.resource,
        payload=body.payload, user_id=str(current_user.id),
    )

    return {
        "request_id": response.request_id,
        "status_code": response.status_code,
        "data": response.data,
        "error": response.error,
        "duration_ms": response.duration_ms,
        "source_space": response.source_space,
    }


@router.get("/audit")
async def get_federation_audit(
    space_id: str | None = None,
    operation: str | None = None,
    limit: int = 100,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.REGULATOR)),
):
    """Get federation audit log."""
    entries = federation_connector.get_audit_log(space_id=space_id, operation=operation, limit=limit)
    return [
        {
            "entry_id": e.entry_id,
            "request_id": e.request_id,
            "source_space": e.source_space,
            "target_space": e.target_space,
            "operation": e.operation,
            "resource": e.resource,
            "status": e.status,
            "user_id": e.user_id,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in entries
    ]


@router.get("/stats")
async def get_federation_stats(
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Get federation statistics."""
    return federation_connector.get_stats()
