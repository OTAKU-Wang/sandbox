"""Monitoring API — system health and statistics."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.core.config import get_settings
from app.models.user import UserRole
from app.core.cache import cache_get, cache_set
from app.models.user import User
from app.models.contract import Contract
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession
from app.models.audit_log import AuditLog
from app.models.alert import AlertRecord
from app.services.alert_center import alert_center
from app.services.tee_capability import TEECapabilityDetector

router = APIRouter()

STATS_CACHE_KEY = "monitoring:stats"
STATS_CACHE_TTL = 30  # seconds


class AlertDispositionRequest(BaseModel):
    note: str | None = None


def _capability(
    capability_id: str,
    name: str,
    status: str,
    risk_level: str,
    summary: str,
    release_gate: str,
    action: str,
    evidence: list[str] | None = None,
) -> dict:
    return {
        "id": capability_id,
        "name": name,
        "status": status,
        "risk_level": risk_level,
        "summary": summary,
        "release_gate": release_gate,
        "action": action,
        "evidence": evidence or [],
    }


def _security_posture_payload() -> dict:
    settings = get_settings()
    detector = TEECapabilityDetector()
    tee_capability = detector.detect(settings.TEE_MODE)
    hardware_hooks_configured = all([
        settings.TEE_HARDWARE_PROVISION_CMD,
        settings.TEE_HARDWARE_EXEC_CMD,
        settings.TEE_HARDWARE_ATTEST_CMD,
    ])

    capabilities: list[dict] = []

    if tee_capability.hardware_available and hardware_hooks_configured:
        tee_status = "verified" if not settings.TEE_ALLOW_SOFTWARE_FALLBACK else "configured"
        tee_risk = "ok" if not settings.TEE_ALLOW_SOFTWARE_FALLBACK else "info"
        tee_gate = "pass" if not settings.TEE_ALLOW_SOFTWARE_FALLBACK else "conditional"
        tee_summary = f"{tee_capability.provider} hardware signal detected and hardware execution hooks are configured"
        tee_action = "Use hardware attestation evidence in release notes"
    elif settings.TEE_ALLOW_SOFTWARE_FALLBACK:
        tee_status = "software"
        tee_risk = "warn"
        tee_gate = "conditional"
        tee_summary = "L1 runs as software confidential sandbox and emits software_hash evidence"
        tee_action = "Do not claim hardware TEE until hooks and vendor quote verification pass"
    else:
        tee_status = "risk"
        tee_risk = "critical"
        tee_gate = "block"
        tee_summary = f"TEE fallback disabled but hardware path is not ready: {tee_capability.reason}"
        tee_action = "Configure TEE hardware hooks or enable software fallback for non-hardware releases"
    capabilities.append(_capability(
        "tee",
        "CPU TEE / L1",
        tee_status,
        tee_risk,
        tee_summary,
        tee_gate,
        tee_action,
        [f"mode={settings.TEE_MODE}", f"provider={tee_capability.provider}", f"evidence_count={len(tee_capability.evidence)}"],
    ))

    gpu_status = "software" if settings.GPU_TEE_SIMULATION else "configured"
    capabilities.append(_capability(
        "gpu_tee",
        "GPU-TEE",
        gpu_status,
        "warn" if settings.GPU_TEE_SIMULATION else "info",
        "GPU training uses local/simulator runtime" if settings.GPU_TEE_SIMULATION else "GPU-TEE simulation disabled; hardware integration must provide runtime evidence",
        "conditional",
        "Do not claim production GPU-TEE without NVIDIA CC or equivalent attestation report",
        [f"simulation={settings.GPU_TEE_SIMULATION}"],
    ))

    vault_configured = bool(settings.VAULT_ADDR and settings.VAULT_TOKEN)
    vault_is_dev = "localhost" in settings.VAULT_ADDR or settings.VAULT_ADDR.startswith("http://vault:") or "127.0.0.1" in settings.VAULT_ADDR
    capabilities.append(_capability(
        "hsm_vault",
        "HSM / Vault",
        "configured" if vault_configured else "software",
        "warn" if (not vault_configured or vault_is_dev) else "info",
        "Vault token configured; production still requires real transit/HSM verification" if vault_configured else "Software HSM fallback is active",
        "conditional" if vault_configured else "conditional",
        "Use a production Vault/HSM endpoint and attach key operation evidence before GA",
        ["vault_addr_configured=true" if vault_configured else "vault_addr_configured=false", f"dev_like_endpoint={vault_is_dev}"],
    ))

    chain_backend = os.environ.get("CDS_BLOCKCHAIN_BACKEND") or "local_hash_chain"
    chain_external = chain_backend not in {"", "local_hash_chain", "pg_append_only"}
    capabilities.append(_capability(
        "audit_chain",
        "链存证 / 审计锚定",
        "configured" if chain_external else "software",
        "info" if chain_external else "warn",
        f"Audit anchoring backend: {chain_backend}",
        "conditional",
        "Attach real tx hash/block height evidence before claiming external chain anchoring",
        [f"backend={chain_backend}"],
    ))

    webhook_count = len(settings.ALERT_WEBHOOK_URLS)
    capabilities.append(_capability(
        "siem_alerts",
        "SIEM / Webhook 告警",
        "configured" if webhook_count else "not_configured",
        "info" if webhook_count else "warn",
        f"{webhook_count} alert webhook target(s) configured" if webhook_count else "No external alert target configured",
        "conditional",
        "Configure SIEM/Webhook targets and verify delivery before production operations",
        [f"webhook_count={webhook_count}"],
    ))

    in_cluster = bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
    kubeconfig_configured = bool(settings.SANDBOX_K8S_KUBECONFIG)
    fqdn_provider = settings.SANDBOX_K8S_FQDN_POLICY_PROVIDER.lower().strip()
    capabilities.append(_capability(
        "k8s_policy",
        "K8s 网络策略",
        "configured" if (in_cluster or kubeconfig_configured) else "software",
        "info" if fqdn_provider == "cilium" else "warn",
        "K8s control plane configured" if (in_cluster or kubeconfig_configured) else "Local runtime; K8s policy requires target cluster validation",
        "conditional",
        "Run K3s/K8s lifecycle e2e and verify NetworkPolicy/FQDN/ResourceQuota in target cluster",
        [f"in_cluster={in_cluster}", f"kubeconfig_configured={kubeconfig_configured}", f"fqdn_provider={fqdn_provider or 'none'}"],
    ))

    capabilities.append(_capability(
        "output_control",
        "输出审查",
        "verified",
        "ok",
        "Sandbox, connector, gateway and development outputs are routed through output inspection paths",
        "pass",
        "Keep release tests covering block/redact/sign/watermark behavior",
        ["dlp=true", "dp_budget=true", "watermark=true", "signature=true"],
    ))

    if settings.DEBUG:
        capabilities.append(_capability(
            "debug_mode",
            "生产 Debug",
            "risk",
            "critical",
            "CDS_DEBUG is enabled",
            "block",
            "Set CDS_DEBUG=false before release",
            ["debug=true"],
        ))

    # N11: MPC is Shamir secret custody today; HE/MPC compute is in
    # evaluation (SecretFlow HEU) — honest capability entry, never implies
    # computation that is not deployed.
    capabilities.append(_capability(
        "mpc",
        "MPC / 安全计算",
        "custody",
        "ok",
        "Shamir 秘密托管（跨重启持久化）；HE/MPC 计算处于评估阶段（N11，SecretFlow HEU/SPU 未部署）",
        "pass",
        "HE/MPC 计算启用前需通过 SecretFlow HEU 评估验收（见 spec N11 节）",
        ["mode=custody", "compute=evaluating", "backend=none"],
    ))

    gate_order = {"block": 3, "conditional": 2, "pass": 1}
    worst_gate = max(capabilities, key=lambda item: gate_order.get(item["release_gate"], 0))["release_gate"]
    recommendation = {
        "pass": "go",
        "conditional": "conditional_go",
        "block": "no_go",
    }.get(worst_gate, "conditional_go")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_recommendation": recommendation,
        "capabilities": capabilities,
    }


def _alert_record_to_item(record: AlertRecord) -> dict:
    return {
        "id": str(record.id),
        "alert_type": record.alert_type,
        "severity": record.severity,
        "status": record.status,
        "message": record.message,
        "user_id": record.user_id,
        "session_id": record.session_id,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "metadata": record.alert_metadata,
        "occurrence_count": record.occurrence_count,
        "first_seen_at": record.first_seen_at.isoformat() if record.first_seen_at else None,
        "last_seen_at": record.last_seen_at.isoformat() if record.last_seen_at else None,
        "acknowledged_by": record.acknowledged_by,
        "acknowledged_at": record.acknowledged_at.isoformat() if record.acknowledged_at else None,
        "resolved_by": record.resolved_by,
        "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
        "resolution_note": record.resolution_note,
        "notification_status": record.notification_status,
        "notification_results": record.notification_results,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


@router.get("/security-posture")
async def get_security_posture(
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Return declarative security capability posture for release and operations."""
    return _security_posture_payload()


