"""Tests for Cross-Space Policy Engine (S6-3)."""
import pytest

from app.services.cross_space_policy import (
    CrossSpacePolicyEngine, PolicyAction, PolicyReason,
    CrossSpacePolicyRule, cross_space_policy,
)
from app.services.federation_connector import (
    FederationConnector, SpaceIdentity, TrustLevel, FederationStatus,
)


@pytest.fixture
def engine():
    return CrossSpacePolicyEngine()


@pytest.fixture
def connector():
    return FederationConnector()


@pytest.fixture
def local_space():
    return SpaceIdentity(space_id="local", space_name="L", endpoint="https://l.example.com")


@pytest.fixture
def remote_space():
    return SpaceIdentity(space_id="remote", space_name="R", endpoint="https://r.example.com")


class TestNoTrust:
    def test_no_trust_denied(self, engine):
        decision = engine.evaluate(None, "read_catalog")
        assert decision.action == PolicyAction.DENY
        assert decision.reason == PolicyReason.NO_TRUST


class TestTrustStatus:
    def test_active_trust_allowed(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "read_catalog")
        assert decision.action == PolicyAction.ALLOW

    def test_suspended_trust_denied(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        connector.suspend_trust(trust.trust_id)
        decision = engine.evaluate(trust, "read_catalog")
        assert decision.action == PolicyAction.DENY
        assert decision.reason == PolicyReason.TRUST_SUSPENDED

    def test_revoked_trust_denied(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        connector.revoke_trust(trust.trust_id)
        decision = engine.evaluate(trust, "read_catalog")
        assert decision.action == PolicyAction.DENY


class TestOperationWhitelist:
    def test_allowed_operation(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog", "search"])
        decision = engine.evaluate(trust, "search")
        assert decision.action == PolicyAction.ALLOW

    def test_disallowed_operation(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "train_model")
        assert decision.action == PolicyAction.DENY
        assert decision.reason == PolicyReason.OPERATION_NOT_ALLOWED


class TestTrustLevelGating:
    def test_basic_trust_for_read(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "read_catalog")
        assert decision.action == PolicyAction.ALLOW

    def test_basic_trust_denied_for_training(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["train_model"])
        decision = engine.evaluate(trust, "train_model")
        assert decision.action == PolicyAction.DENY
        assert decision.reason == PolicyReason.TRUST_LEVEL_INSUFFICIENT

    def test_full_trust_for_training(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.FULL, ["train_model"])
        decision = engine.evaluate(trust, "train_model", sandbox_level="L3")
        assert decision.action == PolicyAction.ALLOW


class TestSandboxLevelEnforcement:
    def test_l1_sandbox_for_read(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "read_catalog", sandbox_level="L1")
        assert decision.action == PolicyAction.ALLOW

    def test_l0_sandbox_denied(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "read_catalog", sandbox_level="L0")
        assert decision.action == PolicyAction.DENY
        assert decision.reason == PolicyReason.SANDBOX_LEVEL_INSUFFICIENT


class TestDPBudget:
    def test_budget_exhausted_denied(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "read_catalog", dp_budget_remaining=0.0)
        assert decision.action == PolicyAction.DENY
        assert decision.reason == PolicyReason.DP_BUDGET_EXHAUSTED

    def test_budget_available_allowed(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "read_catalog", dp_budget_remaining=5.0)
        assert decision.action == PolicyAction.ALLOW

    def test_budget_none_skips_check(self, engine, connector, local_space, remote_space):
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, TrustLevel.BASIC, ["read_catalog"])
        decision = engine.evaluate(trust, "read_catalog", dp_budget_remaining=None)
        assert decision.action == PolicyAction.ALLOW


class TestCustomRules:
    def test_add_custom_rule(self, engine):
        rule = CrossSpacePolicyRule(
            rule_id="custom", name="Custom rule",
            min_trust_level=TrustLevel.VERIFIED,
            allowed_operations=["custom_op"],
        )
        engine.add_rule(rule)
        assert len(engine.list_rules()) > 3

    def test_list_rules(self, engine):
        rules = engine.list_rules()
        assert len(rules) >= 3


class TestTrustScoreEvaluation:
    def test_high_scores_yield_platinum(self, engine):
        level, score = engine.evaluate_trust_score(
            data_quality=95, compliance=95, reputation=95, security=95,
        )
        assert level == "platinum"
        assert score >= 90

    def test_low_scores_yield_new(self, engine):
        level, score = engine.evaluate_trust_score(
            data_quality=20, compliance=20, reputation=20, security=20,
        )
        assert level == "new"
        assert score < 50


class TestSingleton:
    def test_singleton_exists(self):
        assert cross_space_policy is not None
        assert isinstance(cross_space_policy, CrossSpacePolicyEngine)
