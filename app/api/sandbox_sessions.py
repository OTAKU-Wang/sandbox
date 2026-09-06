import json
import logging
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.data_product import DataProduct
from app.models.contract import Contract, ContractStatus
from app.models.sandbox_session import SandboxSession, SessionStatus, SandboxLevel
from app.models.audit_log import AuditLog
from app.schemas.sandbox_session import SandboxExecRequest, SandboxExecuteRequest, SandboxSessionCreate, SandboxSessionResponse
from app.services.audit_service import audit_service
from app.services.kms_service import kms_service
from app.services.sandbox_manager import validate_resource_limits, is_session_expired, get_sandbox_manager, check_tenant_quota, update_tenant_usage, release_tenant_usage, get_resource_limits, attestation_required_for_level as _attestation_required_for_level, attestation_from_provision as _attestation_from_provision, attestation_from_session as _attestation_from_session
from app.services.session_state_machine import session_state_machine
from app.models.kms import KeyDistribution, KeyMetadata, KeyType, KeyStatus
from app.models.network_policy import NetworkPolicy
from app.schemas.network_policy import NetworkPolicyResponse, NetworkPolicyUpdate
from app.services.network_policy import network_policy_engine, NetworkPolicyConfig
from app.api.sandbox_db import get_encryption_config_for_session
from app.services.sandbox_audit import get_sandbox_audit_collector
from app.services.quota_manager import quota_manager, QuotaType
import tempfile
from sqlalchemy import func

router = APIRouter()


PROOF_BUNDLE_SCHEMA_VERSION = "cds.session.proof.v1"


def _can_view_all_sessions(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.REGULATOR, UserRole.ADMIN)


def _can_operate_sessions(user: User) -> bool:
    return user.role in (UserRole.OPERATOR, UserRole.ADMIN)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash_text(value: str | bytes | None) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _session_attestation_summary(session: SandboxSession) -> dict:
    limits = session.resource_limits or {}
    record = limits.get("attestation") if isinstance(limits, dict) else None
    if not isinstance(record, dict):
        return {
            "present": False,
            "required": _attestation_required_for_level(session.sandbox_level),
            "type": None,
            "measurement": None,
            "quote_hash": None,
            "is_simulation": None,
        }

    quote = record.get("quote")
    return {
        "present": bool(quote),
        "required": _attestation_required_for_level(session.sandbox_level),
        "type": record.get("type"),
        "measurement": record.get("measurement"),
        "quote_hash": _hash_text(quote),
        "is_simulation": bool(record.get("is_simulation")),
    }


def _resource_policy_summary(session: SandboxSession) -> dict:
    limits = dict(session.resource_limits or {})
    limits.pop("attestation", None)
    return limits


def _network_policy_summary(policy: NetworkPolicy | None) -> dict | None:
    if not policy:
        return None
    return {
        "mode": policy.mode,
        "active": policy.active,
        "allowed_ips": policy.allowed_ips or [],
        "allowed_domains": policy.allowed_domains or [],
        "allowed_ports": policy.allowed_ports or [],
        "dns_proxy_enabled": policy.dns_proxy_enabled,
        "max_connections_per_second": policy.max_connections_per_second,
        "max_bandwidth_bytes_per_second": policy.max_bandwidth_bytes_per_second,
        "updated_at": _iso(policy.updated_at),
    }


def _contract_policy_summary(contract: Contract | None) -> dict | None:
    if not contract:
        return None
    return {
        "id": str(contract.id),
        "status": contract.status,
        "product_ids": [str(product_id) for product_id in (contract.product_ids or [])],
        "allowed_sandbox_levels": contract.allowed_sandbox_levels,
        "allowed_sandbox_modes": contract.allowed_sandbox_modes or [],
        "allowed_operations": contract.allowed_operations,
        "max_duration_hours": contract.max_duration_hours,
        "max_output_rows": contract.max_output_rows,
        "dp_epsilon_budget": contract.dp_epsilon_budget,
        "allowed_output_formats": contract.allowed_output_formats,
        "inspection_rule_set": contract.inspection_rule_set or {},
    }


def _audit_event_summary(record: AuditLog) -> dict:
    detail = record.detail or {}
    return {
        "id": str(record.id),
        "action": record.action,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "created_at": _iso(record.created_at),
        "signed": bool(detail.get("_sm2_signature")),
        "signature_hash": _hash_text(detail.get("_sm2_signature")),
        "blockchain_tx_hash": record.blockchain_tx_hash or detail.get("blockchain_tx_hash"),
    }


