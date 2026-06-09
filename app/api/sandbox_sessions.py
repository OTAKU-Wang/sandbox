import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus, SandboxLevel
from app.schemas.sandbox_session import SandboxSessionCreate, SandboxSessionResponse
from app.services.audit_service import audit_service
from app.services.kms_service import kms_service
from app.services.sandbox_manager import validate_resource_limits, is_session_expired, get_sandbox_manager, check_tenant_quota, update_tenant_usage, release_tenant_usage, get_resource_limits
from app.services.session_state_machine import session_state_machine
from app.models.kms import KeyMetadata, KeyType, KeyStatus
from app.models.network_policy import NetworkPolicy
from app.services.network_policy import network_policy_engine, NetworkPolicyConfig
from app.api.sandbox_db import get_encryption_config_for_session
from app.services.sandbox_audit import get_sandbox_audit_collector
from app.services.quota_manager import quota_manager, QuotaType
import tempfile
from sqlalchemy import func

router = APIRouter()


def _can_view_all_sessions(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)


def _can_operate_sessions(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.ADMIN)


@router.post("", response_model=SandboxSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_sandbox_session(
    body: SandboxSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify data product exists
    result = await db.execute(select(DataProduct).where(DataProduct.id == body.data_product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.status != "published":
        raise HTTPException(status_code=400, detail="Data product is not published")

    # Per-user concurrency limit: max 5 active sessions per user
    MAX_ACTIVE_SESSIONS = 5
    active_count_result = await db.execute(
        select(func.count()).where(
            SandboxSession.user_id == current_user.id,
            SandboxSession.status.in_([
                SessionStatus.PROVISIONING.value,
                SessionStatus.RUNNING.value,
                SessionStatus.READY.value,
            ]),
        )
    )
    active_count = active_count_result.scalar() or 0
    if active_count >= MAX_ACTIVE_SESSIONS:
        raise HTTPException(
            status_code=429,
            detail=f"并发会话数已达上限 ({MAX_ACTIVE_SESSIONS})，请先终止已有会话",
        )

    # Validate and enforce resource limits
    effective_limits = validate_resource_limits(body.sandbox_level, body.resource_limits)

    # Validate state transition: PENDING → PROVISIONING
    session_state_machine.require_transition(SessionStatus.PENDING, SessionStatus.PROVISIONING)

    session = SandboxSession(
        user_id=current_user.id,
        data_product_id=body.data_product_id,
        sandbox_level=body.sandbox_level,
        contract_id=body.contract_id,
        timeout_seconds=body.timeout_seconds,
        resource_limits=effective_limits,
        status=SessionStatus.PROVISIONING.value,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    # Initialize session quota counters (P1-4)
    await quota_manager.reset(session.id)

    # Pre-fill quota limits from contract
    from app.models.contract import Contract
    if body.contract_id:
        contract = await db.get(Contract, body.contract_id)
        if contract:
            await quota_manager.set_limits(session.id, {
                QuotaType.ROWS: contract.max_output_rows or 10000,
                QuotaType.BYTES: 100 * 1024 * 1024,  # 100MB default
                QuotaType.API_CALLS: 1000,
                QuotaType.GPU_SECONDS: int((contract.max_duration_hours or 24) * 3600),
            })

    # Check tenant resource quota
    limits = get_resource_limits(body.sandbox_level)
    allowed, reason = check_tenant_quota(
        str(current_user.id),
        cpu_cores=limits["cpu_cores"],
        memory_mb=limits["memory_mb"],
        disk_mb=limits["disk_mb"],
    )
    if not allowed:
        raise HTTPException(status_code=429, detail=f"资源配额不足: {reason}")

    # Provision sandbox container
    runtime = await get_sandbox_manager()
    provision_result = runtime.provision(
        session_id=session.id,
        level=body.sandbox_level,
        data_path="",
        timeout=body.timeout_seconds,
        user_id=str(current_user.id),
    )
    session.container_id = provision_result.get("container_id")
    session.status = provision_result.get("status", SessionStatus.RUNNING.value)
    if provision_result.get("error"):
        session.status = SessionStatus.FAILED.value
        session.error_message = provision_result["error"]
    else:
        # Track tenant resource usage
        update_tenant_usage(
            str(current_user.id),
            cpu_cores=limits["cpu_cores"],
            memory_mb=limits["memory_mb"],
            disk_mb=limits["disk_mb"],
        )

    # Generate session key via KMS
    session_key_result = kms_service.generate_session_key(str(session.id))
    session.session_key_id = session_key_result["key_id"]

    # Distribute key into sandbox workspace
    if session.container_id:
        kms_service.distribute_key(session_key_result["key_id"], str(session.id))

    # Record key metadata
    key_meta = KeyMetadata(
        key_id=session_key_result["key_id"],
        key_type=KeyType.SESSION.value,
        status=KeyStatus.ACTIVE.value,
        session_id=session.id,
        product_id=body.data_product_id,
    )
    db.add(key_meta)

    await db.flush()
    await db.refresh(session)

    # Auto-create default deny_all network policy (zero-trust)
    net_policy = NetworkPolicy(
        session_id=str(session.id),
        user_id=str(current_user.id),
        mode="deny_all",
        active=True,
    )
    db.add(net_policy)

    # Enforce network policy via engine
    policy_config = NetworkPolicyConfig(mode="deny_all")
    await network_policy_engine.create_policy(str(session.id), policy_config)

    await audit_service.log(
        db, action="sandbox.create", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"sandbox_level": body.sandbox_level, "data_product_id": str(body.data_product_id), "session_key_id": session.session_key_id},
    )

    return SandboxSessionResponse.model_validate(session)


@router.get("")
async def list_sandbox_sessions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    base = select(SandboxSession)
    count_base = select(func.count()).select_from(SandboxSession)
    if not _can_view_all_sessions(current_user):
        base = base.where(SandboxSession.user_id == current_user.id)
        count_base = count_base.where(SandboxSession.user_id == current_user.id)
    if status:
        base = base.where(SandboxSession.status == status)
        count_base = count_base.where(SandboxSession.status == status)

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    query = base.offset(skip).limit(limit)
    result = await db.execute(query)
    items = [SandboxSessionResponse.model_validate(s) for s in result.scalars().all()]
    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/{session_id}", response_model=SandboxSessionResponse)
async def get_sandbox_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id and not _can_view_all_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")
    return SandboxSessionResponse.model_validate(session)


@router.post("/{session_id}/terminate", response_model=SandboxSessionResponse)
async def terminate_sandbox_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id and not _can_operate_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")

    # Validate state transition via state machine
    current = SessionStatus(session.status)
    result = session_state_machine.validate_transition(current, SessionStatus.TERMINATED)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    session.status = SessionStatus.TERMINATED.value
    session.ended_at = datetime.now(timezone.utc)

    # Collect sandbox activity audit logs before destroying container
    try:
        audit_log_path = Path(tempfile.gettempdir()) / "cds-sandbox-audit" / f"{session_id}.jsonl"
        if audit_log_path.exists():
            collector = get_sandbox_audit_collector()
            collector.collect_and_flush(str(audit_log_path))
            audit_log_path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"[sandbox-audit] Collection on terminate failed: {e}")

    # Destroy sandbox container
    if session.container_id:
        runtime = await get_sandbox_manager()
        runtime.terminate(session.container_id)

    # Clean up network policy enforcement
    await network_policy_engine.remove_policy(str(session.id))
    net_result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == str(session.id))
    )
    net_policy = net_result.scalar_one_or_none()
    if net_policy:
        net_policy.active = False

    # Release tenant resource usage
    limits = get_resource_limits(session.sandbox_level)
    release_tenant_usage(
        str(current_user.id),
        cpu_cores=limits["cpu_cores"],
        memory_mb=limits["memory_mb"],
        disk_mb=limits["disk_mb"],
    )

    # Destroy session key
    if session.session_key_id:
        kms_service.destroy_key(session.session_key_id)
        # Update key metadata
        result_key = await db.execute(select(KeyMetadata).where(KeyMetadata.key_id == session.session_key_id))
        key_meta = result_key.scalar_one_or_none()
        if key_meta:
            key_meta.status = KeyStatus.DESTROYED.value
            key_meta.destroyed_at = datetime.now(timezone.utc)
            key_meta.destroy_reason = "session_terminated"

    await db.flush()
    await db.refresh(session)
    await audit_service.log(
        db, action="sandbox.terminate", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"previous_status": session.status, "key_destroyed": session.session_key_id is not None},
    )
    return SandboxSessionResponse.model_validate(session)


