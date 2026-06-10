"""KMS API — DEK lifecycle management."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.models.kms import DataEncryptionKey, DEKStatus, KeyAuditLog
from app.schemas.high_risk_operation import HighRiskOperationRequest
from app.services.kms_service import kms_service
from app.services.audit_service import audit_service

router = APIRouter()


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_dek(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new Data Encryption Key for a product."""
    # Generate DEK
    result = kms_service.generate_data_key(str(product_id))
    key_id = result["key_id"]

    # Store DEK metadata
    dek = DataEncryptionKey(
        key_id=key_id,
        product_id=product_id,
        encrypted_key=result["key_bytes"].hex(),  # In production: encrypted by KEK
        status=DEKStatus.ACTIVE.value,
    )
    db.add(dek)

    # Audit
    audit = KeyAuditLog(
        key_id=key_id,
        operation="create",
        user_id=current_user.id,
        detail={"product_id": str(product_id)},
    )
    db.add(audit)

    await db.flush()
    await db.refresh(dek)

    await audit_service.log(
        db, action="kms.create_key", resource_type="key",
        user_id=current_user.id, resource_id=key_id,
        detail={"product_id": str(product_id)},
    )

    return {"key_id": dek.key_id, "status": dek.status, "algorithm": dek.algorithm}


@router.get("/keys")
async def list_deks(
    product_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List DEKs, optionally filtered by product or status."""
    query = select(DataEncryptionKey)
    if product_id:
        query = query.where(DataEncryptionKey.product_id == product_id)
    if status_filter:
        query = query.where(DataEncryptionKey.status == status_filter)

    result = await db.execute(query)
    keys = result.scalars().all()
    return {
        "items": [
            {"key_id": k.key_id, "product_id": str(k.product_id), "status": k.status,
             "key_version": k.key_version, "usage_count": k.usage_count, "max_usage": k.max_usage}
            for k in keys
        ]
    }


@router.get("/keys/{key_id}")
async def get_dek(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get DEK details (metadata only, not key material)."""
    result = await db.execute(select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id))
    dek = result.scalar_one_or_none()
    if not dek:
        raise HTTPException(status_code=404, detail="Key not found")
    return {
        "key_id": dek.key_id, "product_id": str(dek.product_id), "status": dek.status,
        "key_version": dek.key_version, "algorithm": dek.algorithm,
        "usage_count": dek.usage_count, "max_usage": dek.max_usage,
        "created_at": dek.created_at.isoformat(),
    }


@router.post("/keys/{key_id}/rotate")
async def rotate_dek(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rotate a DEK — create new version and mark old as rotated."""
    result = await db.execute(select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id))
    dek = result.scalar_one_or_none()
    if not dek:
        raise HTTPException(status_code=404, detail="Key not found")
    if dek.status != DEKStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail=f"Cannot rotate key in {dek.status} status")

    # Generate new DEK
    new_result = kms_service.generate_data_key(str(dek.product_id))
    new_key_id = new_result["key_id"]

    # Create new version
    new_dek = DataEncryptionKey(
        key_id=new_key_id,
        product_id=dek.product_id,
        key_version=dek.key_version + 1,
        encrypted_key=new_result["key_bytes"].hex(),
        status=DEKStatus.ACTIVE.value,
    )
    db.add(new_dek)

    # Mark old as rotated
    dek.status = DEKStatus.ROTATED.value
    dek.rotated_to = new_key_id

    # Audit
    audit = KeyAuditLog(
        key_id=key_id,
        operation="rotate",
        user_id=current_user.id,
        detail={"new_key_id": new_key_id},
    )
    db.add(audit)

    await db.flush()

    return {"old_key_id": key_id, "new_key_id": new_key_id, "status": "rotated"}


@router.delete("/keys/{key_id}")
async def revoke_dek(
    key_id: str,
    body: HighRiskOperationRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke a DEK — marks as revoked, terminates active sessions, data becomes inaccessible."""
    result = await db.execute(select(DataEncryptionKey).where(DataEncryptionKey.key_id == key_id))
    dek = result.scalar_one_or_none()
    if not dek:
        raise HTTPException(status_code=404, detail="Key not found")
    if dek.status in (DEKStatus.REVOKED.value, DEKStatus.DESTROYED.value):
        raise HTTPException(status_code=400, detail=f"Key already {dek.status}")

    dek.status = DEKStatus.REVOKED.value
    dek.revoked_at = datetime.now(timezone.utc)

    # Terminate all active sessions using this key
    from app.services.sandbox_runtime import sandbox_runtime
    terminated_count = await sandbox_runtime.terminate_sessions_by_key(
        key_id, db, reason="key_revoked"
    )

    # Audit
    audit = KeyAuditLog(
        key_id=key_id,
        operation="revoke",
        user_id=current_user.id,
        detail={"reason": body.reason, "ticket_id": body.ticket_id},
    )
    db.add(audit)

    await db.flush()

    await audit_service.log(
        db, action="kms.revoke_key", resource_type="key",
        user_id=current_user.id, resource_id=key_id,
        detail={"terminated_sessions": terminated_count, "reason": body.reason, "ticket_id": body.ticket_id},
    )

    return {"key_id": key_id, "status": "revoked", "terminated_sessions": terminated_count}


@router.get("/audit")
async def get_key_audit(
    key_id: str | None = None,
    operation: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get key audit logs."""
    query = select(KeyAuditLog)
    if key_id:
        query = query.where(KeyAuditLog.key_id == key_id)
    if operation:
        query = query.where(KeyAuditLog.operation == operation)
    query = query.order_by(KeyAuditLog.created_at.desc()).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()
    return {
        "items": [
            {"id": str(l.id), "key_id": l.key_id, "operation": l.operation,
             "user_id": str(l.user_id) if l.user_id else None,
             "detail": l.detail, "created_at": l.created_at.isoformat()}
            for l in logs
        ]
    }