def _parse_contract_id(contract_id: str | None) -> uuid.UUID | None:
    if not contract_id:
        return None
    try:
        return uuid.UUID(str(contract_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid contract_id")


def _mode_allowed_by_contract(session_mode: str, allowed_modes: list | None) -> bool:
    if not allowed_modes:
        return True
    aliases = {
        "structured_query": {"structured_query", "query", "read"},
        "structured_modeling": {"structured_modeling", "modeling", "analyze"},
        "structured_app": {"structured_app", "app", "invoke"},
        "llm_training": {"llm_training", "train", "training"},
        "product_dev": {"product_dev", "develop", "development"},
        "joint_federated": {"joint_federated", "federated", "compute"},
    }.get(session_mode, {session_mode})
    return bool(aliases.intersection({str(mode) for mode in allowed_modes}))


def _operation_allowed_by_contract(session_mode: str, allowed_operations: str | None) -> bool:
    if not allowed_operations:
        return True
    allowed_ops = {op.strip() for op in allowed_operations.split(",") if op.strip()}
    mode_ops = {
        "structured_query": {"query", "read", "analyze", "execute", "structured_query"},
        "structured_modeling": {"analyze", "model", "execute", "structured_modeling"},
        "structured_app": {"invoke", "read", "execute", "structured_app"},
        "llm_training": {"train", "execute", "llm_training"},
        "product_dev": {"read", "transform", "analyze", "execute", "product_dev"},
        "joint_federated": {"compute", "execute", "joint_federated"},
    }.get(session_mode, {"execute", session_mode})
    return not allowed_ops.isdisjoint(mode_ops)


def _attestation_record_from_provision(provision_result: dict) -> dict | None:
    quote = provision_result.get("attestation_quote")
    if not quote:
        return None
    return {
        "quote": quote.decode("utf-8") if isinstance(quote, bytes) else quote,
        "type": provision_result.get("attestation_type"),
        "measurement": provision_result.get("attestation_measurement") or provision_result.get("mrenclave"),
        "is_simulation": bool(provision_result.get("is_simulation")),
    }


async def _execute_runtime_with_context(
    runtime,
    container_id: str,
    code: str,
    language: str,
    *,
    session_key: str | None = None,
    env_vars: dict[str, str] | None = None,
    timeout: int | None = None,
) -> dict:
    import inspect

    params = inspect.signature(runtime.execute).parameters
    kwargs = {}
    optional = {"session_key": session_key, "env_vars": env_vars, "timeout": timeout}
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    for name, value in optional.items():
        if accepts_kwargs or name in params:
            kwargs[name] = value
    return await runtime.execute(container_id, code, language, **kwargs)


async def _validate_session_contract(
    db: AsyncSession,
    body: SandboxSessionCreate,
    current_user: User,
) -> Contract | None:
    contract_uuid = _parse_contract_id(body.contract_id)
    if not contract_uuid:
        return None

    contract = await db.get(Contract, contract_uuid)
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status not in (ContractStatus.ACTIVE.value, ContractStatus.SIGNED.value):
        raise HTTPException(status_code=400, detail=f"Contract is {contract.status}")
    if current_user.id != contract.buyer_id and not _can_operate_sessions(current_user):
        raise HTTPException(status_code=403, detail="Only the contract buyer can create a session for this contract")
    if str(body.data_product_id) not in [str(pid) for pid in contract.product_ids]:
        raise HTTPException(status_code=400, detail="Data product not covered by this contract")

    allowed_levels = {level.strip() for level in (contract.allowed_sandbox_levels or "").split(",") if level.strip()}
    if allowed_levels and body.sandbox_level not in allowed_levels:
        raise HTTPException(status_code=400, detail=f"Sandbox level '{body.sandbox_level}' not allowed by contract")

    session_mode = body.sandbox_mode
    if not _mode_allowed_by_contract(session_mode, contract.allowed_sandbox_modes):
        raise HTTPException(status_code=400, detail=f"Sandbox mode '{session_mode}' not allowed by contract")
    if not _operation_allowed_by_contract(session_mode, contract.allowed_operations):
        raise HTTPException(status_code=400, detail=f"Contract operations do not allow {session_mode} sessions")

    max_timeout = int((contract.max_duration_hours or 24) * 3600)
    if body.timeout_seconds > max_timeout:
        raise HTTPException(status_code=400, detail=f"Session timeout exceeds contract max duration ({max_timeout}s)")

    return contract


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

    contract = await _validate_session_contract(db, body, current_user)

    # Round 40 usability: validate the template name before anything is
    # provisioned so an unknown name cannot leave an orphaned container.
    if body.template:
        from app.services import session_templates as st

        if st.get_template(body.template) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown session template: {body.template!r} (see GET /sandbox-sessions/session-templates)",
            )

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
        sandbox_mode=body.sandbox_mode,
        contract_id=str(contract.id) if contract else None,
        timeout_seconds=body.timeout_seconds,
        resource_limits=effective_limits,
        status=SessionStatus.PROVISIONING.value,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)

    # Initialize session quota counters (P1-4)
    await quota_manager.reset(session.id)

    # Pre-fill quota limits from validated contract
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
    if provision_result.get("error") or not session.container_id or session.status == SessionStatus.FAILED.value:
        session.status = SessionStatus.FAILED.value
        session.error_message = provision_result.get("error") or "Sandbox provision failed"
        await db.flush()
        await db.refresh(session)
        await audit_service.log(
            db, action="sandbox.create_failed", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={
                "sandbox_level": body.sandbox_level,
                "sandbox_mode": body.sandbox_mode,
                "data_product_id": str(body.data_product_id),
                "error": session.error_message,
            },
        )
        return SandboxSessionResponse.model_validate(session)
    else:
        # Track tenant resource usage
        update_tenant_usage(
            str(current_user.id),
            cpu_cores=limits["cpu_cores"],
            memory_mb=limits["memory_mb"],
            disk_mb=limits["disk_mb"],
        )

    attestation_record = _attestation_record_from_provision(provision_result)
    if attestation_record:
        session.resource_limits = {**(session.resource_limits or {}), "attestation": attestation_record}

    # Generate session key via KMS
    session_key_result = kms_service.generate_session_key(str(session.id))

    # Distribute key into sandbox workspace
    attestation = _attestation_from_provision(provision_result)
    if _attestation_required_for_level(body.sandbox_level) and not attestation:
        kms_service.destroy_key(session_key_result["key_id"])
        try:
            runtime.terminate(session.container_id)
        except Exception as e:
            logger.warning("[sandbox] Container rollback after attestation denial failed: %s", e)
        release_tenant_usage(
            str(current_user.id),
            cpu_cores=limits["cpu_cores"],
            memory_mb=limits["memory_mb"],
            disk_mb=limits["disk_mb"],
        )
        session.container_id = None
        session.status = SessionStatus.FAILED.value
        session.error_message = "Sandbox attestation quote missing; key distribution denied"
        await db.flush()
        await db.refresh(session)
        await audit_service.log(
            db, action="sandbox.key_distribution_denied", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"sandbox_level": body.sandbox_level, "reason": session.error_message},
        )
        return SandboxSessionResponse.model_validate(session)

    # Gap B1: pass the attestation whenever the runtime produced one, not
    # only for TEE levels — KMS enforces fail-closed when configured.
    distributed_key = kms_service.distribute_key(
        session_key_result["key_id"],
        str(session.id),
        attestation=attestation,
    )
    if not distributed_key:
        kms_service.destroy_key(session_key_result["key_id"])
        try:
            runtime.terminate(session.container_id)
        except Exception as e:
            logger.warning("[sandbox] Container rollback after key distribution failure failed: %s", e)
        release_tenant_usage(
            str(current_user.id),
            cpu_cores=limits["cpu_cores"],
            memory_mb=limits["memory_mb"],
            disk_mb=limits["disk_mb"],
        )
        session.container_id = None
        session.status = SessionStatus.FAILED.value
        session.error_message = "Session key distribution failed"
        await db.flush()
        await db.refresh(session)
        await audit_service.log(
            db, action="sandbox.key_distribution_failed", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"sandbox_level": body.sandbox_level, "attestation_required": _attestation_required_for_level(body.sandbox_level)},
        )
        return SandboxSessionResponse.model_validate(session)

    session.session_key_id = session_key_result["key_id"]

    # Record key metadata (gap B3: persist the KEK-wrapped blob so the key
    # survives restarts; nulled on destroy by the lifecycle terminator)
    key_meta = KeyMetadata(
        key_id=session_key_result["key_id"],
        key_type=KeyType.SESSION.value,
        status=KeyStatus.ACTIVE.value,
        session_id=session.id,
        product_id=body.data_product_id,
        wrapped_payload=kms_service.export_wrapped(session_key_result["key_id"]),
        sm2_encrypted_payload=kms_service.export_sm2_ciphertext(session_key_result["key_id"]),
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

    # Round 40 usability: seed the workspace with the requested template.
    # Convenience feature — seeding failures are audited, not fatal, so the
    # session stays usable without the starter files.
    if body.template:
        from app.services import session_templates as st

        try:
            workspace = _session_workspace_or_400(session)
            seeded = st.seed_workspace(workspace, st.get_template(body.template) or {})
            session.resource_limits = {
                **(session.resource_limits or {}),
                "template": body.template,
                "template_env": seeded.get("env") or {},
            }
            # Flush + refresh so server-managed columns (updated_at) are
            # re-loaded BEFORE response validation — model_validate on an
            # expired attribute triggers lazy IO and fails with
            # MissingGreenlet outside the greenlet context.
            await db.flush()
            await db.refresh(session)
        except Exception as e:
            logger.warning("[sandbox] Template seeding failed for %s: %s", session.id, e)
            await audit_service.log(
                db, action="sandbox.template_seed_failed", resource_type="sandbox_session",
                user_id=current_user.id, session_id=session.id,
                detail={"template": body.template, "error": str(e)},
            )

    await audit_service.log(
        db, action="sandbox.create", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={
            "sandbox_level": body.sandbox_level,
            "sandbox_mode": body.sandbox_mode,
            "data_product_id": str(body.data_product_id),
            "session_key_id": session.session_key_id,
            "attestation_present": attestation is not None,
        },
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


@router.get("/session-templates")
async def list_session_templates(
    current_user: User = Depends(get_current_user),
):
    """List available workspace templates for session creation (Round 40).

    NOTE: registered before the ``/{session_id}`` route on purpose — a literal
    path appended after ``/{session_id}`` would be shadowed by the UUID
    parameter match (FastAPI returns 422 instead of falling through).
    """
    from app.services import session_templates as st

    return {"templates": st.list_templates()}


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


@router.get("/{session_id}/proof-bundle")
async def get_sandbox_session_proof_bundle(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a release/regulatory proof bundle for a sandbox session.

    The bundle intentionally contains only public evidence, metadata and
    hashes. It never returns session key plaintext, raw sandbox output, raw
    attestation quotes, or secret configuration values.
    """
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id and not _can_view_all_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")

    contract = None
    if session.contract_id:
        try:
            contract = await db.get(Contract, uuid.UUID(str(session.contract_id)))
        except (TypeError, ValueError):
            contract = None

    net_result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == str(session.id))
    )
    network_policy = net_result.scalar_one_or_none()

    key_meta = None
    if session.session_key_id:
        key_result = await db.execute(
            select(KeyMetadata).where(KeyMetadata.key_id == session.session_key_id)
        )
        key_meta = key_result.scalar_one_or_none()

    dist_result = await db.execute(
        select(KeyDistribution)
        .where(KeyDistribution.session_id == str(session.id))
        .order_by(KeyDistribution.created_at.desc())
        .limit(1)
    )
    key_distribution = dist_result.scalar_one_or_none()

    output_result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.session_id == session.id,
            AuditLog.action == "sandbox.output_inspected",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    output_event = output_result.scalar_one_or_none()
    output_detail = output_event.detail if output_event and isinstance(output_event.detail, dict) else {}

    count_result = await db.execute(
        select(func.count()).where(AuditLog.session_id == session.id)
    )
    audit_count = count_result.scalar() or 0
    audit_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.session_id == session.id)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    )
    audit_events = [_audit_event_summary(record) for record in audit_result.scalars().all()]

    attestation = _session_attestation_summary(session)
    resource_policy = _resource_policy_summary(session)
    network_policy_summary = _network_policy_summary(network_policy)
    contract_policy = _contract_policy_summary(contract)
    if attestation["present"] and attestation["type"] not in {"software_hash", "firecracker"} and not attestation["is_simulation"]:
        proof_level = "hardware_tee"
    elif attestation["present"]:
        proof_level = "software_confidential"
    else:
        proof_level = "runtime_isolation"

    policy_evidence = {
        "resource_limits": resource_policy,
        "network_policy": network_policy_summary,
        "contract_policy": contract_policy,
    }
    key_evidence = {
        "session_key_id": session.session_key_id,
        "key_metadata": {
            "status": key_meta.status,
            "key_type": key_meta.key_type,
            "algorithm": key_meta.algorithm,
            "created_at": _iso(key_meta.created_at),
            "destroyed_at": _iso(key_meta.destroyed_at),
            "destroy_reason": key_meta.destroy_reason,
        } if key_meta else None,
        "latest_distribution": {
            "status": key_distribution.status,
            "attestation_status": key_distribution.attestation_status,
            "tee_type": key_distribution.tee_type,
            "tee_mrenclave": key_distribution.tee_mrenclave,
            "tee_quote_hash": key_distribution.tee_quote_hash,
            "created_at": _iso(key_distribution.created_at),
            "expires_at": _iso(key_distribution.expires_at),
            "revoked_at": _iso(key_distribution.revoked_at),
        } if key_distribution else None,
    }
    output_security = {
        "available": output_event is not None,
        "audit_event_id": str(output_event.id) if output_event else None,
        "created_at": _iso(output_event.created_at) if output_event else None,
        "passed": output_detail.get("passed"),
        "blocked": output_detail.get("blocked"),
        "findings_count": output_detail.get("findings_count"),
        "severities": output_detail.get("severities", []),
        "dp_applied": output_detail.get("dp_applied"),
        "watermark": output_detail.get("watermark"),
        "signature": output_detail.get("signature"),
        "report_hash": output_detail.get("report_hash"),
        "released_output_hash": output_detail.get("released_output_hash"),
        "output_released": output_detail.get("output_released"),
    }

    payload = {
        "schema_version": PROOF_BUNDLE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": {
            "id": str(session.id),
            "user_id": str(session.user_id),
            "data_product_id": str(session.data_product_id),
            "contract_id": session.contract_id,
            "sandbox_level": session.sandbox_level,
            "sandbox_mode": session.sandbox_mode,
            "status": session.status,
            "container_id": session.container_id,
            "timeout_seconds": session.timeout_seconds,
            "created_at": _iso(session.created_at),
            "started_at": _iso(session.started_at),
            "ended_at": _iso(session.ended_at),
            "updated_at": _iso(session.updated_at),
        },
        "runtime": {
            "proof_level": proof_level,
            "attestation": attestation,
        },
        "policy": {
            **policy_evidence,
            "resource_limits_hash": _hash_json(resource_policy),
            "network_policy_hash": _hash_json(network_policy_summary),
            "contract_policy_hash": _hash_json(contract_policy),
            "combined_policy_hash": _hash_json(policy_evidence),
        },
        "keys": key_evidence,
        "output_security": output_security,
        "audit": {
            "event_count": audit_count,
            "latest_events": audit_events,
            "latest_events_hash": _hash_json(audit_events),
        },
        "integrity": {
            "hash_alg": "sha256",
            "excluded_fields": ["session_key_plaintext", "raw_sandbox_output", "raw_attestation_quote", "secret_configuration"],
        },
    }
    payload["integrity"]["evidence_hash"] = _hash_json({
        key: value for key, value in payload.items()
        if key not in {"generated_at", "integrity"}
    })
    payload["integrity"]["bundle_hash"] = _hash_json(payload)

    await audit_service.log(
        db,
        action="sandbox.proof_bundle_generated",
        resource_type="sandbox_session",
        user_id=current_user.id,
        session_id=session.id,
        detail={
            "schema_version": PROOF_BUNDLE_SCHEMA_VERSION,
            "evidence_hash": payload["integrity"]["evidence_hash"],
            "bundle_hash": payload["integrity"]["bundle_hash"],
            "proof_level": proof_level,
        },
    )

    return payload


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

    previous_status = session.status
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
        str(session.user_id),
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
        detail={"previous_status": previous_status, "key_destroyed": session.session_key_id is not None},
    )
    return SandboxSessionResponse.model_validate(session)


@router.put("/{session_id}/network-policy")
async def update_network_policy(
    session_id: uuid.UUID,
    body: NetworkPolicyUpdate,
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
    if session.user_id != current_user.id and not _can_operate_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")
    if session.status not in [SessionStatus.RUNNING.value, SessionStatus.READY.value]:
        raise HTTPException(status_code=400, detail=f"Session not running (status: {session.status})")

    # Update DB model
    net_result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == str(session.id))
    )
    net_policy = net_result.scalar_one_or_none()
    if not net_policy:
        net_policy = NetworkPolicy(
            session_id=str(session.id),
            user_id=str(session.user_id),
        )
        db.add(net_policy)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(net_policy, field, value)
    if not update_data:
        net_policy.mode = net_policy.mode or "deny_all"
    if net_policy.allowed_ips is None:
        net_policy.allowed_ips = []
    if net_policy.allowed_domains is None:
        net_policy.allowed_domains = []
    if net_policy.allowed_ports is None:
        net_policy.allowed_ports = [443, 80]
    if net_policy.active is None:
        net_policy.active = True

    # Update engine enforcement
    await network_policy_engine.remove_policy(str(session.id))
    config = NetworkPolicyConfig(
        mode=net_policy.mode,
        allowed_ips=net_policy.allowed_ips or [],
        allowed_domains=net_policy.allowed_domains or [],
        allowed_ports=net_policy.allowed_ports or [443, 80],
        dns_proxy_enabled=net_policy.dns_proxy_enabled,
        max_connections_per_second=net_policy.max_connections_per_second,
        max_bandwidth_bytes_per_second=net_policy.max_bandwidth_bytes_per_second,
    )
    policy_result = None
    if net_policy.active:
        policy_result = await network_policy_engine.create_policy(str(session.id), config)

    await db.flush()
    await db.refresh(net_policy)

    await audit_service.log(
        db, action="sandbox.update_network_policy", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={
            "mode": net_policy.mode,
            "allowed_ips": net_policy.allowed_ips,
            "allowed_domains": net_policy.allowed_domains,
            "active": net_policy.active,
        },
    )

    return {"network_policy": NetworkPolicyResponse.model_validate(net_policy), "enforcement": policy_result}


@router.get("/{session_id}/network-policy", response_model=NetworkPolicyResponse)
async def get_session_network_policy(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get or initialize the zero-trust network policy for a sandbox session."""
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id and not _can_view_all_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")

    net_result = await db.execute(
        select(NetworkPolicy).where(NetworkPolicy.session_id == str(session.id))
    )
    net_policy = net_result.scalar_one_or_none()
    if not net_policy:
        net_policy = NetworkPolicy(
            session_id=str(session.id),
            user_id=str(session.user_id),
            mode="deny_all",
            active=True,
        )
        db.add(net_policy)
        await db.flush()
        await db.refresh(net_policy)
        await network_policy_engine.create_policy(str(session.id), NetworkPolicyConfig(mode="deny_all"))

    return NetworkPolicyResponse.model_validate(net_policy)


@router.post("/{session_id}/execute")
async def execute_in_sandbox(
    session_id: uuid.UUID,
    body: SandboxExecuteRequest | None = Body(None),
    code: str | None = Query(None),
    language: str = Query("python"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Execute code in a sandbox session with session key injection."""
    exec_code = body.code if body else code
    exec_language = body.language if body else language
    exec_language = (exec_language or "python").lower()
    if not exec_code or not exec_code.strip():
        raise HTTPException(status_code=422, detail="code cannot be empty")

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
    scan_result = code_scanner.scan(exec_code, exec_language, sandbox_mode)
    if not scan_result.passed:
        fatal_issues = [i for i in scan_result.issues if i.severity.value == "FATAL"]
        await audit_service.log(
            db, action="sandbox.code_scan_rejected", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"language": exec_language, "issues": [{"code": i.code, "message": i.message, "line": i.line} for i in fatal_issues]},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Code scan failed",
                "issues": [{"severity": i.severity.value, "code": i.code, "message": i.message, "line": i.line} for i in fatal_issues],
            },
        )

    # Retrieve session key through the KMS distribution path for in-memory injection only.
    session_key = None
    if session.session_key_id:
        # Gap B1: pass the stored quote whenever present (not only TEE levels).
        attestation = _attestation_from_session(session)
        if _attestation_required_for_level(session.sandbox_level) and not attestation:
            raise HTTPException(status_code=503, detail="Sandbox attestation quote missing; key distribution denied")
        try:
            session_key = kms_service.distribute_key(
                session.session_key_id,
                str(session.id),
                attestation=attestation,
            )
        except Exception as e:
            logger.warning("[sandbox] Session key distribution failed: %s", e)
            session_key = None
        if not session_key:
            raise HTTPException(status_code=503, detail="Session key distribution failed")

    runtime = await get_sandbox_manager()

    # Pass session context via environment variables to adapters that support it.
    audit_log_dir = Path(tempfile.gettempdir()) / "cds-sandbox-audit"
    audit_log_dir.mkdir(parents=True, exist_ok=True)
    audit_log_path = audit_log_dir / f"{session_id}.jsonl"

    env_vars = {
        "CDS_SESSION_ID": str(session_id),
        "CDS_CONTRACT_ID": str(session.contract_id) if session.contract_id else "",
        "CDS_SANDBOX_LEVEL": session.sandbox_level,
        "CDS_SANDBOX_MODE": sandbox_mode,
        "CDS_DATA_PRODUCT_ID": str(session.data_product_id) if session.data_product_id else "",
        "CDS_USER_ID": str(current_user.id),
        "CDS_AUDIT_LOG": str(audit_log_path),
    }
    # Inject DuckDB SM4 encryption config if available
    enc_config = get_encryption_config_for_session(str(session_id))
    if enc_config:
        env_vars["CDS_DEK_HEX"] = enc_config["dek_hex"]
        env_vars["CDS_KEY_ID"] = enc_config.get("key_id", "sandbox-default")
    # Round 40: template env vars recorded at create time
    env_vars.update(_session_template_env(session))

    from app.services.sandbox_runtime import SceneRuntimeFactory
    scene_runtime = SceneRuntimeFactory.create(sandbox_mode)
    scene_context = {"language": exec_language, "max_output_rows": 10000}
    try:
        prepared_code = await scene_runtime.pre_execute(exec_code, scene_context)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    exec_result = await _execute_runtime_with_context(
        runtime,
        session.container_id,
        prepared_code,
        exec_language,
        session_key=session_key,
        env_vars=env_vars,
        timeout=session.timeout_seconds,
    )
    exec_result = await scene_runtime.post_execute(exec_result, scene_context)

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
        detail={"language": exec_language, "exit_code": exec_result.get("exit_code"), "duration_ms": exec_result.get("duration_ms")},
    )

    try:
        from app.services.output_security import (
            inspect_text_output,
            inspection_to_report,
            should_block,
        )

        output = exec_result.get("output", "")
        if output is None:
            output = ""
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False, default=str)

        if output:
            inspection = inspect_text_output(
                output,
                user_id=str(current_user.id),
                session_id=str(session.id),
                sandbox_mode=sandbox_mode,
            )
            report = inspection_to_report(inspection)
            if should_block(inspection):
                exec_result["output"] = ""
                exec_result["output_blocked"] = True
                exec_result["blocked_reason"] = "output_inspection_blocked"
            else:
                exec_result["output"] = inspection.redacted_output or ""
                exec_result["output_blocked"] = False
            exec_result["security_report"] = report
        else:
            exec_result["security_report"] = {
                "passed": True,
                "blocked": False,
                "stage_results": {"no_output": True},
                "findings": [],
                "findings_count": 0,
            }
            exec_result["output_blocked"] = False
    except Exception as e:
        exec_result["output"] = ""
        exec_result["exit_code"] = -3
        exec_result["security_report"] = {"passed": False, "blocked": True, "error": str(e)}
        exec_result["output_blocked"] = True

    security_report = exec_result.get("security_report") or {}
    try:
        await audit_service.log(
            db,
            action="sandbox.output_inspected",
            resource_type="sandbox_session",
            user_id=current_user.id,
            session_id=session.id,
            detail={
                "passed": bool(security_report.get("passed")),
                "blocked": bool(security_report.get("blocked") or exec_result.get("output_blocked")),
                "findings_count": security_report.get("findings_count", 0),
                "severities": security_report.get("severities", []),
                "dp_applied": bool(security_report.get("dp_applied")),
                "watermark": security_report.get("watermark"),
                "signature": security_report.get("signature"),
                "report_hash": _hash_json(security_report),
                "released_output_hash": _hash_text(exec_result.get("output") or ""),
                "output_released": bool(exec_result.get("output")) and not bool(exec_result.get("output_blocked")),
            },
        )
    except Exception as e:
        logger.warning("[sandbox] Failed to audit output inspection summary: %s", e)

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
    if session.user_id != current_user.id and not _can_view_all_sessions(current_user):
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
    """Clean up expired sandbox sessions.

    Kept for manual/admin triggering; the background session lifecycle loop
    (app/services/session_lifecycle.py, registered in the app lifespan) runs
    the same service function periodically.
    """
    from app.services.session_lifecycle import cleanup_expired_sessions as _cleanup_service

    cleaned = await _cleanup_service(db)
    await db.flush()
    return {"cleaned": cleaned}

# ════════════════════════════════════════════════════════════════
# Round 39 (usability): session files / snapshots / pause-resume-refresh
# ════════════════════════════════════════════════════════════════


def _can_operate_session(user: User, session: SandboxSession) -> bool:
    """Owner or operator/admin — mirrors the frontend canOperate semantics."""
    return session.user_id == user.id or user.role in (UserRole.OPERATOR, UserRole.ADMIN)


async def _load_operable_session(
    db: AsyncSession, session_id: uuid.UUID, current_user: User
) -> SandboxSession:
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if not _can_operate_session(current_user, session):
        raise HTTPException(status_code=403, detail="Not your session")
    return session


def _session_workspace_or_400(session: SandboxSession) -> Path:
    """Resolve the on-host workspace for a provisioned session."""
    from app.services.sandbox_runtime import sandbox_runtime

    if not session.container_id:
        raise HTTPException(status_code=400, detail="Session has no provisioned container")
    workspace = sandbox_runtime.get_workspace(session.container_id, session.sandbox_level)
    if not workspace:
        raise HTTPException(status_code=400, detail="Session workspace not found on host")
    return workspace


async def _session_dek(session: SandboxSession) -> bytes | None:
    """Resolve the session workspace DEK (same chain as RAG corpora)."""
    from app.services.rag_service import _session_workspace_dek

    workspace = _session_workspace_or_400(session)
    return await _session_workspace_dek(workspace)


def _active_or_suspended(session: SandboxSession) -> None:
    current = SessionStatus(session.status)
    if not session_state_machine.is_active(current):
        raise HTTPException(
            status_code=400, detail=f"Session in terminal state (status: {session.status})"
        )


def _session_template_env(session: SandboxSession) -> dict[str, str]:
    """Environment variables recorded at create time by the session template
    (Round 40) — merged into every /execute and /exec call."""
    limits = getattr(session, "resource_limits", None) or {}
    env = limits.get("template_env") if isinstance(limits, dict) else None
    if not isinstance(env, dict):
        return {}
    return {str(k): str(v) for k, v in env.items()}


@router.get("/{session_id}/files")
async def list_session_files(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List files uploaded into the session workspace (sandbox-visible)."""
    session = await _load_operable_session(db, session_id, current_user)
    workspace = _session_workspace_or_400(session)
    from app.services import session_files as sf

    return {"session_id": str(session_id), "files": sf.list_files(workspace)}


@router.post("/{session_id}/files")
async def upload_session_file(
    session_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a file into the session workspace (DEK-encrypted at rest).

    The sandbox reads it from ``workspace/files/<name>``; when the session
    has a workspace DEK the content is encrypted exactly like provision-time
    workspace data and is decrypted by sandbox code via ``CDS_DEK_HEX``.
    """
    session = await _load_operable_session(db, session_id, current_user)
    _active_or_suspended(session)
    workspace = _session_workspace_or_400(session)
    dek = await _session_dek(session)

    data = await file.read()
    from app.services import session_files as sf

    try:
        meta = sf.write_file(workspace, file.filename or "", data, dek=dek)
    except sf.SessionFileError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="sandbox.file_upload", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"filename": meta["filename"], "size": meta["size"], "encrypted": meta["encrypted"]},
    )
    await db.flush()
    return {"session_id": str(session_id), **meta}


@router.get("/{session_id}/files/{filename}")
async def download_session_file(
    session_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download a workspace file — content passes the T5 output review first.

    Blocked (critical DLP finding) content is NEVER released; the response is
    409 with the review findings instead.
    """
    from fastapi import Response

    session = await _load_operable_session(db, session_id, current_user)
    workspace = _session_workspace_or_400(session)
    dek = await _session_dek(session)

    from app.services import session_files as sf

    try:
        content, meta = sf.read_file(workspace, filename, dek=dek)
    except sf.SessionFileError as e:
        status_code = 404 if "not found" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))

    # T5 output review on the decoded text (critical findings block release).
    from app.services.output_inspection import output_inspector

    text = content.decode("utf-8", errors="replace")
    review = output_inspector.inspect(
        text,
        user_id=str(current_user.id),
        session_id=str(session.id),
        sandbox_mode=getattr(session, "sandbox_mode", None) or "structured_query",
    )
    findings = list(getattr(review, "findings", []) or [])

    def _sev_value(f) -> str:
        sev = getattr(f, "severity", "")
        return getattr(sev, "value", None) or str(sev)

    blocked = any(_sev_value(f).lower() == "critical" for f in findings)
    if blocked:
        await audit_service.log(
            db, action="sandbox.file_download_blocked", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"filename": filename, "findings": len(findings)},
        )
        await db.flush()
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Output review blocked the file download",
                "filename": filename,
                "findings_count": len(findings),
                "findings": [
                    {"severity": _sev_value(f),
                     "type": getattr(getattr(f, "type", None), "value", None) or str(getattr(f, "type", "")),
                     "message": getattr(f, "message", "")}
                    for f in findings[:20]
                ],
            },
        )

    await audit_service.log(
        db, action="sandbox.file_download", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"filename": filename, "size": len(content), "findings": len(findings)},
    )
    await db.flush()

    # Round 40: non-critical findings are REDACTED, not blocked — clean text
    # is rewritten through the inspector's redaction (e.g. [REDACTED:email]);
    # binary content cannot be redacted reliably, so it is released as-is and
    # the review header discloses the findings. Critical findings still 409
    # above (never released).
    review_header = f"passed; findings={len(findings)}"
    if findings:
        redacted = getattr(review, "redacted_output", None)
        try:
            content.decode("utf-8")
            is_text = True
        except UnicodeDecodeError:
            is_text = False
        if is_text and redacted is not None:
            content = redacted.encode("utf-8")
            review_header = f"redacted; findings={len(findings)}"
            await audit_service.log(
                db, action="sandbox.file_download_redacted", resource_type="sandbox_session",
                user_id=current_user.id, session_id=session.id,
                detail={"filename": filename, "findings": len(findings)},
            )
            await db.flush()

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-CDS-Output-Review": review_header,
    }
    return Response(content=content, media_type="application/octet-stream", headers=headers)