@router.put("/{session_id}/network-policy")
async def update_network_policy(
    session_id: uuid.UUID,
    mode: str = "deny_all",
    allowed_ips: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    allowed_ports: list[int] | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the network policy for a sandbox session.

    Modes:
    - deny_all: No network access (default)
    - allowlist: Only specified IPs/domains/ports are accessible
    """
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your session")
    if session.status not in [SessionStatus.RUNNING.value, SessionStatus.READY.value]:
        raise HTTPException(status_code=400, detail=f"Session not running (status: {session.status})")

    if mode not in ("deny_all", "allowlist"):
        raise HTTPException(status_code=400, detail="Mode must be 'deny_all' or 'allowlist'")

    # Update DB model
    net_result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == str(session.id))
    )
    net_policy = net_result.scalar_one_or_none()
    if not net_policy:
        net_policy = NetworkPolicy(
            session_id=str(session.id),
            user_id=str(current_user.id),
        )
        db.add(net_policy)

    net_policy.mode = mode
    net_policy.allowed_ips = allowed_ips or []
    net_policy.allowed_domains = allowed_domains or []
    net_policy.allowed_ports = allowed_ports or [443, 80]
    net_policy.active = True

    # Update engine enforcement
    await network_policy_engine.remove_policy(str(session.id))
    config = NetworkPolicyConfig(
        mode=mode,
        allowed_ips=allowed_ips or [],
        allowed_domains=allowed_domains or [],
        allowed_ports=allowed_ports or [443, 80],
    )
    policy_result = await network_policy_engine.create_policy(str(session.id), config)

    await db.flush()

    await audit_service.log(
        db, action="sandbox.update_network_policy", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"mode": mode, "allowed_ips": allowed_ips, "allowed_domains": allowed_domains},
    )

    return {"session_id": str(session_id), "mode": mode, "policy": policy_result}


@router.post("/{session_id}/execute")
async def execute_in_sandbox(
    session_id: uuid.UUID,
    code: str,
    language: str = "python",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute code in a sandbox session with session key injection."""
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your session")
    # State machine validation: only RUNNING sessions can execute
    current = SessionStatus(session.status)
    if not session_state_machine.is_active(current):
        raise HTTPException(status_code=400, detail=f"Session in terminal state (status: {session.status})")
    if session.status != SessionStatus.RUNNING.value:
        raise HTTPException(status_code=400, detail=f"Session not running (status: {session.status})")
    if not session.container_id:
        raise HTTPException(status_code=400, detail="Session has no container")

    # Static code analysis before execution (SS-03 §5)
    from app.services.code_scanner import code_scanner
    sandbox_mode = getattr(session, "sandbox_mode", None) or "structured_query"
    scan_result = code_scanner.scan(code, language, sandbox_mode)
    if not scan_result.passed:
        fatal_issues = [i for i in scan_result.issues if i.severity.value == "FATAL"]
        await audit_service.log(
            db, action="sandbox.code_scan_rejected", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"language": language, "issues": [{"code": i.code, "message": i.message, "line": i.line} for i in fatal_issues]},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Code scan failed",
                "issues": [{"severity": i.severity.value, "code": i.code, "message": i.message, "line": i.line} for i in fatal_issues],
            },
        )

    # Retrieve session key from KMS for injection into sandbox (in-memory only)
    session_key = None
    if session.session_key_id:
        try:
            key_bytes = kms_service.get_key(session.session_key_id)
            if key_bytes:
                session_key = key_bytes.hex()
        except Exception:
            pass  # Non-fatal: sandbox works without session key

    runtime = await get_sandbox_manager()

    # BwrapAdapter supports session_key and env_vars for hardened execution
    if hasattr(runtime, '_adapters') and session.container_id.startswith("bwrap-"):
        from app.models.sandbox_session import SandboxLevel
        adapter = runtime._adapters.get(SandboxLevel.L3.value)
        if adapter and hasattr(adapter, 'execute'):
            # Pass session key and context via environment variables
            # Create audit log tmpfs file for in-sandbox activity logging
            audit_log_dir = Path(tempfile.gettempdir()) / "cds-sandbox-audit"
            audit_log_dir.mkdir(parents=True, exist_ok=True)
            audit_log_path = audit_log_dir / f"{session_id}.jsonl"

            env_vars = {
                "CDS_SESSION_ID": str(session_id),
                "CDS_CONTRACT_ID": str(session.contract_id) if session.contract_id else "",
                "CDS_SANDBOX_LEVEL": session.sandbox_level,
                "CDS_DATA_PRODUCT_ID": str(session.data_product_id) if session.data_product_id else "",
                "CDS_USER_ID": str(current_user.id),
                "CDS_AUDIT_LOG": str(audit_log_path),
            }
            # Inject DuckDB SM4 encryption config if available
            enc_config = get_encryption_config_for_session(str(session_id))
            if enc_config:
                env_vars["CDS_DEK_HEX"] = enc_config["dek_hex"]
                env_vars["CDS_KEY_ID"] = enc_config.get("key_id", "sandbox-default")
            exec_result = await adapter.execute(
                session.container_id, code, language,
                session_key=session_key,
                env_vars=env_vars,
                timeout=session.timeout_seconds,
            )
        else:
            exec_result = await runtime.execute(session.container_id, code, language)
    else:
        exec_result = await runtime.execute(session.container_id, code, language)

    # Update session status if execution failed
    if exec_result.get("exit_code", 0) != 0 and "timed out" in exec_result.get("output", "").lower():
        session.status = SessionStatus.FAILED.value
        session.error_message = "Execution timed out"
        await db.flush()

    # Collect sandbox activity audit logs from tmpfs
    try:
        audit_log_path = Path(tempfile.gettempdir()) / "cds-sandbox-audit" / f"{session_id}.jsonl"
        if audit_log_path.exists():
            collector = get_sandbox_audit_collector()
            event_count = collector.collect_and_flush(str(audit_log_path))
            exec_result["audit_events_collected"] = event_count
    except Exception as e:
        logger.warning(f"[sandbox-audit] Collection failed: {e}")

    # Audit the execution
    await audit_service.log(
        db, action="sandbox.execute", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"language": language, "exit_code": exec_result.get("exit_code"), "duration_ms": exec_result.get("duration_ms")},
    )

    return exec_result


