"""Output Control API — DLP inspection and differential privacy."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User, UserRole
from app.models.sandbox_session import SandboxSession
from app.services.output_inspection import output_inspector, dp_engine
from app.services.output_gateway import output_gateway, OutputPolicy, VALID_FORMATS
from app.services.audit_service import audit_service

router = APIRouter()
_dp_budget_owners: dict[str, str] = {}
_output_session_owners: dict[str, str] = {}


def _can_manage_output_control(user: User) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.OPERATOR)


async def _authorize_dp_budget_session(
    db: AsyncSession,
    session_id: str,
    current_user: User,
    *,
    create_if_missing: bool = False,
) -> None:
    await _authorize_owned_session_id(
        db,
        session_id,
        current_user,
        owner_store=_dp_budget_owners,
        resource_name="DP budget",
        create_if_missing=create_if_missing,
    )


async def _authorize_output_session(
    db: AsyncSession,
    session_id: str | None,
    current_user: User,
    *,
    create_if_missing: bool = True,
) -> None:
    if not session_id:
        return
    await _authorize_owned_session_id(
        db,
        session_id,
        current_user,
        owner_store=_output_session_owners,
        resource_name="output control",
        create_if_missing=create_if_missing,
    )


async def _authorize_owned_session_id(
    db: AsyncSession,
    session_id: str,
    current_user: User,
    *,
    owner_store: dict[str, str],
    resource_name: str,
    create_if_missing: bool = False,
) -> None:
    if _can_manage_output_control(current_user):
        return

    try:
        session_uuid = uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        session_uuid = None

    if session_uuid:
        session = await db.get(SandboxSession, session_uuid)
        if session:
            if session.user_id != current_user.id:
                raise HTTPException(status_code=403, detail=f"Not your {resource_name} session")
            return

    owner_id = owner_store.get(session_id)
    if owner_id:
        if owner_id != str(current_user.id):
            raise HTTPException(status_code=403, detail=f"Not your {resource_name} session")
        return

    if create_if_missing:
        owner_store[session_id] = str(current_user.id)
        return
    raise HTTPException(status_code=404, detail=f"{resource_name} session {session_id} not found")


class InspectRequest(BaseModel):
    output: str
    session_id: str
    dp_epsilon: float | None = None


class DPNoiseRequest(BaseModel):
    value: float
    sensitivity: float
    epsilon: float
    mechanism: str = "laplace"  # "laplace" or "gaussian"
    delta: float = 1e-5


class ReconstructionCheckRequest(BaseModel):
    output_rows: list[dict]
    source_rows: list[dict]
    threshold: float | None = None  # default 5%


class FieldReconstructionCheckRequest(BaseModel):
    output_values: list[str]
    source_values: list[str]
    threshold: float | None = None  # default 5%


@router.post("/inspect")
async def inspect_output(
    body: InspectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run DLP inspection pipeline on sandbox output.

    Stages: Format → DLP Scan → DP → Watermark → Signature → Approval
    """
    await _authorize_output_session(db, body.session_id, current_user)
    result = output_inspector.inspect(
        body.output,
        str(current_user.id),
        body.session_id,
        dp_epsilon=body.dp_epsilon,
    )

    # Audit log
    await audit_service.log(
        db,
        action="output_inspection_pass" if result.passed else "output_inspection_fail",
        resource_type="sandbox_session",
        resource_id=body.session_id,
        user_id=current_user.id,
        detail={
            "passed": result.passed,
            "findings_count": len(result.findings),
            "dp_applied": result.dp_applied,
            "stage_results": result.stage_results,
        },
    )

    return {
        "passed": result.passed,
        "stage_results": result.stage_results,
        "findings": [
            {"stage": f.stage, "severity": f.severity, "type": f.type, "message": f.message, "samples": f.samples}
            for f in result.findings
        ],
        "redacted_output": result.redacted_output,
        "watermark": result.watermark,
        "signature": result.signature,
        "dp_applied": result.dp_applied,
    }


@router.post("/dp/noise")
async def apply_dp_noise(
    body: DPNoiseRequest,
    current_user: User = Depends(get_current_user),
):
    """Apply differential privacy noise to a numerical value."""
    if body.mechanism == "laplace":
        noisy = dp_engine.add_laplace_noise(body.value, body.sensitivity, body.epsilon)
    elif body.mechanism == "gaussian":
        noisy = dp_engine.add_gaussian_noise(body.value, body.sensitivity, body.epsilon, body.delta)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown mechanism: {body.mechanism}")

    return {
        "original": body.value,
        "noisy_value": round(noisy, 6),
        "mechanism": body.mechanism,
        "epsilon": body.epsilon,
        "sensitivity": body.sensitivity,
    }


