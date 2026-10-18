"""Audit API — immutable audit trail with Merkle tree anchoring."""
from app.services.crypto_service import crypto_service
import uuid
import csv
import io
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, or_
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.user import User, UserRole
from app.models.audit_log import AuditLog
from app.models.contract import Contract
from app.services.audit_service import audit_service
from app.services.blockchain_adapter import (
    AnchorRecord,
    blockchain_adapter,
    ChainBackend,
    chain_anchoring_state,
)
from app.services.merkle_service import merkle_leaf_preimage, merkle_service

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

# 4-1 操作日志屏的「操作结果」与「操作类型页签」（SS-19 D-e2e-19）。
#
# 审计行里没有独立的「结果」列。可用的真值是项目自己的写入约定：失败/拒绝/阻断
# 路径一律写独立动作名（sandbox.create_failed、sandbox.key_distribution_denied、
# openapi.call_failed、delivery.dataset.blocked…），成功路径写本名。所以结果只能从
# 动作名派生，而且这条规则归后端持有并由 tests/test_audit_query_filters.py 机检，
# 前端不得各自猜（否则同一行在不同页面会得到不同结果）。
# 集合里的每个标记都必须能在真实动作词表里命中（tests/test_audit_query_filters.py 机检），
# 不得预留一个代码里从来没出现过的“以防万一”标记。
AUDIT_FAILURE_MARKERS = ("failed", "denied", "rejected", "blocked")

# 原型 4-1 的四个类型页签 → 真实动作集合（单一真值源：这里）。
# 集合里的名字必须是 app/ 里真正写出的 action，不得为了凑页签造名字；
# 某类暂无记录时前端如实显示 0 条，而不是拼数据。
AUDIT_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "dev_sandbox": ("notebook.created", "notebook.dev_session_created", "sandbox.create"),
    "production": ("compute.run.requested", "workload.published"),
    "export": (
        "delivery.dataset.bundled",
        "delivery.dataset.downloaded",
        "delivery.dataset.blocked",
        "delivery.dataset.review_required",
    ),
    "call": ("openapi.call", "openapi.call_failed"),
}


def audit_outcome(action: str | None) -> str:
    """Derive the per-row outcome from the action name (see AUDIT_FAILURE_MARKERS)."""
    lowered = (action or "").lower()
    return "failure" if any(marker in lowered for marker in AUDIT_FAILURE_MARKERS) else "success"


def audit_event_type(action: str | None) -> str | None:
    """Reverse-map an action to its 4-1 tab, or None when the row belongs to no tab."""
    for event_type, actions in AUDIT_EVENT_TYPES.items():
        if action in actions:
            return event_type
    return None


def audit_display_no(log: AuditLog) -> str | None:
    """原型 4-2 的「日志编号 LOG-YYYYMMDD-HHMMSS-XXXX」。

    库里没有业务编号列，也没人写一个发号器，所以这不是存储字段而是展示编码：
    由 created_at (UTC) + 记录 ID 前 4 位确定性拼出，同一行永远同一个值。
    页面上必须写清这个推导规则，不得让用户当成后端签发的业务单号。
    """
    if not log.created_at:
        return None
    stamp = log.created_at.strftime("%Y%m%d-%H%M%S")
    return f"LOG-{stamp}-{str(log.id)[:4]}"


