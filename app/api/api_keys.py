"""SS-12 — platform API key lifecycle management (admin-only, human-only surface).

Prefix `/api/v1/api-keys` (mounted in app/main.py with the `require_human`
gate). Every endpoint requires the ADMIN role. The bare key is returned
exactly once (create/rotate); all other responses carry only the display
prefix. All admin actions are high-priority audited (SS-06 pipeline,
resource_type="api_key", resource_id=key id — the activity query path).
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_roles
from app.core.errors import CDSError
from app.core.scopes import expand_preset, validate_scope
from app.models.audit_log import AuditLog
from app.models.api_key import ApiKey
from app.models.user import User, UserRole
from app.services.audit_service import audit_service
from app.utils.crypto import sm3_hash

router = APIRouter(dependencies=[Depends(require_roles(UserRole.ADMIN))])


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    preset: Literal["readonly", "operator", "full", "custom"] | None = None
    scopes: list[str] | None = None
    expires_in_days: int = Field(90)
    rate_limit: int = Field(120, ge=1, le=1000)

    @model_validator(mode="after")
    def _validate(self):
        if self.preset is None and not self.scopes:
            raise ValueError("either preset or scopes is required")
        if self.expires_in_days not in (0, 30, 90, 180, 365):
            raise ValueError("expires_in_days must be one of 0, 30, 90, 180, 365")
        if self.preset != "custom" and self.preset is not None and self.scopes:
            raise ValueError("scopes is only accepted with preset=custom")
        return self


class ApiKeyPatch(BaseModel):
    status: Literal["active", "disabled"]


class ApiKeyCreatedResponse(BaseModel):
    key: str
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    status: str
    expires_at: datetime | None
    created_at: datetime


class ApiKeyListItem(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    key_prefix: str
    scopes: list[str]
    status: str
    rate_limit: int
    issued_by_username: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bad_request(message: str, **detail) -> CDSError:
    return CDSError(message, code="BAD_REQUEST", http_status=400, detail=detail)


def _get_key_or_404(key: ApiKey | None) -> None:
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")


def _list_item(key: ApiKey, username: str) -> ApiKeyListItem:
    return ApiKeyListItem(
        id=key.id, name=key.name, description=key.description, key_prefix=key.key_prefix,
        scopes=key.scopes or [], status=key.status, rate_limit=key.rate_limit,
        issued_by_username=username, created_at=key.created_at, expires_at=key.expires_at,
        last_used_at=key.last_used_at, revoked_at=key.revoked_at,
    )


async def _audit_action(db: AsyncSession, action: str, admin: User, key: ApiKey, extra: dict | None = None) -> None:
    await audit_service.log(
        db,
        action=action,
        resource_type="api_key",
        resource_id=str(key.id),
        user_id=admin.id,
        detail={"name": key.name, "scopes": key.scopes or [], "actor_type": "user",
                "actor_id": str(admin.id), **(extra or {})},
        actor_type="user",
        actor_id=str(admin.id),
    )


@router.post("", status_code=201, response_model=ApiKeyCreatedResponse)
async def create_api_key(
    payload: ApiKeyCreate,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    try:
        if payload.preset is not None:
            scopes = expand_preset(payload.preset, payload.scopes)
        else:
            for scope in payload.scopes:
                validate_scope(scope)
            scopes = list(payload.scopes)
    except ValueError as e:
        raise _bad_request(str(e))

    now = _now()
    expires_at = now + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None
    bare_key = ApiKey.generate_bare_key()
    key = ApiKey(
        name=payload.name,
        description=payload.description,
        key_hash=sm3_hash(bare_key.encode("utf-8")),
        key_prefix=ApiKey.display_prefix(bare_key),
        scopes=scopes,
        status="active",
        rate_limit=payload.rate_limit,
        issued_by=admin.id,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
    )
    db.add(key)
    await db.flush()
    await _audit_action(db, "api_key.created", admin, key,
                  extra={"expires_in_days": payload.expires_in_days, "rate_limit": payload.rate_limit})
    return ApiKeyCreatedResponse(
        key=bare_key, id=key.id, name=key.name, key_prefix=key.key_prefix,
        scopes=key.scopes, status=key.status, expires_at=key.expires_at, created_at=key.created_at,
    )


@router.get("", response_model=list[ApiKeyListItem])
async def list_api_keys(
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey, User.username)
        .join(User, ApiKey.issued_by == User.id)
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    )
    return [_list_item(k, username) for k, username in result.all()]


@router.get("/{key_id}/activity")
async def api_key_activity(
    key_id: uuid.UUID,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    key = await db.get(ApiKey, key_id)
    _get_key_or_404(key)
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.resource_type == "api_key", AuditLog.resource_id == str(key.id))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(50)
    )
    items = []
    for row in result.scalars().all():
        detail = row.detail or {}
        item = {
            "time": row.created_at,
            "path": detail.get("path"),
            "method": detail.get("method"),
            "status_code": detail.get("status_code"),
            "ip": row.ip_address,
        }
        if detail.get("denied_reason"):
            item["denied_reason"] = detail["denied_reason"]
        if detail.get("action_reason"):
            item["denied_reason"] = detail["action_reason"]
        items.append(item)
    return items


@router.post("/{key_id}/rotate", status_code=201)
async def rotate_api_key(
    key_id: uuid.UUID,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    old = await db.get(ApiKey, key_id)
    _get_key_or_404(old)
    now = _now()
    if old.status == "revoked":
        raise _bad_request("key is already revoked; rotation requires an active or disabled key",
                           reason="already revoked")
    # CR-2026-10-15 M2: an expired key must not come back as a credential that
    # never expires. The old code dropped the past deadline ("else None") and
    # handed out a permanent key.
    if old.is_expired(now):
        raise _bad_request("key has expired; issue a new key instead of rotating it",
                           reason="expired")

    bare_key = ApiKey.generate_bare_key()
    new = ApiKey(
        name=old.name,
        description=old.description,
        key_hash=sm3_hash(bare_key.encode("utf-8")),
        key_prefix=ApiKey.display_prefix(bare_key),
        scopes=old.scopes or [],
        status="active",
        rate_limit=old.rate_limit,
        issued_by=admin.id,
        created_at=now,
        updated_at=now,
        expires_at=old.expires_at,
    )
    db.add(new)
    old.status = "revoked"
    old.revoked_at = now
    old.revoke_reason = "rotated"
    old.updated_at = now
    await db.flush()
    await _audit_action(db, "api_key.rotated", admin, old,
                  extra={"new_id": str(new.id), "revoke_reason": "rotated"})
    return {"key": bare_key, "id": new.id}


@router.patch("/{key_id}", response_model=ApiKeyListItem)
async def update_api_key(
    key_id: uuid.UUID,
    payload: ApiKeyPatch,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    key = await db.get(ApiKey, key_id)
    _get_key_or_404(key)
    if key.status == "revoked":
        raise _bad_request("revoked is terminal; the key cannot be re-activated",
                           reason="already revoked")

    if payload.status != key.status:
        now = _now()
        key.status = payload.status
        key.updated_at = now
        await db.flush()
        action = "api_key.enabled" if payload.status == "active" else "api_key.disabled"
        await _audit_action(db, action, admin, key)

    result = await db.execute(
        select(User.username).where(User.id == key.issued_by)
    )
    username = result.scalar_one()
    return _list_item(key, username)


@router.delete("/{key_id}")
async def revoke_api_key(
    key_id: uuid.UUID,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    key = await db.get(ApiKey, key_id)
    _get_key_or_404(key)
    if key.status != "revoked":
        now = _now()
        key.status = "revoked"
        key.revoked_at = now
        key.revoke_reason = "revoked"
        key.updated_at = now
        await db.flush()
        await _audit_action(db, "api_key.revoked", admin, key)
    return {"id": key.id, "status": "revoked", "revoked_at": key.revoked_at}
