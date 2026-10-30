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

# Ad-hoc owner claims (A21 item 3): caller-chosen output-control ids (labels that
# are NOT sandbox session rows) are bound to whoever used them first, so a second
# user cannot post under someone else's id. These tables used to be unbounded
# module-level dicts — any authenticated caller could grow them without limit by
# presenting fresh ids. `_OWNER_CLAIM_CAP` bounds them, oldest claim first. They
# stay process-local caches, which is precisely why a claim may only ever back an
# *ad-hoc* label: an id shaped like a session is resolved against the sessions
# table and is never claimed here.
_OWNER_CLAIM_CAP = 4096

_dp_budget_owners: dict[str, str] = {}
_output_session_owners: dict[str, str] = {}


def _can_manage_output_control(user: User) -> bool:
    return user.role in (UserRole.ADMIN, UserRole.OPERATOR)


def _claim_owner(store: dict[str, str], session_id: str, user_id: str) -> None:
    """Record (or refresh) an ad-hoc owner claim, keeping the store bounded.

    Insertion order is the eviction order; re-claiming moves the id to the young
    end, so an actively used session is not evicted by its own age.
    """
    store.pop(session_id, None)
    store[session_id] = user_id
    while len(store) > _OWNER_CLAIM_CAP:
        store.pop(next(iter(store)), None)


def _as_session_uuid(session_id: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        return None


async def _authorize_owned_session_id(
    db: AsyncSession,
    session_id: str,
    current_user: User,
    *,
    owner_store: dict[str, str],
    resource_name: str,
    allow_ad_hoc_claim: bool = False,
) -> None:
    """Bind an output-control operation to a session the caller may act on.

    Fail-closed (A21): an id shaped like a sandbox session must exist as one and
    belong to the caller. It used to fall through to the claim table instead, so
    guessing an unused session id minted ownership for the guesser and locked the
    real owner out with 403 afterwards — an authorization failure had been turned
    into a grant of ownership. Only ids that cannot be sessions (caller-chosen
    labels) reach the claim table, and only where `allow_ad_hoc_claim` is set.
    """
    if _can_manage_output_control(current_user):
        return

    session_uuid = _as_session_uuid(session_id)
    if session_uuid is not None:
        session = await db.get(SandboxSession, session_uuid)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=f"{resource_name} session {session_id} not found",
            )
        if session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail=f"Not your {resource_name} session")
        return

    owner_id = owner_store.get(session_id)
    if owner_id is not None:
        if owner_id != str(current_user.id):
            raise HTTPException(status_code=403, detail=f"Not your {resource_name} session")
        # Keep the claim young: eviction is by age, not by hit count.
        _claim_owner(owner_store, session_id, str(current_user.id))
        return

    if allow_ad_hoc_claim:
        _claim_owner(owner_store, session_id, str(current_user.id))
        return
    raise HTTPException(
        status_code=404, detail=f"{resource_name} session {session_id} not found"
    )


async def _authorize_dp_budget_session(
    db: AsyncSession,
    session_id: str,
    current_user: User,
    *,
    allow_ad_hoc_claim: bool = False,
) -> None:
    await _authorize_owned_session_id(
        db,
        session_id,
        current_user,
        owner_store=_dp_budget_owners,
        resource_name="DP budget",
        allow_ad_hoc_claim=allow_ad_hoc_claim,
    )


async def _authorize_output_session(
    db: AsyncSession,
    session_id: str | None,
    current_user: User,
    *,
    allow_ad_hoc_claim: bool = True,
) -> None:
    if not session_id:
        raise HTTPException(
            status_code=404,
            detail="output control session_id is required",
        )
    await _authorize_owned_session_id(
        db,
        session_id,
        current_user,
        owner_store=_output_session_owners,
        resource_name="output control",
        allow_ad_hoc_claim=allow_ad_hoc_claim,
    )


async def _require_bound_output_session(
    db: AsyncSession,
    session_id: str | None,
    current_user: User,
) -> str:
    """A21: a data release must be bound to a session the caller owns.

    Without the binding neither the ownership check nor the governing contract
    can be resolved, and `build_output_policy(db, None)` hands back the platform
    default allowance — so omitting `session_id` (or inventing a label) was a one
    field escape from a stricter contract, which Gap E1 forbids. The release path
    therefore accepts only a real session; ad-hoc labels are refused too.

    Returns the (now known to be present) session id.
    """
    try:
        if not session_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "session_id is required: an unbound output release would bypass "
                    "session ownership and the contract's output allowance"
                ),
            )
        await _authorize_output_session(
            db, session_id, current_user, allow_ad_hoc_claim=False
        )
    except HTTPException as exc:
        await _audit_denial("output_gateway_denied", current_user, session_id, exc)
        raise
    return session_id


