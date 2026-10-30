"""A144 — scenario_engine 11 条潜伏缺陷红钉（接线前必修，本波次转绿；本模块仍不接线）。

台账：docs/ocr-review-2026-10-18 ocr-verified-findings A144（11 条全 LATENT，接线即 critical）。
钉序与修法对应：
  D1  关键字黑名单 + str.format 拼接 ⇒ UNION/SELECT 等结构 token 直接注入 ⇒ fail-closed 拒绝
  D2  先剥关键字再剥注释 ⇒ 静默篡改合法值 ⇒ 一律显式拒绝，绝不改写取值
  D3  max_output_rows 靠 `"LIMIT" not in sql.upper()` 子串判 ⇒ 值内 "LIMIT" 抑制行数帽
      ⇒ LIMIT 感知移到模板文本（作者面）+ 词边界
  D4  forbidden_operations 声明不执行（subquery_leak 只 log；未知约束名静默失效）
      ⇒ 可执行项执行、不可执行项如实披露 notes、未知约束名报错
  D5  allowed_operations 从没被读 ⇒ JOIN/聚合 需声明对应能力，否则拒绝
  D6  table_count = join_count + 1 ⇒ `FROM a, b` 逗号笛卡尔既不计表也不查 ON ⇒ 拒绝
  D7  `"ON" not in section` 子串判定 ⇒ `JOIN button b`（真笛卡尔）因 "button" 含 "on" 假过
      ⇒ 词边界 ON/USING
  D8  subquery 泄漏正则要求单行 `(SELECT` ⇒ `( SELECT`/换行/非 WHERE 前缀漏检 ⇒ 收口
  D9  format 只捕 KeyError ⇒ `{0}`/坏花括号的 IndexError/ValueError 直冲调用方
  D10 register_template 同 id 静默覆盖（旧模板约束参数无声消失）⇒ 拒绝
  D11 allowed_values 对数值参数按 str(100.0)=="100.0" 比对 ⇒ 误拒合法值
"""
import pytest

from app.services.scenario_engine import (
    ScenarioEngine, ScenarioParameter, ScenarioTemplate, OutputColumn,
)


def make_template(**overrides) -> ScenarioTemplate:
    defaults = {
        "id": "pin-scenario",
        "name": "Pin Scenario",
        "description": "A144 pins",
        "category": "test",
        "parameters": [
            ScenarioParameter(name="industry", type="string", description="Industry code",
                              required=True, example="C39"),
            ScenarioParameter(name="min_capital", type="number", description="Min capital",
                              required=False, default=100, min_value=0),
        ],
        "sql_template": (
            "SELECT ent_name, reg_capital FROM enterprise_basic "
            "WHERE industry_code = '{industry}' AND reg_capital >= {min_capital}"
        ),
        "output_schema": [OutputColumn(name="ent_name", type="string", description="name")],
        "max_tables": 4,
        "max_output_rows": 500,
    }
    defaults.update(overrides)
    return ScenarioTemplate(**defaults)


@pytest.fixture
def engine():
    return ScenarioEngine()


class TestD1InjectionFailClosed:
    def test_union_select_payload_refused_not_stripped(self, engine):
        """黑名单没有 UNION/SELECT：旧代码让 `x' UNION SELECT ... WHERE 'a'='a` 通过结构检查。

        修后 fail-closed：含 SQL 结构 token 的取值 ⇒ success=False，错误点名取值。
        """
        engine.register_template(make_template())
        result = engine.render_sql("pin-scenario", {
            "industry": "x' UNION SELECT secret FROM creds WHERE 'a'='a",
            "min_capital": 100,
        })
        assert result.success is False
        assert "industry" in (result.error or "")

    def test_keyword_value_refused_not_stripped(self, engine):
        engine.register_template(make_template())
        result = engine.render_sql("pin-scenario", {"industry": "DROP", "min_capital": 100})
        assert result.success is False
        assert "industry" in (result.error or "")

    def test_legit_value_never_tampered(self, engine):
        """D2：合法值（含撇号/与号）逐字保留——只按 SQL 字面量规则转义引号，不改写内容。"""
        engine.register_template(make_template())
        result = engine.render_sql("pin-scenario", {"industry": "O'Brien & Sons", "min_capital": 100})
        assert result.success is True
        assert "O''Brien & Sons" in result.sql


class TestD3LimitCeiling:
    def test_value_containing_limit_cannot_suppress_cap(self, engine):
        engine.register_template(make_template(
            id="limit-val",
            parameters=[ScenarioParameter(name="tag", type="string", description="t", required=True)],
            sql_template="SELECT a FROM t WHERE tag = '{tag}'",
        ))
        result = engine.render_sql("limit-val", {"tag": "LIMIT"})
        assert result.success is True
        # 封顶必须落在子句层（模板里没有 LIMIT 就补上），值里的 "LIMIT" 字样不算数
        assert result.sql.upper().rstrip().endswith("LIMIT 500")

    def test_column_named_limit_still_capped(self, engine):
        """旧子串判定把列名 limit_id 误读成已有 LIMIT ⇒ 不封顶；词边界后必须封顶。"""
        engine.register_template(make_template(
            id="limit-col", parameters=[],
            sql_template="SELECT limit_id FROM t",
        ))
        result = engine.render_sql("limit-col", {})
        assert result.success is True
        assert result.sql.upper().rstrip().endswith("LIMIT 500")

    def test_template_limit_respected(self, engine):
        engine.register_template(make_template(
            id="limit-tpl", parameters=[],
            sql_template="SELECT 1 FROM t LIMIT 50",
        ))
        result = engine.render_sql("limit-tpl", {})
        assert result.success is True
        assert result.sql.count("LIMIT") == 1


