"""OCR A95 wiring — 查询期字段 ACL 必须接进 SecureDuckDBEngine 的策略门。

被接线的东西：
- 合约编译的 PolicyBundle.rules 里可以携带字段分级（raw field→level 映射、
  预编译的 {"mask_fields","deny_out_fields"}、或全量 PDP 的 field_acl 形态）；
- 调用方也可以直接 engine.set_field_classifications(...)（sandbox_db 接线用，
  形态与 output_gateway 消费的 DataResource.field_classifications 完全一致，
  编译复用 policy_compiler.field_rules_from_classifications 单一事实源）。

改前（HEAD）实况：secure_duckdb._check_policy 把 field 传给 policy_compiler
的那套 PDP，而后者从不读 field ⇒ 被分级为 deny_out 的列在查询期可以原文读出。
"""
import pytest

from app.services.policy_compiler import PolicyBundle, policy_compiler
from app.services.secure_duckdb import SecureDuckDBEngine

ROWS = [
    {"name": "Alice", "age": 30, "salary": 99999},
    {"name": "Bob", "age": 25, "salary": 88888},
]


def _active_contract(contract_id="c-a95"):
    return {
        "contract_id": contract_id,
        "allowed_sandbox_levels": ["L2", "L3"],
        "allowed_operations": ["query"],
        "max_duration_seconds": 3600,
        "status": "active",
    }


def _engine():
    engine = SecureDuckDBEngine(f"a95-{id(object())}", mode="memory")
    engine.register_table("emp", [dict(r) for r in ROWS])
    engine.set_sandbox_level("L3")
    return engine


def _bundle(**extra_rules):
    contract = _active_contract()
    bundle = policy_compiler.compile_for_mode(contract, "structured_query")
    bundle.rules.update(extra_rules)
    return bundle


# ── 行为级红（不需要任何新 API）：策略包里带字段分级，改前查询期照样放行 ──

def test_policy_bundle_field_classifications_denied_column_blocked_at_query():
    """level>=4 的列在查询期必须被拒——而不是只在出沙箱网关才被拦。"""
    engine = _engine()
    engine.set_policy(_bundle(field_classifications={"salary": 4, "age": 3}))
    result = engine.execute_query("SELECT name, salary FROM emp")
    assert result.success is False, (
        "查询期字段 ACL 未接线：被 deny_out 分级的 salary 仍被原文读出 "
        f"({result.rows})"
    )
    assert "salary" in (result.error or "")  # 拒绝理由可审计（列名不是密态数据）
    # 未分级的列照常可查
    ok = engine.execute_query("SELECT name FROM emp")
    assert ok.success and ok.rows[0][0] == "Alice"
    engine.close()


def test_wildcard_select_is_denied_when_deny_out_fields_exist():
    """SELECT * 覆盖到 deny_out 列 ⇒ 拒（fail-closed，不许静默丢列放行）。"""
    engine = _engine()
    engine.set_policy(_bundle(field_classifications={"salary": 4}))
    result = engine.execute_query("SELECT * FROM emp")
    assert result.success is False and "*" in (result.error or "")
    engine.close()


def test_mask_level_column_returns_masked_values_at_query_time():
    """level>=3 的列：查询放行但出值必须是掩码，原文不许从查询结果里走。"""
    engine = _engine()
    engine.set_policy(_bundle(field_classifications={"salary": 4, "age": 3}))
    result = engine.execute_query("SELECT name, age FROM emp")
    assert result.success, result.error
    assert [r[1] for r in result.rows] == ["***", "***"], (
        f"mask 级列未做查询期掩码：{result.rows}"
    )
    engine.close()


# ── 新接线面：engine.set_field_classifications 三种输入形态 ──

def test_set_field_classifications_raw_levels_precompiled_and_field_acl():
    engine = _engine()
    engine.set_policy(_bundle())  # 合约本身不带字段分级

    # 形态 1：raw field→level（与 DataResource.field_classifications 同形）
    engine.set_field_classifications({"salary": 4, "age": 3})
    assert engine.execute_query("SELECT salary FROM emp").success is False
    masked = engine.execute_query("SELECT name, age FROM emp")
    assert masked.success and masked.rows[0][1] == "***"

    # 形态 2：预编译 {"mask_fields", "deny_out_fields"}（output_gateway 同形）
    engine.set_field_classifications({"mask_fields": ["salary"], "deny_out_fields": []})
    m = engine.execute_query("SELECT salary FROM emp")
    assert m.success and m.rows[0][0] == "***"

    # 形态 3：全量 PDP 的 field_acl 形态 {field: {"read", "mask_pattern"}}
    engine.set_field_classifications({"salary": {"read": False}})
    assert engine.execute_query("SELECT salary FROM emp").success is False

    # 清空 ⇒ 恢复无字段分级行为
    engine.set_field_classifications(None)
    assert engine.execute_query("SELECT salary FROM emp").success
    engine.close()


def test_field_acl_wins_over_missing_bundle_rules_and_merges_bundle():
    """engine 侧与 bundle 侧的字段规则取并集（都是执法面，不做覆盖式收窄）。"""
    engine = _engine()
    engine.set_policy(_bundle(field_classifications={"age": 3}))
    engine.set_field_classifications({"salary": 4})
    assert engine.execute_query("SELECT salary FROM emp").success is False
    masked = engine.execute_query("SELECT name, age FROM emp")
    assert masked.success and masked.rows[0][1] == "***"
    engine.close()


# ── 兼容锁：没有任何字段分级 ⇒ 行为与改前逐字节一致 ──

def test_no_field_rules_keeps_pre_wiring_behaviour():
    engine = _engine()
    engine.set_policy(_bundle())
    result = engine.execute_query("SELECT * FROM emp")
    assert result.success, result.error
    assert result.rows[0] == ["Alice", "30", "99999"]  # register_table 既有 str 化行为，原样钉住
    # 无策略（not_required 路径）同样不受影响
    engine2 = SecureDuckDBEngine("a95-nopolicy", mode="memory")
    engine2.register_table("emp", [dict(r) for r in ROWS])
    assert engine2.execute_query("SELECT salary FROM emp").success
    engine.close()
    engine2.close()