@router.delete("/{session_id}/files/{filename}")
async def delete_session_file(
    session_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _load_operable_session(db, session_id, current_user)
    workspace = _session_workspace_or_400(session)
    from app.services import session_files as sf

    removed = sf.delete_file(workspace, filename)
    if not removed:
        raise HTTPException(status_code=404, detail=f"file not found: {filename}")
    await audit_service.log(
        db, action="sandbox.file_delete", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"filename": filename},
    )
    await db.flush()
    return {"session_id": str(session_id), "deleted": filename}


@router.get("/{session_id}/snapshots")
async def list_session_snapshots(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _load_operable_session(db, session_id, current_user)
    workspace = _session_workspace_or_400(session)
    from app.services import session_snapshots as ss

    return {"session_id": str(session_id), "snapshots": ss.list_snapshots(workspace)}


@router.post("/{session_id}/snapshots")
async def create_session_snapshot(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Snapshot the session workspace (content is DEK-encrypted at rest)."""
    session = await _load_operable_session(db, session_id, current_user)
    _active_or_suspended(session)
    workspace = _session_workspace_or_400(session)
    from app.services import session_snapshots as ss

    try:
        manifest = ss.create_snapshot(workspace, session_id=str(session.id))
    except ss.SnapshotError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="sandbox.snapshot_create", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"snapshot_id": manifest["snapshot_id"], "bytes": manifest["bytes"],
                "file_count": manifest["file_count"]},
    )
    await db.flush()
    return {"session_id": str(session_id), **manifest}


@router.post("/{session_id}/snapshots/{snapshot_id}/rollback")
async def rollback_session_snapshot(
    session_id: uuid.UUID,
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Restore the workspace from a snapshot (current workspace content is
    discarded — the archive checksum is verified before any deletion)."""
    session = await _load_operable_session(db, session_id, current_user)
    _active_or_suspended(session)
    workspace = _session_workspace_or_400(session)
    from app.services import session_snapshots as ss

    try:
        result = ss.rollback(workspace, snapshot_id)
    except ss.SnapshotError as e:
        status_code = 404 if "not found" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))

    await audit_service.log(
        db, action="sandbox.snapshot_rollback", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"snapshot_id": snapshot_id, "discarded_files": result.get("discarded_files", 0)},
    )
    await db.flush()
    return {"session_id": str(session_id), **result}