@router.get("/{session_id}/audit")
async def get_sandbox_audit_logs(
    session_id: uuid.UUID,
    category: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get sandbox activity audit logs for a session.

    Returns in-sandbox activity events: data access, network, application activity.
    Filter by category: data_access, network, application.
    """
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your session")

    # Try ClickHouse first
    try:
        from clickhouse_driver import Client
        from app.core.config import get_settings
        settings = get_settings()
        url = settings.CLICKHOUSE_URL
        host, port, user, password, database = "localhost", 9000, "default", "", "cds_audit"
        if "://" in url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 9000
            user = parsed.username or "default"
            password = parsed.password or ""
            database = parsed.path.lstrip("/") or "cds_audit"
        elif ":" in url:
            h, p = url.rsplit(":", 1)
            host, port = h, int(p)
        else:
            host = url
        client = Client(host=host, port=port, database=database, user=user, password=password)

        # Whitelist valid categories to prevent SQL injection
        VALID_CATEGORIES = {"data_access", "network", "application"}
        params = {"sid": str(session_id), "lim": int(limit)}
        where_clause = "session_id = %(sid)s"
        if category and category in VALID_CATEGORIES:
            where_clause += " AND category = %(cat)s"
            params["cat"] = category
        query = (
            "SELECT event_id, timestamp, category, event_type, detail, "
            "duration_ms, bytes_read, bytes_written, rows_affected, risk_level, blocked "
            f"FROM sandbox_activity_events WHERE {where_clause} ORDER BY timestamp DESC LIMIT %(lim)s"
        )
        rows = client.execute(query, params)
        events = [
            {
                "event_id": str(r[0]),
                "timestamp": r[1].isoformat() if hasattr(r[1], 'isoformat') else str(r[1]),
                "category": r[2],
                "event_type": r[3],
                "detail": json.loads(r[4]) if isinstance(r[4], str) else r[4],
                "duration_ms": r[5],
                "bytes_read": r[6],
                "bytes_written": r[7],
                "rows_affected": r[8],
                "risk_level": r[9],
                "blocked": r[10],
            }
            for r in rows
        ]
        return {"session_id": str(session_id), "events": events, "total": len(events), "source": "clickhouse"}
    except Exception:
        pass

    # Fallback: read from tmpfs log file
    try:
        audit_log_path = Path(tempfile.gettempdir()) / "cds-sandbox-audit" / f"{session_id}.jsonl"
        if audit_log_path.exists():
            from app.services.sandbox_audit import SandboxActivityEvent
            events = []
            with open(audit_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = SandboxActivityEvent.from_json(line)
                        if category and ev.category != category:
                            continue
                        events.append({
                            "event_id": ev.event_id,
                            "timestamp": ev.timestamp,
                            "category": ev.category,
                            "event_type": ev.event_type,
                            "detail": ev.detail,
                            "duration_ms": ev.duration_ms,
                            "bytes_read": ev.bytes_read,
                            "bytes_written": ev.bytes_written,
                            "rows_affected": ev.rows_affected,
                            "risk_level": ev.risk_level,
                            "blocked": ev.blocked,
                        })
                    except Exception:
                        continue
            events.sort(key=lambda e: e["timestamp"], reverse=True)
            return {"session_id": str(session_id), "events": events[:limit], "total": len(events), "source": "tmpfs"}
    except Exception:
        pass

    return {"session_id": str(session_id), "events": [], "total": 0, "source": "none"}


@router.post("/cleanup-expired")
async def cleanup_expired_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Clean up expired sandbox sessions. Called by background scheduler or admin."""
    query = select(SandboxSession).where(
        SandboxSession.status.in_([
            SessionStatus.PENDING.value,
            SessionStatus.PROVISIONING.value,
            SessionStatus.RUNNING.value,
        ])
    )
    result = await db.execute(query)
    sessions = result.scalars().all()

    cleaned = 0
    for session in sessions:
        if is_session_expired(session):
            # Validate state transition via state machine
            current = SessionStatus(session.status)
            result = session_state_machine.validate_transition(current, SessionStatus.TERMINATED)
            if not result.success:
                logger.warning(f"[cleanup] Cannot terminate expired session {session.id}: {result.error}")
                continue
            session.status = SessionStatus.TERMINATED.value
            session.ended_at = datetime.now(timezone.utc)
            session.error_message = "Session expired (timeout)"

            # Destroy container
            if session.container_id:
                try:
                    runtime = await get_sandbox_manager()
                    runtime.terminate(session.container_id)
                except Exception:
                    pass

            # Destroy session key
            if session.session_key_id:
                kms_service.destroy_key(session.session_key_id)
                key_result = await db.execute(select(KeyMetadata).where(KeyMetadata.key_id == session.session_key_id))
                key_meta = key_result.scalar_one_or_none()
                if key_meta:
                    key_meta.status = KeyStatus.DESTROYED.value
                    key_meta.destroyed_at = datetime.now(timezone.utc)
                    key_meta.destroy_reason = "session_expired"

            cleaned += 1

    await db.flush()
    return {"cleaned": cleaned}
