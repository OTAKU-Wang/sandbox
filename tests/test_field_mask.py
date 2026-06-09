"""Tests for Field-Level Masking — HASH/REDACT/GENERALIZE modes."""
import pytest
from app.services.field_mask import (
    FieldMasker, MaskMode, MaskRule, MaskResult,
)


@pytest.fixture
def masker():
    return FieldMasker()


# ── HASH Mode ─────────────────────────────────────────────────────

class TestHashMode:
    def test_hash_deterministic(self, masker):
        rules = [MaskRule("email", MaskMode.HASH)]
        r1 = masker.mask_row({"email": "test@example.com"}, rules)
        r2 = masker.mask_row({"email": "test@example.com"}, rules)
        assert r1.masked_row["email"] == r2.masked_row["email"]

    def test_hash_different_values(self, masker):
        rules = [MaskRule("email", MaskMode.HASH)]
        r1 = masker.mask_row({"email": "a@b.com"}, rules)
        r2 = masker.mask_row({"email": "c@d.com"}, rules)
        assert r1.masked_row["email"] != r2.masked_row["email"]

    def test_hash_sha256_length(self, masker):
        rules = [MaskRule("val", MaskMode.HASH)]
        result = masker.mask_row({"val": "test"}, rules)
        assert len(result.masked_row["val"]) == 64  # SHA-256 hex

    def test_hash_preserves_other_fields(self, masker):
        rules = [MaskRule("secret", MaskMode.HASH)]
        row = {"secret": "hidden", "public": "visible"}
        result = masker.mask_row(row, rules)
        assert result.masked_row["public"] == "visible"


# ── REDACT Mode ───────────────────────────────────────────────────

class TestRedactMode:
    def test_redact_default(self, masker):
        rules = [MaskRule("name", MaskMode.REDACT)]
        result = masker.mask_row({"name": "Alice"}, rules)
        assert result.masked_row["name"] == "[REDACTED]"

    def test_redact_custom_placeholder(self, masker):
        rules = [MaskRule("name", MaskMode.REDACT, placeholder="***")]
        result = masker.mask_row({"name": "Alice"}, rules)
        assert result.masked_row["name"] == "***"

    def test_redact_multiple_fields(self, masker):
        rules = [
            MaskRule("name", MaskMode.REDACT),
            MaskRule("phone", MaskMode.REDACT, placeholder="XXX"),
        ]
        row = {"name": "Alice", "phone": "13800138000", "city": "Beijing"}
        result = masker.mask_row(row, rules)
        assert result.masked_row["name"] == "[REDACTED]"
        assert result.masked_row["phone"] == "XXX"
        assert result.masked_row["city"] == "Beijing"
        assert set(result.rules_applied) == {"name", "phone"}


# ── GENERALIZE Mode ──────────────────────────────────────────────

