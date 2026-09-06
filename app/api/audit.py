"""Audit API — immutable audit trail with Merkle tree anchoring."""
from app.services.crypto_service import crypto_service
import uuid
import csv
import io
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog
from app.services.audit_service import audit_service
from app.services.blockchain_adapter import blockchain_adapter, ChainBackend
from app.services.merkle_service import merkle_service

router = APIRouter()

# Honest anchoring-backend disclosure (T8 / F1). The active backend may be only
# the local PG append-only tamper-evident log — callers must never mistake it
# for a real consortium-chain anchor. Every anchoring/verification response
# carries these fields so the effective backend is explicit.
_BACKEND_LABELS = {
    ChainBackend.PG_APPEND_ONLY.value: "本地防篡改追加日志（非联盟链，非区块链上链）",
    ChainBackend.FISCO_BCOS.value: "FISCO BCOS 联盟链",
    ChainBackend.ANT_CHAIN.value: "蚂蚁链 AntChain",
}
_CONSORTIUM_CHAIN_BACKENDS = {ChainBackend.FISCO_BCOS.value, ChainBackend.ANT_CHAIN.value}


def _backend_disclosure(backend: ChainBackend) -> dict:
    """Return an honest disclosure block for an anchoring backend."""
    value = backend.value
    return {
        "backend": value,
        "backend_label": _BACKEND_LABELS.get(value, value),
        "is_consortium_chain": value in _CONSORTIUM_CHAIN_BACKENDS,
    }


class AnchorRequest(BaseModel):
    record_ids: list[str]


