"""Cross-Space Policy Engine — enforce sandbox and trust policies for federation.

Enforces cross-space data access policies:
1. Sandbox Level Enforcement — remote access requires L1+ sandbox
2. Trust Level Gating — operations require minimum trust level
3. Operation Whitelist — only approved operations allowed per trust
4. DP Budget Check — cross-space queries consume DP budget

Policy rules are evaluated in order; first matching rule wins.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum

from app.services.federation_connector import (
    TrustLevel, FederationTrust, FederationStatus,
)
from app.services.trust_evaluator import trust_evaluator, TrustLevel as EvalTrustLevel

logger = logging.getLogger(__name__)


class PolicyAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    LOG_AND_ALLOW = "log_and_allow"


class PolicyReason(str, Enum):
    TRUST_LEVEL_INSUFFICIENT = "trust_level_insufficient"
    SANDBOX_LEVEL_INSUFFICIENT = "sandbox_level_insufficient"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    DP_BUDGET_EXHAUSTED = "dp_budget_exhausted"
    TRUST_SUSPENDED = "trust_suspended"
    NO_TRUST = "no_trust"
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
OPERATION_TRUST_MAP: dict[str, TrustLevel] = {
    "read_catalog": TrustLevel.BASIC,
    "search": TrustLevel.BASIC,
    "verify_audit": TrustLevel.BASIC,
    "read_data": TrustLevel.VERIFIED,
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

        # Check operation is in trust's allowed list
        if operation not in trust.allowed_operations:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.OPERATION_NOT_ALLOWED,
                message=f"Operation '{operation}' not in trust allowlist",
            )

        # Check trust level for operation
        required_trust = OPERATION_TRUST_MAP.get(operation, TrustLevel.BASIC)
        trust_order = {TrustLevel.NONE: 0, TrustLevel.BASIC: 1, TrustLevel.VERIFIED: 2, TrustLevel.FULL: 3}
        if trust_order.get(trust.trust_level, 0) < trust_order.get(required_trust, 0):
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.TRUST_LEVEL_INSUFFICIENT,
                message=f"Operation '{operation}' requires {required_trust.value} trust, have {trust.trust_level.value}",
            )

        # Check sandbox level
        required_sandbox = self._get_min_sandbox_level(operation)
        if SANDBOX_LEVEL_ORDER.get(sandbox_level, 0) < SANDBOX_LEVEL_ORDER.get(required_sandbox, 0):
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.SANDBOX_LEVEL_INSUFFICIENT,
                message=f"Operation '{operation}' requires {required_sandbox} sandbox, have {sandbox_level}",
            )

        # Check DP budget
        if dp_budget_remaining is not None and dp_budget_remaining <= 0:
            return PolicyDecision(
                action=PolicyAction.DENY,
                reason=PolicyReason.DP_BUDGET_EXHAUSTED,
                message="DP privacy budget exhausted",
            )

        return PolicyDecision(
            action=PolicyAction.ALLOW,
            reason=PolicyReason.ALLOWED,
            message="Access granted",
        )

    def _get_min_sandbox_level(self, operation: str) -> str:
        """Get minimum sandbox level for an operation."""
        for rule in sorted(self._rules, key=lambda r: r.priority, reverse=True):
            if operation in rule.allowed_operations:
                return rule.min_sandbox_level
        return "L1"

    def add_rule(self, rule: CrossSpacePolicyRule):
        """Add a custom policy rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)

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
