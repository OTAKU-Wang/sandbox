"""A149 — cross_space_policy.py 潜伏缺陷钉（三引擎之三；模块不接线）。

分组：
  P1  `export_result` 出现在 verified-data 规则白名单里但不在 OPERATION_TRUST_MAP ⇒
      map.get(op, BASIC) 默认 BASIC ⇒ VERIFIED 门槛被绕过（旗舰缺陷）
  P2  未知操作默认按 BASIC 放行（fail-open）⇒ 未注册操作一律 DENY（default-deny，
      与 policies/system.rego 锚同构）
  P3  rule.min_trust_level 是从没被读的 dead field ⇒ 绑定规则的可信地板真正生效
  P4  rule.require_dp_budget 是从没被读的 dead field ⇒ 规则要求预算而调用方没给 ⇒
      无法验证即拒绝（DP_BUDGET_REQUIRED 新枚举）
  P5  trust.expires_at 从不检查：过期信托照常 ALLOW ⇒ TRUST_EXPIRED
  P6  priority 语义两套（add_rule 升序排、查询按降序取）⇒ 统一「数值大者优先」，
      同值按 rule_id 定序（确定性）
  P7  规则里 min_sandbox_level 拼错（如 "L9"）经 get(...,0) 变成无门槛放行 ⇒ fail-closed
  P8  决策 details 恒空、命中哪条规则不可审计 ⇒ details 披露绑定规则；
      LOG_AND_ALLOW 是保留值（无产出路径）——接线语义由人拍板，TODO(A149) 入账
"""
from datetime import datetime, timezone, timedelta

import pytest

from app.services.cross_space_policy import (
    CrossSpacePolicyEngine, CrossSpacePolicyRule, PolicyAction, PolicyReason,
    OPERATION_TRUST_MAP,
)
from app.services.federation_connector import (
    SpaceIdentity, TrustLevel, FederationStatus, FederationTrust,
)


@pytest.fixture
def engine():
    return CrossSpacePolicyEngine()


def make_trust(level=TrustLevel.BASIC, ops=("read_catalog", "search"), expires_at=None):
    return FederationTrust(
        trust_id="t-1",
        local_space=SpaceIdentity(space_id="local", space_name="L", endpoint="https://l"),
        remote_space=SpaceIdentity(space_id="remote", space_name="R", endpoint="https://r"),
        trust_level=level,
        status=FederationStatus.ACTIVE,
        allowed_operations=list(ops),
        expires_at=expires_at,
    )


class TestP1ExportResultTrustGate:
    def test_export_result_now_requires_verified(self):
        assert OPERATION_TRUST_MAP.get("export_result") == TrustLevel.VERIFIED

    def test_basic_trust_export_result_denied(self, engine):
        trust = make_trust(TrustLevel.BASIC, ops=["export_result"])
        d = engine.evaluate(trust, "export_result", sandbox_level="L2")
        # 旧代码：map 缺项默认 BASIC ⇒ BASIC 信托在 L2 沙箱就能导出结果
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.TRUST_LEVEL_INSUFFICIENT

    def test_verified_trust_export_result_allowed(self, engine):
        trust = make_trust(TrustLevel.VERIFIED, ops=["export_result"])
        d = engine.evaluate(trust, "export_result", sandbox_level="L2")
        assert d.action == PolicyAction.ALLOW


class TestP2UnknownOperationDeny:
    def test_unregistered_operation_denied(self, engine):
        trust = make_trust(TrustLevel.FULL, ops=["mystery_op"])
        d = engine.evaluate(trust, "mystery_op", sandbox_level="L3")
        # 未注册到 OPERATION_TRUST_MAP 的操作不得按最低门槛放行
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.OPERATION_NOT_ALLOWED


class TestP3RuleTrustFloor:
    def test_rule_min_trust_level_enforced(self, engine):
        engine.add_rule(CrossSpacePolicyRule(
            rule_id="strict-read", name="Strict read",
            min_trust_level=TrustLevel.VERIFIED,
            allowed_operations=["read_catalog"],
            priority=500,
        ))
        trust = make_trust(TrustLevel.BASIC)
        d = engine.evaluate(trust, "read_catalog")
        # 旧代码：min_trust_level 从未被读，BASIC 直接过
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.TRUST_LEVEL_INSUFFICIENT


