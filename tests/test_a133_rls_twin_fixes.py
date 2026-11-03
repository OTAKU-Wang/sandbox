"""A133 RLS 引擎修复针（wave 2，2026-10-30）。

本文件把修复轮中 rls_engine 的每条语义变化钉死在代码级（原 pg_rls_manager
双子半区随未接线的 pg_rls_manager 模块一并移除，2026-11 死代码清理）。
RLS 策略下发与表名拼接的语义仍是代码级证据，
远程 PG16 复验前不得宣称已验证。
"""
import pytest

from app.services.rls_engine import (
    RLSEngine,
    RLSPolicy,
    RLSContext,
    PolicyType,
    SensitivityLevel,
    inject_condition_outermost,
)


# ══════════════════════════════ rls_engine：fail-closed ══════════════════════════════

def _region_policy(**conditions):
    return RLSPolicy(
        policy_id="r1", name="region",
        policy_type=PolicyType.REGION,
        conditions={"allowed_regions": ["CN", "HK"], "region_column": "data_region", **conditions},
    )


class TestFailClosed:
    def test_region_policy_without_allowed_list_denies(self):
        """#17：enabled 区域策略没有白名单 ⇒ FALSE（旧行为 None=过滤消失）。"""
        p = RLSPolicy(policy_id="r", name="r", policy_type=PolicyType.REGION, conditions={})
        assert p.to_sql_condition({"user_region": "CN"}) == "FALSE"

    def test_region_policy_without_user_context_denies(self):
        """#17：enabled 区域策略评估时缺 user_region ⇒ FALSE（旧行为 None）。"""
        assert _region_policy().to_sql_condition({}) == "FALSE"

    def test_unknown_sensitivity_level_denies(self):
        """#18/#29：未知等级不再产出 `IN ()` 废 SQL 或放行，而是 FALSE。"""
        bad_max = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                            conditions={"max_allowed_level": "confidential-ish"})
        assert bad_max.to_sql_condition({"user_clearance": "secret"}) == "FALSE"
        bad_user = RLSPolicy(policy_id="s2", name="s2", policy_type=PolicyType.SENSITIVITY,
                             conditions={"max_allowed_level": "secret"})
        assert bad_user.to_sql_condition({"user_clearance": "unobtanium"}) == "FALSE"

    def test_none_clearance_filters_at_public_not_broken_sql(self):
        """#29：clearance=None 旧代码产出 `security_level IN ()`（语法废）；
        现按默认 public 过滤——可执行且 fail-closed。"""
        p = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                      conditions={"max_allowed_level": "secret"})
        cond = p.to_sql_condition({})
        assert cond == "security_level IN ('public')"

    def test_ceiling_applies_above_clearance(self):
        """#18：用户 clearance 高于上限时上限必须封顶（旧行为 None=不设防）。"""
        p = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                      conditions={"max_allowed_level": "confidential"})
        cond = p.to_sql_condition({"user_clearance": "secret"})
        assert cond is not None and "'secret'" not in cond
        assert "'confidential'" in cond and "'public'" in cond

    def test_unknown_policy_type_denies_sql(self):
        """#26：无法解析的 policy_type ⇒ to_sql_condition 返回 FALSE，
        而不是 None（None 会被 AND 组合器当作"本策略无谓词"直接放行）。"""
        p = RLSPolicy(policy_id="x", name="x", policy_type="bogus")  # dataclass 不设运行时闸
        assert p.to_sql_condition({}) == "FALSE"

    def test_unknown_dict_policy_type_denies_row(self):
        assert RLSEngine().evaluate({"type": "bogus"}, {}, user_region="CN") is False

    def test_remove_policy_idempotent(self):
        """#32：原子 pop；二次删除如实 False，不 KeyError。"""
        e = RLSEngine()
        e.add_policy(_region_policy())
        assert e.remove_policy("r1") is True
        assert e.remove_policy("r1") is False


