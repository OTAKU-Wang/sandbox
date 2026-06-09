"""Cross-Space Connector API — register external spaces, proxy sessions.

External trusted data spaces can:
1. Register as a connector (admin approval required)
2. Browse the data product catalog via API key
3. Create contracts for data product access
4. Proxy sandbox sessions for secure data usage
"""
import json
import uuid
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.connector import Connector, ConnectorStatus, ConnectorSession, generate_api_key
from app.models.data_product import DataProduct, DataProductStatus
from app.models.contract import Contract, ContractStatus
from app.models.kms import KeyMetadata, KeyStatus, KeyType
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services.audit_service import audit_service
from app.services.kms_service import kms_service
from app.services.network_policy import NetworkPolicyConfig, network_policy_engine
from app.services.sandbox_manager import get_sandbox_manager
from app.services.quota_manager import QuotaType, quota_manager

router = APIRouter()


def _contract_terms_value(contract: Contract, *keys: str) -> str | None:
    """Read a contract term from top-level or connector/remote_space nested terms."""
    terms = contract.terms or {}
    if not isinstance(terms, dict):
        return None
    for key in keys:
        value = terms.get(key)
        if value:
            return str(value)
    for section_name in ("connector", "remote_space", "federation"):
        section = terms.get(section_name)
        if isinstance(section, dict):
            for key in keys:
                value = section.get(key)
                if value:
                    return str(value)
    return None


def _ensure_contract_bound_to_connector(contract: Contract, connector: Connector) -> None:
    """Enforce optional contract terms that bind a contract to one connector/space."""
    expected_connector = _contract_terms_value(contract, "connector_id")
    if expected_connector and expected_connector != str(connector.id):
        raise HTTPException(status_code=403, detail="Contract is not bound to this connector")

    expected_space = _contract_terms_value(contract, "space_id", "remote_space_id", "connector_space_id")
    if expected_space and expected_space != connector.space_id:
        raise HTTPException(status_code=403, detail="Contract is not bound to this connector space")


def _connector_execution_allowed(contract: Contract, session: SandboxSession) -> tuple[bool, str]:
    """Check whether a proxied connector session can execute under its contract."""
    if contract.status != ContractStatus.ACTIVE.value:
        return False, f"Contract is {contract.status}"
    if str(session.data_product_id) not in [str(pid) for pid in contract.product_ids]:
        return False, "Session product is not covered by contract"
    if contract.allowed_sandbox_modes and session.sandbox_mode not in contract.allowed_sandbox_modes:
        return False, f"Sandbox mode '{session.sandbox_mode}' not allowed by contract"
    if contract.allowed_operations:
        allowed_ops = {op.strip() for op in contract.allowed_operations.split(",") if op.strip()}
        mode_ops = {
            "structured_query": {"query", "read", "execute", "structured_query"},
            "structured_modeling": {"analyze", "model", "execute", "structured_modeling"},
            "structured_app": {"invoke", "execute", "structured_app"},
            "llm_training": {"train", "execute", "llm_training"},
            "product_dev": {"read", "transform", "analyze", "execute", "product_dev"},
            "joint_federated": {"compute", "execute", "joint_federated"},
        }.get(session.sandbox_mode, {"execute", session.sandbox_mode})
        if allowed_ops.isdisjoint(mode_ops):
            return False, f"Contract operations {sorted(allowed_ops)} do not allow connector execution"
    return True, "Contract permits connector execution"


