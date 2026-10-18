"""Data Product Development Sandbox API."""
import asyncio
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.config import get_settings
from app.core.errors import CDSError
from app.core.path_security import sanitize_filename, PathTraversalError
from app.models.user import User
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource, ResourceStatus
from app.models.contract import Contract, ContractStatus
from app.services.audit_service import audit_service
from app.services.data_product_sandbox import (
    dev_sandbox,
    DevMode,
    DevSandboxConfig,
    structured_tools,
    unstructured_tools,
    semi_structured_tools,
)

router = APIRouter()

# ── 供数明文暂存（A52 / A54）───────────────────────────────────────────────
# 这是 download+decrypt 的落点，只允许在该会话 provision 的那几秒内存在：
# 所有 runtime 适配器都是把 data_path **复制**进自己的工作区（sandbox_runtime
# 的 shutil.copy2/copytree、firecracker transfer_file、tee _copy_input_data；
# k8s / l2c 则完全忽略 data_path），没有任何适配器持有这个文件的活引用，
# 所以 provision 返回后必须回收——密态供数的明文不得跳会话/跳重启留在宿主
# （密态数据不出域）。目录沿用 /tmp/cds-sandbox 下的既有位置（jupyter 的
# root_dir 以内不暴露给用户，见 jupyter_gateway 的 deny 规则）。
_DATA_LOAD_ROOT = Path("/tmp/cds-sandbox") / "data-load"
# provision 以秒计；超过这个时限的暂存目录只可能是“进程被杀”的残渣。
_STALE_STAGING_SECONDS = 3600


def _data_load_root() -> Path:
    return _DATA_LOAD_ROOT


def _sweep_stale_data_load_dirs(max_age_seconds: int = _STALE_STAGING_SECONDS) -> int:
    """Best-effort reclamation of crash residue under the staging root (A54).

    A request killed between provisioning and cleanup would otherwise leave
    decrypted supply data on the host across process restarts. Only directories
    older than ``max_age_seconds`` are removed, so a concurrent in-flight
    provisioning is never touched. Returns how many dirs were reclaimed.
    """
    root = _data_load_root()
    now = time.time()
    removed = 0
    try:
        user_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return 0
    for user_dir in user_dirs:
        try:
            session_dirs = [p for p in user_dir.iterdir() if p.is_dir()]
        except OSError:
            continue
        for session_dir in session_dirs:
            try:
                stale = (now - session_dir.stat().st_mtime) > max_age_seconds
            except OSError:
                continue
            if stale:
                shutil.rmtree(session_dir, ignore_errors=True)
                removed += 1
        try:  # 不留空壳目录
            user_dir.rmdir()
        except OSError:
            pass
    return removed


def _stage_decrypted_data(storage_service, resource, user_id: str) -> Path:
    """Blocking: download + decrypt + write the supply data into a private dir.

    Runs under ``asyncio.to_thread`` — never on the event loop (A52: a big load
    would otherwise stall every other request on this worker, heartbeats and
    health checks included). 0700 dir / 0600 file, and nothing here is kept on
    failure.
    """
    _sweep_stale_data_load_dirs()
    decrypted = storage_service.download_decrypted(resource.storage_path)
    staging = _data_load_root() / str(user_id) / uuid.uuid4().hex
    try:
        staging.mkdir(parents=True, exist_ok=True)
        staging.chmod(0o700)
        ext = resource.format or "csv"
        data_file = staging / f"{resource.id}.{ext}"
        data_file.write_bytes(decrypted)
        data_file.chmod(0o600)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)  # a half-written copy is still plaintext
        raise
    return data_file


def _reclaim_staged_data(data_file: Path | None) -> None:
    """Drop the decrypted staging copy — the sandbox holds its own copy (A54)."""
    if data_file is None:
        return
    staging = data_file.parent
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.parent.rmdir()  # only succeeds when the per-user dir is empty
    except OSError:
        pass


def _data_resource_block_reason(resource) -> str | None:
    """Why the product's data cannot be loaded (A53); None when it can."""
    if resource is None:
        return "data resource row not found"
    if resource.status != ResourceStatus.READY.value:
        return f"data resource is not READY (status={resource.status})"
    if not resource.storage_path:
        return "data resource has no stored object (storage_path is empty)"
    return None


