"""Federation API — Cross-space trust and request proxying."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.data_product import DataProduct
from app.models.federation_trust import FederationAuditRecord
from app.models.user import User, UserRole
from app.services.audit_service import audit_service
from app.services.catalog_sync import SyncMode, catalog_sync, remote_name_like
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
    db: AsyncSession = Depends(get_db),
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
    try:
        trust = federation_connector.establish_trust(
            remote, trust_level,
            allowed_operations=body.allowed_operations,
            policy_sync=body.policy_sync,
        )
    except ValueError as exc:
        # A60: 端点校验在连接器里 fail-closed，但操作员面对的是 HTTP 面 ——
        # 不接住 ValueError 就走兵底 handler 的裸 500（无可行动信息）。在操作员边界
        # 转成 400，不放宽校验。
        raise HTTPException(status_code=400, detail=f"Federation endpoint refused: {exc}") from exc

    # A64: trust establishment is a cross-space governance decision — it must
    # enter the audit funnel like every other mutating endpoint.
    await audit_service.log(
        db, action="federation.trust.established", resource_type="federation_trust",
        user_id=current_user.id, resource_id=trust.trust_id,
        detail={
            "remote_space": trust.remote_space.space_id,
            "remote_endpoint": trust.remote_space.endpoint,
            "trust_level": trust.trust_level.value,
            "allowed_operations": list(trust.allowed_operations),
            "policy_sync": trust.policy_sync_enabled,
        },
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
        select(func.count()).select_from(DataProduct).where(remote_name_like(trust.remote_space.space_id))
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Revoke a trust relationship."""
    if not federation_connector.revoke_trust(trust_id):
        raise HTTPException(status_code=404, detail="Trust not found")
    trust = federation_connector.get_trust(trust_id)
    # A64: revocation cuts off a whole remote space — audited.
    await audit_service.log(
        db, action="federation.trust.revoked", resource_type="federation_trust",
        user_id=current_user.id, resource_id=trust_id,
        detail={"remote_space": trust.remote_space.space_id if trust else None},
    )
    return _trust_payload(trust)


@router.post("/trusts/{trust_id}/suspend")
async def suspend_trust(
    trust_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Suspend a trust relationship."""
    if not federation_connector.suspend_trust(trust_id):
        raise HTTPException(status_code=404, detail="Trust not found")
    trust = federation_connector.get_trust(trust_id)
    # A64: suspension is a governance action — audited like revoke.
    await audit_service.log(
        db, action="federation.trust.suspended", resource_type="federation_trust",
        user_id=current_user.id, resource_id=trust_id,
        detail={"remote_space": trust.remote_space.space_id if trust else None},
    )
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
            select(func.count()).select_from(DataProduct).where(remote_name_like(space_id))
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
    # A64: a catalog sync mutates the local mirror with remote-supplied
    # metadata — the operator action and its deltas are audited.
    await audit_service.log(
        db, action="federation.catalog.synced", resource_type="federation_space",
        user_id=current_user.id, resource_id=space_id,
        detail={
            "sync_id": result.sync_id, "mode": result.mode.value, "status": result.status.value,
            "synced": result.entries_synced, "added": result.entries_added,
            "updated": result.entries_updated, "removed": result.entries_removed,
            "errors": len(result.errors),
        },
    )
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
    query = select(DataProduct).where(remote_name_like())
    if space_id:
        query = query.where(remote_name_like(space_id))
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Send a proxied request to a remote space."""
    trust = federation_connector.get_trust(body.trust_id)
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")

    response = await federation_connector.send_request_async(
        trust, body.operation, body.resource,
        payload=body.payload, user_id=str(current_user.id),
    )

    # A64: every cross-space egress goes through the audit funnel — the
    # connector's in-process log is neither durable nor cross-worker.
    await audit_service.log(
        db, action="federation.request.sent", resource_type="federation_trust",
        user_id=current_user.id, resource_id=body.trust_id,
        detail={
            "request_id": response.request_id,
            "operation": body.operation,
            "resource": body.resource,
            "status_code": response.status_code,
            "error": response.error,
            "remote_space": trust.remote_space.space_id,
        },
    )

    return {
        "request_id": response.request_id,
        "status_code": response.status_code,
        "data": response.data,
        "error": response.error,
        "duration_ms": response.duration_ms,
        "source_space": response.source_space,
    }


def _entry_view(entry_id: str, request_id: str, source_space: str, target_space: str,
                operation: str, resource: str, status: str, user_id: str | None,
                timestamp: datetime) -> dict:
    return {
        "entry_id": entry_id,
        "request_id": request_id,
        "source_space": source_space,
        "target_space": target_space,
        "operation": operation,
        "resource": resource,
        "status": status,
        "user_id": user_id,
        "timestamp": timestamp.isoformat(),
    }


def _sort_key(entry: dict) -> str:
    return entry["timestamp"]


@router.get("/audit")
async def get_federation_audit(
    space_id: str | None = None,
    operation: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.REGULATOR)),
):
    """Get the federation audit log — the persisted record, not process memory.

    A64: `_persist_audit_entry` wrote `FederationAuditRecord` rows that no
    endpoint read, while this endpoint served only the connector's in-process
    list (empty after restart, invisible across workers). The durable table is
    now the primary source; in-process entries not yet flushed by the
    fire-and-forget writer are merged in and deduplicated by entry_id.
    """
    query = select(FederationAuditRecord)
    if space_id:
        query = query.where(or_(
            FederationAuditRecord.source_space == space_id,
            FederationAuditRecord.target_space == space_id,
        ))
    if operation:
        query = query.where(FederationAuditRecord.operation == operation)
    query = query.order_by(FederationAuditRecord.timestamp.desc()).limit(limit)
    rows = (await db.execute(query)).scalars().all()
    entries = [
        _entry_view(r.entry_id, r.request_id, r.source_space, r.target_space,
                    r.operation, r.resource, r.status, r.user_id, r.timestamp)
        for r in rows
    ]
    seen = {e["entry_id"] for e in entries}

    def _aware(dt: datetime) -> str:
        # sqlite server_default yields naive datetimes; isoformat strings only
        # sort correctly across naive/aware sources when normalised.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    for e in federation_connector.get_audit_log(space_id=space_id, operation=operation, limit=limit):
        if e.entry_id in seen:
            continue
        view = _entry_view(e.entry_id, e.request_id, e.source_space, e.target_space,
                           e.operation, e.resource, e.status, e.user_id, e.timestamp)
        view["timestamp"] = _aware(e.timestamp)
        entries.append(view)
    entries.sort(key=_sort_key, reverse=True)
    return entries[:limit]


@router.get("/stats")
async def get_federation_stats(
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Get federation statistics."""
    return federation_connector.get_stats()