class TestIdentifierGates:
    """#19/#23/#36：策略携带的标识符是 SQL 拼接位，必须过白名单。"""

    def test_malicious_region_column_raises_not_interpolates(self):
        p = _region_policy(region_column="data_region = 'CN' OR 1=1 --")
        with pytest.raises(ValueError):
            p.to_sql_condition({"user_region": "CN"})

    def test_malicious_column_rejected_at_validation(self):
        e = RLSEngine()
        errors = e.validate_policy(_region_policy(region_column="c; DROP TABLE t --"))
        assert any("region_column" in err or "column" in err for err in errors)

    def test_numeric_time_conditions_validated(self):
        e = RLSEngine()
        p = RLSPolicy(policy_id="t", name="t", policy_type=PolicyType.TIME,
                      conditions={"embargo_hours": "24; "})
        assert any("embargo_hours" in err for err in e.validate_policy(p))

    def test_sensitivity_bad_level_rejected_by_value_set(self):
        """#36：枚举成员判断换成显式 value 集合（str-Enum 解绑后仍有效）。"""
        e = RLSEngine()
        p = RLSPolicy(policy_id="s", name="s", policy_type=PolicyType.SENSITIVITY,
                      conditions={"max_allowed_level": "bogus"})
        assert any("bogus" in err for err in e.validate_policy(p))

    def test_qualified_column_allowed(self):
        p = _region_policy(region_column="t.data_region")
        assert p.to_sql_condition({"user_region": "CN"}).startswith("t.data_region = ")

    def test_inject_filter_clearance_injection_raises(self):
        """#23：user_clearance 无运行时类型闸的历史受害者，现在 int 强制。"""
        e = RLSEngine()
        with pytest.raises(ValueError):
            e.inject_filter("SELECT s FROM t", {"type": "sensitivity"}, user_clearance="3 OR 1=1")
        assert "sensitivity_level <= 3" in e.inject_filter(
            "SELECT s FROM t", {"type": "sensitivity"}, user_clearance="3")

    def test_inject_filter_unknown_type_raises(self):
        """fail-closed：未知类型不得原样返回未过滤 SQL。"""
        e = RLSEngine()
        with pytest.raises(ValueError):
            e.inject_filter("SELECT 1", {"type": "who-knows"})


class TestPlacementSemantics:
    """#21/#22/#27/#28：注入定位的唯一规则。"""

    def test_where_lands_in_outer_query_not_subquery(self):
        """#21：旧 rfind 拿最内层 WHERE——过滤被塞进子查询，外层裸奔。"""
        sql = "SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.id = t.id)"
        out = inject_condition_outermost(sql, "region = 'cn-north'")
        assert out.index("(region = 'cn-north')") > out.index("u.id = t.id)")
        assert "AND (region = 'cn-north')" in out

    def test_existing_or_clause_is_wrapped(self):
        """#28：AND 优先级高于 OR——既有子句不加括号，b=2 的行整类绕过过滤。"""
        out = inject_condition_outermost(
            "SELECT * FROM t WHERE a = 1 OR b = 2", "sec <= 1")
        assert out == "SELECT * FROM t WHERE (a = 1 OR b = 2) AND (sec <= 1)"

    def test_multiline_where_no_second_where(self):
        """#22：WHERE 前有换行时旧 find(" WHERE ") 漏检 ⇒ 注入出第二个 WHERE，
        整条 SQL 语法废（拒绝执行=策略消失）。"""
        out = inject_condition_outermost("SELECT * FROM t\nWHERE a = 1", "x = 'y'")
        assert out.upper().count(" WHERE ") == 1
        assert out == "SELECT * FROM t WHERE (a = 1) AND (x = 'y')"

    def test_where_inside_string_literal_not_a_clause(self):
        out = inject_condition_outermost(
            "SELECT * FROM t WHERE description = 'a WHERE b'", "x = 'y'")
        assert out == "SELECT * FROM t WHERE (description = 'a WHERE b') AND (x = 'y')"

    def test_trailing_semicolon_preserved(self):
        out = inject_condition_outermost("SELECT * FROM t;", "x = 1")
        assert out.endswith("WHERE (x = 1);")

    def test_having_gets_a_where_before_it(self):
        """#27：旧分支组没有 HAVING——`SELECT c FROM t HAVING ...` 的产出是
        `... HAVING (cond) AND count(*) > 2`，策略谓词落进 HAVING，语义报废。"""
        out = inject_condition_outermost(
            "SELECT c FROM t HAVING count(*) > 2", "sec <= 1")
        assert out.index("WHERE (sec <= 1)") < out.index(" HAVING ")

    def test_earliest_clause_wins(self):
        out = inject_condition_outermost(
            "SELECT c FROM t ORDER BY c LIMIT 5", "sec <= 1")
        assert out == "SELECT c FROM t WHERE (sec <= 1) ORDER BY c LIMIT 5"

    def test_rls_and_filter_injectors_agree(self):
        """#28：两个注入器历史各带一套错误规则，现在共享同一原语。"""
        e = RLSEngine()
        sql, cond = "SELECT * FROM t WHERE a = 1 OR b = 2", "x = 1"
        assert e.inject_rls_where(sql, cond) == e._inject_where(sql, cond)


