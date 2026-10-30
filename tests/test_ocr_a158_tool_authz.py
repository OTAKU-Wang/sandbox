"""A158: 工具注册表的授权字段必须是单一入口的真门禁，不是装饰。

validate_run(tool_ids, *, role, budget) 必须消费 roles/budget_cost；
network_targets 字段在全仓零消费者且 http 工具填 [] —— 按台账修法删除，
门禁真相是 validate_network_target + runner 侧 _net_allowed（A157），不在字段里。
"""
import dataclasses

from app.services.agent_tools import AgentTool, tool_registry


def test_role_gate_rejects_tool_outside_role():
    # inference 的 roles=("admin","operator","data_provider")，buyer 不可选
    err = tool_registry.validate_run(["inference"], role="buyer")
    assert err is not None and "inference" in err


def test_role_gate_allows_permitted_role():
    assert tool_registry.validate_run(["compute"], role="buyer") is None
    assert tool_registry.validate_run(["inference"], role="operator") is None


def test_budget_gate_enforced_in_validate_run():
    # llm_reason budget_cost=2 > 1
    assert tool_registry.validate_run(["llm_reason"], budget=1) is not None
    # compute + rag_retrieve = 1+1 = 2 <= 2
    assert tool_registry.validate_run(["compute", "rag_retrieve"], budget=2) is None
    # compute + llm_reason = 1+2 = 3 > 2
    assert tool_registry.validate_run(["compute", "llm_reason"], budget=2) is not None


def test_legacy_signature_behaviour_unchanged():
    # 不传 role/budget 的既有调用点（api/agent.py、agent_service.py）行为不变
    assert tool_registry.validate_run([]) is not None
    assert tool_registry.validate_run(["compute", "nope"]) is not None
    assert tool_registry.validate_run(["compute", "compute"]) is not None
    assert tool_registry.validate_run(["compute", "rag_retrieve"]) is None


def test_decorative_network_targets_field_removed():
    names = {f.name for f in dataclasses.fields(AgentTool)}
    assert "network_targets" not in names
