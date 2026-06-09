"""Tests for Encrypted Database — DuckDB + masked views."""
import pytest
from app.services.encrypted_db import EncryptedDatabase, QueryResult
from app.services.field_mask import MaskRule, MaskMode


@pytest.fixture
def db():
    return EncryptedDatabase()


@pytest.fixture
def db_with_data(db):
    db.load_table("users", ["name", "age", "email"], [
        ["Alice", "25", "alice@example.com"],
        ["Bob", "70", "bob@test.org"],
        ["Charlie", "35", "charlie@dev.io"],
    ])
    return db


# ── Table Loading ─────────────────────────────────────────────────

class TestTableLoading:
    def test_load_table(self, db):
        db.load_table("t1", ["a", "b"], [["1", "2"]])
        assert "t1" in db.list_tables()

    def test_get_schema(self, db):
        db.load_table("t1", ["a", "b"], [])
        assert db.get_table_schema("t1") == ["a", "b"]

    def test_schema_missing(self, db):
        assert db.get_table_schema("nonexistent") is None


# ── Masked Views ──────────────────────────────────────────────────

class TestMaskedViews:
    def test_create_view(self, db_with_data):
        db_with_data.create_masked_view("users_safe", "users", [
            MaskRule("email", MaskMode.HASH),
        ])
        views = db_with_data.list_views()
        assert len(views) == 1
        assert views[0].name == "users_safe"

    def test_query_masked_view_hash(self, db_with_data):
        db_with_data.create_masked_view("users_safe", "users", [
            MaskRule("email", MaskMode.HASH),
        ])
        result = db_with_data.query("SELECT * FROM users_safe")
        assert result.row_count == 3
        # Email should be hashed (64-char hex)
        for row in result.rows:
            email_idx = result.columns.index("email")
            assert len(row[email_idx]) == 64  # SHA-256 hex

    def test_query_masked_view_redact(self, db_with_data):
        db_with_data.create_masked_view("users_safe", "users", [
            MaskRule("name", MaskMode.REDACT),
        ])
        result = db_with_data.query("SELECT * FROM users_safe")
        name_idx = result.columns.index("name")
        for row in result.rows:
            assert row[name_idx] == "[REDACTED]"

    def test_query_masked_view_generalize(self, db_with_data):
        db_with_data.create_masked_view("users_safe", "users", [
            MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range"),
        ])
        result = db_with_data.query("SELECT * FROM users_safe")
        age_idx = result.columns.index("age")
        ages = {row[age_idx] for row in result.rows}
        assert "18-29" in ages  # Alice (25)
        assert "60+" in ages    # Bob (70)
        assert "30-44" in ages  # Charlie (35)

    def test_masked_columns_tracked(self, db_with_data):
        db_with_data.create_masked_view("users_safe", "users", [
            MaskRule("email", MaskMode.HASH),
            MaskRule("name", MaskMode.REDACT),
        ])
        result = db_with_data.query("SELECT * FROM users_safe")
        assert set(result.masked_columns) == {"email", "name"}


# ── Direct Query ──────────────────────────────────────────────────

class TestDirectQuery:
    def test_query_table_direct(self, db_with_data):
        result = db_with_data.query("SELECT * FROM users")
        assert result.row_count == 3
        assert "name" in result.columns
        assert result.masked_columns == []

    def test_query_with_where(self, db_with_data):
        result = db_with_data.query("SELECT * FROM users WHERE age > '30'")
        assert result.row_count >= 1

    def test_query_empty_table(self, db):
        db.load_table("empty", ["x"], [])
        result = db.query("SELECT * FROM empty")
        assert result.row_count == 0


# ── QueryResult ───────────────────────────────────────────────────

class TestQueryResult:
    def test_result_fields(self, db_with_data):
        result = db_with_data.query("SELECT * FROM users")
        assert isinstance(result, QueryResult)
        assert isinstance(result.columns, list)
        assert isinstance(result.rows, list)
        assert result.execution_ms >= 0

    def test_execution_time(self, db_with_data):
        result = db_with_data.query("SELECT * FROM users")
        assert result.execution_ms > 0 or result.execution_ms == 0  # may be very fast


# ── Multiple Views ────────────────────────────────────────────────

class TestMultipleViews:
    def test_multiple_views(self, db_with_data):
        db_with_data.create_masked_view("v1", "users", [MaskRule("email", MaskMode.HASH)])
        db_with_data.create_masked_view("v2", "users", [MaskRule("name", MaskMode.REDACT)])
        assert len(db_with_data.list_views()) == 2

    def test_views_independent(self, db_with_data):
        db_with_data.create_masked_view("v_hash", "users", [MaskRule("email", MaskMode.HASH)])
        db_with_data.create_masked_view("v_redact", "users", [MaskRule("email", MaskMode.REDACT)])

        r1 = db_with_data.query("SELECT * FROM v_hash")
        r2 = db_with_data.query("SELECT * FROM v_redact")

        email_idx = r1.columns.index("email")
        # Hash produces hex, redact produces [REDACTED]
        assert r1.rows[0][email_idx] != r2.rows[0][email_idx]


# ── Close ─────────────────────────────────────────────────────────

class TestClose:
    def test_close(self, db):
        db.close()
        # Should not raise
        db.close()
