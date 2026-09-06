"""Contract-derived output policy resolution (gap E1).

The output gateway previously ran with caller-supplied limits (the
/output-control/gateway endpoint trusted request-body values) and the task
pipeline's output inspection never consulted the governing contract at all.
This module resolves the ACTUAL OutputPolicy from the contract bound to a
session, with conservative defaults when no contract applies.
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.output_gateway import OutputPolicy

logger = logging.getLogger(__name__)

_CONSERVATIVE_MAX_ROWS = 10000
_CONSERVATIVE_FORMATS = ["csv", "json"]


async def build_output_policy(db: AsyncSession, contract_id: str | uuid.UUID | None) -> OutputPolicy:
    """Build the OutputPolicy enforced for a session's outputs.

    Args:
        db: Database session.
        contract_id: Contract governing the session (may be None/unresolvable).

    Returns:
        OutputPolicy from the contract, or conservative defaults when there
        is no contract. Failures resolve to defaults, never to an open policy.
    """
    policy = OutputPolicy(
        max_output_rows=_CONSERVATIVE_MAX_ROWS,
        allowed_output_formats=list(_CONSERVATIVE_FORMATS),
    )
    if contract_id in (None, ""):
        return policy
    try:
        cid = uuid.UUID(str(contract_id))
    except (TypeError, ValueError):
        return policy

    try:
        from app.models.contract import Contract
        result = await db.execute(select(Contract).where(Contract.id == cid))
        contract = result.scalar_one_or_none()
    except Exception as e:
        logger.warning("[OutputPolicy] Contract lookup failed (%s): %s", contract_id, e)
        return policy

    if not contract:
        return policy

    inspection_rules = dict(contract.inspection_rule_set or {})
    # Gap A5/T7: enrich the inspection rules with the field-level
    # classification of the contracted data product's resource so masked /
    # denied fields are enforced on every output release.
    field_rules = await _field_rules_for_contract(db, contract)
    if field_rules:
        inspection_rules["field_classifications"] = field_rules

    return OutputPolicy(
        max_output_rows=int(contract.max_output_rows or _CONSERVATIVE_MAX_ROWS),
        allowed_output_formats=_parse_formats(contract.allowed_output_formats),
        dp_epsilon_budget=float(contract.dp_epsilon_budget) if contract.dp_epsilon_budget is not None else None,
        inspection_rule_set=inspection_rules,
    )


async def _field_rules_for_contract(db: AsyncSession, contract) -> dict | None:
    """Resolve the field classification rules of the first contracted product.

    Returns None when the product/resource has no field_classifications.
    """
    try:
        from app.models.data_product import DataProduct
        from app.models.data_resource import DataResource
        from app.services.policy_compiler import policy_compiler
        product_ids = contract.product_ids or []
        if not product_ids:
            return None
        result = await db.execute(
            select(DataProduct).where(DataProduct.id.in_([str(p) for p in product_ids])).limit(1)
        )
        product = result.scalars().first()
        if not product or not product.resource_id:
            return None
        resource = await db.get(DataResource, product.resource_id)
        if not resource or not resource.field_classifications:
            return None
        rules = policy_compiler.field_rules_from_classifications(resource.field_classifications)
        if not rules.get("mask_fields") and not rules.get("deny_out_fields"):
            return None
        return rules
    except Exception as e:
        logger.warning("[OutputPolicy] Field classification resolution failed: %s", e)
        return None


def _parse_formats(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(f).strip() for f in raw if str(f).strip()]
    if isinstance(raw, str) and raw.strip():
        return [f.strip() for f in raw.split(",") if f.strip()]
    return list(_CONSERVATIVE_FORMATS)


async def session_contract_id(db: AsyncSession, session_id: str | uuid.UUID) -> str | None:
    """Resolve the contract_id bound to a sandbox session."""
    try:
        sid = uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        return None
    try:
        from app.models.sandbox_session import SandboxSession
        session = await db.get(SandboxSession, sid)
        return session.contract_id if session else None
    except Exception as e:
        logger.warning("[OutputPolicy] Session lookup failed (%s): %s", session_id, e)
        return None


async def enforce_text_output_policy(
    db: AsyncSession,
    session_id: str | uuid.UUID | None,
    redacted_output: str,
    report: dict,
) -> tuple[str, dict]:
    """Clamp a text output against the governing contract's row limit.

    Used by the task pipeline output-inspecting handler and the legacy task
    worker so both paths enforce the same contract policy. The report is
    annotated with the applied policy for auditability.
    """
    contract_id = await session_contract_id(db, session_id) if session_id else None
    policy = await build_output_policy(db, contract_id)

    if redacted_output:
        lines = redacted_output.splitlines()
        if len(lines) > policy.max_output_rows:
            redacted_output = "\n".join(lines[: policy.max_output_rows])
            report["policy_row_limit_truncated"] = True
            report["policy_row_limit"] = policy.max_output_rows

    report["policy"] = {
        "max_output_rows": policy.max_output_rows,
        "allowed_output_formats": policy.allowed_output_formats,
        "source": "contract" if contract_id else "default",
    }
    return redacted_output, report


def clamp_gateway_request(
    requested_max_rows: int | None,
    requested_formats: list[str] | None,
    policy: OutputPolicy,
) -> tuple[int, list[str]]:
    """Clamp caller-supplied gateway limits by the contract policy (gap E1).

    The request may only NARROW the contract's allowance, never widen it.
    """
    effective_rows = policy.max_output_rows
    if requested_max_rows is not None and requested_max_rows > 0:
        effective_rows = min(int(requested_max_rows), policy.max_output_rows)

    contract_formats = {f.strip() for f in policy.allowed_output_formats}
    if requested_formats:
        requested = {str(f).strip() for f in requested_formats if str(f).strip()}
        effective_formats = [f for f in policy.allowed_output_formats if f in requested]
        if not effective_formats:
            # Requested nothing the contract allows — fall back to the
            # contract's first allowed format rather than widening.
            effective_formats = list(policy.allowed_output_formats)
    else:
        effective_formats = list(policy.allowed_output_formats)

    return effective_rows, [f for f in effective_formats if f in contract_formats]