async def _resolve_dev_contract(
    db,
    body: "CreateDevSessionRequest",
    data_product: DataProduct,
    current_user: User,
) -> Contract | None:
    """Gap A3: enforce the contract gate for dev sandboxes carrying data.

    When DEV_SANDBOX_REQUIRE_CONTRACT is enabled (production default), a dev
    session that loads a data product must reference an effective contract
    that covers the product. Returns None when the gate is disabled.
    """
    if not get_settings().DEV_SANDBOX_REQUIRE_CONTRACT:
        return None

    if not body.contract_id:
        raise HTTPException(
            status_code=400,
            detail="contract_id is required when attaching a data product "
                   "(DEV_SANDBOX_REQUIRE_CONTRACT is enabled)",
        )

    result = await db.execute(select(Contract).where(Contract.id == body.contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    if contract.status not in (ContractStatus.SIGNED.value, ContractStatus.ACTIVE.value):
        raise HTTPException(
            status_code=403,
            detail=f"Contract status '{contract.status}' does not permit sandbox access",
        )
    if contract.provider_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not a party to this contract")
    covered_ids = [str(p) for p in (contract.product_ids or [])]
    if str(data_product.id) not in covered_ids:
        raise HTTPException(
            status_code=403,
            detail="Contract does not cover this data product",
        )

    # A55: 合约的隔离档位条款在这条入口上同样生效。此前 sandbox_level 是客户端
    # 自由字符串，经 DevSandboxConfig 直通 runtime.provision —— 合约写了档位要求
    # 也从没人比对。判据与 sandbox_sessions._validate_session_contract 一致：
    # 条款为空 ⇒ 不设限；否则请求档位必须落在条款内。
    allowed_levels = {
        level.strip() for level in (contract.allowed_sandbox_levels or "").split(",") if level.strip()
    }
    if allowed_levels and body.sandbox_level not in allowed_levels:
        raise CDSError(
            f"Sandbox level '{body.sandbox_level}' not allowed by contract "
            f"(allowed: {', '.join(sorted(allowed_levels))})",
            code="VALIDATION_ERROR",
            http_status=400,
            detail={
                "sandbox_level": body.sandbox_level,
                "allowed_sandbox_levels": sorted(allowed_levels),
            },
        )
    return contract


def _dev_dp_budget(body: "CreateDevSessionRequest", contract: Contract | None) -> float | None:
    """DP budget for a dev session — inherited from the contract, never self-declared.

    Gap A3: the request body value was previously trusted directly, letting a
    client grant itself unlimited DP budget.
    """
    if contract is not None and contract.dp_epsilon_budget is not None:
        return float(contract.dp_epsilon_budget)
    if contract is not None:
        # Contract governs this session but has no DP budget — no DP allowed.
        return None
    return body.dp_epsilon_budget


class CreateDevSessionRequest(BaseModel):
    mode: str  # structured | unstructured | semi_structured
    data_product_id: uuid.UUID | None = None  # Link to a data product for data access
    # Gap A3: contract covering the data product — required when the
    # data_product_id is set and DEV_SANDBOX_REQUIRE_CONTRACT is enabled.
    contract_id: uuid.UUID | None = None
    sandbox_level: str = "L3"
    max_duration_seconds: int = 7200
    max_input_files: int = 100
    dp_epsilon_budget: float | None = None
    # W9 sync: accepted for API parity. Dev sessions only support kill expiry;
    # "pause" is rejected at creation (honest failure — no fake pause).
    idle_policy: str | None = None
    auto_resume: bool = False


class ExecuteCodeRequest(BaseModel):
    code: str
    language: str = "python"


class DevSessionResponse(BaseModel):
    session_id: str
    mode: str
    status: str
    container_id: str | None
    input_files: list[str]
    output_files: list[str]
    # A53: 显式披露供数到底加载了没有——“建了个开发沙箱”与“数据已挂载”
    # 不得同形（铁律：降级路径必须披露，宁可拒绝不可假装）。
    data_loaded: bool = False
    data_load_reason: str | None = None


def _dev_session_response(session) -> DevSessionResponse:
    """Response builder shared by create/list/get so the disclosure is readable
    on every surface, not only in the creation response."""
    return DevSessionResponse(
        session_id=session.session_id,
        mode=session.config.mode,
        status=session.status,
        container_id=session.container_id,
        input_files=session.input_files,
        output_files=session.output_files,
        data_loaded=bool(session.metadata.get("data_loaded", False)),
        data_load_reason=session.metadata.get("data_load_reason"),
    )


@router.post("/sessions", response_model=DevSessionResponse, status_code=201)
async def create_dev_session(
    body: CreateDevSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new data product development sandbox session.

    If data_product_id is provided, the associated encrypted data resource is
    decrypted and staged for provisioning; when the product carries a data
    resource that cannot be loaded the request is refused (409) instead of
    handing back an empty sandbox.
    """
    if body.mode not in (DevMode.STRUCTURED, DevMode.UNSTRUCTURED, DevMode.SEMI_STRUCTURED):
        raise HTTPException(status_code=400, detail=f"Invalid mode: {body.mode}")

    # W9 sync: dev sessions are ephemeral and support kill-only expiry —
    # pause semantics would be a lie here, so reject it explicitly.
    if body.idle_policy == "pause":
        raise HTTPException(status_code=400, detail="dev sandbox sessions do not support idle_policy='pause'")

    # Resolve data path from data product if provided
    data_path = None
    staged_file: Path | None = None
    data_product = None
    contract = None
    effective_dp_budget = body.dp_epsilon_budget
    data_loaded = False
    data_load_reason: str | None = None
    if body.data_product_id:
        result = await db.execute(select(DataProduct).where(DataProduct.id == body.data_product_id))
        data_product = result.scalar_one_or_none()
        if not data_product:
            raise HTTPException(status_code=404, detail="Data product not found")
        if data_product.provider_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not the owner of this data product")

        # Gap A3: contract enforcement for dev sandboxes carrying provider
        # data. Previously this path only checked product ownership — the DP
        # budget was self-declared by the request and no contract was needed,
        # bypassing the contract gate that governs every other sandbox entry.
        contract = await _resolve_dev_contract(db, body, data_product, current_user)
        effective_dp_budget = _dev_dp_budget(body, contract)

        # Load and decrypt the associated data resource
        if data_product.resource_id:
            res_result = await db.execute(select(DataResource).where(DataResource.id == data_product.resource_id))
            resource = res_result.scalar_one_or_none()
            blocked = _data_resource_block_reason(resource)
            if blocked is not None:
                # A53: 产品带着资源却加载不了 ⇒ 拒绝。以前这里静默跳过并照旧
                # 返回 201：调用方以为自己在密态供数上开发，实际面对的是一个
                # 空目录（铁律：宁可拒绝，不可假装）。
                detail = {
                    "data_product_id": str(data_product.id),
                    "resource_id": str(data_product.resource_id),
                    "resource_status": getattr(resource, "status", None),
                    "data_loaded": False,
                    "reason": blocked,
                }
                await audit_service.log_standalone(
                    action="dev_session.data_load_refused",
                    resource_type="dev_sandbox_session",
                    user_id=current_user.id,
                    detail={**detail, "mode": body.mode},
                )
                raise CDSError(
                    f"Cannot load the data product's data: {blocked}",
                    code="SESSION_STATE_CONFLICT",
                    http_status=409,
                    detail=detail,
                )
            from app.services.storage_service import storage_service
            try:
                # A52: 对象存储读 + 信封解密 + 落盘都是阻塞 I/O，走线程，
                # 不得占事件循环（同文件 execute、sandbox_sessions 同一先例）。
                staged_file = await asyncio.to_thread(
                    _stage_decrypted_data, storage_service, resource, str(current_user.id)
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to load data resource: {e}")
            data_path = str(staged_file)
            data_loaded = True
        else:
            # 产品本就未绑定数据资源：合法形态（纯开发），但与「已加载」不同形。
            data_load_reason = "data product has no attached data resource"
    else:
        data_load_reason = "no data product attached"

    config = DevSandboxConfig(
        mode=body.mode,
        sandbox_level=body.sandbox_level,
        max_duration_seconds=body.max_duration_seconds,
        max_input_files=body.max_input_files,
        dp_epsilon_budget=effective_dp_budget,
    )
    try:
        session = dev_sandbox.create_session(
            config,
            data_path=data_path,
            user_id=str(current_user.id),
        )
        # Store data product reference in session metadata
        if data_product:
            session.metadata["data_product_id"] = str(data_product.id)
            session.metadata["data_product_name"] = data_product.name
            if contract:
                session.metadata["contract_id"] = str(contract.id)
        session.metadata["data_loaded"] = data_loaded
        if data_load_reason:
            session.metadata["data_load_reason"] = data_load_reason
        await audit_service.log(
            db,
            action="dev_session.create",
            resource_type="dev_sandbox_session",
            user_id=current_user.id,
            detail={
                "session_id": session.session_id,
                "mode": body.mode,
                "data_product_id": str(data_product.id) if data_product else None,
                "contract_id": str(contract.id) if contract else None,
                "dp_epsilon_budget": effective_dp_budget,
                "contract_enforced": get_settings().DEV_SANDBOX_REQUIRE_CONTRACT,
                "data_loaded": data_loaded,
                "data_load_reason": data_load_reason,
            },
        )
    finally:
        # A54: provision 已把暂存文件复制进沙箱自己的工作区，宿主上这份解密
        # 明文到此为止 —— provision 抛错、审计失败同样回收。
        if staged_file is not None:
            await asyncio.to_thread(_reclaim_staged_data, staged_file)

    return _dev_session_response(session)


@router.get("/sessions", response_model=list[DevSessionResponse])
async def list_dev_sessions(
    current_user: User = Depends(get_current_user),
):
    """List development sandbox sessions owned by the current user."""
    sessions = await dev_sandbox.list_sessions()
    return [
        _dev_session_response(s)
        for s in sessions
        if s.user_id == str(current_user.id)
    ]


@router.get("/sessions/{session_id}", response_model=DevSessionResponse)
async def get_dev_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """Get a development sandbox session by ID."""
    session = await dev_sandbox.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not the owner of this session")
    return _dev_session_response(session)


@router.post("/sessions/{session_id}/execute")
async def execute_in_dev_sandbox(
    session_id: str,
    body: ExecuteCodeRequest,
    current_user: User = Depends(get_current_user),
):
    """Execute code in a development sandbox session."""
    session = await dev_sandbox.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not the owner of this session")
    if session.status != "running":
        raise HTTPException(status_code=400, detail=f"Session not running (status: {session.status})")
    result = await dev_sandbox.execute_code(session_id, body.code, body.language)
    output = result.get("output", "") or ""
    try:
        from app.services.output_security import inspect_text_output, inspection_to_report
        if output:
            inspection = inspect_text_output(
                output,
                user_id=str(current_user.id),
                session_id=session_id,
                sandbox_mode="develop",
            )
            result["output"] = inspection.redacted_output or ""
            result["security_report"] = inspection_to_report(inspection)
            result["output_blocked"] = not inspection.passed
        else:
            result["security_report"] = {
                "passed": True,
                "blocked": False,
                "stage_results": {"no_output": True},
                "findings": [],
                "findings_count": 0,
            }
            result["output_blocked"] = False
    except Exception as e:
        result["output"] = ""
        result["exit_code"] = -3
        result["security_report"] = {"passed": False, "blocked": True, "error": str(e)}
        result["output_blocked"] = True
    return result


@router.post("/sessions/{session_id}/terminate")
async def terminate_dev_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    """Terminate a development sandbox session."""
    session = await dev_sandbox.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not the owner of this session")
    success = await dev_sandbox.terminate_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "terminated", "session_id": session_id}


@router.get("/sessions/{session_id}/output/{output_name}")
async def get_dev_output(
    session_id: str,
    output_name: str,
    current_user: User = Depends(get_current_user),
):
    """Retrieve output from a development sandbox session."""
    try:
        safe_name = sanitize_filename(output_name)
    except PathTraversalError:
        raise HTTPException(status_code=400, detail="Invalid output filename")
    session = await dev_sandbox.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not the owner of this session")
    result = await dev_sandbox.get_output(session_id, safe_name)
    if not result.success:
        raise HTTPException(status_code=404, detail=result.error)
    metadata = result.metadata or {}
    output_path = Path(result.output_path or "")
    response = {
        "output_path": safe_name,
        "metadata": metadata,
    }
    size = int(metadata.get("size_bytes") or 0)
    if size > 1024 * 1024:
        response["security_report"] = {
            "passed": False,
            "blocked": True,
            "reason": "Output file is too large for inline inspection",
        }
        response["output_blocked"] = True
        return response

    raw = output_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        from app.utils.crypto import sm3_hash
        response["security_report"] = {
            "passed": True,
            "blocked": False,
            "binary_output": True,
            "size_bytes": size,
            "content_hash": sm3_hash(raw),
        }
        response["output_blocked"] = False
        return response

    from app.services.output_security import inspect_text_output, inspection_to_report
    inspection = inspect_text_output(
        text,
        user_id=str(current_user.id),
        session_id=session_id,
        sandbox_mode="develop",
    )
    response["content"] = inspection.redacted_output
    response["security_report"] = inspection_to_report(inspection)
    response["output_blocked"] = not inspection.passed
    return {
        **response,
    }


@router.get("/templates/sql")
async def get_sql_template(
    table_name: str = "data_table",
    current_user: User = Depends(get_current_user),
):
    """Get a SQL template for structured data exploration."""
    columns = [
        {"name": "id", "type": "INTEGER"},
        {"name": "name", "type": "TEXT"},
        {"name": "value", "type": "FLOAT"},
    ]
    return {"template": structured_tools.generate_sql_template(table_name, columns)}


@router.get("/templates/pandas")
async def get_pandas_template(
    csv_path: str = "/workspace/input/data.csv",
    current_user: User = Depends(get_current_user),
):
    """Get a pandas template for CSV analysis."""
    return {"template": structured_tools.generate_pandas_template(csv_path)}


@router.get("/templates/image-batch")
async def get_image_batch_template(
    current_user: User = Depends(get_current_user),
):
    """Get a template for batch image processing."""
    return {"template": unstructured_tools.generate_image_batch_template(
        "/workspace/input", "/workspace/output"
    )}


@router.get("/templates/json-etl")
async def get_json_etl_template(
    current_user: User = Depends(get_current_user),
):
    """Get a template for JSON ETL pipeline."""
    return {"template": semi_structured_tools.generate_json_etl_template(
        "/workspace/input/data.json", "/workspace/output/transformed.json"
    )}
