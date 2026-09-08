"""MPC Key Sharing API — Shamir's Secret Sharing for secure key distribution.

Persistence: keys and shares are stored in the DB (spec N3) — split returns
persisted shares, reconstruct/verify/rotate/destroy read from the DB. API
behaviour is unchanged from the in-memory implementation; every write path
reports ``persisted: true``.
"""
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.services.mpc_service import mpc_service
from app.services.audit_service import audit_service

router = APIRouter()


class SplitKeyRequest(BaseModel):
    secret_hex: str = Field(..., min_length=2, max_length=128, pattern="^[0-9a-fA-F]+$")
    threshold: int = Field(..., ge=2, le=20)
    total_shares: int = Field(..., ge=2, le=50)
    algorithm: str = Field(default="sm4", pattern="^(sm4|aes256)$")
    holders: list[str] | None = None


class ReconstructRequest(BaseModel):
    key_id: str
    share_ids: list[str] = Field(..., min_length=2)


class ShareResponse(BaseModel):
    share_id: str
    key_id: str
    index: int
    share_value: str
    threshold: int
    total_shares: int
    holder: str | None = None


class KeyResponse(BaseModel):
    key_id: str
    algorithm: str
    threshold: int
    total_shares: int
    share_count: int
    status: str
    persisted: bool = True


@router.post("/split", response_model=list[ShareResponse])
async def split_key(
    body: SplitKeyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Split a secret key into shares using Shamir's Secret Sharing."""
    try:
        secret = bytes.fromhex(body.secret_hex)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex string")

    try:
        mpc_key = await mpc_service.split_key(
            db,
            secret=secret,
            threshold=body.threshold,
            total_shares=body.total_shares,
            algorithm=body.algorithm,
            holders=body.holders,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="mpc.key.split", resource_type="mpc_key",
        user_id=current_user.id, resource_id=mpc_key.key_id,
        detail={"threshold": body.threshold, "total_shares": body.total_shares, "persisted": True},
    )

    return [
        ShareResponse(
            share_id=s.share_id,
            key_id=s.key_id,
            index=s.index,
            share_value=s.share_value,
            threshold=s.threshold,
            total_shares=s.total_shares,
            holder=s.holder,
        )
        for s in mpc_key.shares
    ]


@router.post("/reconstruct")
async def reconstruct_key(
    body: ReconstructRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Reconstruct a secret from shares."""
    try:
        secret = await mpc_service.reconstruct_key(db, body.key_id, body.share_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="mpc.key.reconstruct", resource_type="mpc_key",
        user_id=current_user.id, resource_id=body.key_id,
        detail={"share_count": len(body.share_ids)},
    )

    return {
        "key_id": body.key_id,
        "secret_hex": secret.hex(),
        "shares_used": len(body.share_ids),
    }


@router.get("/keys")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all managed MPC keys."""
    keys = await mpc_service.list_keys(db)
    return [
        KeyResponse(
            key_id=k.key_id,
            algorithm=k.algorithm,
            threshold=k.threshold,
            total_shares=k.total_shares,
            share_count=len(k.shares),
            status=k.status,
        )
        for k in keys
    ]


@router.get("/keys/{key_id}")
async def get_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get key details (without shares)."""
    mpc_key = await mpc_service.get_key(db, key_id)
    if not mpc_key:
        raise HTTPException(status_code=404, detail="Key not found")
    return KeyResponse(
        key_id=mpc_key.key_id,
        algorithm=mpc_key.algorithm,
        threshold=mpc_key.threshold,
        total_shares=mpc_key.total_shares,
        share_count=len(mpc_key.shares),
        status=mpc_key.status,
    )


@router.get("/keys/{key_id}/shares")
async def list_shares(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List shares for a key."""
    shares = await mpc_service.list_shares(db, key_id)
    if not shares:
        raise HTTPException(status_code=404, detail="Key not found or no shares")
    return [
        ShareResponse(
            share_id=s.share_id,
            key_id=s.key_id,
            index=s.index,
            share_value=s.share_value,
            threshold=s.threshold,
            total_shares=s.total_shares,
            holder=s.holder,
        )
        for s in shares
    ]


@router.post("/keys/{key_id}/rotate")
async def rotate_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Rotate a key — generate new shares."""
    old_key = await mpc_service.get_key(db, key_id)
    if not old_key:
        raise HTTPException(status_code=404, detail="Key not found")

    try:
        new_key = await mpc_service.rotate_key(db, key_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="mpc.key.rotate", resource_type="mpc_key",
        user_id=current_user.id, resource_id=key_id,
        detail={"new_key_id": new_key.key_id},
    )

    return KeyResponse(
        key_id=new_key.key_id,
        algorithm=new_key.algorithm,
        threshold=new_key.threshold,
        total_shares=new_key.total_shares,
        share_count=len(new_key.shares),
        status=new_key.status,
    )


@router.post("/keys/{key_id}/destroy")
async def destroy_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Destroy a key — mark destroyed and crypto-erase all shares."""
    destroyed = await mpc_service.destroy_key(db, key_id)
    if not destroyed:
        raise HTTPException(status_code=404, detail="Key not found")

    await audit_service.log(
        db, action="mpc.key.destroy", resource_type="mpc_key",
        user_id=current_user.id, resource_id=key_id,
        detail={"persisted": True},
    )

    return {"key_id": key_id, "destroyed": True, "persisted": True}


@router.post("/verify")
async def verify_shares(
    key_id: str,
    share_ids: list[str],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Verify that shares are valid for a key."""
    valid = await mpc_service.verify_shares(db, key_id, share_ids)
    return {"key_id": key_id, "valid": valid, "shares_provided": len(share_ids)}