async def _audit_denial(
    action: str,
    current_user: User,
    session_id: str | None,
    exc: HTTPException,
) -> None:
    """Leave an audit trail for a refusal, outside the request transaction.

    `get_db` rolls the request session back when an endpoint raises, so an
    in-transaction `audit_service.log` before the raise would be swallowed in
    production.
    """
    await audit_service.log_standalone(
        action=action,
        resource_type="output",
        resource_id=session_id,
        user_id=current_user.id,
        detail={
            "reason": str(exc.detail),
            "status_code": exc.status_code,
            "session_id": session_id,
        },
    )


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
    try:
        await _authorize_output_session(db, body.session_id, current_user)
    except HTTPException as exc:
        await _audit_denial("output_inspection_denied", current_user, body.session_id, exc)
        raise
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
    try:
        await _authorize_dp_budget_session(
            db, session_id, current_user, allow_ad_hoc_claim=True
        )
    except HTTPException as exc:
        await _audit_denial("dp_budget_denied", current_user, session_id, exc)
        raise
    dp_engine.init_budget(session_id, epsilon)
    return {"session_id": session_id, "epsilon_allocated": epsilon}


@router.get("/dp/budget/{session_id}")
async def get_dp_budget(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get remaining DP budget for a session."""
    try:
        await _authorize_dp_budget_session(
            db, session_id, current_user, allow_ad_hoc_claim=True
        )
    except HTTPException as exc:
        await _audit_denial("dp_budget_denied", current_user, session_id, exc)
        raise
    # A192e 口径。这个端点走的是 ledger 的同步快路：能连上预算后端时它是跨进程的（Redis），
    # 只有连不上时才是进程内的——所以旧注释说的「process-local」已不是事实。更要紧的是
    # `budget_tracked` 答不了「还剩多少」：可读且不够、与根本读不到，两者都回 remaining=0.0，
    # 于是「无法核实」在 API 上被说成「已耗尽」。第三态由 `budget_state` 回答
    # （sufficient / exhausted / unverified / unallocated，与 InspectionResult.dp_budget_state
    # 同一词汇表），`budget_source` 说清数字来源。本路径仍不保存分配总量，所以仍报未知
    # 而不是从「还剩多少」反推一个总量出来。
    from app.services.dp_budget import dp_budget_ledger

    state = dp_budget_ledger.budget_state_sync(session_id)
    if state["source"] == "unverified":
        budget_state = "unverified"
    elif not state["tracked"]:
        budget_state = "unallocated"
    else:
        budget_state = "sufficient" if state["remaining"] > 0 else "exhausted"

    return {
        "session_id": session_id,
        # 读不到时剩余量是未知，不是 0：别把未核实压成一个可算术的数字。
        "epsilon_remaining": None if budget_state == "unverified" else state["remaining"],
        "epsilon_allocated": None,
        "budget_tracked": state["tracked"],
        "budget_state": budget_state,
        "budget_source": state["source"],
        "budget_authoritative": state["authoritative"],
    }


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

    Gap E1: limits are bounded by the CONTRACT governing the session — the
    request may only narrow the contract's allowance, never widen it
    (previously max_output_rows/formats were caller-supplied and trusted).
    """
    session_id = await _require_bound_output_session(db, body.session_id, current_user)

    from app.services.output_policy import (
        build_output_policy,
        session_contract_id,
        clamp_gateway_request,
    )
    contract_id = await session_contract_id(db, session_id)
    contract_policy = await build_output_policy(db, contract_id)
    effective_rows, effective_formats = clamp_gateway_request(
        body.max_output_rows, body.allowed_output_formats, contract_policy,
    )
    effective_epsilon = body.dp_epsilon_budget
    if contract_policy.dp_epsilon_budget is not None:
        effective_epsilon = (
            min(body.dp_epsilon_budget, contract_policy.dp_epsilon_budget)
            if body.dp_epsilon_budget is not None
            else contract_policy.dp_epsilon_budget
        )
    policy = OutputPolicy(
        max_output_rows=effective_rows,
        allowed_output_formats=effective_formats,
        dp_epsilon_budget=effective_epsilon,
        inspection_rule_set=contract_policy.inspection_rule_set,
    )

    result = output_gateway.process(
        data=body.data,
        output_format=body.output_format,
        policy=policy,
        user_id=str(current_user.id),
        session_id=session_id,
    )

    await audit_service.log(
        db,
        action="output_gateway_pass" if result.success else "output_gateway_fail",
        resource_type="output",
        resource_id=session_id,
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
            "empty_output": result.empty_output,
            "contract_id": contract_id,
            "effective_max_output_rows": effective_rows,
            "effective_output_formats": effective_formats,
            "requested_max_output_rows": body.max_output_rows,
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
        "empty_output": result.empty_output,
    }


@router.get("/formats")
async def list_output_formats():
    """List supported output formats."""
    return {"formats": list(VALID_FORMATS)}