async def _verify_connector_api_key(
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Connector:
    """Verify connector API key from Authorization header."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    api_key = authorization[7:]
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    result = await db.execute(
        select(Connector).where(
            Connector.api_key_hash == key_hash,
            Connector.status == ConnectorStatus.ACTIVE.value,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=401, detail="Invalid or inactive connector API key")
    return connector


# --- Admin: Manage Connectors ---

@router.post("/register", status_code=201)
async def register_connector(
    space_id: str,
    space_name: str,
    space_url: str,
    description: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Register a new external trusted data space connector (admin only)."""
    # Check for duplicate space_id
    existing = await db.execute(select(Connector).where(Connector.space_id == space_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Connector with space_id '{space_id}' already exists")

    api_key, key_hash = generate_api_key()

    connector = Connector(
        space_id=space_id,
        space_name=space_name,
        space_url=space_url,
        description=description,
        api_key_hash=key_hash,
        api_key_prefix=api_key[:15],
        status=ConnectorStatus.ACTIVE.value,
        registered_by=current_user.id,
    )
    db.add(connector)
    await db.flush()
    await db.refresh(connector)

    await audit_service.log(
        db, action="connector.register", resource_type="connector",
        user_id=current_user.id, resource_id=str(connector.id),
        detail={"space_id": space_id, "space_name": space_name},
    )

    return {
        "id": str(connector.id),
        "space_id": space_id,
        "space_name": space_name,
        "status": connector.status,
        "api_key": api_key,  # Only returned once at registration
        "api_key_prefix": connector.api_key_prefix,
    }


@router.get("/")
async def list_connectors(
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """List all registered connectors (admin only)."""
    query = select(Connector)
    if status_filter:
        query = query.where(Connector.status == status_filter)
    query = query.order_by(Connector.created_at.desc())
    result = await db.execute(query)

    items = []
    for c in result.scalars().all():
        items.append({
            "id": str(c.id),
            "space_id": c.space_id,
            "space_name": c.space_name,
            "space_url": c.space_url,
            "status": c.status,
            "is_healthy": c.is_healthy,
            "last_heartbeat": c.last_heartbeat.isoformat() if c.last_heartbeat else None,
            "api_key_prefix": c.api_key_prefix,
            "supported_protocols": c.supported_protocols,
            "max_concurrent_sessions": c.max_concurrent_sessions,
            "created_at": c.created_at.isoformat(),
        })
    return {"items": items, "total": len(items)}


@router.post("/{connector_id}/suspend")
async def suspend_connector(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Suspend a connector."""
    result = await db.execute(select(Connector).where(Connector.id == connector_id))
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    connector.status = ConnectorStatus.SUSPENDED.value
    await db.flush()
    return {"id": str(connector.id), "status": connector.status}


@router.post("/{connector_id}/reactivate")
async def reactivate_connector(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Reactivate a suspended connector."""
    result = await db.execute(select(Connector).where(Connector.id == connector_id))
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    if connector.status != ConnectorStatus.SUSPENDED.value:
        raise HTTPException(status_code=400, detail="Can only reactivate suspended connectors")
    connector.status = ConnectorStatus.ACTIVE.value
    await db.flush()
    return {"id": str(connector.id), "status": connector.status}


@router.post("/{connector_id}/rotate-key")
async def rotate_connector_key(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    """Rotate the API key for a connector."""
    result = await db.execute(select(Connector).where(Connector.id == connector_id))
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    api_key, key_hash = generate_api_key()
    connector.api_key_hash = key_hash
    connector.api_key_prefix = api_key[:15]
    await db.flush()

    return {
        "id": str(connector.id),
        "api_key": api_key,
        "api_key_prefix": connector.api_key_prefix,
    }


# --- Connector-Facing APIs (authenticated via API key) ---

@router.get("/remote/catalog")
async def connector_browse_catalog(
    q: str | None = Query(None),
    product_type: str | None = Query(None),
    industry: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    connector: Connector = Depends(_verify_connector_api_key),
):
    """Browse published data products (connector-facing).

    Uses API key authentication. Returns products matching the
    connector's allowed_product_types.
    """
    query = select(DataProduct).where(DataProduct.status == DataProductStatus.PUBLISHED.value)
    count_query = select(func.count()).select_from(DataProduct).where(DataProduct.status == DataProductStatus.PUBLISHED.value)

    # Filter by connector's allowed product types
    if connector.allowed_product_types:
        query = query.where(DataProduct.product_type.in_(connector.allowed_product_types))
        count_query = count_query.where(DataProduct.product_type.in_(connector.allowed_product_types))

    if q:
        from sqlalchemy import or_
        search = or_(DataProduct.name.ilike(f"%{q}%"), DataProduct.description.ilike(f"%{q}%"))
        query = query.where(search)
        count_query = count_query.where(search)
    if product_type:
        query = query.where(DataProduct.product_type == product_type)
        count_query = count_query.where(DataProduct.product_type == product_type)
    if industry:
        query = query.where(DataProduct.industry == industry)
        count_query = count_query.where(DataProduct.industry == industry)

    total = (await db.execute(count_query)).scalar() or 0
    query = query.order_by(DataProduct.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)

    items = []
    for p in result.scalars().all():
        items.append({
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "product_type": p.product_type,
            "industry": p.industry,
            "security_level": p.security_level,
            "allowed_operations": p.allowed_operations,
            "row_count": p.row_count,
            "output_constraints": p.output_constraints,
        })

    return {"items": items, "total": total, "page": skip // limit + 1, "page_size": limit}


@router.post("/remote/sessions", status_code=201)
async def connector_create_session(
    product_id: uuid.UUID,
    contract_id: uuid.UUID,
    remote_user_id: str,
    sandbox_level: str = "L3",
    db: AsyncSession = Depends(get_db),
    connector: Connector = Depends(_verify_connector_api_key),
):
    """Create a proxied sandbox session for a remote user.

    The connector must have an active contract for the product.
    """
    # Verify product exists and is published
    result = await db.execute(select(DataProduct).where(DataProduct.id == product_id))
    product = result.scalar_one_or_none()
    if not product or product.status != DataProductStatus.PUBLISHED.value:
        raise HTTPException(status_code=404, detail="Product not found or not published")

    # Verify contract exists and is active
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status != ContractStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Contract is not active")
    if str(product_id) not in [str(pid) for pid in contract.product_ids]:
        raise HTTPException(status_code=400, detail="Product not covered by this contract")
    _ensure_contract_bound_to_connector(contract, connector)

    # Check concurrent session limit
    active_sessions = await db.execute(
        select(func.count()).select_from(ConnectorSession).where(
            ConnectorSession.connector_id == connector.id,
            ConnectorSession.status == "active",
        )
    )
    count = active_sessions.scalar() or 0
    if count >= connector.max_concurrent_sessions:
        raise HTTPException(status_code=429, detail=f"Concurrent session limit reached ({connector.max_concurrent_sessions})")

    # Create the proxied sandbox session
    from app.services.sandbox_manager import validate_resource_limits
    effective_limits = validate_resource_limits(sandbox_level, None)

    session = SandboxSession(
        user_id=contract.buyer_id,  # Use the buyer's identity
        data_product_id=product_id,
        sandbox_level=sandbox_level,
        contract_id=str(contract_id),
        timeout_seconds=contract.max_duration_hours * 3600,
        resource_limits=effective_limits,
        status=SessionStatus.PROVISIONING.value,
    )
    db.add(session)
    await db.flush()

    # Initialize session quota counters (P1-4)
    await quota_manager.reset(session.id)

    # Provision sandbox
    runtime = await get_sandbox_manager()
    provision_result = runtime.provision(
        session_id=session.id,
        level=sandbox_level,
        data_path="",
        timeout=session.timeout_seconds,
    )
    session.container_id = provision_result.get("container_id")
    session.status = provision_result.get("status", SessionStatus.PROVISIONING.value)
    if provision_result.get("error") or not session.container_id or session.status == SessionStatus.FAILED.value:
        session.status = SessionStatus.FAILED.value
        session.error_message = provision_result.get("error") or "Sandbox provision failed"
        await db.flush()
        await audit_service.log(
            db, action="connector.session.create_failed", resource_type="connector_session",
            user_id=contract.buyer_id, session_id=session.id,
            detail={
                "connector_id": str(connector.id),
                "space_id": connector.space_id,
                "remote_user_id": remote_user_id,
                "product_id": str(product_id),
                "sandbox_level": sandbox_level,
                "error": session.error_message,
            },
        )
        raise HTTPException(status_code=503, detail=session.error_message)

    await quota_manager.set_limits(session.id, {
        QuotaType.ROWS: contract.max_output_rows or 10000,
        QuotaType.BYTES: 100 * 1024 * 1024,
        QuotaType.API_CALLS: 1000,
        QuotaType.GPU_SECONDS: int((contract.max_duration_hours or 24) * 3600),
    })

    key_result = kms_service.generate_session_key(str(session.id))
    session.session_key_id = key_result["key_id"]
    distributed_key = kms_service.distribute_key(key_result["key_id"], str(session.id))
    if not distributed_key:
        kms_service.destroy_key(key_result["key_id"])
        session.session_key_id = None
        session.status = SessionStatus.FAILED.value
        session.error_message = "Session key distribution failed"
        termination_error = None
        try:
            if not runtime.terminate(session.container_id):
                termination_error = "runtime terminate returned false"
        except Exception as e:
            termination_error = str(e)
        await db.flush()
        await audit_service.log(
            db, action="connector.session.key_distribution_failed", resource_type="connector_session",
            user_id=contract.buyer_id, session_id=session.id,
            detail={
                "connector_id": str(connector.id),
                "space_id": connector.space_id,
                "remote_user_id": remote_user_id,
                "product_id": str(product_id),
                "termination_error": termination_error,
            },
        )
        raise HTTPException(status_code=503, detail=session.error_message)

    db.add(KeyMetadata(
        key_id=key_result["key_id"],
        key_type=KeyType.SESSION.value,
        status=KeyStatus.ACTIVE.value,
        session_id=session.id,
        product_id=product_id,
    ))

    db.add(NetworkPolicy(
        session_id=str(session.id),
        user_id=str(contract.buyer_id),
        mode="deny_all",
        active=True,
    ))
    await network_policy_engine.create_policy(str(session.id), NetworkPolicyConfig(mode="deny_all"))

    # Create connector session record
    conn_session = ConnectorSession(
        connector_id=connector.id,
        contract_id=contract_id,
        sandbox_session_id=session.id,
        remote_user_id=remote_user_id,
        product_id=product_id,
        status="active",
    )
    db.add(conn_session)

    await db.flush()
    await db.refresh(session)
    await db.refresh(conn_session)

    await audit_service.log(
        db, action="connector.session.create", resource_type="connector_session",
        user_id=contract.buyer_id, session_id=session.id,
        detail={
            "connector_id": str(connector.id),
            "space_id": connector.space_id,
            "remote_user_id": remote_user_id,
            "product_id": str(product_id),
        },
    )

    return {
        "connector_session_id": str(conn_session.id),
        "sandbox_session_id": str(session.id),
        "status": session.status,
        "product_id": str(product_id),
        "sandbox_level": sandbox_level,
    }


@router.post("/remote/sessions/{connector_session_id}/execute")
async def connector_execute_in_session(
    connector_session_id: uuid.UUID,
    code: str,
    language: str = "python",
    db: AsyncSession = Depends(get_db),
    connector: Connector = Depends(_verify_connector_api_key),
):
    """Execute code in a proxied sandbox session (connector-facing)."""
    if not code or not code.strip():
        raise HTTPException(status_code=422, detail="code cannot be empty")

    result = await db.execute(
        select(ConnectorSession).where(
            ConnectorSession.id == connector_session_id,
            ConnectorSession.connector_id == connector.id,
        )
    )
    conn_session = result.scalar_one_or_none()
    if not conn_session:
        raise HTTPException(status_code=404, detail="Connector session not found")
    if conn_session.status != "active":
        raise HTTPException(status_code=400, detail="Session is not active")

    # Get the underlying sandbox session
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == conn_session.sandbox_session_id))
    session = result.scalar_one_or_none()
    if not session or session.status != SessionStatus.RUNNING.value:
        raise HTTPException(status_code=400, detail="Sandbox session not running")
    if not session.container_id:
        raise HTTPException(status_code=400, detail="Sandbox session has no container")

    result = await db.execute(select(Contract).where(Contract.id == conn_session.contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=403, detail="Bound contract not found")
    _ensure_contract_bound_to_connector(contract, connector)
    allowed, reason = _connector_execution_allowed(contract, session)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    from app.services.code_scanner import code_scanner

    sandbox_mode = session.sandbox_mode or "structured_query"
    scan_result = code_scanner.scan(code, language, sandbox_mode)
    if not scan_result.passed:
        fatal_issues = [i for i in scan_result.issues if i.severity.value == "FATAL"]
        await audit_service.log(
            db, action="connector.session.code_scan_rejected", resource_type="connector_session",
            user_id=session.user_id, session_id=session.id,
            detail={
                "connector_id": str(connector.id),
                "language": language,
                "issues": [{"code": i.code, "message": i.message, "line": i.line} for i in fatal_issues],
            },
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Code scan failed",
                "issues": [
                    {"severity": i.severity.value, "code": i.code, "message": i.message, "line": i.line}
                    for i in fatal_issues
                ],
            },
        )

    runtime = await get_sandbox_manager()
    exec_result = await runtime.execute(session.container_id, code, language)

    await audit_service.log(
        db, action="connector.session.execute", resource_type="connector_session",
        user_id=session.user_id, session_id=session.id,
        detail={
            "connector_id": str(connector.id),
            "language": language,
            "exit_code": exec_result.get("exit_code"),
        },
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
                user_id=str(session.user_id),
                session_id=str(session.id),
                sandbox_mode=sandbox_mode,
            )
            exec_result["security_report"] = inspection_to_report(inspection)
            if should_block(inspection):
                exec_result["output"] = ""
                exec_result["output_blocked"] = True
                exec_result["blocked_reason"] = "output_inspection_blocked"
            else:
                exec_result["output"] = inspection.redacted_output or ""
                exec_result["output_blocked"] = False
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

    return exec_result


@router.post("/remote/sessions/{connector_session_id}/terminate")
async def connector_terminate_session(
    connector_session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    connector: Connector = Depends(_verify_connector_api_key),
):
    """Terminate a proxied sandbox session (connector-facing)."""
    result = await db.execute(
        select(ConnectorSession).where(
            ConnectorSession.id == connector_session_id,
            ConnectorSession.connector_id == connector.id,
        )
    )
    conn_session = result.scalar_one_or_none()
    if not conn_session:
        raise HTTPException(status_code=404, detail="Connector session not found")

    conn_session.status = "terminated"
    conn_session.ended_at = datetime.now(timezone.utc)

    # Terminate underlying sandbox
    if conn_session.sandbox_session_id:
        result = await db.execute(select(SandboxSession).where(SandboxSession.id == conn_session.sandbox_session_id))
        session = result.scalar_one_or_none()
        if session and session.status in (SessionStatus.RUNNING.value, SessionStatus.PROVISIONING.value):
            session.status = SessionStatus.TERMINATED.value
            session.ended_at = datetime.now(timezone.utc)
            if session.container_id:
                runtime = await get_sandbox_manager()
                runtime.terminate(session.container_id)

    await db.flush()
    return {"connector_session_id": str(connector_session_id), "status": "terminated"}


@router.get("/remote/heartbeat")
async def connector_heartbeat(
    db: AsyncSession = Depends(get_db),
    connector: Connector = Depends(_verify_connector_api_key),
):
    """Connector heartbeat — updates health status."""
    connector.last_heartbeat = datetime.now(timezone.utc)
    connector.is_healthy = True
    await db.flush()
    return {"status": "ok", "space_id": connector.space_id, "timestamp": connector.last_heartbeat.isoformat()}