@router.get("/records")
async def list_audit_records(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """List audit records with filtering."""
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
        count_query = count_query.where(AuditLog.resource_type == resource_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    items = []
    for log in result.scalars().all():
        items.append({
            "id": str(log.id),
            "user_id": str(log.user_id) if log.user_id else None,
            "session_id": str(log.session_id) if log.session_id else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "blockchain_tx_hash": log.blockchain_tx_hash,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/records/{record_id}")
async def get_audit_record(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Get a single audit record by ID."""
    try:
        rid = uuid.UUID(record_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid record ID")

    result = await db.execute(select(AuditLog).where(AuditLog.id == rid))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Audit record not found")

    return {
        "id": str(log.id),
        "user_id": str(log.user_id) if log.user_id else None,
        "session_id": str(log.session_id) if log.session_id else None,
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_id": log.resource_id,
        "detail": log.detail,
        "ip_address": log.ip_address,
        "blockchain_tx_hash": log.blockchain_tx_hash,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.post("/anchor")
async def anchor_audit_records(
    body: AnchorRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Anchor a batch of audit records to blockchain via Merkle tree.

    1. Fetch records by ID
    2. Compute Merkle root of their hashes
    3. Anchor Merkle root to blockchain
    4. Update records with blockchain_tx_hash
    """
    record_ids = []
    for rid_str in body.record_ids:
        try:
            record_ids.append(uuid.UUID(rid_str))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid record ID: {rid_str}")

    # Fetch records
    result = await db.execute(select(AuditLog).where(AuditLog.id.in_(record_ids)))
    records = list(result.scalars().all())

    if not records:
        raise HTTPException(status_code=404, detail="No records found")

    # Compute hashes and build Merkle tree
    record_hashes = []
    for r in records:
        data = f"{r.id}|{r.action}|{r.resource_type}|{r.resource_id}|{r.created_at}"
        record_hashes.append(crypto_service.sm3_hash(data.encode()))

    # Build Merkle tree and get root
    merkle_root = merkle_service.get_root(record_hashes)

    # Anchor to blockchain (PG append-only or configured backend)
    anchor_result = await blockchain_adapter.anchor(merkle_root.encode(), {
        "record_count": len(records),
        "anchored_by": str(current_user.id),
        "record_ids": [str(r.id) for r in records],
    })

    if anchor_result.success and anchor_result.anchor:
        tx_hash = anchor_result.anchor.tx_hash
        if tx_hash:
            for r in records:
                r.blockchain_tx_hash = tx_hash
            await db.flush()

    return {
        "anchored": len(records),
        "merkle_root": merkle_root,
        "tx_hash": anchor_result.anchor.tx_hash if anchor_result.anchor else None,
        "anchor_id": anchor_result.anchor.anchor_id if anchor_result.anchor else None,
        "confirmed": anchor_result.anchor.confirmed if anchor_result.anchor else False,
        "backend": _backend_disclosure(anchor_result.anchor.backend) if anchor_result.anchor else None,
    }


@router.get("/verify/{record_id}")
async def verify_audit_record(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Verify an audit record's blockchain attestation."""
    try:
        rid = uuid.UUID(record_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid record ID")

    result = await db.execute(select(AuditLog).where(AuditLog.id == rid))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Audit record not found")

    if not log.blockchain_tx_hash:
        return {"verified": False, "reason": "Record not anchored to blockchain"}

    # Reconstruct Merkle root from sibling records to verify
    result = await db.execute(
        select(AuditLog).where(AuditLog.blockchain_tx_hash == log.blockchain_tx_hash)
    )
    sibling_records = list(result.scalars().all())
    record_hashes = []
    for r in sibling_records:
        data = f"{r.id}|{r.action}|{r.resource_type}|{r.resource_id}|{r.created_at}"
        record_hashes.append(crypto_service.sm3_hash(data.encode()))
    merkle_root = merkle_service.get_root(record_hashes)

    # Verify via blockchain adapter (find anchor by tx_hash)
    verified = False
    anchor_backend = None
    for aid, record in blockchain_adapter._anchors.items():
        if record.tx_hash == log.blockchain_tx_hash:
            verified = await blockchain_adapter.verify(aid, merkle_root.encode())
            anchor_backend = _backend_disclosure(record.backend)
            break

    if anchor_backend and not anchor_backend["is_consortium_chain"]:
        note = "本地防篡改日志校验通过（非联盟链上链，见 backend 标注）"
    elif anchor_backend:
        note = "联盟链存证校验通过"
    else:
        note = "未找到与该 tx_hash 匹配的锚定记录"

    return {
        "verified": verified,
        "tx_hash": log.blockchain_tx_hash,
        "record_id": str(log.id),
        "backend": anchor_backend,
        "verification_note": note,
    }


@router.get("/merkle-proof/{record_id}")
async def get_merkle_proof(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Get Merkle proof for an audit record (for third-party verification)."""
    try:
        rid = uuid.UUID(record_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid record ID")

    result = await db.execute(select(AuditLog).where(AuditLog.id == rid))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Audit record not found")

    if not log.blockchain_tx_hash:
        raise HTTPException(status_code=400, detail="Record not anchored — no Merkle proof available")

    # Compute this record's hash
    data = f"{log.id}|{log.action}|{log.resource_type}|{log.resource_id}|{log.created_at}"
    record_hash = crypto_service.sm3_hash(data.encode())

    # Reconstruct Merkle tree from all records sharing the same tx_hash
    result = await db.execute(
        select(AuditLog).where(AuditLog.blockchain_tx_hash == log.blockchain_tx_hash)
    )
    sibling_records = list(result.scalars().all())

    # Build hashes in order
    all_hashes = []
    target_index = -1
    for i, r in enumerate(sibling_records):
        h = crypto_service.sm3_hash(
            f"{r.id}|{r.action}|{r.resource_type}|{r.resource_id}|{r.created_at}".encode()
        )
        all_hashes.append(h)
        if r.id == log.id:
            target_index = i

    # Generate Merkle proof
    proof = merkle_service.generate_proof(all_hashes, target_index) if target_index >= 0 else None

    return {
        "record_id": str(log.id),
        "record_hash": record_hash,
        "tx_hash": log.blockchain_tx_hash,
        "merkle_root": proof.root_hash if proof else None,
        "proof_path": proof.proof_path if proof else [],
        "leaf_index": proof.leaf_index if proof else target_index,
        "total_leaves": len(all_hashes),
        "backend": _backend_disclosure(blockchain_adapter.get_backend_type()),
    }


@router.get("/statistics")
async def get_audit_statistics(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.ADMIN)),
):
    """Get audit log statistics for the dashboard."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Total count
    total_result = await db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= since)
    )
    total = total_result.scalar() or 0

    # By action
    action_result = await db.execute(
        select(AuditLog.action, func.count())
        .where(AuditLog.created_at >= since)
        .group_by(AuditLog.action)
        .order_by(func.count().desc())
    )
    by_action = {row[0]: row[1] for row in action_result.all()}

    # By resource type
    resource_result = await db.execute(
        select(AuditLog.resource_type, func.count())
        .where(AuditLog.created_at >= since)
        .group_by(AuditLog.resource_type)
        .order_by(func.count().desc())
    )
    by_resource = {row[0]: row[1] for row in resource_result.all()}

    # Anchored vs unanchored
    anchored_result = await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.created_at >= since, AuditLog.blockchain_tx_hash.isnot(None))
    )
    anchored = anchored_result.scalar() or 0

    return {
        "period_days": days,
        "total_records": total,
        "by_action": by_action,
        "by_resource_type": by_resource,
        "anchored_count": anchored,
        "unanchored_count": total - anchored,
    }


@router.get("/export")
async def export_audit_records(
    days: int = Query(30, ge=1, le=365),
    format: str = Query("csv"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Export audit records for regulatory review."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(AuditLog).where(AuditLog.created_at >= since).order_by(AuditLog.created_at)
    )
    records = result.scalars().all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "user_id", "session_id", "action", "resource_type", "resource_id", "blockchain_tx_hash", "created_at"])
        for r in records:
            writer.writerow([
                str(r.id),
                str(r.user_id) if r.user_id else "",
                str(r.session_id) if r.session_id else "",
                r.action,
                r.resource_type,
                r.resource_id or "",
                r.blockchain_tx_hash or "",
                r.created_at.isoformat() if r.created_at else "",
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=audit_export_{days}d.csv"},
        )
    else:
        items = [
            {
                "id": str(r.id),
                "user_id": str(r.user_id) if r.user_id else None,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "blockchain_tx_hash": r.blockchain_tx_hash,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
        return {"records": items, "total": len(items), "period_days": days}


@router.get("/compliance-report")
async def get_compliance_report(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Generate a compliance report for regulators."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Key security events
    security_events = await db.execute(
        select(AuditLog.action, func.count())
        .where(
            AuditLog.created_at >= since,
            AuditLog.action.in_([
                "output_inspection_fail", "reconstruction_check_fail",
                "sandbox.terminate", "kms.revoke_key",
            ])
        )
        .group_by(AuditLog.action)
    )
    security_summary = {row[0]: row[1] for row in security_events.all()}

    # Data access count
    access_result = await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.created_at >= since, AuditLog.action.like("sandbox.%"))
    )
    sandbox_operations = access_result.scalar() or 0

    # Blockchain anchoring coverage
    total_result = await db.execute(
        select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= since)
    )
    total = total_result.scalar() or 0

    anchored_result = await db.execute(
        select(func.count()).select_from(AuditLog)
        .where(AuditLog.created_at >= since, AuditLog.blockchain_tx_hash.isnot(None))
    )
    anchored = anchored_result.scalar() or 0

    return {
        "report_type": "compliance",
        "period_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "security_events": security_summary,
        "sandbox_operations": sandbox_operations,
        "audit_coverage": {
            "total_records": total,
            "anchored_records": anchored,
            "coverage_pct": round(anchored / total * 100, 1) if total > 0 else 0,
        },
        "anchoring_backend": _backend_disclosure(blockchain_adapter.get_backend_type()),
    }