def _as_utc(value: datetime) -> datetime:
    """Treat naive bounds as UTC — audit_rows.created_at is timestamptz."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _failure_action_condition():
    return or_(
        *[AuditLog.action.ilike(f"%{marker}%") for marker in AUDIT_FAILURE_MARKERS]
    )



def _backend_disclosure(backend: ChainBackend) -> dict:
    """Return an honest disclosure block for an anchoring backend."""
    value = backend.value
    return {
        "backend": value,
        "backend_label": _BACKEND_LABELS.get(value, value),
        "is_consortium_chain": value in _CONSORTIUM_CHAIN_BACKENDS,
        # PZ-03: derived chain-anchoring state (pg-log / fisco-sim / ant-sim /
        # not_configured) so callers can distinguish a real chain anchor from
        # the local log or an in-process simulation.
        "chain_anchoring": chain_anchoring_state(backend),
    }


# SS-17 deterministic Merkle ordering. merkle_service builds the tree from the
# *positional* order of the leaf list (build_tree pairs neighbours level by level
# and duplicates the last leaf on odd levels), so every query that feeds it must
# return rows in one fixed total order — without an ORDER BY the rebuilt root
# depends on the DB's row-return order and verification can fail on data that
# was never tampered with. The canonical order is the same one the anchoring side
# (`POST /anchor`) feeds the tree with, and it is built from the same key the
# leaf hash payload uses (`id|action|resource_type|resource_id|created_at`):
# created_at ascending with id as tiebreaker — created_at alone ties (the column
# defaults to now() at second granularity), and an unordered tie would place the
# two leaves at different positions on the two sides.
#
# NOTE on the egress tailer (app/services/cube_egress_audit._anchor_batch): it
# anchors a batch in file order and copies the upstream `ts` into created_at, so
# created_at is monotonic within a batch and this order reproduces it. If two
# upstream lines ever carry an identical ts, verify recomputes a different root
# and honestly reports a failure — it never reports a pass it did not compute.
_MERKLE_ORDER_BY = (AuditLog.created_at.asc(), AuditLog.id.asc())


async def _find_anchor_by_tx_hash(tx_hash: str) -> tuple[str | None, AnchorRecord | None]:
    """Locate the anchor record carrying ``tx_hash`` (cache re-hydrated from DB).

    Returns ``(anchor_id, record)`` or ``(None, None)`` when no anchor matches —
    callers must surface that as "no on-chain height known", never invent one.
    """
    # PZ-03: re-hydrate the in-memory anchor cache from the DB first so
    # lookups survive process restarts.
    await blockchain_adapter.hydrate_from_db()
    for aid, record in blockchain_adapter._anchors.items():
        if record.tx_hash == tx_hash:
            return aid, record
    return None, None


class AnchorRequest(BaseModel):
    record_ids: list[str]


@router.get("/records")
async def list_audit_records(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    source: str | None = Query(
        None,
        description="Audit source filter (SS-10 CB-06). E.g. 'cube_egress' matches "
        "actions 'cube_egress' and 'cube_egress.*' (network-layer forced-point audit; "
        "W13 app-layer audit rows carry no source prefix).",
    ),
    date_from: datetime | None = Query(None, description="Inclusive created_at lower bound (4-1 操作时间)"),
    date_to: datetime | None = Query(None, description="Inclusive created_at upper bound (4-1 操作时间)"),
    keyword: str | None = Query(
        None,
        max_length=128,
        description="Matches action, resource_id, or the operator's username/email (4-1 关键字)",
    ),
    outcome: str | None = Query(None, pattern="^(success|failure)$", description="Derived result (4-1 操作结果)"),
    event_type: str | None = Query(
        None,
        pattern="^(" + "|".join(AUDIT_EVENT_TYPES) + ")$",
        description="4-1 类型页签，取值见 AUDIT_EVENT_TYPES",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """List audit records with filtering."""
    query = select(
        AuditLog, User.username, User.organization, User.role,
    ).outerjoin(User, User.id == AuditLog.user_id)
    count_query = select(func.count()).select_from(AuditLog)

    if event_type:
        actions = AUDIT_EVENT_TYPES[event_type]
        query = query.where(AuditLog.action.in_(actions))
        count_query = count_query.where(AuditLog.action.in_(actions))
    if outcome:
        failure_cond = _failure_action_condition()
        cond = failure_cond if outcome == "failure" else ~failure_cond
        query = query.where(cond)
        count_query = count_query.where(cond)
    if date_from:
        query = query.where(AuditLog.created_at >= _as_utc(date_from))
        count_query = count_query.where(AuditLog.created_at >= _as_utc(date_from))
    if date_to:
        query = query.where(AuditLog.created_at <= _as_utc(date_to))
        count_query = count_query.where(AuditLog.created_at <= _as_utc(date_to))
    if keyword:
        pattern = f"%{keyword.strip()}%"
        # 关键字按图要能搜「操作对象 / 操作人」：操作人在 User 表里，所以左连一次；
        # 没有用户归属的系统行（user_id 为空）仍要能被 action/resource_id 命中。
        key_cond = or_(
            AuditLog.action.ilike(pattern),
            AuditLog.resource_id.ilike(pattern),
            User.username.ilike(pattern),
            User.email.ilike(pattern),
        )
        query = query.where(key_cond)
        count_query = count_query.outerjoin(User, User.id == AuditLog.user_id).where(key_cond)

    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
        count_query = count_query.where(AuditLog.resource_type == resource_type)
    if source:
        source_cond = or_(
            AuditLog.action == source,
            AuditLog.action.like(source + ".%"),
        )
        query = query.where(source_cond)
        count_query = count_query.where(source_cond)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    items = []
    for log, username, organization, role in result.all():
        items.append({
            "id": str(log.id),
            "user_id": str(log.user_id) if log.user_id else None,
            # 4-1 的「操作人」列：库里只存 user_id，展示名/单位/角色在这里 join 出来。
            "username": username,
            "organization": organization,
            "role": role,
            "display_no": audit_display_no(log),
            "session_id": str(log.session_id) if log.session_id else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "blockchain_tx_hash": log.blockchain_tx_hash,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            # 4-1 列与页签的派生值（后端单源，见 AUDIT_FAILURE_MARKERS 上方说明）
            "outcome": audit_outcome(log.action),
            "event_type": audit_event_type(log.action),
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

    result = await db.execute(
        select(AuditLog, User.username, User.organization, User.role)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.id == rid)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Audit record not found")
    log, username, organization, role = row

    # 4-2「关联合约」：库里只存 resource_id / detail.contract_id，名称在这里真查一次，
    # 查不到就给前端 None（页面渲染「未记录」），不拼一个看上去像真的名字。
    related_contract = None
    contract_id = log.resource_id if log.resource_type == "contract" else (log.detail or {}).get("contract_id")
    if contract_id:
        try:
            cres = await db.execute(
                select(Contract.contract_no, Contract.title).where(Contract.id == uuid.UUID(str(contract_id)))
            )
            contract_row = cres.first()
            if contract_row:
                related_contract = {"contract_no": contract_row.contract_no, "title": contract_row.title}
        except (ValueError, TypeError):
            related_contract = None

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
        "username": username,
        "organization": organization,
        "role": role,
        "display_no": audit_display_no(log),
        "outcome": audit_outcome(log.action),
        "event_type": audit_event_type(log.action),
        "related_contract": related_contract,
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

    # Fetch records in the canonical Merkle leaf order (see _MERKLE_ORDER_BY) so
    # the anchored root is reproducible by /verify and /merkle-proof.
    result = await db.execute(
        select(AuditLog).where(AuditLog.id.in_(record_ids)).order_by(*_MERKLE_ORDER_BY)
    )
    records = list(result.scalars().all())

    if not records:
        raise HTTPException(status_code=404, detail="No records found")

    # Compute hashes and build Merkle tree (leaf bytes: merkle_service helper,
    # the one place this frozen format lives).
    record_hashes = []
    for r in records:
        record_hashes.append(crypto_service.sm3_hash(merkle_leaf_preimage(r)))

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
        # Chain height only when the active backend actually reports one; the PG
        # append-only log has no block height, so this is null — never 0/fake.
        "block_number": anchor_result.anchor.block_number if anchor_result.anchor else None,
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

    # Reconstruct Merkle root from sibling records to verify — in the same
    # canonical leaf order the anchoring side used (see _MERKLE_ORDER_BY).
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.blockchain_tx_hash == log.blockchain_tx_hash)
        .order_by(*_MERKLE_ORDER_BY)
    )
    sibling_records = list(result.scalars().all())
    record_hashes = []
    for r in sibling_records:
        record_hashes.append(crypto_service.sm3_hash(merkle_leaf_preimage(r)))
    merkle_root = merkle_service.get_root(record_hashes)

    # Verify via blockchain adapter (find anchor by tx_hash).
    verified = False
    anchor_backend = None
    anchor_id, anchor_record = await _find_anchor_by_tx_hash(log.blockchain_tx_hash)
    if anchor_record is not None:
        verified = await blockchain_adapter.verify(anchor_id, merkle_root.encode())
        anchor_backend = _backend_disclosure(anchor_record.backend)

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
        "anchor_id": anchor_id,
        # Chain height + confirmation straight from the anchor record; null when
        # the backend has no height (PG append-only log) or no anchor was found.
        "block_number": anchor_record.block_number if anchor_record else None,
        "confirmed": bool(anchor_record.confirmed) if anchor_record else False,
        "merkle_root": merkle_root,
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
    record_hash = crypto_service.sm3_hash(merkle_leaf_preimage(log))

    # Reconstruct Merkle tree from all records sharing the same tx_hash, in the
    # canonical leaf order the anchoring side used (see _MERKLE_ORDER_BY).
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.blockchain_tx_hash == log.blockchain_tx_hash)
        .order_by(*_MERKLE_ORDER_BY)
    )
    sibling_records = list(result.scalars().all())

    # Build hashes in order
    all_hashes = []
    target_index = -1
    for i, r in enumerate(sibling_records):
        h = crypto_service.sm3_hash(merkle_leaf_preimage(r))
        all_hashes.append(h)
        if r.id == log.id:
            target_index = i

    # Generate Merkle proof
    proof = merkle_service.generate_proof(all_hashes, target_index) if target_index >= 0 else None

    anchor_id, anchor_record = await _find_anchor_by_tx_hash(log.blockchain_tx_hash)
    disclosure_backend = (
        anchor_record.backend if anchor_record else blockchain_adapter.get_backend_type()
    )

    return {
        "record_id": str(log.id),
        "record_hash": record_hash,
        "tx_hash": log.blockchain_tx_hash,
        "anchor_id": anchor_id,
        # Chain height only when the anchoring backend actually has one (null for
        # the PG append-only log — no block height exists there).
        "block_number": anchor_record.block_number if anchor_record else None,
        "confirmed": bool(anchor_record.confirmed) if anchor_record else False,
        "merkle_root": proof.root_hash if proof else None,
        "proof_path": proof.proof_path if proof else [],
        "leaf_index": proof.leaf_index if proof else target_index,
        "total_leaves": len(all_hashes),
        "backend": _backend_disclosure(disclosure_backend),
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