class TestD4ForbiddenOpsEnforced:
    def test_subquery_leak_rejects_not_logs(self, engine):
        engine.register_template(make_template(
            id="leak", parameters=[],
            sql_template="SELECT 1 FROM t WHERE id IN (SELECT id FROM secret)",
        ))
        result = engine.render_sql("leak", {})
        assert result.success is False
        assert "subquery" in (result.error or "").lower()

    def test_unknown_forbidden_operation_is_error(self, engine):
        """拼错的约束名过去静默失效（声明了但没人执行）⇒ 现在拒绝渲染。"""
        engine.register_template(make_template(
            id="typo-forbid", parameters=[],
            sql_template="SELECT 1 FROM t",
            forbidden_operations=["subquery_leakk"],
        ))
        result = engine.render_sql("typo-forbid", {})
        assert result.success is False
        assert "subquery_leakk" in (result.error or "")

    def test_runtime_only_operation_disclosed_not_silent(self, engine):
        """row_level_export 渲染期不可判定 ⇒ 成功时也必须如实披露「渲染期未执行」。"""
        engine.register_template(make_template(
            id="rle", parameters=[], sql_template="SELECT 1 FROM t",
            forbidden_operations=["row_level_export"],
        ))
        result = engine.render_sql("rle", {})
        assert result.success is True
        assert any("row_level_export" in n for n in result.constraint_notes)


class TestD5AllowedOpsRead:
    def test_join_requires_multi_table_join_capability(self, engine):
        engine.register_template(make_template(
            id="no-join-cap", parameters=[],
            sql_template="SELECT 1 FROM t1 JOIN t2 ON t1.id = t2.id",
            allowed_operations=["filter"],
        ))
        result = engine.render_sql("no-join-cap", {})
        assert result.success is False
        assert "multi_table_join" in (result.error or "")

    def test_aggregate_requires_aggregate_capability(self, engine):
        engine.register_template(make_template(
            id="no-agg-cap", parameters=[],
            sql_template="SELECT COUNT(*) FROM t",
            allowed_operations=["filter", "multi_table_join"],
        ))
        result = engine.render_sql("no-agg-cap", {})
        assert result.success is False
        assert "aggregate" in (result.error or "")


class TestD6CommaJoin:
    def test_comma_from_is_cartesian_rejected(self, engine):
        """`FROM t1, t2` = 隐式笛卡尔：旧代码 join_count=0 ⇒ 表数/ON 双绕过。"""
        engine.register_template(make_template(
            id="comma", parameters=[], max_tables=4,
            sql_template="SELECT 1 FROM t1, t2 WHERE t1.id = t2.id",
        ))
        result = engine.render_sql("comma", {})
        assert result.success is False
        assert "cartesian" in (result.error or "").lower()


class TestD7OnWordBoundary:
    def test_button_table_no_longer_false_pass(self, engine):
        """`JOIN button b`（无 ON，真笛卡尔）旧代码因 "button" 含 "on" 假过。"""
        engine.register_template(make_template(
            id="button", parameters=[],
            sql_template="SELECT 1 FROM t1 JOIN button b WHERE t1.id = 1",
        ))
        result = engine.render_sql("button", {})
        assert result.success is False
        assert "ON clause" in (result.error or "")

    def test_using_clause_accepted(self, engine):
        engine.register_template(make_template(
            id="using", parameters=[],
            sql_template="SELECT 1 FROM t1 JOIN t2 USING (id)",
        ))
        result = engine.render_sql("using", {})
        assert result.success is True


class TestD8SubqueryRegex:
    def test_spaced_and_multiline_subquery_caught(self, engine):
        engine.register_template(make_template(
            id="leak2", parameters=[],
            sql_template="SELECT 1 FROM t WHERE id IN ( SELECT id FROM secret )",
        ))
        result = engine.render_sql("leak2", {})
        assert result.success is False

        engine.register_template(make_template(
            id="leak3", parameters=[],
            sql_template="SELECT (SELECT mx FROM s) AS m FROM t",
        ))
        result = engine.render_sql("leak3", {})
        assert result.success is False


class TestD9FormatHardened:
    @pytest.mark.parametrize("bad_tpl", ["SELECT '{0}'", "SELECT {broken"])
    def test_format_errors_do_not_escape_as_exceptions(self, engine, bad_tpl):
        """旧代码只捕 KeyError：IndexError/ValueError 直冲调用方（接线即 500）。"""
        engine.register_template(make_template(id=f"bad-{abs(hash(bad_tpl)) % 10**6}",
                                               parameters=[], sql_template=bad_tpl))
        result = engine.render_sql(engine.list_templates()[-1].id, {})
        assert result.success is False
        assert result.error


class TestD10NoSilentOverwrite:
    def test_duplicate_registration_refused(self, engine):
        engine.register_template(make_template())
        with pytest.raises(ValueError, match="[Aa]lready registered"):
            engine.register_template(make_template(max_output_rows=99))


class TestD11AllowedValuesCoercion:
    def test_numeric_allowed_values_match(self, engine):
        """数值参数被 float 化后 str 比对（"100.0" != "100"）误拒合法值。"""
        engine.register_template(make_template(
            id="allowed-num",
            parameters=[ScenarioParameter(
                name="tier", type="number", description="tier",
                required=True, allowed_values=["100", "200"],
            )],
            sql_template="SELECT 1 FROM t WHERE tier = {tier}",
        ))
        is_valid, errors = engine.validate_parameters("allowed-num", {"tier": 100})
        assert is_valid, errors
