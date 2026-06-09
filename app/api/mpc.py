"""MPC Key Sharing API — Shamir's Secret Sharing for secure key distribution."""
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
        mpc_key = mpc_service.split_key(
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
        detail={"threshold": body.threshold, "total_shares": body.total_shares},
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
        secret = mpc_service.reconstruct_key(body.key_id, body.share_ids)
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
    current_user: User = Depends(get_current_user),
):
    """List all managed MPC keys."""
    keys = mpc_service.list_keys()
    return [
        KeyResponse(
            key_id=k.key_id,
            algorithm=k.algorithm,
            threshold=k.threshold,
            total_shares=k.total_shares,
            share_count=len(k.shares),
        )
        for k in keys
    ]


@router.get("/keys/{key_id}")
async def get_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get key details (without shares)."""
    mpc_key = mpc_service.get_key(key_id)
    if not mpc_key:
        raise HTTPException(status_code=404, detail="Key not found")
    return KeyResponse(
        key_id=mpc_key.key_id,
        algorithm=mpc_key.algorithm,
        threshold=mpc_key.threshold,
        total_shares=mpc_key.total_shares,
        share_count=len(mpc_key.shares),
    )


@router.get("/keys/{key_id}/shares")
async def list_shares(
    key_id: str,
    current_user: User = Depends(get_current_user),
):
    """List shares for a key."""
    shares = mpc_service.list_shares(key_id)
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
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Rotate a key — generate new shares."""
    old_key = mpc_service.get_key(key_id)
    if not old_key:
        raise HTTPException(status_code=404, detail="Key not found")

    new_key = mpc_service.rotate_key(key_id)
    return KeyResponse(
        key_id=new_key.key_id,
        algorithm=new_key.algorithm,
        threshold=new_key.threshold,
        total_shares=new_key.total_shares,
        share_count=len(new_key.shares),
    )


@router.post("/verify")
async def verify_shares(
    key_id: str,
    share_ids: list[str],
    current_user: User = Depends(get_current_user),
):
    """Verify that shares are valid for a key."""
    valid = mpc_service.verify_shares(key_id, share_ids)
    return {"key_id": key_id, "valid": valid, "shares_provided": len(share_ids)}
