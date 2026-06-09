"""Tests for Scenario Template Engine."""
import pytest
from app.services.scenario_engine import (
    ScenarioEngine, ScenarioTemplate, ScenarioParameter, OutputColumn,
    ScenarioExecution,
)


def make_template(**overrides) -> ScenarioTemplate:
    defaults = {
        "id": "test-scenario",
        "name": "Test Scenario",
        "description": "A test scenario",
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
        "output_schema": [
            OutputColumn(name="ent_name", type="string", description="Enterprise name"),
            OutputColumn(name="reg_capital", type="number", description="Registered capital"),
        ],
        "max_tables": 4,
        "max_output_rows": 500,
    }
    defaults.update(overrides)
    return ScenarioTemplate(**defaults)


class TestScenarioEngine:

    def setup_method(self):
        self.engine = ScenarioEngine()

    def test_register_and_retrieve(self):
        template = make_template()
        self.engine.register_template(template)
        assert self.engine.get_template("test-scenario") is template

    def test_retrieve_nonexistent(self):
        assert self.engine.get_template("missing") is None

    def test_list_all(self):
        self.engine.register_template(make_template(id="t1"))
        self.engine.register_template(make_template(id="t2", category="other"))
        assert len(self.engine.list_templates()) == 2

    def test_list_by_category(self):
        self.engine.register_template(make_template(id="t1", category="risk"))
        self.engine.register_template(make_template(id="t2", category="other"))
        assert len(self.engine.list_templates(category="risk")) == 1


class TestParameterValidation:

    def setup_method(self):
        self.engine = ScenarioEngine()
        self.engine.register_template(make_template())

    def test_valid_params(self):
        is_valid, errors = self.engine.validate_parameters(
            "test-scenario", {"industry": "C39", "min_capital": 200}
        )
        assert is_valid
        assert len(errors) == 0

    def test_missing_required_param(self):
        is_valid, errors = self.engine.validate_parameters(
            "test-scenario", {"min_capital": 100}
        )
        assert not is_valid
        assert any("industry" in e for e in errors)

    def test_default_applied(self):
        params = {"industry": "C39"}
        is_valid, errors = self.engine.validate_parameters("test-scenario", params)
        assert is_valid
        assert params["min_capital"] == 100  # default applied

    def test_type_coercion_number(self):
        params = {"industry": "C39", "min_capital": "500"}
        is_valid, errors = self.engine.validate_parameters("test-scenario", params)
        assert is_valid
        assert params["min_capital"] == 500.0

    def test_invalid_type(self):
        is_valid, errors = self.engine.validate_parameters(
            "test-scenario", {"industry": "C39", "min_capital": "not_a_number"}
        )
        assert not is_valid
        assert any("number" in e for e in errors)

    def test_range_check(self):
        is_valid, errors = self.engine.validate_parameters(
            "test-scenario", {"industry": "C39", "min_capital": -10}
        )
        assert not is_valid
        assert any(">=" in e for e in errors)

    def test_allowed_values(self):
        template = make_template(
            id="restricted",
            parameters=[
                ScenarioParameter(name="mode", type="string", description="Mode",
                                  required=True, allowed_values=["fast", "slow"]),
            ],
            sql_template="SELECT 1 WHERE mode = '{mode}'",
        )
        self.engine.register_template(template)
        is_valid, _ = self.engine.validate_parameters("restricted", {"mode": "fast"})
        assert is_valid
        is_valid, errors = self.engine.validate_parameters("restricted", {"mode": "invalid"})
        assert not is_valid

    def test_nonexistent_template(self):
        is_valid, errors = self.engine.validate_parameters("missing", {})
        assert not is_valid


class TestSQLRendering:

    def setup_method(self):
        self.engine = ScenarioEngine()
        self.engine.register_template(make_template())

    def test_render_success(self):
        result = self.engine.render_sql("test-scenario", {"industry": "C39", "min_capital": 100})
        assert result.success
        assert "C39" in result.sql
        assert "100" in result.sql
        assert "LIMIT 500" in result.sql

    def test_render_with_limit(self):
        template = make_template(
            id="with-limit",
            parameters=[],
            sql_template="SELECT 1 FROM t LIMIT 50",
        )
        self.engine.register_template(template)
        result = self.engine.render_sql("with-limit", {})
        assert result.success
        assert "LIMIT 50" in result.sql
        # Should not add another LIMIT
        assert result.sql.count("LIMIT") == 1

    def test_render_invalid_params(self):
        result = self.engine.render_sql("test-scenario", {})
        assert not result.success
        assert "error" in result.error.lower() or "Missing" in result.error

    def test_render_nonexistent_template(self):
        result = self.engine.render_sql("missing", {})
        assert not result.success


class TestJoinConstraints:

    def setup_method(self):
        self.engine = ScenarioEngine()

    def test_too_many_tables(self):
        template = make_template(
            id="many-tables",
            parameters=[],
            max_tables=2,
            sql_template="SELECT 1 FROM t1 JOIN t2 ON t1.id=t2.id JOIN t3 ON t2.id=t3.id",
        )
        self.engine.register_template(template)
        result = self.engine.render_sql("many-tables", {})
        assert not result.success
        assert "Too many tables" in result.error

    def test_cross_join_rejected(self):
        template = make_template(
            id="cross-join",
            parameters=[],
            sql_template="SELECT 1 FROM t1 CROSS JOIN t2",
        )
        self.engine.register_template(template)
        result = self.engine.render_sql("cross-join", {})
        assert not result.success
        assert "cartesian" in result.error.lower()

    def test_join_without_on_rejected(self):
        template = make_template(
            id="no-on",
            parameters=[],
            sql_template="SELECT 1 FROM t1 JOIN t2 WHERE t1.id = 1",
        )
        self.engine.register_template(template)
        result = self.engine.render_sql("no-on", {})
        assert not result.success
        assert "ON clause" in result.error

    def test_valid_join(self):
        template = make_template(
            id="valid-join",
            sql_template="SELECT 1 FROM t1 JOIN t2 ON t1.id = t2.id",
            parameters=[],
        )
        self.engine.register_template(template)
        result = self.engine.render_sql("valid-join", {})
        assert result.success


class TestSQLInjection:

    def setup_method(self):
        self.engine = ScenarioEngine()
        self.engine.register_template(make_template())

    def test_sanitize_single_quotes(self):
        result = self.engine.render_sql(
            "test-scenario",
            {"industry": "C39'; DROP TABLE--", "min_capital": 100},
        )
        # Should sanitize the injection attempt
        if result.success:
            assert "DROP" not in result.sql
            assert ";" not in result.sql

    def test_sanitize_comments(self):
        result = self.engine.render_sql(
            "test-scenario",
            {"industry": "C39--comment", "min_capital": 100},
        )
        if result.success:
            assert "--" not in result.sql
