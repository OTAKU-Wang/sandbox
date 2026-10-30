"""MPC Key Sharing API — Shamir's Secret Sharing for secure key distribution.

Persistence: keys and shares are stored in the DB (spec N3) — split returns
persisted shares, reconstruct/verify/rotate/destroy read from the DB. API
behaviour is unchanged from the in-memory implementation; every write path
reports ``persisted: true``.

Honest boundary (N11): this service is Shamir secret ESCROW, not multi-party
computation. HE/MPC compute (SecretFlow HEU/SPU) is in evaluation — see the
``/capabilities`` endpoint and spec N11 for the current (not-deployed) state.
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


def _mpc_capabilities() -> dict:
    """Honest HE/MPC state disclosure (N11 evaluation, not deployed).

    Mirrors the audit._backend_disclosure pattern: reports exactly what this
    service is (Shamir secret custody) and what it is NOT (no MPC compute),
    plus the SecretFlow HEU evaluation status. Fail-closed — never implies
    computation that is not running.
    """
    return {
        "mode": "custody",
        "label": "Shamir 秘密托管（非 MPC 计算协议）",
        "compute": "evaluating",
        "backend": None,
        "backend_label": "SecretFlow HEU/SPU 评估中（N11）；当前运行时未部署",
        "note": (
            "评估结论：SecretFlow/SPU/HEU 均为 Apache-2.0；HEU 独立库（sf-heu）可作为 "
            "mpc_service 的计算内核（PHE sum/mean/var）；当前运行时 Python 3.14 无 cp314 "
            "wheel 且依赖未安装——计算能力保持 fail-closed（详见 spec N11 节）。"
        ),
    }


@router.get("/capabilities")
async def mpc_capabilities(
    current_user: User = Depends(get_current_user),
):
    """Report the honest MPC capability state (custody vs compute)."""
    return _mpc_capabilities()


class SplitKeyRequest(BaseModel):
    secret_hex: str = Field(..., min_length=2, max_length=128, pattern="^[0-9a-fA-F]+$")
    threshold: int = Field(..., ge=2, le=20)
    total_shares: int = Field(..., ge=2, le=50)
    algorithm: str = Field(default="sm4", pattern="^(sm4|aes256)$")
    holders: list[str] | None = None


class ReconstructRequest(BaseModel):
    key_id: str
    share_ids: list[str] = Field(..., min_length=2)


class KeyDestructionTrailRequest(BaseModel):
    """MAJOR-3 (cr-2026-10-15 §4): 轮换/销毁 = 高危操作, 审计留痕必须能回答
    "谁授权做的"。契约形状参考 PZ-09/PZ-10 角色 PATCH 的 reason/ticket_id
    (app/schemas/high_risk_operation.py), 但两字段对本端点为**可选** ——
    既有调用方(含前端旧版)不带 body 的 POST 必须继续 200, 只是旧调用不再
    看起来像"已留痕": 缺失时审计 detail 会显式标 reason_missing/
    ticket_id_missing, 高危留痕不再假象。
    """

    reason: str | None = Field(default=None, min_length=8, max_length=500)
    ticket_id: str | None = Field(default=None, max_length=128)


def _trail_detail(detail: dict, body: KeyDestructionTrailRequest | None) -> dict:
    """Merge the optional reason/ticket_id into the audit detail (MAJOR-3).

    Absent values are recorded as explicit ``*_missing`` flags rather than
    silently dropped — the audit row then states what evidence it lacks.
    """
    if body is not None and body.reason:
        detail["reason"] = body.reason
    else:
        detail["reason_missing"] = True
    if body is not None and body.ticket_id:
        detail["ticket_id"] = body.ticket_id
    else:
        detail["ticket_id_missing"] = True
    return detail


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
        "mpc_capabilities": _mpc_capabilities(),
    }


@router.get("/keys")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    # 密钥清单是全局托管台账：mpc_keys 没有归属列（见 app/models/mpc_key.py），
    # 无法按属主过滤，只能按角色收口 —— 与同 router 的 split/reconstruct 同组
    # （ADMIN/OPERATOR），不放行 data_provider/buyer/regulator。
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """List all managed MPC keys (ops roles only — no per-owner column exists)."""
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
    # 与 GET /keys 同一数据（清单里的一行）：按 id 取详情不得成为清单门禁的绕路。
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Get key details (without shares) — ops roles only, same as GET /keys."""
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
    # 本端点返回 share_value 明文：拿到 threshold 份即可 Lagrange 重构主密钥，
    # 等同导出密钥本身。因此收口到本 router 最严的 ADMIN 门禁（同 rotate/destroy），
    # 任何已认证用户都不得批量读取他方密钥的分片。
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """List shares for a key — ADMIN only (share values reconstruct the secret)."""
    shares = await mpc_service.list_shares(db, key_id)
    if not shares:
        raise HTTPException(status_code=404, detail="Key not found or no shares")
    # 读侧留痕：本响应等于把密钥材料交付出去（跟 rotate/destroy 同一敏感级），
    # 没有这条审计记录就无法回答“谁在什么时候取走了哪个密钥的分片”。
    await audit_service.log(
        db, action="mpc.key.read_shares", resource_type="mpc_key",
        user_id=current_user.id, resource_id=key_id,
        detail={"share_count": len(shares), "threshold": shares[0].threshold},
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
        for s in shares
    ]


@router.post("/keys/{key_id}/rotate")
async def rotate_key(
    key_id: str,
    body: KeyDestructionTrailRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Rotate a key — generate new shares.

    MAJOR-3: optional ``reason``/``ticket_id`` body (PZ-09 role-PATCH shape,
    both optional here for wire compatibility) lands in the audit detail.
    """
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
        detail=_trail_detail({"new_key_id": new_key.key_id}, body),
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
    body: KeyDestructionTrailRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Destroy a key — mark destroyed and crypto-erase all shares.

    MAJOR-3: optional ``reason``/``ticket_id`` body (PZ-09 role-PATCH shape,
    both optional here for wire compatibility) lands in the audit detail.
    """
    destroyed = await mpc_service.destroy_key(db, key_id)
    if not destroyed:
        raise HTTPException(status_code=404, detail="Key not found")

    await audit_service.log(
        db, action="mpc.key.destroy", resource_type="mpc_key",
        user_id=current_user.id, resource_id=key_id,
        detail=_trail_detail({"persisted": True}, body),
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
    return {
        "key_id": key_id,
        "valid": valid,
        "shares_provided": len(share_ids),
        "mpc_capabilities": _mpc_capabilities(),
    }
