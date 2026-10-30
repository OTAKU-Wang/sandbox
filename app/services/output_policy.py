"""Contract-derived output policy resolution (gap E1).

The output gateway previously ran with caller-supplied limits (the
/output-control/gateway endpoint trusted request-body values) and the task
pipeline's output inspection never consulted the governing contract at all.
This module resolves the ACTUAL OutputPolicy from the contract bound to a
session, with conservative defaults when no contract applies.

A95c: a contract whose field classification state cannot be established
(unparseable product id, dangling resource row, DB error) gets a QUARANTINED
policy — 0 rows, no allowed format — instead of a policy that silently looks
like "this data was never classified".
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.output_gateway import OutputPolicy

logger = logging.getLogger(__name__)

_CONSERVATIVE_MAX_ROWS = 10000
_CONSERVATIVE_FORMATS = ["csv", "json"]

QUARANTINE_REASON_KEY = "field_classification_error"


class ClassificationUnavailableError(RuntimeError):
    """Field classification could not be established for a contract.

    Distinct from "this data carries no classifications": only the latter may
    compile to empty rules. Collapsing the two (the pre-A95c behaviour) made
    mask/deny fields vanish whenever the lookup raised, i.e. fail-open.
    """


def _quarantine_policy(contract, reason: str) -> OutputPolicy:
    """Policy for "classification state unknown": release nothing, say why.

    max_output_rows=0 plus an empty format allowlist cannot be widened by
    clamp_gateway_request, so both release paths (gateway and the text-output
    clamp) refuse instead of releasing possibly classified output.
    """
    logger.error(
        "[OutputPolicy] SECURITY: field classification lookup failed for contract %s (%s) — "
        "output release QUARANTINED (0 rows, no allowed format) instead of releasing "
        "output as if it were unclassified",
        getattr(contract, "id", "?"), reason,
    )
    rules = dict(contract.inspection_rule_set or {})
    rules.pop("field_classifications", None)
    rules[QUARANTINE_REASON_KEY] = reason
    return OutputPolicy(
        max_output_rows=0,
        allowed_output_formats=[],
        dp_epsilon_budget=(
            float(contract.dp_epsilon_budget) if contract.dp_epsilon_budget is not None else None
        ),
        inspection_rule_set=rules,
    )


async def build_output_policy(db: AsyncSession, contract_id: str | uuid.UUID | None) -> OutputPolicy:
    """Build the OutputPolicy enforced for a session's outputs.

    Args:
        db: Database session.
        contract_id: Contract governing the session (may be None/unresolvable).

    Returns:
        OutputPolicy from the contract, or conservative defaults when there
        is no contract. An unreadable contract still yields conservative
        defaults; an unreadable FIELD CLASSIFICATION yields a quarantined
        policy, because "no classification" would release masked/denied fields.
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
    try:
        field_rules = await _field_rules_for_contract(db, contract)
    except ClassificationUnavailableError as e:
        return _quarantine_policy(contract, str(e))
    if field_rules:
        inspection_rules["field_classifications"] = field_rules

    return OutputPolicy(
        max_output_rows=int(contract.max_output_rows or _CONSERVATIVE_MAX_ROWS),
        allowed_output_formats=_parse_formats(contract.allowed_output_formats),
        dp_epsilon_budget=float(contract.dp_epsilon_budget) if contract.dp_epsilon_budget is not None else None,
        inspection_rule_set=inspection_rules,
    )


async def _field_rules_for_contract(db: AsyncSession, contract) -> dict | None:
    """Compile the contracted resources' field classifications into gate rules.

    Lookup path matches the query-time wiring
    (app/api/sandbox_db._field_classifications_for_contract):
    contract.product_ids → DataProduct.resource_id → DataResource, with the
    HIGHER level winning when several products classify the same field.

    Returns None only when the state is positively "nothing classified".
    Raises ClassificationUnavailableError when it cannot be established.
    """
    from app.models.data_product import DataProduct
    from app.models.data_resource import DataResource
    from app.services.policy_compiler import policy_compiler

    product_ids = contract.product_ids or []
    if not product_ids:
        return None

    # uuid.UUID, never str: the SQLite UUID bind processor reads value.hex, so a
    # str raises AttributeError at bind time (the A95c red) while PostgreSQL only
    # matched it by implicit-cast luck. One comparable type, both dialects.
    wanted: list[uuid.UUID] = []
    for pid in product_ids:
        try:
            wanted.append(pid if isinstance(pid, uuid.UUID) else uuid.UUID(str(pid)))
        except (TypeError, ValueError) as e:
            raise ClassificationUnavailableError(
                f"contract product_ids contains a non-UUID entry: {pid!r}"
            ) from e

    merged: dict[str, int] = {}
    try:
        result = await db.execute(select(DataProduct).where(DataProduct.id.in_(wanted)))
        for product in result.scalars().all():
            if not product.resource_id:
                continue
            resource = await db.get(DataResource, product.resource_id)
            if resource is None:
                raise ClassificationUnavailableError(
                    f"product {product.id} references missing resource {product.resource_id}"
                )
            for field, raw_level in (resource.field_classifications or {}).items():
                try:
                    level = int(raw_level)
                except (TypeError, ValueError):
                    continue
                name = str(field)
                if level > merged.get(name, -1):
                    merged[name] = level
    except ClassificationUnavailableError:
        raise
    except Exception as e:
        raise ClassificationUnavailableError(f"{type(e).__name__}: {e}") from e

    rules = policy_compiler.field_rules_from_classifications(merged)
    if not rules.get("mask_fields") and not rules.get("deny_out_fields"):
        return None
    return rules


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
    quarantine_reason = (policy.inspection_rule_set or {}).get(QUARANTINE_REASON_KEY)
    if quarantine_reason:
        report["policy"][QUARANTINE_REASON_KEY] = quarantine_reason
        report["policy"]["release_quarantined"] = True
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