@router.delete("/{session_id}/snapshots/{snapshot_id}")
async def delete_session_snapshot(
    session_id: uuid.UUID,
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _load_operable_session(db, session_id, current_user)
    workspace = _session_workspace_or_400(session)
    from app.services import session_snapshots as ss

    removed = ss.delete_snapshot(workspace, snapshot_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"snapshot not found: {snapshot_id}")
    await audit_service.log(
        db, action="sandbox.snapshot_delete", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"snapshot_id": snapshot_id},
    )
    await db.flush()
    return {"session_id": str(session_id), "deleted": snapshot_id}


@router.post("/{session_id}/pause")
async def pause_sandbox_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pause a session (READY/RUNNING → SUSPENDED): execution is gated while
    workspace, files, snapshots and keys are all preserved."""
    session = await _load_operable_session(db, session_id, current_user)
    current = SessionStatus(session.status)
    if not session_state_machine.is_active(current):
        raise HTTPException(status_code=400, detail=f"Session in terminal state (status: {session.status})")
    result = session_state_machine.validate_transition(current, SessionStatus.SUSPENDED)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    session.pre_pause_status = session.status
    session.status = SessionStatus.SUSPENDED.value
    await db.flush()
    await db.refresh(session)
    await audit_service.log(
        db, action="sandbox.pause", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"pre_pause_status": session.pre_pause_status},
    )
    return SandboxSessionResponse.model_validate(session)


@router.post("/{session_id}/resume")
async def resume_sandbox_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resume a paused session → restore the pre-pause status (fallback READY)."""
    session = await _load_operable_session(db, session_id, current_user)
    current = SessionStatus(session.status)
    if current != SessionStatus.SUSPENDED:
        raise HTTPException(status_code=400, detail=f"Session not suspended (status: {session.status})")

    target_raw = session.pre_pause_status or SessionStatus.READY.value
    try:
        target = SessionStatus(target_raw)
    except ValueError:
        target = SessionStatus.READY
    if target not in (SessionStatus.READY, SessionStatus.RUNNING):
        target = SessionStatus.READY
    result = session_state_machine.validate_transition(current, target)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)

    session.status = target.value
    session.pre_pause_status = None
    await db.flush()
    await db.refresh(session)
    await audit_service.log(
        db, action="sandbox.resume", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"resumed_to": target.value},
    )
    return SandboxSessionResponse.model_validate(session)


