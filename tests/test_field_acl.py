"""Tests for Field ACL service."""
import pytest

from app.services.field_acl import FieldACLService, FieldRule, FieldACLResult


@pytest.fixture
def service():
    return FieldACLService()


class TestFieldACLService:
    def test_filter_no_rules_allows_all(self, service):
        result = service.filter_fields(["a", "b", "c"], {})
        assert result.allowed_fields == ["a", "b", "c"]
        assert result.denied_fields == []

    def test_filter_with_read_rule(self, service):
        acl = {
            "name": {"read": True},
            "ssn": {"read": False},
        }
        result = service.filter_fields(["name", "ssn", "age"], acl)
        assert "name" in result.allowed_fields
        assert "age" in result.allowed_fields  # No rule = allow
        assert "ssn" in result.denied_fields

    def test_filter_with_mask(self, service):
        acl = {
            "phone": {"read": True, "mask_pattern": "prefix:3"},
            "email": {"read": True, "mask_pattern": "***"},
        }
        result = service.filter_fields(["phone", "email"], acl)
        assert result.masked_fields["phone"] == "prefix:3"
        assert result.masked_fields["email"] == "***"

    def test_filter_aggregate_only(self, service):
        acl = {
            "income": {"read": True, "aggregate_only": True},
        }
        result = service.filter_fields(["income", "name"], acl)
        assert "income" in result.aggregate_only_fields
        assert "name" not in result.aggregate_only_fields

    def test_parse_rules(self, service):
        acl = {
            "f1": {"read": True, "mask_pattern": "***"},
            "f2": {"read": False, "aggregate_only": True},
        }
        rules = service.parse_rules(acl)
        assert isinstance(rules["f1"], FieldRule)
        assert rules["f1"].read is True
        assert rules["f1"].mask_pattern == "***"
        assert rules["f2"].read is False
        assert rules["f2"].aggregate_only is True


class TestMaskApplication:
    def test_mask_star_pattern(self, service):
        rows = [{"name": "John Doe", "age": 30}]
        result = service.apply_masks(rows, {"name": "***"})
        assert result[0]["name"] == "***"
        assert result[0]["age"] == 30

    def test_mask_prefix_pattern(self, service):
        rows = [{"phone": "13812345678"}]
        result = service.apply_masks(rows, {"phone": "prefix:3"})
        assert result[0]["phone"] == "138***"

    def test_mask_sha256_pattern(self, service):
        rows = [{"ssn": "123-45-6789"}]
        result = service.apply_masks(rows, {"ssn": "sha256"})
        assert result[0]["ssn"] != "123-45-6789"
        assert len(result[0]["ssn"]) == 16

    def test_mask_null_pattern(self, service):
        rows = [{"secret": "value"}]
        result = service.apply_masks(rows, {"secret": "null"})
        assert result[0]["secret"] is None

    def test_mask_none_value(self, service):
        rows = [{"field": None}]
        result = service.apply_masks(rows, {"field": "***"})
        assert result[0]["field"] is None

    def test_mask_empty_rows(self, service):
        result = service.apply_masks([], {"field": "***"})
        assert result == []

    def test_no_masks_passthrough(self, service):
        rows = [{"a": 1, "b": 2}]
        result = service.apply_masks(rows, {})
        assert result == rows


class TestSQLProjection:
    def test_build_sql_projection(self, service):
        acl = {
            "name": {"read": True},
            "ssn": {"read": False},
            "income": {"read": True, "aggregate_only": True},
        }
        select_fields, agg_fields = service.build_sql_projection(
            ["name", "ssn", "income"], acl
        )
        assert "name" in select_fields
        assert "income" in select_fields
        assert "ssn" not in select_fields
        assert "income" in agg_fields

    def test_validate_aggregate_query(self, service):
        acl = {"income": {"read": True, "aggregate_only": True}}
        # Without aggregation — should fail
        violations = service.validate_aggregate_query(["income"], acl, has_aggregation=False)
        assert len(violations) == 1
        # With aggregation — should pass
        violations = service.validate_aggregate_query(["income"], acl, has_aggregation=True)
        assert len(violations) == 0