class TestRowLevelApi:
    def test_region_requires_row_equality(self):
        """#24：行在允许区域≠行属于用户区域。cn-east 用户此前可读走白名单
        内任何其他区域的行。"""
        e = RLSEngine()
        policy = {"type": "region", "allowed_regions": ["cn-east", "cn-north"]}
        assert e.evaluate(policy, {"region": "cn-north"}, user_region="cn-east") is False
        assert e.evaluate(policy, {"region": "cn-east"}, user_region="cn-east") is True

    def test_low_clearance_reads_public_rows(self):
        """#25：旧谓词 `user >= max_level` 让低 clearance 连 public 行都读不到，
        同时上限从不封顶数据行——两头都是错的。"""
        e = RLSEngine()
        policy = {"type": "sensitivity", "max_level": 2}
        assert e.evaluate(policy, {"sensitivity": 0}, user_clearance=1) is True
        assert e.evaluate(policy, {"sensitivity": 2}, user_clearance=1) is False

    def test_time_window_wraps_midnight(self):
        """#31：22→6 窗口旧代码在每个钟点都返回 False。"""
        e = RLSEngine()
        policy = {"type": "time", "start_hour": 22, "end_hour": 6}
        assert e.evaluate_time_policy(policy, current_hour=23) is True
        assert e.evaluate_time_policy(policy, current_hour=3) is True
        assert e.evaluate_time_policy(policy, current_hour=12) is False
        # 普通窗口保持 含左不含右
        assert e.evaluate_time_policy({"start_hour": 8, "end_hour": 18}, current_hour=8) is True
        assert e.evaluate_time_policy({"start_hour": 8, "end_hour": 18}, current_hour=18) is False


class TestRequestTimeAndTableScoping:
    def test_request_time_is_honoured(self):
        """#33：context 携带 request_time 但求值从不读它——每次求值不可复现。"""
        from datetime import datetime, timezone, timedelta
        anchor = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
        e = RLSEngine()
        e.add_policy(RLSPolicy(policy_id="t", name="t", policy_type=PolicyType.TIME,
                               conditions={"embargo_hours": 24}))
        cond = e.evaluate_sql(RLSContext(request_time=anchor))
        # evaluate_sql 用括号 AND 组合：(created_at <= '<iso>')
        assert anchor - timedelta(hours=24) == datetime.fromisoformat(
            cond.split("<= '")[1].split("'")[0]
        )

    def test_table_name_parameter_selects_policies(self):
        """#30：table_name 旧参数从未被读——所有策略 AND 到所有表。"""
        e = RLSEngine()
        e.add_policy(RLSPolicy(policy_id="pa", name="a", policy_type=PolicyType.REGION,
                               conditions={"allowed_regions": ["CN"], "region_column": "a_region"},
                               table="t_a"))
        e.add_policy(RLSPolicy(policy_id="pb", name="b", policy_type=PolicyType.REGION,
                               conditions={"allowed_regions": ["CN"], "region_column": "b_region"},
                               table="t_b"))
        where = e.evaluate_sql(RLSContext(user_region="CN"), table_name="t_a")
        assert "a_region" in where and "b_region" not in where
