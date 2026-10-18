import json
import logging
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse

import asyncio

from app.core.errors import CDSError, OperationLocked, QuotaExceeded, RateLimited, RequestTimeout, SessionNotFound, SessionStateConflict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user, require_roles
from app.models.user import User, UserRole
from app.models.data_product import DataProduct
from app.models.contract import Contract, ContractStatus
from app.models.sandbox_session import SandboxSession, SessionStatus, SandboxLevel
from app.models.audit_log import AuditLog
from app.schemas.sandbox_session import SandboxExecRequest, SandboxExecuteRequest, SandboxSessionCreate, SandboxSessionResponse
from app.services.audit_service import audit_service
from app.services.kms_service import kms_service
from app.services.compliance_report import disclosure_fields
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
    from app.schemas.sandbox_session import SANDBOX_MODE_ALIASES

    aliases = SANDBOX_MODE_ALIASES.get(session_mode, {session_mode})
    return bool(aliases.intersection({str(mode).strip().lower() for mode in allowed_modes}))


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
    db=None,
) -> dict:
    import inspect

    params = inspect.signature(runtime.execute).parameters
    kwargs = {}
    optional = {"session_key": session_key, "env_vars": env_vars, "timeout": timeout}
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    for name, value in optional.items():
        if accepts_kwargs or name in params:
            kwargs[name] = value

    if db is not None and container_id.startswith("l0-"):
        from uuid import UUID as _UUID

        from app.services import shared_volumes

        try:
            binds = await shared_volumes.resolve_binds(
                db, _UUID(container_id.removeprefix("l0-"))
            )
        except ValueError:
            binds = []
        if binds and ("extra_binds" in params or accepts_kwargs):
            kwargs["extra_binds"] = binds
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
    current_user: User = Depends(get_business_user),
):
    # Verify data product exists
    result = await db.execute(select(DataProduct).where(DataProduct.id == body.data_product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Data product not found")
    if product.status != "published":
        raise HTTPException(status_code=400, detail="Data product is not published")

    contract = await _validate_session_contract(db, body, current_user)

    # SS-13 single-path cutover: legacy sandbox levels (L0/L1/L2/L3/k8s) are
    # retired when the legacy gate is off — honest 400 (W2), never a silent
    # fallback to another level.
    if body.sandbox_level != "l2c":
        from app.core.config import get_settings
        from app.core.errors import CDSError

        _legacy_settings = get_settings()
        if not (_legacy_settings.SANDBOX_LEGACY_ENABLED or _legacy_settings.LEGACY_L2_ENABLED):
            raise CDSError(
                f"sandbox level {body.sandbox_level} is retired (SS-13 single-path: l2c only)",
                code="LEGACY_LEVEL_RETIRED",
                http_status=400,
                detail={"reason": "legacy_level_retired", "requested_level": body.sandbox_level},
            )

    # Round 40 usability: validate the template name before anything is
    # provisioned so an unknown name cannot leave an orphaned container.
    # SS-10 CB-05: the field also accepts a CDS cube template id/name
    # (Round 40 JSON templates may declare ``cube_template_id``).
    if body.template:
        from app.services import session_templates as st
        from app.services.cube_template_service import cube_template_service

        if st.get_template(body.template) is None and (
            await cube_template_service.resolve_cube_template(db, body.template) is None
        ):
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
        raise RateLimited(
            f"并发会话数已达上限 ({MAX_ACTIVE_SESSIONS})，请先终止已有会话",
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
        idle_policy=body.idle_policy,
        auto_resume=body.auto_resume,
        tee_policy=body.tee_policy,
        trust_root="none",  # SS-13: overwritten below once the cube node is selected
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
        raise QuotaExceeded(f"资源配额不足: {reason}")

    # L2' (Cube) data-plane gate (SS-10 CB-01): honest rejection when the
    # dataplane is off or no eligible node exists → 400 RUNTIME_UNAVAILABLE.
    cube_node = None
    if body.sandbox_level == "l2c":
        from app.core.config import get_settings
        from app.core.errors import CDSError
        from app.services.cube_runtime_adapter import select_cube_node, pinned_cube_version

        if not get_settings().CUBE_DATAPLANE_ENABLED:
            raise CDSError(
                "MicroVM 沙箱数据面未启用或未就绪（需先完成 Cube 计算节点部署并开启"
                " CDS_CUBE_DATAPLANE_ENABLED）；按 SS-13 不降级到进程隔离执行",
                code="RUNTIME_UNAVAILABLE",
                http_status=400,
                detail={"reason": "dataplane_disabled"},
            )
        # SS-13: tee_policy=tee_required narrows selection to TEE-capable
        # nodes (capabilities.tee in {tdx, sev_snp}); no such node → honest
        # 400 TEE_NODE_UNAVAILABLE.
        require_tee = body.tee_policy == "tee_required"
        cube_node = await select_cube_node(
            db, runtime_version=pinned_cube_version(), template_id=body.template,
            require_tee=require_tee,
        )
        if cube_node is None:
            if require_tee:
                raise CDSError(
                    "no TEE-capable cube node is available (tee_policy=tee_required)",
                    code="TEE_NODE_UNAVAILABLE",
                    http_status=400,
                    detail={"reason": "no_tee_node", "tee_policy": body.tee_policy},
                )
            raise CDSError(
                "no eligible cube node (no online node with a matching runtime_version + cubelet endpoint)",
                code="RUNTIME_UNAVAILABLE",
                http_status=400,
                detail={"reason": "no_eligible_node"},
            )
        # SS-13: persist the per-session trust root from the selected node's
        # TEE capability — honest: missing/malformed capability means "none"
        # (no per-VM TEE), never an upgraded claim.
        _caps = cube_node.get("capabilities") or {}
        _tee = _caps.get("tee", "none") if isinstance(_caps, dict) else "none"
        session.trust_root = _tee if isinstance(_tee, str) else "none"

    # Provision sandbox container
    runtime = await get_sandbox_manager()
    # N7: k8s sessions attach shared volumes as RWX PVCs (host binds are
    # bwrap-only); resolve mounts up front so the pod spec carries them.
    volume_mounts = None
    if body.sandbox_level == "k8s":
        try:
            from app.services import shared_volumes
            volume_mounts = await shared_volumes.resolve_mounts(db, session.id)
        except Exception as e:
            logger.warning("[Session] shared volume mount resolution failed: %s", e)
            volume_mounts = []
    volume_dek_ref = None
    # SS-14 RT-07: 会话数据（l2c 产品数据注入源）。无条件初始化 —— 注入块在
    # l2c 分支之外引用它（见下方 inject_session_data 处），非 Cube 适配器
    # （测试 dummy/未来运行时）不能让它未绑定（UnboundLocalError 实测）。
    session_data: bytes | None = None
    if body.sandbox_level == "l2c":
        from app.models.data_resource import DataResource
        from app.services.cube_runtime_adapter import CubeRuntimeAdapter
        from app.services.storage_service import storage_service

        adapter = runtime.get_adapter(body.sandbox_level)
        if isinstance(adapter, CubeRuntimeAdapter):
            if product.resource_id is not None:
                res_row = await db.execute(
                    select(DataResource).where(DataResource.id == product.resource_id)
                )
                res = res_row.scalar_one_or_none()
                if res is not None and res.storage_path:
                    # 同步 MinIO 下载 + KMS 解密走 to_thread, 不占事件循环。
                    session_data = await asyncio.to_thread(
                        storage_service.download, res.storage_path
                    )
            if session_data is None:
                session.status = SessionStatus.FAILED.value
                session.error_message = ("l2c session requires a published product "
                                         "with an uploaded data resource (fail-closed)")
                db.add(session)
                await db.commit()
                raise CDSError(
                    "l2c session requires a published product with an uploaded "
                    "data resource (fail-closed)",
                    code="INTERNAL_ERROR",
                    http_status=500,
                )
            # SS-14 RT-07 (2026-10-14): 卷预建 + DEK ref 异步准备（卷面 httpx 走
            # 线程、DEK 回查走主循环, 见 CubeRuntimeAdapter.prepare_session_volume）。
            try:
                volume_dek_ref = await adapter.prepare_session_volume(
                    str(session.id), session_data
                )
            except CDSError as exc:
                session.status = SessionStatus.FAILED.value
                session.error_message = str(exc)
                db.add(session)
                await db.commit()
                raise

    # SS-14 RT-07 (2026-10-14): 同步 provision（内含 gRPC create）必须离事件循环 ——
    # cubelet 建沙箱时插件 Attach 会回调本 API 的 KMS /distribute（解 DEK）;
    # 若 gRPC 阻塞事件循环则循环依赖死锁（插件 20s 超时 → Attach 拒绝）。
    # DB 访问已全部走 SyncDbBridge（独立 loop+引擎）, 跨线程 native 段错误源
    # （_run_sync 新 loop 复用共享 asyncpg 引擎）已消除。
    # Provisioning callbacks are cross-connection: the cubelet plugin calls
    # back into this API's /kms/distribute (and the egress policy loader reads
    # AppCredential/Contract rows) while this request is still open, so the
    # session row must be committed before the provision call — an uncommitted
    # row is invisible to those readers (and a nested service session closing
    # its own connection would discard it).
    await db.commit()

    provision_result = await asyncio.to_thread(
        runtime.provision,
        session_id=session.id,
        level=body.sandbox_level,
        data_path="",
        timeout=body.timeout_seconds,
        user_id=str(current_user.id),
        volume_mounts=volume_mounts,
        cube_node=cube_node,
        volume_dek_ref=volume_dek_ref,
    )
    session.container_id = provision_result.get("container_id")
    session.status = provision_result.get("status", SessionStatus.RUNNING.value)
    from app.core.metrics import record_session_transition

    record_session_transition(SessionStatus.PROVISIONING.value, session.status)
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
        from app.core.metrics import record_kms_distribution

        record_kms_distribution("rejected")
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
    from app.core.metrics import record_kms_distribution

    record_kms_distribution("success" if distributed_key else "failed")
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

    # SS-14 RT-07 (2026-10-14): 产品数据注入 VM。上游冷启动沙箱不把声明卷
    # 挂进 guest（2026-10-14 实证: /sandbox/data 仅为可写 rootfs 里的普通目录）, 会话
    # 数据卷是节点侧 SM4 加密副本, VM 内可用副本经 exec 通道注入
    # /sandbox/data/session-data（SS-18 口径）。有界重试; 失败 = fail-closed 终止会话（l2c 会话
    # 的数据面是会话契约的一部分, 不静默降级）。
    if body.sandbox_level == "l2c" and session_data is not None:
        try:
            await adapter.inject_session_data(session.container_id, session_data)
        except Exception as e:
            inject_error = e
        else:
            inject_error = None
        if inject_error is not None:
            kms_service.destroy_key(session_key_result["key_id"])
            try:
                runtime.terminate(session.container_id)
            except Exception as e:
                logger.warning("[sandbox] Container rollback after data injection failure failed: %s", e)
            release_tenant_usage(
                str(current_user.id),
                cpu_cores=limits["cpu_cores"],
                memory_mb=limits["memory_mb"],
                disk_mb=limits["disk_mb"],
            )
            session.container_id = None
            session.status = SessionStatus.FAILED.value
            session.error_message = f"Session data injection into sandbox failed: {inject_error}"
            await db.flush()
            await db.refresh(session)
            await audit_service.log(
                db, action="sandbox.data_injection_failed", resource_type="sandbox_session",
                user_id=current_user.id, session_id=session.id,
                detail={"sandbox_level": body.sandbox_level, "error": str(inject_error)},
            )
            return SandboxSessionResponse.model_validate(session)

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
    await network_policy_engine.create_policy(str(session.id), policy_config, db=db)

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
    limit: int = Query(20, ge=1, le=500),
    status: str | None = Query(None),
    cursor: str | None = Query(None),
    response: Response = None,  # noqa: B008 — FastAPI header-injection parameter
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    base = select(SandboxSession)
    count_base = select(func.count()).select_from(SandboxSession)
    if not _can_view_all_sessions(current_user):
        base = base.where(SandboxSession.user_id == current_user.id)
        count_base = count_base.where(SandboxSession.user_id == current_user.id)
    if status:
        base = base.where(SandboxSession.status == status)
        count_base = count_base.where(SandboxSession.status == status)

    # W8: opt-in keyset mode — response envelope unchanged, paging info goes
    # to the X-Next-Cursor header (total is not computed in cursor mode).
    if cursor is not None:
        from app.core.pagination import paginate_keyset

        try:
            rows, next_cursor = await paginate_keyset(db, SandboxSession, cursor=cursor, limit=limit)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid pagination cursor")
        if next_cursor:
            response.headers["X-Next-Cursor"] = next_cursor
        return {
            "items": [SandboxSessionResponse.model_validate(s) for s in rows],
            "total": None,
            "page": None,
            "page_size": limit,
        }

    total_result = await db.execute(count_base)
    total = total_result.scalar() or 0

    query = base.offset(skip).limit(limit)
    result = await db.execute(query)
    items = [SandboxSessionResponse.model_validate(s) for s in result.scalars().all()]
    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.get("/session-templates")
async def list_session_templates(
    current_user: User = Depends(get_business_user),
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
    current_user: User = Depends(get_business_user),
):
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise SessionNotFound("Sandbox session not found")
    if session.user_id != current_user.id and not _can_view_all_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")
    return SandboxSessionResponse.model_validate(session)


@router.get("/{session_id}/proof-bundle")
async def get_sandbox_session_proof_bundle(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
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
    # PZ-06 (SS-11 §3.1): disclosure metadata as a sibling of evidence.
    # Added AFTER evidence_hash so the evidence input set (and therefore the
    # evidence_hash) stays unchanged; bundle_hash covers the full payload.
    payload["bundle_meta"] = {
        **disclosure_fields(),
        "dp_applied": output_detail.get("dp_applied"),
        "is_simulation": getattr(session, "is_simulation", None),
    }
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
    current_user: User = Depends(get_business_user),
):
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise SessionNotFound("Sandbox session not found")
    if session.user_id != current_user.id and not _can_operate_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")

    # Validate state transition via state machine
    current = SessionStatus(session.status)
    result = session_state_machine.validate_transition(current, SessionStatus.TERMINATED)
    if not result.success:
        raise SessionStateConflict(result.error)

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

    # Destroy sandbox container — SS-14 RT-07 (2026-10-14): to_thread, 销毁时插件
    # Detach/Destroy 会回调本 API KMS（DEK revoke）, 占事件循环 = 循环依赖死锁。
    if session.container_id:
        runtime = await get_sandbox_manager()
        await asyncio.to_thread(runtime.terminate, session.container_id)

    # Clean up network policy enforcement
    await network_policy_engine.remove_policy(str(session.id), db=db)
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
    current_user: User = Depends(get_business_user),
):
    """Update the network policy for a sandbox session.

    Modes:
    - deny_all: No network access (default)
    - allowlist: Only specified IPs/domains/ports are accessible
    """
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise SessionNotFound("Sandbox session not found")
    if session.user_id != current_user.id and not _can_operate_sessions(current_user):
        raise HTTPException(status_code=403, detail="Not your session")
    if session.status not in [SessionStatus.RUNNING.value, SessionStatus.READY.value]:
        raise SessionStateConflict(f"Session not running (status: {session.status})")

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
    await network_policy_engine.remove_policy(str(session.id), db=db)
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
        policy_result = await network_policy_engine.create_policy(str(session.id), config, db=db)

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
    current_user: User = Depends(get_business_user),
):
    """Get or initialize the zero-trust network policy for a sandbox session."""
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise SessionNotFound("Sandbox session not found")
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
        await network_policy_engine.create_policy(str(session.id), NetworkPolicyConfig(mode="deny_all"), db=db)

    return NetworkPolicyResponse.model_validate(net_policy)


@router.post("/{session_id}/execute")
async def execute_in_sandbox(
    session_id: uuid.UUID,
    body: SandboxExecuteRequest | None = Body(None),
    code: str | None = Query(None),
    language: str = Query("python"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
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
        raise SessionNotFound("Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your session")
    await _maybe_auto_resume(session, db)
    # State machine validation: only RUNNING sessions can execute
    current = SessionStatus(session.status)
    if not session_state_machine.is_active(current):
        raise SessionStateConflict(f"Session in terminal state (status: {session.status})")
    if session.status != SessionStatus.RUNNING.value:
        raise SessionStateConflict(f"Session not running (status: {session.status})")
    if not session.container_id:
        raise HTTPException(status_code=400, detail="Session has no container")

    # Static code analysis before execution (SS-03 §5)
    from app.services.code_scanner import code_scanner
    sandbox_mode = getattr(session, "sandbox_mode", None) or "structured_query"
    scan_code = exec_code
    trusted_runner = False
    if body is not None and body.trusted_runner:
        from app.services.notebook_service import split_trusted_driver

        trusted = split_trusted_driver(exec_code)
        if trusted is not None:
            scan_code = trusted["code"]
            trusted_runner = True
    scan_result = code_scanner.scan(scan_code, exec_language, sandbox_mode)
    if not scan_result.passed:
        fatal_issues = [i for i in scan_result.issues if i.severity.value == "FATAL"]
        await audit_service.log_standalone(
            action="sandbox.code_scan_rejected", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"language": exec_language, "trusted_runner": trusted_runner, "issues": [{"code": i.code, "message": i.message, "line": i.line} for i in fatal_issues]},
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
        from app.core.metrics import record_kms_distribution

        record_kms_distribution("success" if session_key else "failed")
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

    # SS-15：Notebook 单元格驱动需要“会话常驻 kernel”。bwrap 每次 exec 都是一个新 namespace，
    # 单元格自己拉起的子进程活不到下一个单元格，所以 kernel 由运行面按会话拉起。
    # 这里只“准备” kernel，不执行任何用户代码：用户代码仍然只走下面这一条策略通路。
    if body is not None and body.trusted_runner and exec_language == "python":
        kernel_info = await runtime.ensure_session_kernel(
            session.container_id,
            env_vars=dict(env_vars) if env_vars else None,
            timeout=session.timeout_seconds,
        )
        await audit_service.log(
            db,
            action="sandbox.session_kernel",
            resource_type="sandbox_session",
            user_id=current_user.id,
            session_id=session.id,
            detail={
                "level": session.sandbox_level,
                "supported": kernel_info.get("supported"),
                "ok": kernel_info.get("ok"),
                "state": kernel_info.get("state"),
                "error": str(kernel_info.get("error") or "")[:400],
                "seccomp": kernel_info.get("seccomp"),
                "kernel_id": str((kernel_info.get("kernel") or {}).get("kernel_id") or ""),
            },
        )
        if not kernel_info.get("ok"):
            logger.warning("session kernel not ready for %s: %s",
                           session_id, kernel_info.get("state") or kernel_info.get("error"))

    exec_result = await _execute_runtime_with_context(
        runtime,
        session.container_id,
        prepared_code,
        exec_language,
        session_key=session_key,
        env_vars=env_vars,
        timeout=session.timeout_seconds,
        db=db,
    )
    exec_result = await scene_runtime.post_execute(exec_result, scene_context)

    # Update session status if execution failed
    exec_timed_out = (
        exec_result.get("exit_code", 0) != 0
        and "timed out" in (exec_result.get("output") or "").lower()
    )
    if exec_timed_out:
        session.status = SessionStatus.FAILED.value
        session.error_message = "Execution timed out"
        await db.flush()

    # SS-14 RT-07 (2026-10-14): l2c 无 in-VM 审计 helper（bwrap 专属），
    # 控制面在每次执行后写 application/code_execution 活动事件到同一
    # tmpfs jsonl（/audit fallback 读此文件；ClickHouse 在场时由
    # collector 统一 flush）。
    if (session.sandbox_level or "") == "l2c":
        try:
            import tempfile as _tf

            from app.services.sandbox_audit import ActivityCategory, SandboxActivityEvent

            _al_path = Path(_tf.gettempdir()) / "cds-sandbox-audit" / f"{session_id}.jsonl"
            _al_path.parent.mkdir(parents=True, exist_ok=True)
            _ev = SandboxActivityEvent(
                session_id=str(session_id),
                user_id=str(current_user.id),
                sandbox_level="l2c",
                category=ActivityCategory.APPLICATION.value,
                event_type="code_execution",
                detail={
                    "language": exec_language,
                    "exit_code": exec_result.get("exit_code"),
                    "output_size": len(exec_result.get("output") or ""),
                    "runtime": "cubesandbox",
                },
                duration_ms=exec_result.get("duration_ms") or 0,
            )
            with open(_al_path, "a", encoding="utf-8") as _fh:
                _fh.write(_ev.to_json() + "\n")
            logger.info(f"[sandbox-audit] l2c activity event written: {_al_path.name}")
        except Exception as e:
            logger.warning(f"[sandbox-audit] l2c activity event failed: {e}")

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
            from app.core.metrics import record_output_inspection

            if should_block(inspection):
                exec_result["output"] = ""
                exec_result["output_blocked"] = True
                exec_result["blocked_reason"] = "output_inspection_blocked"
                record_output_inspection("blocked")
            else:
                exec_result["output"] = inspection.redacted_output or ""
                exec_result["output_blocked"] = False
                record_output_inspection("redacted" if inspection.redacted_output != output else "passed")
            report["no_output"] = False
            exec_result["security_report"] = report
        else:
            # 空输出不等于"审查通过"：passed 只说明没有可审查的内容，
            # 顶层 no_output 让调用方无法把它读成一次干净的审查。
            exec_result["security_report"] = {
                "passed": True,
                "blocked": False,
                "no_output": True,
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

    # W2 (PZ-15): a timed-out synchronous wait is a 408 REQUEST_TIMEOUT — the
    # operation may still have finished in the sandbox; query before retrying.
    if exec_timed_out:
        raise RequestTimeout(
            "Execution timed out",
            detail={"timed_out_after_seconds": session.timeout_seconds},
        )

    return exec_result


@router.get("/{session_id}/audit")
async def get_sandbox_audit_logs(
    session_id: uuid.UUID,
    category: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Get sandbox activity audit logs for a session.

    Returns in-sandbox activity events: data access, network, application activity.
    Filter by category: data_access, network, application.
    """
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise SessionNotFound("Sandbox session not found")
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
        raise SessionNotFound("Sandbox session not found")
    if not _can_operate_session(current_user, session):
        raise HTTPException(status_code=403, detail="Not your session")
    await _maybe_auto_resume(session, db)
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


# SS-14 RT-07 (2026-10-14) / SS-18 (2026-10-16): l2c 会话文件面 —— cube MicroVM 无宿主 workspace,
# 文件全在 VM 内, 经 exec 通道读写（exec 输出经 GUEST_EXEC_DIR 中转后由节点插件读回）。
# SS-18 后的目录口径: /sandbox/output = 成果交付采集面（原型唯一可出域目录）,
# /scratch = 工作区（兼容期仍参与采集, 逐文件标源目录）, /sandbox/data = 授权数据（不采集）。
# 注: “不采集”不等于“写不了”—— 上游冷启动不把声明卷挂进 guest, 真只读约不到 hypervisor 层,
# 真正的出域约束由 T5 出口网关承担（诚实披露, 见 specs/ss-18-prototype-alignment.md §2）.
async def _l2c_file_op(session: SandboxSession, op: str, filename: str = "", data: bytes | None = None,
                       where: str = "") -> dict:
    from app.services import session_files as sf
    from app.services.cube_runtime_adapter import CubeRuntimeAdapter
    from app.services.sandbox_manager import get_sandbox_manager

    name = sf.validate_filename(filename) if filename else ""
    # SS-18 (2026-10-16): 目录口径收敛到 cube_runtime_adapter 的 GUEST_*/HARVEST_DIRS。
    #   where=""          读按优先级自动解析 (/sandbox/output → /scratch), 写落 /sandbox/output
    #   where="output"     仅 /sandbox/output（产物, 原型口径）
    #   where="workspace" 仅 /scratch（工作区, 兼容期）
    from app.services.cube_runtime_adapter import HARVEST_DIRS

    if where not in ("", "output", "workspace"):
        raise HTTPException(status_code=400, detail=f"unknown file dir: {where}")
    write_dir = {tag: d for d, tag in HARVEST_DIRS}[where or "output"]
    candidates = tuple(d for d, _tag in HARVEST_DIRS) if not where else (write_dir,)
    runtime = await get_sandbox_manager()
    adapter = runtime.get_adapter("l2c")
    if not isinstance(adapter, CubeRuntimeAdapter):
        raise HTTPException(status_code=400, detail="l2c data plane not enabled")
    if op == "upload":
        import base64

        b64 = base64.b64encode(data or b"").decode("ascii")
        # 256KB 上限: 经 exec 参数面传输, 超出应走数据卷（数据产品面）。
        if len(b64) > 256 * 1024:
            raise HTTPException(status_code=400, detail="file too large for the exec channel (>256KB); use data products for large files")
        code = ("import base64,os;"
                f"os.makedirs({write_dir!r},exist_ok=True);"
                f"open({write_dir!r}+'/{name}','wb').write(base64.b64decode('{b64}'))")
        result = await adapter.execute(session.container_id, code, "python", timeout=120)
    elif op == "download":
        code = ("import base64,os;"
                f"_c=[d+'/{name}' for d in {candidates!r} if os.path.isfile(d+'/{name}')];"
                "print(base64.b64encode(open(_c[0],'rb').read()).decode()) "
                "if _c else print('CDS404')")
        result = await adapter.execute(session.container_id, code, "python", timeout=120)
        if result.get("exit_code") != 0 or "error" in result:
            raise HTTPException(status_code=502, detail=f"l2c file download failed: {result.get('error') or result.get('output', '')[:200]}")
        out = (result.get("output") or "").strip()
        if out.startswith("CDS404"):
            raise HTTPException(status_code=404, detail="file not found")
        import base64 as _b64

        return {"data": _b64.b64decode(out.splitlines()[-1])}
    elif op == "list":
        code = ("import os;"
                f"_h={[(d, t) for d, t in HARVEST_DIRS if not where or d == write_dir]!r};"
                "_r=[t+'\\t'+n for d,t in _h if os.path.isdir(d) for n in sorted(os.listdir(d)) "
                "if os.path.isfile(os.path.join(d,n))];"
                "print(os.linesep.join(_r))")
        result = await adapter.execute(session.container_id, code, "python", timeout=60)
        out = (result.get("output") or "").strip()
        entries: list[dict] = []
        seen: set[str] = set()
        for line in out.splitlines():
            if not line.strip() or "\t" not in line:
                continue
            tag, fname = line.split("\t", 1)
            if fname in seen:  # 同名以采集优先级为准 (/sandbox/output 先)
                continue
            seen.add(fname)
            entries.append({"name": fname, "dir": tag})
        return {"files": [e["name"] for e in entries], "entries": entries}
    elif op == "delete":
        code = ("import os;"
                f"_c=[d+'/{name}' for d in {candidates!r} if os.path.isfile(d+'/{name}')];"
                "os.remove(_c[0]) if _c else __import__('sys').exit(3)")
        result = await adapter.execute(session.container_id, code, "python", timeout=60)
        if result.get("exit_code") == 3:
            raise HTTPException(status_code=404, detail="file not found")
        return {"deleted": name}
    else:  # pragma: no cover
        raise HTTPException(status_code=400, detail="unknown op")


async def _session_dek(session: SandboxSession) -> bytes | None:
    """Resolve the session workspace DEK (same chain as RAG corpora)."""
    from app.services.rag_service import _session_workspace_dek

    workspace = _session_workspace_or_400(session)
    return await _session_workspace_dek(workspace)


def _active_or_suspended(session: SandboxSession) -> None:
    current = SessionStatus(session.status)
    if not session_state_machine.is_active(current):
        raise SessionStateConflict(
            f"Session in terminal state (status: {session.status})"
        )


async def _maybe_auto_resume(session: SandboxSession, db: AsyncSession) -> None:
    """W9: wake an auto-resumable suspended session on interaction.

    Safety chain (order fixed): ① the governing contract must still be
    ACTIVE/SIGNED — a terminated contract means the data-access grant is
    gone and the session stays asleep (410, client stops retrying);
    ② transition back to the pre-pause state; ③ grant one fresh timeout
    window so the resumed session does not instantly re-expire (Round 39
    refreshes cap still applies).
    """
    if session.status != SessionStatus.SUSPENDED.value or not session.auto_resume:
        return

    from app.core.errors import SessionTerminated

    contract_active = False
    if session.contract_id:
        try:
            contract_uuid = uuid.UUID(str(session.contract_id))
        except ValueError:
            contract_uuid = None
        if contract_uuid is not None:
            from app.models.contract import Contract, ContractStatus

            c_result = await db.execute(select(Contract).where(Contract.id == contract_uuid))
            contract = c_result.scalar_one_or_none()
            contract_active = bool(
                contract
                and contract.status in (ContractStatus.ACTIVE.value, ContractStatus.SIGNED.value)
            )

    if not contract_active:
        from app.services.audit_service import audit_service

        await audit_service.log_standalone(
            action="session.auto_resume_rejected", resource_type="sandbox_session",
            user_id=session.user_id, session_id=session.id,
            detail={"reason": "contract_not_active", "contract_id": session.contract_id},
        )
        raise SessionTerminated(
            "Session is suspended (idle auto-pause) and its governing contract "
            "is not active — auto-resume rejected; resume manually or contact "
            "the contract parties."
        )

    try:
        target = SessionStatus(session.pre_pause_status or SessionStatus.READY.value)
    except ValueError:
        target = SessionStatus.READY
    if target not in (SessionStatus.READY, SessionStatus.RUNNING):
        target = SessionStatus.READY
    transition = session_state_machine.validate_transition(SessionStatus.SUSPENDED, target)
    if not transition.success:
        return

    from app.core.config import get_settings
    from app.core.metrics import record_session_transition

    session.status = target.value
    session.pre_pause_status = None
    record_session_transition(SessionStatus.SUSPENDED.value, target.value)
    current_ext = int(session.extended_seconds or 0)
    max_ext = get_settings().SESSION_MAX_EXTENDED_SECONDS
    session.extended_seconds = min(current_ext + int(session.timeout_seconds), max_ext)
    await db.flush()
    await db.refresh(session)
    from app.services.audit_service import audit_service

    await audit_service.log(
        db, action="session.auto_resume", resource_type="sandbox_session",
        user_id=session.user_id, session_id=session.id,
        detail={"resumed_to": target.value, "contract_id": session.contract_id},
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
    current_user: User = Depends(get_business_user),
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
    current_user: User = Depends(get_business_user),
):
    """Upload a file into the session workspace (DEK-encrypted at rest).

    The sandbox reads it from ``workspace/files/<name>``; when the session
    has a workspace DEK the content is encrypted exactly like provision-time
    workspace data and is decrypted by sandbox code via ``CDS_DEK_HEX``.
    """
    session = await _load_operable_session(db, session_id, current_user)
    _active_or_suspended(session)
    if session.sandbox_level == "l2c":
        data = await file.read()
        result = await _l2c_file_op(session, "upload", file.filename or "", data, where="workspace")
        await audit_service.log(
            db, action="sandbox.file_upload", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"filename": file.filename or "", "size": len(data), "encrypted": False,
                    "note": "l2c: file lands in the VM /scratch via exec channel (at-rest inside the sandbox)"},
        )
        await db.flush()
        return {"session_id": str(session_id), "filename": file.filename or "", "size": len(data)}
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
    current_user: User = Depends(get_business_user),
):
    """Download a workspace file — content passes the T5 output review first.

    Blocked (critical DLP finding) content is NEVER released; the response is
    409 with the review findings instead.
    """
    from fastapi import Response

    session = await _load_operable_session(db, session_id, current_user)
    if session.sandbox_level == "l2c":
        result = await _l2c_file_op(session, "download", filename)
        content = result["data"]
        # SS-14 RT-07: l2c 下载与 L3 同契约 —— 内容先过 T5 输出审查,
        # 响应携带 X-CDS-Output-Review（critical 发现 409 不放出）。
        from fastapi import Response as _Resp

        from app.services.output_inspection import output_inspector

        text = content.decode("utf-8", errors="replace")
        _review = output_inspector.inspect(
            text,
            user_id=str(current_user.id),
            session_id=str(session.id),
            sandbox_mode=getattr(session, "sandbox_mode", None) or "structured_query",
        )
        _findings = list(getattr(_review, "findings", []) or [])

        def _sev(f) -> str:
            s = getattr(f, "severity", "")
            return getattr(s, "value", None) or str(s)

        _blocked = any(_sev(f).lower() == "critical" for f in _findings)
        from app.core.metrics import record_output_inspection

        if _blocked:
            record_output_inspection("blocked")
            await audit_service.log_standalone(
                action="sandbox.file_download_blocked", resource_type="sandbox_session",
                user_id=current_user.id, session_id=session.id,
                detail={"filename": filename, "findings": len(_findings)},
            )
            await db.flush()
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Output review blocked the file download",
                    "filename": filename,
                    "findings_count": len(_findings),
                },
            )
        _header = f"passed; findings={len(_findings)}"
        if _findings:
            _redacted = getattr(_review, "redacted_output", None)
            try:
                content.decode("utf-8")
                _is_text = True
            except UnicodeDecodeError:
                _is_text = False
            if _is_text and _redacted is not None:
                content = _redacted.encode("utf-8")
                _header = f"redacted; findings={len(_findings)}"
                record_output_inspection("redacted")
            else:
                record_output_inspection("passed")
        else:
            record_output_inspection("passed")
        await audit_service.log(
            db, action="sandbox.file_download", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"filename": filename, "size": len(content), "findings": len(_findings)},
        )
        await db.flush()
        return _Resp(
            content=content, media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-CDS-Output-Review": _header,
            },
        )
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
    from app.core.metrics import record_output_inspection

    if blocked:
        record_output_inspection("blocked")
        await audit_service.log_standalone(
            action="sandbox.file_download_blocked", resource_type="sandbox_session",
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
    if not findings:
        record_output_inspection("passed")

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
            record_output_inspection("redacted")
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
    current_user: User = Depends(get_business_user),
):
    session = await _load_operable_session(db, session_id, current_user)
    if session.sandbox_level == "l2c":
        result = await _l2c_file_op(session, "delete", filename)
        await audit_service.log(
            db, action="sandbox.file_delete", resource_type="sandbox_session",
            user_id=current_user.id, session_id=session.id,
            detail={"filename": filename},
        )
        await db.flush()
        return {"session_id": str(session_id), "deleted": result["deleted"]}
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
    current_user: User = Depends(get_business_user),
):
    session = await _load_operable_session(db, session_id, current_user)
    workspace = _session_workspace_or_400(session)
    from app.services import session_snapshots as ss

    return {"session_id": str(session_id), "snapshots": ss.list_snapshots(workspace)}


@router.post("/{session_id}/snapshots")
async def create_session_snapshot(
    session_id: uuid.UUID,
    async_mode: bool = Query(False, alias="async"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Snapshot the session workspace (content is DEK-encrypted at rest).

    W11: pass ``?async=true`` to get ``202 {operation_id}`` and poll
    ``GET /operations/{op_id}``; the default synchronous behavior is
    unchanged (an operation record is still written for audit parity).
    """
    session = await _load_operable_session(db, session_id, current_user)
    _active_or_suspended(session)
    workspace = _session_workspace_or_400(session)
    from app.services import session_snapshots as ss
    from app.services import session_operations as ops

    async def _run(op):
        manifest = await asyncio.to_thread(ss.create_snapshot, workspace, str(session.id))
        op.progress = 0.9
        return {"snapshot_id": manifest["snapshot_id"], **manifest}

    try:
        op = await ops.submit_operation(db, session.id, "snapshot", run_async=async_mode, executor=_run)
    except ss.SnapshotError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if async_mode:
        return JSONResponse(status_code=202, content=ops.operation_payload(op))

    manifest = dict(op.result_ref or {})
    if op.status == "failed":
        raise HTTPException(status_code=400, detail=op.error or "snapshot failed")

    await audit_service.log(
        db, action="sandbox.snapshot_create", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"snapshot_id": manifest.get("snapshot_id"), "bytes": manifest.get("bytes"),
                "file_count": manifest.get("file_count"), "operation_id": str(op.op_id)},
    )
    await db.flush()
    return {"session_id": str(session_id), "operation_id": str(op.op_id), **manifest}


@router.get("/operations/{op_id}")
async def get_session_operation(
    op_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Poll a session operation (W11 async model)."""
    from app.services import session_operations as ops

    op = await ops.get_operation(db, op_id)
    if not op:
        raise HTTPException(status_code=404, detail="Operation not found")
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == op.session_id))
    session = result.scalar_one_or_none()
    if not session or not _can_operate_session(current_user, session):
        raise HTTPException(status_code=404, detail="Operation not found")
    return ops.operation_payload(op)


@router.post("/{session_id}/snapshots/{snapshot_id}/rollback")
async def rollback_session_snapshot(
    session_id: uuid.UUID,
    snapshot_id: str,
    async_mode: bool = Query(False, alias="async"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Restore the workspace from a snapshot (current workspace content is
    discarded — the archive checksum is verified before any deletion).

    W11: concurrent rollbacks on one session are mutually exclusive
    (503 OPERATION_LOCKED + Retry-After); ``?async=true`` returns 202.
    """
    session = await _load_operable_session(db, session_id, current_user)
    _active_or_suspended(session)
    workspace = _session_workspace_or_400(session)
    from app.services import session_snapshots as ss
    from app.services import session_operations as ops

    async def _run(op):
        result = await asyncio.to_thread(ss.rollback, workspace, snapshot_id)
        op.progress = 0.9
        return {"snapshot_id": snapshot_id, **result}

    try:
        op = await ops.submit_operation(db, session.id, "rollback", run_async=async_mode, executor=_run)
    except OperationLocked as e:
        raise HTTPException(
            status_code=e.http_status,
            detail={"code": e.code, "message": str(e), "retry_after": e.retry_after},
            headers={"Retry-After": str(e.retry_after)},
        )
    except ss.SnapshotError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if async_mode:
        return JSONResponse(status_code=202, content=ops.operation_payload(op))

    result = dict(op.result_ref or {})
    if op.status == "failed":
        raise HTTPException(status_code=400, detail=op.error or "rollback failed")

    await audit_service.log(
        db, action="sandbox.snapshot_rollback", resource_type="sandbox_session",
        user_id=current_user.id, session_id=session.id,
        detail={"snapshot_id": snapshot_id, "discarded_files": result.get("discarded_files", 0),
                "operation_id": str(op.op_id)},
    )
    await db.flush()
    return {"session_id": str(session_id), "operation_id": str(op.op_id), **result}


@router.delete("/{session_id}/snapshots/{snapshot_id}")
async def delete_session_snapshot(
    session_id: uuid.UUID,
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
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
    current_user: User = Depends(get_business_user),
):
    """Pause a session (READY/RUNNING → SUSPENDED): execution is gated while
    workspace, files, snapshots and keys are all preserved."""
    session = await _load_operable_session(db, session_id, current_user)
    current = SessionStatus(session.status)
    if not session_state_machine.is_active(current):
        raise SessionStateConflict(f"Session in terminal state (status: {session.status})")
    result = session_state_machine.validate_transition(current, SessionStatus.SUSPENDED)
    if not result.success:
        raise SessionStateConflict(result.error)

    session.pre_pause_status = session.status
    session.status = SessionStatus.SUSPENDED.value
    from app.core.metrics import record_session_transition

    record_session_transition(session.pre_pause_status, SessionStatus.SUSPENDED.value)
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
    current_user: User = Depends(get_business_user),
):
    """Resume a paused session → restore the pre-pause status (fallback READY)."""
    session = await _load_operable_session(db, session_id, current_user)
    current = SessionStatus(session.status)
    if current != SessionStatus.SUSPENDED:
        raise SessionStateConflict(f"Session not suspended (status: {session.status})")

    target_raw = session.pre_pause_status or SessionStatus.READY.value
    try:
        target = SessionStatus(target_raw)
    except ValueError:
        target = SessionStatus.READY
    if target not in (SessionStatus.READY, SessionStatus.RUNNING):
        target = SessionStatus.READY
    result = session_state_machine.validate_transition(current, target)
    if not result.success:
        raise SessionStateConflict(result.error)

    session.status = target.value
    session.pre_pause_status = None
    from app.core.metrics import record_session_transition

    record_session_transition(SessionStatus.SUSPENDED.value, target.value)
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
    current_user: User = Depends(get_business_user),
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
    current_user: User = Depends(get_business_user),
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
        raise SessionStateConflict(f"Session in terminal state (status: {session.status})")
    if session.status != SessionStatus.RUNNING.value:
        raise SessionStateConflict(f"Session not running (status: {session.status})")
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
        db=db,
    )
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    # SS-14 RT-07 (2026-10-14): l2c 无 in-VM 审计 helper（与 /execute 同因）:
    # 控制面写 application/shell_exec 活动事件到 tmpfs jsonl（/audit 读此文件）。
    if (session.sandbox_level or "") == "l2c":
        try:
            import tempfile as _tf

            from app.services.sandbox_audit import ActivityCategory, SandboxActivityEvent

            _al_path = Path(_tf.gettempdir()) / "cds-sandbox-audit" / f"{session_id}.jsonl"
            _al_path.parent.mkdir(parents=True, exist_ok=True)
            _ev = SandboxActivityEvent(
                session_id=str(session_id),
                user_id=str(current_user.id),
                sandbox_level="l2c",
                category=ActivityCategory.APPLICATION.value,
                event_type="shell_exec",
                detail={
                    "command_sha256": hashlib.sha256(body.command.encode("utf-8")).hexdigest(),
                    "exit_code": exec_result.get("exit_code"),
                    "runtime": "cubesandbox",
                },
                duration_ms=duration_ms,
            )
            with open(_al_path, "a", encoding="utf-8") as _fh:
                _fh.write(_ev.to_json() + "\n")
            logger.info(f"[sandbox-audit] l2c shell activity event written: {_al_path.name}")
        except Exception as e:
            logger.warning(f"[sandbox-audit] l2c shell activity event failed: {e}")

    output = exec_result.get("output") or ""
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False, default=str)
    max_chars = int(settings.SESSION_EXEC_MAX_OUTPUT_CHARS)
    truncated = len(output) > max_chars
    if truncated:
        output = output[:max_chars]

    # T5 DLP review on the captured output (same gateway semantics as /execute).
    sandbox_mode = getattr(session, "sandbox_mode", None) or "structured_query"
    # /execute 空输出至少披露 stage_results.no_output；这里此前是零披露的预置 True，同一口径补齐。
    security_report: dict = {"passed": True, "blocked": False, "no_output": True, "findings": [], "findings_count": 0}
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
            report["no_output"] = False
            from app.core.metrics import record_output_inspection

            if should_block(inspection):
                output = ""
                output_blocked = True
                record_output_inspection("blocked")
            else:
                output = inspection.redacted_output or ""
                record_output_inspection("redacted" if inspection.redacted_output != output else "passed")
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
    current_user: User = Depends(get_business_user),
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


@router.get("/{session_id}/metering")
async def get_session_metering(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """D-e2e-48：状态条 CPU/内存计量——只读 cgroup v2 真实计数。

    读不到（l2c 在 VM 内、非运行态、cgroup 已被回收）就返回 available:false，
    前端继续显示「未计量」——绝不编一个百分比充数（禁伪造铁律）。
    """
    import asyncio
    from pathlib import Path

    session = await _load_operable_session(db, session_id, current_user)
    cg = None
    if session.status == "running":
        for root in ("/sys/fs/cgroup/cds-l0", "/sys/fs/cgroup/cds-l3"):
            for name in (session_id.hex, str(session_id)):
                cand = Path(root) / name
                if (cand / "memory.current").exists():
                    cg = cand
                    break
            if cg:
                break
    if cg is None:
        return {
            "available": False,
            "reason": "运行时无可读 cgroup（l2c 在 VM 内／会话非运行／cgroup 已回收）",
            "cpu_percent": None,
            "memory_used_mb": None,
            "memory_limit_mb": None,
            "pids": None,
        }

    def _int(path: Path) -> int | None:
        try:
            raw = path.read_text().strip()
            return int(raw) if raw.isdigit() else None
        except OSError:
            return None

    def _cpu_usec() -> int | None:
        try:
            for line in (cg / "cpu.stat").read_text().splitlines():
                k, _, v = line.partition(" ")
                if k == "usage_usec":
                    return int(v)
        except (OSError, ValueError):
            return None
        return None

    mem_used = _int(cg / "memory.current")
    mem_max = _int(cg / "memory.max")  # "max"（无上限）→ None
    pids = _int(cg / "pids.current")
    sample_ms = 250
    u0, u1 = _cpu_usec(), None
    cpu_pct = None
    if u0 is not None:
        await asyncio.sleep(sample_ms / 1000)
        u1 = _cpu_usec()
        if u1 is not None:
            # 单核百分比：Δusage_usec / Δ墙钟；空闲沙箱自然是 0.0%，不是未计量。
            cpu_pct = round(max(0.0, (u1 - u0) / (sample_ms * 1000) * 100), 1)
    return {
        "available": True,
        "source": str(cg),
        "cpu_percent": cpu_pct,
        "memory_used_mb": round(mem_used / 1048576, 1) if mem_used is not None else None,
        "memory_limit_mb": round(mem_max / 1048576, 1) if mem_max is not None else None,
        "pids": pids,
        "sampled_ms": sample_ms,
    }


@router.get("/{session_id}/usage")
async def get_session_usage(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    """Workspace / files / snapshots usage snapshot for one session (Round 40)."""
    session = await _load_operable_session(db, session_id, current_user)
    if session.sandbox_level == "l2c":
        # SS-14 RT-07: l2c 无宿主 workspace（文件在 VM 内 /data /results /scratch）。
        # 诚实口径: 数据卷大小（节点侧 SM4 at-rest 副本）+ VM 侧目录统计
        # （exec 通道 du, best-effort）。
        from app.services import cube_node_client
        from app.services.cube_runtime_adapter import CubeRuntimeAdapter
        from app._stubs.cube.cubebox.v1 import cubebox_pb2

        data_bytes = 0
        vm_dirs: dict = {"data": 0, "results": 0, "scratch": 0}
        try:
            from app.services.sandbox_runtime import get_sandbox_manager

            _mgr = await get_sandbox_manager()
            _cra = _mgr.get_adapter("l2c")
            _node_id, _cubebox = CubeRuntimeAdapter._parse_container_id(session.container_id)
            _node = await _cra._resolve_node(_node_id)
            _client = _cra._client_for(_node)
            _vol = await asyncio.to_thread(
                _client.inspect_storage_volumes,
                cubebox_pb2.InspectStorageVolumesRequest(cubebox_id=_cubebox),
            )
            for v in getattr(_vol, "volumes", None) or []:
                if str(getattr(v, "name", "")).startswith("cds-data-"):
                    data_bytes = int(getattr(v, "size", 0) or 0)
        except Exception:
            pass
        try:
            _du = await asyncio.to_thread(
                _cra.execute_sync_for_usage,
                session.container_id,
                f"du -sk {_cra.GUEST_DATA_DIR} {_cra.GUEST_OUTPUT_DIR} {_cra.GUEST_WORK_DIR} 2>/dev/null",
            )
            _usage_dirs = (_cra.GUEST_DATA_DIR, _cra.GUEST_OUTPUT_DIR, _cra.GUEST_WORK_DIR)
            for line in str(_du.get("output") or "").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1].rstrip("/") in _usage_dirs:
                    vm_dirs[parts[1].rstrip("/")] = int(parts[0]) * 1024
        except Exception:
            pass
        return {
            "session_id": str(session_id),
            "status": session.status,
            "runtime": "cubesandbox",
            "workspace": {
                "files": None,  # VM 内, 宿主侧不可枚举（诚实: 不伪造）
                "bytes": data_bytes + sum(vm_dirs.values()),
                "data_volume_bytes": data_bytes,
                "vm_directories": vm_dirs,
            },
            "uploaded_files": {"count": None, "bytes": None},
            "snapshots": {"count": 0, "bytes": 0},
            "timeout": {
                "timeout_seconds": session.timeout_seconds,
                "extended_seconds": int(getattr(session, "extended_seconds", 0) or 0),
            },
        }
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