@router.post("/dp/budget/init")
async def init_dp_budget(
    session_id: str,
    epsilon: float = Query(..., gt=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Initialize DP budget for a sandbox session."""
    await _authorize_dp_budget_session(db, session_id, current_user, create_if_missing=True)
    dp_engine.init_budget(session_id, epsilon)
    return {"session_id": session_id, "epsilon_allocated": epsilon}


@router.get("/dp/budget/{session_id}")
async def get_dp_budget(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get remaining DP budget for a session."""
    await _authorize_dp_budget_session(db, session_id, current_user)
    remaining = dp_engine.get_remaining(session_id)
    return {"session_id": session_id, "epsilon_remaining": remaining}


@router.post("/reconstruction-check")
async def check_data_reconstruction(
    body: ReconstructionCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check if output data could reconstruct source data (row-level comparison).

    Blocks if exact row match rate exceeds threshold (default 5%).
    """
    passed, match_rate = output_inspector.check_data_reconstruction(
        body.output_rows, body.source_rows, body.threshold,
    )

    await audit_service.log(
        db,
        action="reconstruction_check_pass" if passed else "reconstruction_check_fail",
        resource_type="output",
        resource_id=None,
        user_id=current_user.id,
        detail={"match_rate": round(match_rate, 4), "threshold": body.threshold or 0.05, "passed": passed},
    )

    return {
        "passed": passed,
        "match_rate": round(match_rate, 4),
        "threshold": body.threshold or 0.05,
        "output_rows": len(body.output_rows),
        "source_rows": len(body.source_rows),
    }


@router.post("/field-reconstruction-check")
async def check_field_reconstruction(
    body: FieldReconstructionCheckRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Check if output field values could reconstruct source field (set overlap).

    Blocks if set overlap rate exceeds threshold (default 5%).
    Detects SELECT DISTINCT attacks on sensitive columns.
    """
    passed, overlap_rate = output_inspector.check_field_reconstruction(
        body.output_values, body.source_values, body.threshold,
    )

    await audit_service.log(
        db,
        action="field_reconstruction_check_pass" if passed else "field_reconstruction_check_fail",
        resource_type="output",
        resource_id=None,
        user_id=current_user.id,
        detail={"overlap_rate": round(overlap_rate, 4), "threshold": body.threshold or 0.05, "passed": passed},
    )

    return {
        "passed": passed,
        "overlap_rate": round(overlap_rate, 4),
        "threshold": body.threshold or 0.05,
        "output_unique_values": len(set(body.output_values)),
        "source_unique_values": len(set(body.source_values)),
    }


class GatewayRequest(BaseModel):
    data: list[dict]
    output_format: str = "csv"
    max_output_rows: int = 10000
    allowed_output_formats: list[str] | None = None
    dp_epsilon_budget: float | None = None
    session_id: str | None = None


@router.post("/gateway")
async def process_output_gateway(
    body: GatewayRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Process output through the gateway: format validation + row limiting + conversion.

    Enforces contract-derived limits (max_output_rows, allowed_output_formats).
    Returns processed output in the requested format.
    """
    await _authorize_output_session(db, body.session_id, current_user)
    policy = OutputPolicy(
        max_output_rows=body.max_output_rows,
        allowed_output_formats=body.allowed_output_formats or ["csv", "json"],
        dp_epsilon_budget=body.dp_epsilon_budget,
    )

    result = output_gateway.process(
        data=body.data,
        output_format=body.output_format,
        policy=policy,
        user_id=str(current_user.id),
        session_id=body.session_id or "",
    )

    await audit_service.log(
        db,
        action="output_gateway_pass" if result.success else "output_gateway_fail",
        resource_type="output",
        resource_id=body.session_id,
        user_id=current_user.id,
        detail={
            "success": result.success,
            "output_format": result.output_format,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "findings": result.findings,
            "security_report": result.security_report,
            "watermark": result.watermark,
            "signature": result.signature,
            "error": result.error,
        },
    )

    return {
        "success": result.success,
        "output_format": result.output_format,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "output_data": result.output_data if result.success else None,
        "findings": result.findings,
        "security_report": result.security_report,
        "watermark": result.watermark,
        "signature": result.signature,
        "error": result.error,
    }


@router.get("/formats")
async def list_output_formats():
    """List supported output formats."""
    return {"formats": list(VALID_FORMATS)}
