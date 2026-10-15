"""KMS API — DEK lifecycle management."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user, verify_service_token
from app.models.user import User
from app.models.kms import DataEncryptionKey, DEKStatus, KeyAuditLog
from app.models.volume_key import VolumeKey, VolumeKeyStatus
from app.schemas.high_risk_operation import HighRiskOperationRequest
from app.schemas.kms import (
    VolumeKeyDistributeRequest,
    VolumeKeyDistributeResponse,
    VolumeKeyRegisterRequest,
    VolumeKeyRegisterResponse,
)
from app.services.kms_service import kms_service
from app.services.audit_service import audit_service

router = APIRouter()

logger = logging.getLogger(__name__)


def _volume_cipher_label(declared: str | None) -> str:
    """Normalize the cipher the volume plugin says it sealed the volume with.

    The plugin owns the cipher choice (`CDS_SM4_AEAD_BACKEND` defaults to the
    AES fallback), so the persisted compliance record must carry what the
    plugin declared — never a hardcoded SM4-GCM claim for a volume that was
    sealed with AES. Undeclared is recorded as `unknown`, not as GM crypto.
    """
    if not declared:
        return "unknown"
    d = declared.lower()
    if "sm4" in d:
        return "SM4-GCM"
    if "aes-128" in d or "aes128" in d:
        return "AES-128-GCM"
    if "aes" in d:
        return "AES-GCM"
    return declared[:32]


def _wrapped_key_hex(key_id: str) -> str | None:
    """Return the KEK-wrapped DEK blob as hex for persistence.

    The wrapped blob is useless without the KEK held in HSM/Vault — persisting
    it (instead of the plaintext key hex) is the envelope-encryption-safe
    representation. Fixes the previous behaviour where ``encrypted_key``
    stored the raw DEK plaintext at rest.
    """
    wrapped = kms_service.export_wrapped(key_id)
    return wrapped.hex() if wrapped is not None else None


@router.post("/keys", status_code=status.HTTP_201_CREATED)
async def create_dek(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new Data Encryption Key for a product."""
    # Generate DEK (wrapped with KEK inside kms_service)
    result = kms_service.generate_data_key(str(product_id))
    key_id = result["key_id"]

    # Store DEK metadata — persist the KEK-wrapped blob, never the plaintext key
    dek = DataEncryptionKey(
        key_id=key_id,
        product_id=product_id,
        encrypted_key=_wrapped_key_hex(key_id),
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
        encrypted_key=_wrapped_key_hex(new_key_id),
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


# ── Volume-key endpoints (SS-10 CB-03 / CB-01) ─────────────────────────────────
# Machine-facing (Go volume plugin cds-volume-plugin/kms.go): service-token
# auth, NOT user JWT. Wire shapes are fixed by the Go client — see
# app/schemas/kms.py. Do not rename any request/response field.
#
#   POST   /volume-keys                   {volume_id, name, dek_hex} -> {key_id}
#   POST   /volume-keys/{ref}/distribute  {session_id}              -> {key_hex}
#   DELETE /volume-keys/{ref}                                -> 204 (idempotent)
# The DEK is generated by the trusted plugin and delivered as hex; the control
# plane wraps it with the master KEK and persists ONLY the wrapped blob. Raw DEK
# material is never persisted and never returned at registration.


@router.post("/volume-keys", response_model=VolumeKeyRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_volume_key(
    body: VolumeKeyRegisterRequest,
    db: AsyncSession = Depends(get_db),
    _svc: None = Depends(verify_service_token),
):
    """Register a plugin-generated volume DEK; return the wrapped-key ref.

    Wraps the provided DEK with the master KEK and stores only the wrapped
    blob — the raw DEK is never persisted or returned to the plugin here.
    """
    try:
        dek = bytes.fromhex(body.dek_hex)
    except ValueError:
        raise HTTPException(status_code=400, detail="dek_hex must be valid hex")
    settings = get_settings()
    if len(dek) != settings.CUBE_DEK_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"DEK must be {settings.CUBE_DEK_LENGTH} bytes, got {len(dek)}",
        )
    wrapped = kms_service.wrap_dek_bytes(dek)
    key_id = f"vkey-{uuid.uuid4().hex}"
    algorithm = _volume_cipher_label(body.cipher)
    if algorithm != "SM4-GCM":
        logger.warning(
            "[KMS] volume key %s (volume %s) registered with %s, not SM4-GCM — at-rest "
            "encryption for this volume is NOT GM/T compliant (plugin backend %r)",
            key_id, body.volume_id, algorithm, body.cipher,
        )
    db.add(VolumeKey(
        key_id=key_id,
        volume_id=body.volume_id,
        name=body.name or body.volume_id,
        algorithm=algorithm,
        key_length=len(dek),
        wrapped_key=wrapped,
        status=VolumeKeyStatus.ACTIVE.value,
    ))
    db.add(KeyAuditLog(
        key_id=key_id,
        operation="create",
        user_id=None,
        detail={"volume_id": body.volume_id, "name": body.name, "algorithm": algorithm},
    ))
    await db.flush()
    await audit_service.log(
        db, action="kms.volume_key.create", resource_type="volume_key",
        resource_id=key_id,
        detail={"volume_id": body.volume_id, "name": body.name, "key_length": len(dek),
                "algorithm": algorithm, "declared_cipher": body.cipher},
    )
    # SS-14 D-e2e-14 同族 (2026-10-14): 显式 commit 后再响应 —— 插件拿到 key_id
    # 后 CDS provision 立即按 volume_id 回查 VolumeKey 行, 201 先于 commit
    # 可见会查到空行 (fail-closed 拒绝会话创建)。
    await db.commit()
    return VolumeKeyRegisterResponse(key_id=key_id)


@router.post("/volume-keys/{ref}/distribute", response_model=VolumeKeyDistributeResponse)
async def distribute_volume_key(
    ref: str,
    body: VolumeKeyDistributeRequest,
    db: AsyncSession = Depends(get_db),
    _svc: None = Depends(verify_service_token),
):
    """Distribute (unwrap) an active volume DEK to a session-scoped attach.

    The only route that releases key material, and only to an authenticated
    service caller that holds the plugin-issued ref (session-scoped per spec
    CB-03 step 4). The Go client hex-decodes key_hex and length-checks it.
    """
    result = await db.execute(select(VolumeKey).where(VolumeKey.key_id == ref))
    vk = result.scalar_one_or_none()
    if vk is None or vk.status != VolumeKeyStatus.ACTIVE.value or vk.wrapped_key is None:
        raise HTTPException(status_code=404, detail="Volume key not found or not active")
    dek = kms_service.unwrap_wrapped(vk.wrapped_key)
    if dek is None:
        raise HTTPException(status_code=500, detail="Failed to unwrap volume key (fail-closed)")
    db.add(KeyAuditLog(
        key_id=ref,
        operation="distribute",
        user_id=None,
        session_id=body.session_id,
        detail={"volume_id": vk.volume_id},
    ))
    await db.flush()
    await audit_service.log(
        db, action="kms.volume_key.distribute", resource_type="volume_key",
        resource_id=ref,
        detail={"volume_id": vk.volume_id, "session_id": body.session_id},
    )
    return VolumeKeyDistributeResponse(key_hex=dek.hex())


@router.delete("/volume-keys/{ref}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_volume_key(
    ref: str,
    db: AsyncSession = Depends(get_db),
    _svc: None = Depends(verify_service_token),
):
    """Revoke a volume DEK. Idempotent: an unknown or already-revoked ref is a
    204 success, never a 404 — the plugin retries revoke and retains on-disk
    data on failure, so a 404 would be misread as a hard error.
    """
    result = await db.execute(select(VolumeKey).where(VolumeKey.key_id == ref))
    vk = result.scalar_one_or_none()
    if vk is not None and vk.status not in (VolumeKeyStatus.REVOKED.value, VolumeKeyStatus.DESTROYED.value):
        vk.status = VolumeKeyStatus.REVOKED.value
        vk.revoked_at = datetime.now(timezone.utc)
        vk.wrapped_key = None  # crypto-erase the persisted wrapped blob
        db.add(KeyAuditLog(
            key_id=ref,
            operation="revoke",
            user_id=None,
            detail={"volume_id": vk.volume_id},
        ))
        await audit_service.log(
            db, action="kms.volume_key.revoke", resource_type="volume_key",
            resource_id=ref, detail={"volume_id": vk.volume_id},
        )
    # Unknown ref or already revoked/destroyed: no-op success (idempotent).
    return Response(status_code=status.HTTP_204_NO_CONTENT)
