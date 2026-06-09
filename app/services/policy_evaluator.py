"""Policy Evaluator — PDP (Policy Decision Point) runtime.

Evaluates access requests against compiled Rego policy bundles.
Delegates to OPA sidecar when available; falls back to Python evaluation.
Returns ALLOW/DENY decisions with field-level ACL enforcement.
"""
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.policy_bundle import PolicyBundle
from app.models.contract import Contract

logger = logging.getLogger(__name__)


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class AccessRequest:
    """An access request to be evaluated by the PDP."""
    user_id: uuid.UUID
    contract_id: uuid.UUID
    operation: str  # read, query, train, export
    sandbox_mode: str  # query, train, develop, application
    resource_path: str = ""
    fields: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    session_id: str = ""
    epsilon_cost: float = 0.1  # Default DP epsilon cost per request


@dataclass
class PolicyDecision:
    """PDP evaluation result."""
    decision: Decision
    reason: str
    allowed_fields: list[str] = field(default_factory=list)
    denied_fields: list[str] = field(default_factory=list)
    field_masks: dict[str, str] = field(default_factory=dict)  # field → mask_pattern
    quota_limits: dict | None = None


class PolicyEvaluator:
    """PDP — evaluates access requests against policy bundles.

    Delegates to OPA sidecar for Rego evaluation when available.
    Falls back to Python evaluation if OPA is unreachable.
    """

    async def evaluate(self, request: AccessRequest, db: AsyncSession) -> PolicyDecision:
        """Evaluate an access request against the active policy bundle."""
        # Load active policy bundle for the contract
        result = await db.execute(
            select(PolicyBundle)
            .where(PolicyBundle.contract_id == request.contract_id)
            .order_by(PolicyBundle.version.desc())
            .limit(1)
        )
        bundle = result.scalar_one_or_none()

        if not bundle:
            logger.warning(f"No policy bundle for contract {request.contract_id}")
            return PolicyDecision(
                decision=Decision.DENY,
                reason="No active policy bundle found for contract",
            )

        # Verify bundle integrity
        from app.utils.crypto import sm3_hash
        expected_hash = sm3_hash(bundle.rego_source.encode())
        if expected_hash != bundle.integrity_hash:
            logger.error(f"Policy bundle integrity check failed: {bundle.policy_id}")
            return PolicyDecision(
                decision=Decision.DENY,
                reason="Policy bundle integrity check failed",
            )

        # Try OPA evaluation first
        opa_result = await self._evaluate_via_opa(request, bundle)
        if opa_result is not None:
            decision = opa_result
        else:
            # Fallback: Python evaluation
            logger.warning("OPA unavailable, falling back to Python PDP")
            decision = self._evaluate_python(request, bundle)

        # DP budget check — only apply on successful policy evaluation
        if decision.decision == Decision.ALLOW and request.epsilon_cost > 0:
            budget_check = await self._check_dp_budget(request, db)
            if not budget_check:
                return PolicyDecision(
                    decision=Decision.DENY,
                    reason="DP epsilon budget exhausted for this contract",
                )

        return decision

    async def _evaluate_via_opa(self, request: AccessRequest, bundle: PolicyBundle) -> PolicyDecision | None:
        """Evaluate via OPA sidecar. Returns None if OPA is unavailable."""
        try:
            from app.services.opa_client import opa_client

            # Ensure policy is pushed to OPA
            contract_id = str(request.contract_id)
            package_path = f"cds.policies.{contract_id}"

            # Push policy if not already in OPA
            pushed = await opa_client.push_policy(package_path, bundle.rego_source)
            if not pushed:
                return None

            # Build OPA input document
            opa_input = {
                "user_id": str(request.user_id),
                "contract_id": contract_id,
                "operation": request.operation,
                "sandbox_mode": request.sandbox_mode,
                "resource_path": request.resource_path,
                "fields": request.fields,
                "metadata": request.metadata,
            }

            # Query OPA
            result = await opa_client.evaluate(package_path, opa_input)
            if result is None:
                return None

            # Parse OPA response
            allowed = result.get("result", False)
            if allowed:
                # OPA allowed — still apply field ACL from bundle
                field_acl = bundle.field_acl or {}
                allowed_fields, denied_fields, field_masks = self._evaluate_field_acl(
                    request.fields, field_acl
                )
                return PolicyDecision(
                    decision=Decision.ALLOW,
                    reason="OPA policy evaluation passed",
                    allowed_fields=allowed_fields,
                    denied_fields=denied_fields,
                    field_masks=field_masks,
                )
            else:
                return PolicyDecision(
                    decision=Decision.DENY,
                    reason="OPA policy evaluation denied",
                )
        except Exception as e:
            logger.error(f"OPA evaluation error: {e}")
            return None

    def _evaluate_python(self, request: AccessRequest, bundle: PolicyBundle) -> PolicyDecision:
        """Pure Python PDP fallback."""
        # Check operation allowed
        allowed_ops = bundle.allowed_ops or []
        if request.operation not in allowed_ops:
            return PolicyDecision(
                decision=Decision.DENY,
                reason=f"Operation '{request.operation}' not in allowed ops: {allowed_ops}",
            )

        # Check sandbox mode
        allowed_modes = bundle.sandbox_modes or []
        if request.sandbox_mode not in allowed_modes:
            return PolicyDecision(
                decision=Decision.DENY,
                reason=f"Sandbox mode '{request.sandbox_mode}' not allowed: {allowed_modes}",
            )

        # Evaluate field-level ACL
        field_acl = bundle.field_acl or {}
        allowed_fields, denied_fields, field_masks = self._evaluate_field_acl(
            request.fields, field_acl
        )

        # If specific fields were requested but none are allowed, deny
        if request.fields and not allowed_fields:
            return PolicyDecision(
                decision=Decision.DENY,
                reason="All requested fields are denied by field ACL",
            )

        return PolicyDecision(
            decision=Decision.ALLOW,
            reason="Policy evaluation passed (Python fallback)",
            allowed_fields=allowed_fields,
            denied_fields=denied_fields,
            field_masks=field_masks,
            quota_limits=bundle.allowed_ops,
        )

    async def _check_dp_budget(self, request: AccessRequest, db: AsyncSession) -> bool:
        """Check and consume DP epsilon budget. Returns True if budget is sufficient."""
        from app.models.contract import Contract
        from app.services.dp_budget import dp_budget_ledger

        # Check if contract has DP budget configured
        try:
            result = await db.execute(
                select(Contract).where(Contract.id == request.contract_id)
            )
            contract = result.scalar_one_or_none()
            if contract is None:
                logger.warning("[DP] Contract %s not found during budget check", request.contract_id)
                return False
            if not hasattr(contract, "dp_epsilon_budget"):
                return True  # Legacy tests/mocks without DP field configured.
            if not getattr(contract, 'dp_epsilon_budget', None):
                return True  # No DP budget configured — allow
        except Exception as e:
            logger.warning("[DP] Budget check failed closed for contract %s: %s", request.contract_id, e)
            return False

        # Check remaining budget
        remaining = await dp_budget_ledger.get_remaining(db, str(request.contract_id))
        if remaining < request.epsilon_cost:
            logger.warning(
                f"[DP] Budget exhausted for contract {request.contract_id}: "
                f"remaining={remaining:.4f}, required={request.epsilon_cost:.4f}"
            )
            return False

        # Consume epsilon
        consumed = await dp_budget_ledger.consume(
            db, str(request.contract_id), request.session_id,
            request.epsilon_cost, operation=request.operation,
        )
        if consumed:
            logger.info(
                f"[DP] Consumed epsilon={request.epsilon_cost:.4f} for "
                f"contract={request.contract_id}, op={request.operation}, "
                f"remaining={remaining - request.epsilon_cost:.4f}"
            )
        return consumed

    def _evaluate_field_acl(
        self, requested_fields: list[str], field_acl: dict
    ) -> tuple[list[str], list[str], dict[str, str]]:
        """Evaluate field-level ACL rules.

        Returns: (allowed_fields, denied_fields, field_masks)
        """
        if not field_acl:
            return requested_fields, [], {}

        allowed = []
        denied = []
        masks = {}

        for field_name in requested_fields:
            rule = field_acl.get(field_name)
            if rule is None:
                # Default: allow if no specific rule
                allowed.append(field_name)
            elif rule.get("read", False):
                allowed.append(field_name)
                if rule.get("mask_pattern"):
                    masks[field_name] = rule["mask_pattern"]
            else:
                denied.append(field_name)

        return allowed, denied, masks


# Singleton
policy_evaluator = PolicyEvaluator()