class TestGeneralizeAge:
    def test_age_minor(self, masker):
        rules = [MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range")]
        result = masker.mask_row({"age": "15"}, rules)
        assert result.masked_row["age"] == "0-17"

    def test_age_young_adult(self, masker):
        rules = [MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range")]
        result = masker.mask_row({"age": "25"}, rules)
        assert result.masked_row["age"] == "18-29"

    def test_age_middle(self, masker):
        rules = [MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range")]
        result = masker.mask_row({"age": "35"}, rules)
        assert result.masked_row["age"] == "30-44"

    def test_age_senior(self, masker):
        rules = [MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range")]
        result = masker.mask_row({"age": "70"}, rules)
        assert result.masked_row["age"] == "60+"

    def test_age_invalid(self, masker):
        rules = [MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range")]
        result = masker.mask_row({"age": "unknown"}, rules)
        assert result.masked_row["age"] == "unknown"


class TestGeneralizeDate:
    def test_date_month(self, masker):
        rules = [MaskRule("date", MaskMode.GENERALIZE, generalize_type="date_month")]
        result = masker.mask_row({"date": "2026-06-15"}, rules)
        assert result.masked_row["date"] == "2026-06"

    def test_date_year(self, masker):
        rules = [MaskRule("date", MaskMode.GENERALIZE, generalize_type="date_year")]
        result = masker.mask_row({"date": "2026-06-15"}, rules)
        assert result.masked_row["date"] == "2026"

    def test_date_iso_format(self, masker):
        rules = [MaskRule("date", MaskMode.GENERALIZE, generalize_type="date_month")]
        result = masker.mask_row({"date": "2026-06-15T10:30:00"}, rules)
        assert result.masked_row["date"] == "2026-06"


class TestGeneralizeRegion:
    def test_beijing_to_huabei(self, masker):
        rules = [MaskRule("city", MaskMode.GENERALIZE, generalize_type="region")]
        result = masker.mask_row({"city": "北京市"}, rules)
        assert result.masked_row["city"] == "华北"

    def test_shanghai_to_huadong(self, masker):
        rules = [MaskRule("city", MaskMode.GENERALIZE, generalize_type="region")]
        result = masker.mask_row({"city": "上海"}, rules)
        assert result.masked_row["city"] == "华东"

    def test_guangdong_to_huanan(self, masker):
        rules = [MaskRule("city", MaskMode.GENERALIZE, generalize_type="region")]
        result = masker.mask_row({"city": "广东省"}, rules)
        assert result.masked_row["city"] == "华南"

    def test_unknown_region_passthrough(self, masker):
        rules = [MaskRule("city", MaskMode.GENERALIZE, generalize_type="region")]
        result = masker.mask_row({"city": "Tokyo"}, rules)
        assert result.masked_row["city"] == "Tokyo"


class TestGeneralizeNumericBucket:
    def test_numeric_bucket(self, masker):
        rules = [MaskRule("salary", MaskMode.GENERALIZE, generalize_type="numeric_bucket", generalize_params={"bucket_size": 1000})]
        result = masker.mask_row({"salary": "5500"}, rules)
        assert result.masked_row["salary"] == "[5000, 6000)"

    def test_numeric_bucket_small(self, masker):
        rules = [MaskRule("score", MaskMode.GENERALIZE, generalize_type="numeric_bucket", generalize_params={"bucket_size": 10})]
        result = masker.mask_row({"score": "85"}, rules)
        assert result.masked_row["score"] == "[80, 90)"

    def test_numeric_bucket_invalid(self, masker):
        rules = [MaskRule("val", MaskMode.GENERALIZE, generalize_type="numeric_bucket")]
        result = masker.mask_row({"val": "abc"}, rules)
        assert result.masked_row["val"] == "abc"


class TestGeneralizeTruncate:
    def test_truncate(self, masker):
        rules = [MaskRule("phone", MaskMode.GENERALIZE, generalize_type="truncate", generalize_params={"keep_chars": 3})]
        result = masker.mask_row({"phone": "13800138000"}, rules)
        assert result.masked_row["phone"] == "138***"

    def test_truncate_short_value(self, masker):
        rules = [MaskRule("code", MaskMode.GENERALIZE, generalize_type="truncate", generalize_params={"keep_chars": 5})]
        result = masker.mask_row({"code": "AB"}, rules)
        assert result.masked_row["code"] == "AB"


# ── Multi-Row ─────────────────────────────────────────────────────

class TestMultiRow:
    def test_mask_rows(self, masker):
        rules = [MaskRule("name", MaskMode.REDACT)]
        rows = [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}]
        results = masker.mask_rows(rows, rules)
        assert len(results) == 3
        for r in results:
            assert r.masked_row["name"] == "[REDACTED]"

    def test_mask_rows_mixed_rules(self, masker):
        rules = [
            MaskRule("name", MaskMode.REDACT),
            MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range"),
        ]
        rows = [
            {"name": "Alice", "age": "25"},
            {"name": "Bob", "age": "70"},
        ]
        results = masker.mask_rows(rows, rules)
        assert results[0].masked_row["age"] == "18-29"
        assert results[1].masked_row["age"] == "60+"


# ── Edge Cases ────────────────────────────────────────────────────

class TestEdgeCases:
    def test_missing_field_skipped(self, masker):
        rules = [MaskRule("nonexistent", MaskMode.REDACT)]
        result = masker.mask_row({"name": "Alice"}, rules)
        assert result.rules_applied == []
        assert result.masked_row["name"] == "Alice"

    def test_none_value_skipped(self, masker):
        rules = [MaskRule("name", MaskMode.REDACT)]
        result = masker.mask_row({"name": None}, rules)
        assert result.masked_row["name"] is None
        assert "name" not in result.rules_applied

    def test_empty_row(self, masker):
        rules = [MaskRule("name", MaskMode.REDACT)]
        result = masker.mask_row({}, rules)
        assert result.masked_row == {}

    def test_result_type(self, masker):
        rules = [MaskRule("x", MaskMode.HASH)]
        result = masker.mask_row({"x": "v"}, rules)
        assert isinstance(result, MaskResult)
        assert isinstance(result.masked_row, dict)
        assert isinstance(result.rules_applied, list)