@router.get("/stats")
async def get_monitoring_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get system monitoring statistics. Cached for 30s to reduce DB load."""
    cached = await cache_get(STATS_CACHE_KEY)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Parallel count queries via single round-trip
    active_q = select(func.count()).select_from(SandboxSession).where(SandboxSession.status == "running")
    contracts_q = select(func.count()).select_from(Contract).where(Contract.created_at >= today_start)
    products_q = select(func.count()).select_from(DataProduct)
    audit_q = select(func.count()).select_from(AuditLog)
    anchored_q = select(func.count()).select_from(AuditLog).where(AuditLog.blockchain_tx_hash.isnot(None))
    insp_total_q = select(func.count()).select_from(AuditLog).where(AuditLog.action.like("output_inspection%"))
    insp_pass_q = select(func.count()).select_from(AuditLog).where(AuditLog.action == "output_inspection_pass")

    results = await asyncio.gather(
        db.execute(active_q),
        db.execute(contracts_q),
        db.execute(products_q),
        db.execute(audit_q),
        db.execute(anchored_q),
        db.execute(insp_total_q),
        db.execute(insp_pass_q),
    )

    active_sessions = results[0].scalar() or 0
    today_contracts = results[1].scalar() or 0
    total_products = results[2].scalar() or 0
    total_audit_records = results[3].scalar() or 0
    total_anchored = results[4].scalar() or 0
    total_inspections = results[5].scalar() or 0
    passed_inspections = results[6].scalar() or 0
    pass_rate = (passed_inspections / total_inspections * 100) if total_inspections > 0 else 100.0

    result = {
        "active_sessions": active_sessions,
        "today_contracts": today_contracts,
        "total_products": total_products,
        "total_audit_records": total_audit_records,
        "total_anchored": total_anchored,
        "inspection_pass_rate": round(pass_rate, 1),
    }

    await cache_set(STATS_CACHE_KEY, result, STATS_CACHE_TTL)
    return result


@router.get("/alerts")
async def list_alerts(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    severity: str | None = Query(None),
    alert_type: str | None = Query(None),
    include_legacy: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """List persistent security alerts.

    Falls back to security audit events while a deployment is being upgraded and
    has not yet emitted AlertRecord rows.
    """
    records, total = await alert_center.list_alerts(
        db,
        status=status,
        severity=severity,
        alert_type=alert_type,
        skip=skip,
        limit=limit,
    )
    if total > 0 or not include_legacy:
        return {
            "items": [_alert_record_to_item(record) for record in records],
            "total": total,
            "page": skip // limit + 1,
            "page_size": limit,
        }

    # Security-relevant audit actions
    security_actions = [
        "output_inspection_fail",
        "unauthorized_access",
        "dp_budget_exceeded",
        "contract_violation",
        "sandbox_escape_attempt",
    ]

    query = select(AuditLog).where(AuditLog.action.in_(security_actions))
    count_query = select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(security_actions))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)

    items = []
    for log in result.scalars().all():
        items.append({
            "id": str(log.id),
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    body: AlertDispositionRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Acknowledge an alert and assign operator ownership."""
    try:
        parsed_id = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid alert_id")

    record = await alert_center.acknowledge_alert(
        db,
        parsed_id,
        str(current_user.id),
        note=body.note if body else None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _alert_record_to_item(record)


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    body: AlertDispositionRequest | None = Body(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)),
):
    """Resolve an alert with disposition notes."""
    try:
        parsed_id = uuid.UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid alert_id")

    record = await alert_center.resolve_alert(
        db,
        parsed_id,
        str(current_user.id),
        note=body.note if body else None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return _alert_record_to_item(record)


@router.get("/task-trend")
async def get_task_trend(
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get task execution trend for the last N days."""
    cache_key = f"monitoring:task_trend:{days}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Group audit events by date for task-related actions
    task_actions = [
        "sandbox.create", "sandbox.start", "sandbox.stop",
        "task.execute", "task.complete", "task.fail",
    ]

    query = (
        select(
            cast(AuditLog.created_at, Date).label("date"),
            func.count().label("count"),
        )
        .where(AuditLog.created_at >= start_date)
        .where(AuditLog.action.in_(task_actions))
        .group_by(cast(AuditLog.created_at, Date))
        .order_by(cast(AuditLog.created_at, Date))
    )

    result = await db.execute(query)
    rows = result.all()

    # Fill in missing dates with 0
    trend = []
    current = start_date.date()
    end = now.date()
    row_dict = {str(r.date): r.count for r in rows}

    while current <= end:
        date_str = current.isoformat()
        trend.append({"date": date_str, "count": row_dict.get(date_str, 0)})
        current += timedelta(days=1)

    await cache_set(cache_key, trend, 60)
    return trend


@router.get("/policy-rejection")
async def get_policy_rejection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get policy rejection rate breakdown."""
    cache_key = "monitoring:policy_rejection"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Count total and rejected queries
    total_q = select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= today_start)
    rejected_q = (
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.created_at >= today_start)
        .where(AuditLog.action.in_(["policy.deny", "output_inspection_fail", "contract_violation"]))
    )

    # Rejection by category
    category_q = (
        select(
            AuditLog.action.label("category"),
            func.count().label("count"),
        )
        .where(AuditLog.created_at >= today_start)
        .where(AuditLog.action.in_(["policy.deny", "output_inspection_fail", "contract_violation"]))
        .group_by(AuditLog.action)
    )

    total_r, rejected_r, category_r = await asyncio.gather(
        db.execute(total_q),
        db.execute(rejected_q),
        db.execute(category_q),
    )

    total = total_r.scalar() or 0
    rejected = rejected_r.scalar() or 0
    categories = [{"category": r.category, "count": r.count} for r in category_r.all()]

    result = {
        "total": total,
        "rejected": rejected,
        "pass_rate": round(((total - rejected) / total * 100) if total > 0 else 100.0, 1),
        "categories": categories,
    }

    await cache_set(cache_key, result, 60)
    return result


@router.get("/security-distribution")
async def get_security_distribution(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get security level distribution of data products."""
    cache_key = "monitoring:security_distribution"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    query = (
        select(
            DataProduct.security_level.label("level"),
            func.count().label("count"),
        )
        .group_by(DataProduct.security_level)
    )

    result = await db.execute(query)
    rows = result.all()

    # Normalize to L1/L2/L3
    distribution = []
    for r in rows:
        level = r.level or "L1"
        distribution.append({"level": level, "count": r.count})

    await cache_set(cache_key, distribution, 60)
    return distribution


@router.get("/recent-events")
async def get_recent_events(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get recent audit events for real-time event stream."""
    query = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )

    result = await db.execute(query)
    events = []

    for log in result.scalars().all():
        events.append({
            "id": str(log.id),
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "user_id": str(log.user_id) if log.user_id else None,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return events
