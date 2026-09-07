"""Data Product Development Sandbox API."""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.config import get_settings
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


@router.post("/sessions", response_model=DevSessionResponse, status_code=201)
async def create_dev_session(
    body: CreateDevSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new data product development sandbox session.

    If data_product_id is provided, the associated encrypted data resource is
    decrypted and loaded into the sandbox workspace automatically.
    """
    if body.mode not in (DevMode.STRUCTURED, DevMode.UNSTRUCTURED, DevMode.SEMI_STRUCTURED):
        raise HTTPException(status_code=400, detail=f"Invalid mode: {body.mode}")

    # W9 sync: dev sessions are ephemeral and support kill-only expiry —
    # pause semantics would be a lie here, so reject it explicitly.
    if body.idle_policy == "pause":
        raise HTTPException(status_code=400, detail="dev sandbox sessions do not support idle_policy='pause'")

    # Resolve data path from data product if provided
    data_path = None
    data_product = None
    contract = None
    effective_dp_budget = body.dp_epsilon_budget
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
            if resource and resource.status == ResourceStatus.READY.value and resource.storage_path:
                from app.services.storage_service import storage_service
                try:
                    decrypted = storage_service.download_decrypted(resource.storage_path)
                    # Write to sandbox workspace
                    sandbox_dir = Path("/tmp/cds-sandbox") / "data-load" / str(current_user.id) / uuid.uuid4().hex
                    sandbox_dir.mkdir(parents=True, exist_ok=True)
                    sandbox_dir.chmod(0o700)
                    ext = resource.format or "csv"
                    data_file = sandbox_dir / f"{resource.id}.{ext}"
                    data_file.write_bytes(decrypted)
                    data_file.chmod(0o600)
                    data_path = str(data_file)
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Failed to load data resource: {e}")

    config = DevSandboxConfig(
        mode=body.mode,
        sandbox_level=body.sandbox_level,
        max_duration_seconds=body.max_duration_seconds,
        max_input_files=body.max_input_files,
        dp_epsilon_budget=effective_dp_budget,
    )
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
        },
    )
    return DevSessionResponse(
        session_id=session.session_id,
        mode=session.config.mode,
        status=session.status,
        container_id=session.container_id,
        input_files=session.input_files,
        output_files=session.output_files,
    )


@router.get("/sessions", response_model=list[DevSessionResponse])
async def list_dev_sessions(
    current_user: User = Depends(get_current_user),
):
    """List development sandbox sessions owned by the current user."""
    sessions = await dev_sandbox.list_sessions()
    return [
        DevSessionResponse(
            session_id=s.session_id,
            mode=s.config.mode,
            status=s.status,
            container_id=s.container_id,
            input_files=s.input_files,
            output_files=s.output_files,
        )
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
    return DevSessionResponse(
        session_id=session.session_id,
        mode=session.config.mode,
        status=session.status,
        container_id=session.container_id,
        input_files=session.input_files,
        output_files=session.output_files,
    )


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