class TestP4RequireDpBudget:
    def _with_rule(self, engine):
        engine.add_rule(CrossSpacePolicyRule(
            rule_id="budgeted", name="Budgeted search",
            min_trust_level=TrustLevel.BASIC,
            allowed_operations=["search"],
            require_dp_budget=True,
            priority=500,
        ))

    def test_missing_budget_denied(self, engine):
        self._with_rule(engine)
        trust = make_trust(ops=["search"])
        d = engine.evaluate(trust, "search", dp_budget_remaining=None)
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.DP_BUDGET_REQUIRED

    def test_positive_budget_allowed(self, engine):
        self._with_rule(engine)
        trust = make_trust(ops=["search"])
        d = engine.evaluate(trust, "search", dp_budget_remaining=5.0)
        assert d.action == PolicyAction.ALLOW

    def test_exhausted_budget_still_denied(self, engine):
        self._with_rule(engine)
        trust = make_trust(ops=["search"])
        d = engine.evaluate(trust, "search", dp_budget_remaining=0.0)
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.DP_BUDGET_EXHAUSTED


class TestP5Expiry:
    def test_expired_trust_denied(self, engine):
        trust = make_trust(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        d = engine.evaluate(trust, "read_catalog")
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.TRUST_EXPIRED

    def test_future_expiry_allowed(self, engine):
        trust = make_trust(expires_at=datetime.now(timezone.utc) + timedelta(days=7))
        d = engine.evaluate(trust, "read_catalog")
        assert d.action == PolicyAction.ALLOW

    def test_naive_expiry_normalized(self, engine):
        naive = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
        d = engine.evaluate(make_trust(expires_at=naive), "read_catalog")
        assert d.action == PolicyAction.DENY


class TestP6PrioritySemantics:
    def test_higher_priority_rule_wins_deterministically(self, engine):
        engine.add_rule(CrossSpacePolicyRule(
            rule_id="b-strict", name="B", min_sandbox_level="L3",
            min_trust_level=TrustLevel.BASIC,
            allowed_operations=["search"], priority=500,
        ))
        engine.add_rule(CrossSpacePolicyRule(
            rule_id="a-loose", name="A", min_sandbox_level="L1",
            min_trust_level=TrustLevel.BASIC,
            allowed_operations=["search"], priority=400,
        ))
        trust = make_trust(ops=["search"])
        d = engine.evaluate(trust, "search", sandbox_level="L2")
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.SANDBOX_LEVEL_INSUFFICIENT
        # 反向再验一次：注册顺序不得影响绑定结果
        engine2 = CrossSpacePolicyEngine()
        engine2.add_rule(CrossSpacePolicyRule(
            rule_id="a-loose", name="A", min_sandbox_level="L1",
            min_trust_level=TrustLevel.BASIC,
            allowed_operations=["search"], priority=400,
        ))
        engine2.add_rule(CrossSpacePolicyRule(
            rule_id="b-strict", name="B", min_sandbox_level="L3",
            min_trust_level=TrustLevel.BASIC,
            allowed_operations=["search"], priority=500,
        ))
        d2 = engine2.evaluate(make_trust(ops=["search"]), "search", sandbox_level="L2")
        assert d2.reason == PolicyReason.SANDBOX_LEVEL_INSUFFICIENT


class TestP7BadSandboxLevelFailClosed:
    def test_typo_rule_sandbox_level_denies(self, engine):
        engine.add_rule(CrossSpacePolicyRule(
            rule_id="typo", name="Typo rule", min_sandbox_level="L9",
            min_trust_level=TrustLevel.BASIC,
            allowed_operations=["search"], priority=500,
        ))
        trust = make_trust(ops=["search"])
        d = engine.evaluate(trust, "search", sandbox_level="L3")
        # 旧代码 get("L9", 0)：拼错规则把门槛降为 0，等于放行
        assert d.action == PolicyAction.DENY
        assert d.reason == PolicyReason.SANDBOX_LEVEL_INSUFFICIENT


class TestP8DecisionAuditability:
    def test_allow_decision_names_binding_rule(self, engine):
        d = engine.evaluate(make_trust(), "read_catalog")
        assert d.action == PolicyAction.ALLOW
        assert d.details.get("rule_id") == "default-read"
        assert d.details.get("rule_priority") == 100
