"""Cross-Space Policy Engine — enforce sandbox and trust policies for federation.

Enforces cross-space data access policies:
1. Sandbox Level Enforcement — remote access requires L1+ sandbox
2. Trust Level Gating — operations require minimum trust level
3. Operation Whitelist — only approved operations allowed per trust
4. DP Budget Check — cross-space queries consume DP budget

Decision pipeline (A149: the old docstring promised "rules evaluated in order;
first matching rule wins" — rules were in fact never consulted for the decision
itself). Now: trust exists/active/unexpired → trust operation allowlist →
operation registered in OPERATION_TRUST_MAP (unknown = DENY, default-deny like
policies/system.rego) → trust level ≥ max(map floor, binding rule floor) →
sandbox level ≥ binding rule floor → DP budget (rule-declared or caller-supplied).
The binding rule for an operation is the highest-priority rule whose
allowed_operations contain it (higher priority number wins; ties by rule_id).
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from app.services.federation_connector import (
    TrustLevel, FederationTrust, FederationStatus,
)
from app.services.trust_evaluator import trust_evaluator, TrustLevel as EvalTrustLevel

logger = logging.getLogger(__name__)


class PolicyAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    # A149: reserved — no evaluate() path produces it today. Wiring an
    # audit-only "allow with disclosure" tier is a decision for the wiring owner.
    # TODO(A149): define which rule tier (if any) maps to log_and_allow before this
    # module is connected to a live path.
    LOG_AND_ALLOW = "log_and_allow"


class PolicyReason(str, Enum):
    TRUST_LEVEL_INSUFFICIENT = "trust_level_insufficient"
    SANDBOX_LEVEL_INSUFFICIENT = "sandbox_level_insufficient"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    DP_BUDGET_EXHAUSTED = "dp_budget_exhausted"
    TRUST_SUSPENDED = "trust_suspended"
    NO_TRUST = "no_trust"
    # A149（additive）：过期信托与「规则要求预算但调用方没给」此前都无脸可现——
    # 前者根本没有检查（照常 ALLOW），后者是 dead field。
    TRUST_EXPIRED = "trust_expired"
    DP_BUDGET_REQUIRED = "dp_budget_required"
    ALLOWED = "allowed"


@dataclass
class PolicyDecision:
    action: PolicyAction
    reason: PolicyReason
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class CrossSpacePolicyRule:
    """A single policy rule for cross-space access."""
    rule_id: str
    name: str
    min_trust_level: TrustLevel = TrustLevel.BASIC
    min_sandbox_level: str = "L1"
    allowed_operations: list[str] = field(default_factory=lambda: ["read_catalog", "search"])
    require_dp_budget: bool = False
    priority: int = 100


# Minimum trust level per operation category
# A149/P1: `export_result` is whitelisted by the verified-data rule but was absent
# here, so map.get(op, BASIC) let BASIC trust export results from an L2 sandbox —
# the VERIFIED gate was bypassable by name omission alone.
OPERATION_TRUST_MAP: dict[str, TrustLevel] = {
    "read_catalog": TrustLevel.BASIC,
    "search": TrustLevel.BASIC,
    "verify_audit": TrustLevel.BASIC,
    "read_data": TrustLevel.VERIFIED,
    "export_result": TrustLevel.VERIFIED,
    "write_data": TrustLevel.FULL,
    "train_model": TrustLevel.FULL,
}

# Sandbox level hierarchy
SANDBOX_LEVEL_ORDER: dict[str, int] = {
    "L0": 0,
    "L1": 1,
    "L2": 2,
    "L3": 3,
}


class CrossSpacePolicyEngine:
    """Cross-space policy enforcement engine.

    Evaluates access requests against trust levels, sandbox levels,
    and operation whitelists to determine allow/deny decisions.
    """

    def __init__(self):
        self._rules: list[CrossSpacePolicyRule] = self._default_rules()

    def _default_rules(self) -> list[CrossSpacePolicyRule]:
        return [
            CrossSpacePolicyRule(
                rule_id="default-read",
                name="Default read access",
                min_trust_level=TrustLevel.BASIC,
                min_sandbox_level="L1",
                allowed_operations=["read_catalog", "search", "verify_audit"],
                priority=100,
            ),
            CrossSpacePolicyRule(
                rule_id="verified-data",
                name="Verified data access",
                min_trust_level=TrustLevel.VERIFIED,
                min_sandbox_level="L2",
                allowed_operations=["read_data", "export_result"],
                priority=200,
            ),
            CrossSpacePolicyRule(
                rule_id="full-training",
                name="Full training access",
                min_trust_level=TrustLevel.FULL,
                min_sandbox_level="L3",
                allowed_operations=["train_model", "write_data"],
                priority=300,
            ),
        ]

    def evaluate(
        self,
        trust: FederationTrust | None,
        operation: str,
        sandbox_level: str = "L1",
        dp_budget_remaining: float | None = None,
    ) -> PolicyDecision:
        """Evaluate a cross-space access request.

        Args:
            trust: Federation trust relationship (None if no trust).
            operation: Requested operation.
            sandbox_level: Sandbox level of the request.
            dp_budget_remaining: Remaining DP budget (None = skip check).

        Returns:
            PolicyDecision with allow/deny and reason.
        """
        # Check trust exists
        if not trust:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.NO_TRUST,
                message="No trust relationship with remote space",
            )

        # Check trust is active
        if trust.status != FederationStatus.ACTIVE:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.TRUST_SUSPENDED,
                message=f"Trust is {trust.status.value}",
            )

        # A149/P5: expires_at was declared and persisted but never compared —
        # an expired trust kept authorizing as if active.
        if trust.expires_at is not None:
            expires = trust.expires_at if trust.expires_at.tzinfo else \
                trust.expires_at.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                return PolicyDecision(
                    action=PolicyAction.DENY,
                    reason=PolicyReason.TRUST_EXPIRED,
                    message=f"Trust expired at {expires.isoformat()}",
                    details={"expired_at": expires.isoformat()},
                )

        # Check operation is in trust's allowed list
        if operation not in trust.allowed_operations:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.OPERATION_NOT_ALLOWED,
                message=f"Operation '{operation}' not in trust allowlist",
            )

        # A149/P2: unregistered operations used to fall through map.get(op, BASIC)
        # — a name the engine has never heard of silently got the *lowest* bar.
        # Default-deny: an operation must be registered in OPERATION_TRUST_MAP.
        required_trust = OPERATION_TRUST_MAP.get(operation)
        if required_trust is None:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.OPERATION_NOT_ALLOWED,
                message=(
                    f"Operation '{operation}' is not registered in OPERATION_TRUST_MAP — "
                    "refused fail-closed (A149)"
                ),
            )

        # A149/P6: deterministic binding rule — highest priority wins, ties by
        # rule_id, so decision outcomes never depend on registration order.
        binding = self._binding_rule(operation)

        # A149/P3: the binding rule's min_trust_level is a real floor now (it was
        # a dead field), combined with the map requirement via max().
        trust_order = {TrustLevel.NONE: 0, TrustLevel.BASIC: 1, TrustLevel.VERIFIED: 2, TrustLevel.FULL: 3}
        effective_required = required_trust
        if binding is not None and trust_order[binding.min_trust_level] > trust_order[effective_required]:
            effective_required = binding.min_trust_level
        if trust_order.get(trust.trust_level, 0) < trust_order.get(effective_required, 0):
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.TRUST_LEVEL_INSUFFICIENT,
                message=f"Operation '{operation}' requires {effective_required.value} trust, have {trust.trust_level.value}",
                details=self._binding_details(binding),
            )

        # Check sandbox level
        required_sandbox = binding.min_sandbox_level if binding is not None else "L1"
        # A149/P7: a typo'd rule level ("L9") used to resolve via get(..., 0) to
        # "no threshold at all" — fail closed on unknown levels instead.
        if required_sandbox not in SANDBOX_LEVEL_ORDER:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.SANDBOX_LEVEL_INSUFFICIENT,
                message=(
                    f"Binding rule '{binding.rule_id}' declares unknown sandbox level "
                    f"'{required_sandbox}' — refused fail-closed (A149)"
                ),
                details=self._binding_details(binding),
            )
        if SANDBOX_LEVEL_ORDER.get(sandbox_level, -1) < SANDBOX_LEVEL_ORDER[required_sandbox]:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.SANDBOX_LEVEL_INSUFFICIENT,
                message=f"Operation '{operation}' requires {required_sandbox} sandbox, have {sandbox_level}",
                details=self._binding_details(binding),
            )

        # A149/P4: require_dp_budget was a dead field. When the rule demands a
        # budget and the caller supplies none, the pre-condition is unverifiable —
        # refuse (DP_BUDGET_REQUIRED), don't silently skip the check.
        if binding is not None and binding.require_dp_budget and dp_budget_remaining is None:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.DP_BUDGET_REQUIRED,
                message=(
                    f"Rule '{binding.rule_id}' requires a DP budget but none was supplied "
                    "— refused fail-closed (A149)"
                ),
                details=self._binding_details(binding),
            )

        # Check DP budget
        if dp_budget_remaining is not None and dp_budget_remaining <= 0:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.DP_BUDGET_EXHAUSTED,
                message="DP privacy budget exhausted",
                details=self._binding_details(binding),
            )

        # A149/P8: decisions are auditable — which rule bound, what it required.
        details = self._binding_details(binding)
        details.update({
            "operation": operation,
            "required_trust": effective_required.value,
            "required_sandbox": required_sandbox,
        })
        return PolicyDecision(
            action=PolicyAction.ALLOW,
            reason=PolicyReason.ALLOWED,
            message="Access granted",
            details=details,
        )

    def _binding_rule(self, operation: str) -> CrossSpacePolicyRule | None:
        """Highest-priority rule whose allowed_operations cover `operation` (A149/P6)."""
        candidates = [r for r in self._rules if operation in r.allowed_operations]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.priority, r.rule_id))

    @staticmethod
    def _binding_details(rule: CrossSpacePolicyRule | None) -> dict:
        if rule is None:
            return {"rule_id": None}
        return {"rule_id": rule.rule_id, "rule_priority": rule.priority}

    def add_rule(self, rule: CrossSpacePolicyRule):
        """Add a custom policy rule.

        A149/P6: stored sorted by (-priority, rule_id) — "higher number wins" is
        now the single documented semantics everywhere (the old add_rule sorted
        ascending while the lookup scanned descending). Binding itself resolves by
        max(), so correctness never depends on list order; the sort is cosmetic.
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: (-r.priority, r.rule_id))

    def list_rules(self) -> list[CrossSpacePolicyRule]:
        """List all policy rules."""
        return list(self._rules)

    def evaluate_trust_score(
        self,
        data_quality: int = 50,
        compliance: int = 50,
        reputation: int = 50,
        security: int = 50,
    ) -> tuple[str, int]:
        """Evaluate trust score using the trust evaluator.

        Returns (trust_level_name, score).
        """
        score = trust_evaluator.evaluate(
            data_quality_score=data_quality,
            compliance_score=compliance,
            reputation_score=reputation,
            security_score=security,
        )
        return score.level.value, score.score


# Singleton
cross_space_policy = CrossSpacePolicyEngine()