@router.post("/{session_id}/refreshes")
async def refresh_sandbox_session(
    session_id: uuid.UUID,
    extend_seconds: int | None = Body(None, embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Extend the session expiry (default: one more timeout_seconds window).

    The background lifecycle sweep computes expiry from
    ``created_at + timeout_seconds + extended_seconds``; total extension is
    capped at ``CDS_SESSION_MAX_EXTENDED_SECONDS``.
    """
    from app.core.config import get_settings

    settings = get_settings()
    session = await _load_operable_session(db, session_id, current_user)
    _active_or_suspended(session)

    extend = int(session.timeout_seconds) if extend_seconds is None else int(extend_seconds)
    if extend <= 0:
        raise HTTPException(status_code=400, detail="extend_seconds must be positive")
    current_ext = int(session.extended_seconds or 0)
    if current_ext + extend > settings.SESSION_MAX_EXTENDED_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(f"total extension would exceed the cap "
                    f"({settings.SESSION_MAX_EXTENDED_SECONDS}s); current={current_ext}s"),
        )
    session.extended_seconds = current_ext + extend
    await db.flush()
    await db.refresh(session)
    await audit_service.log(
        db, action="sandbox.refresh", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"extend_seconds": extend, "extended_seconds": session.extended_seconds},
    )
    return SandboxSessionResponse.model_validate(session)


# ════════════════════════════════════════════════════════════════
# Round 40 (usability): exec / logs / usage
# ════════════════════════════════════════════════════════════════


@router.post("/{session_id}/exec")
async def exec_in_sandbox(
    session_id: uuid.UUID,
    body: SandboxExecRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run a shell command inside the live sandbox workspace (Round 40).

    The interactive building block (CubeSandbox-style exec API): short
    hard-capped timeout, captured output passes the same T5 DLP review as
    ``/execute`` before release. ``CDS_DEK_HEX`` is injected so shell code
    can decrypt uploaded files; the raw session key is NOT injected into
    interactive shells.
    """
    from app.core.config import get_settings

    settings = get_settings()
    session = await _load_operable_session(db, session_id, current_user)
    current = SessionStatus(session.status)
    if not session_state_machine.is_active(current):
        raise HTTPException(status_code=400, detail=f"Session in terminal state (status: {session.status})")
    if session.status != SessionStatus.RUNNING.value:
        raise HTTPException(status_code=400, detail=f"Session not running (status: {session.status})")
    if not session.container_id:
        raise HTTPException(status_code=400, detail="Session has no container")

    timeout = body.timeout_seconds or settings.SESSION_EXEC_TIMEOUT_SECONDS
    timeout = max(1, min(int(timeout), int(settings.SESSION_EXEC_TIMEOUT_SECONDS)))

    env_vars = {
        "CDS_SESSION_ID": str(session_id),
        "CDS_SANDBOX_LEVEL": session.sandbox_level,
        "CDS_USER_ID": str(current_user.id),
    }
    enc_config = get_encryption_config_for_session(str(session_id))
    if enc_config:
        env_vars["CDS_DEK_HEX"] = enc_config["dek_hex"]
    env_vars.update(_session_template_env(session))

    runtime = await get_sandbox_manager()
    started = datetime.now(timezone.utc)
    exec_result = await _execute_runtime_with_context(
        runtime,
        session.container_id,
        body.command,
        "bash",
        env_vars=env_vars,
        timeout=timeout,
    )
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    output = exec_result.get("output") or ""
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False, default=str)
    max_chars = int(settings.SESSION_EXEC_MAX_OUTPUT_CHARS)
    truncated = len(output) > max_chars
    if truncated:
        output = output[:max_chars]

    # T5 DLP review on the captured output (same gateway semantics as /execute).
    sandbox_mode = getattr(session, "sandbox_mode", None) or "structured_query"
    security_report: dict = {"passed": True, "blocked": False, "findings": [], "findings_count": 0}
    output_blocked = False
    if output:
        try:
            from app.services.output_security import (
                inspect_text_output,
                inspection_to_report,
                should_block,
            )

            inspection = inspect_text_output(
                output,
                user_id=str(current_user.id),
                session_id=str(session.id),
                sandbox_mode=sandbox_mode,
            )
            report = inspection_to_report(inspection)
            if should_block(inspection):
                output = ""
                output_blocked = True
            else:
                output = inspection.redacted_output or ""
            security_report = report
        except Exception as e:
            output = ""
            output_blocked = True
            security_report = {"passed": False, "blocked": True, "error": str(e)}

    await audit_service.log(
        db, action="sandbox.exec", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={
            "command_sha256": hashlib.sha256(body.command.encode("utf-8")).hexdigest(),
            "command_preview": body.command[:500],
            "timeout_seconds": timeout,
            "exit_code": exec_result.get("exit_code"),
            "output_truncated": truncated,
            "output_blocked": output_blocked,
        },
    )
    await db.flush()

    return {
        "session_id": str(session_id),
        "command": body.command,
        "exit_code": exec_result.get("exit_code", -1),
        "output": output,
        "output_truncated": truncated,
        "output_blocked": output_blocked,
        "blocked_reason": "output_inspection_blocked" if output_blocked else None,
        "duration_ms": duration_ms,
        "timeout_seconds": timeout,
        "security_report": security_report,
    }


@router.get("/{session_id}/logs")
async def get_session_logs(
    session_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    since: str | None = Query(None, max_length=64),
    action: str | None = Query(None, max_length=128),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Tail the session's audit trail (Round 40 log-polling stream).

    Poll with ``since=<previous latest created_at>`` for follow-style
    streaming; ``action`` filters (e.g. ``sandbox.exec``). In-sandbox
    activity events (data_access/network/application) remain on
    ``GET /{session_id}/audit``.
    """
    session = await _load_operable_session(db, session_id, current_user)

    query = select(AuditLog).where(AuditLog.session_id == session_id)
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid since timestamp: {since!r}")
        query = query.where(AuditLog.created_at >= since_dt)
    if action:
        query = query.where(AuditLog.action == action)
    query = query.order_by(AuditLog.created_at.desc()).limit(limit)

    rows = (await db.execute(query)).scalars().all()
    return {
        "session_id": str(session_id),
        "logs": [
            {
                "id": str(row.id),
                "action": row.action,
                "detail": row.detail,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "user_id": str(row.user_id) if row.user_id else None,
            }
            for row in rows
        ],
        "count": len(rows),
        "latest_created_at": rows[0].created_at.isoformat() if rows and rows[0].created_at else None,
    }


@router.get("/{session_id}/usage")
async def get_session_usage(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Workspace / files / snapshots usage snapshot for one session (Round 40)."""
    session = await _load_operable_session(db, session_id, current_user)
    workspace = _session_workspace_or_400(session)

    from app.services import session_files as sf
    from app.services import session_snapshots as ss

    workspace_files = 0
    workspace_bytes = 0
    for p in workspace.rglob("*"):
        if p.is_file():
            workspace_files += 1
            try:
                workspace_bytes += p.stat().st_size
            except OSError:  # pragma: no cover - racing deletion
                pass

    files = sf.list_files(workspace)
    snapshots = ss.list_snapshots(workspace)

    return {
        "session_id": str(session_id),
        "status": session.status,
        "workspace": {"files": workspace_files, "bytes": workspace_bytes},
        "uploaded_files": {"count": len(files), "bytes": sum(int(f["size"]) for f in files)},
        "snapshots": {
            "count": len(snapshots),
            "bytes": sum(int(s.get("bytes") or 0) for s in snapshots),
        },
        "timeout": {
            "timeout_seconds": session.timeout_seconds,
            "extended_seconds": int(getattr(session, "extended_seconds", 0) or 0),
        },
    }
