"""Tests for DB Access Proxy.

SS-09#5: SQL interception, policy evaluation, and RLS injection.
Ensures all database queries go through security checks before execution.
"""
import pytest


# ============================================================
# SQL Interception
# ============================================================
class TestSQLInterception:
    """Proxy intercepts and analyzes SQL queries before execution."""

    def test_parse_select_query(self):
        """Proxy correctly parses SELECT queries."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        parsed = proxy.parse_sql("SELECT id, name FROM users WHERE age > 18")
        assert parsed["type"] == "SELECT"
        assert "users" in parsed["tables"]
        assert "id" in parsed["columns"]
        assert "name" in parsed["columns"]

    def test_parse_insert_query(self):
        """Proxy correctly parses INSERT queries."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        parsed = proxy.parse_sql("INSERT INTO users (name, age) VALUES ('Alice', 30)")
        assert parsed["type"] == "INSERT"
        assert "users" in parsed["tables"]

    def test_block_dangerous_queries(self):
        """Proxy blocks dangerous SQL operations."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        with pytest.raises(Exception, match="blocked|forbidden|denied"):
            proxy.validate_sql("DROP TABLE users")
        with pytest.raises(Exception, match="blocked|forbidden|denied"):
            proxy.validate_sql("DELETE FROM users WHERE 1=1")
        with pytest.raises(Exception, match="blocked|forbidden|denied"):
            proxy.validate_sql("ALTER TABLE users ADD COLUMN secret TEXT")

    def test_block_select_star(self):
        """Proxy blocks SELECT * to prevent mass data exfiltration."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        with pytest.raises(Exception, match="select.*star|column.*required"):
            proxy.validate_sql("SELECT * FROM users")

    def test_allow_valid_select(self):
        """Proxy allows valid SELECT with specific columns."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        result = proxy.validate_sql("SELECT id, name FROM users WHERE id = 1")
        assert result["allowed"] is True


# ============================================================
# Row Count Limits
# ============================================================
class TestRowCountLimits:
    """Proxy enforces row count limits to prevent bulk extraction."""

    def test_inject_row_limit(self):
        """Proxy adds LIMIT clause if missing."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy(max_rows=1000)
        sql = proxy.inject_limit("SELECT id, name FROM users")
        assert "LIMIT" in sql.upper()

    def test_cap_existing_limit(self):
        """Proxy caps existing LIMIT to max_rows."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy(max_rows=100)
        sql = proxy.inject_limit("SELECT id, name FROM users LIMIT 9999")
        assert "100" in sql

    def test_allow_smaller_limit(self):
        """Proxy preserves LIMIT smaller than max."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy(max_rows=1000)
        sql = proxy.inject_limit("SELECT id, name FROM users LIMIT 50")
        assert "50" in sql


# ============================================================
# Policy Evaluation Integration
# ============================================================
class TestPolicyEvaluation:
    """Proxy evaluates RLS policies before query execution."""

    def test_query_with_matching_policy(self):
        """Query passes when RLS policy allows."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        policies = [{"type": "region", "allowed_regions": ["cn-east"], "column": "region"}]
        sql = "SELECT id, name FROM users WHERE region = 'cn-east'"
        result = proxy.evaluate_query(sql, policies, user_region="cn-east")
        assert result["allowed"] is True

    def test_query_with_blocking_policy(self):
        """Query is modified when RLS policy restricts."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        policies = [{"type": "region", "allowed_regions": ["cn-east"], "column": "region"}]
        sql = "SELECT id, name FROM users"
        result = proxy.evaluate_query(sql, policies, user_region="cn-east")
        # Should inject region filter
        assert "region" in result.get("rewritten_sql", sql).lower()


# ============================================================
# Query Audit
# ============================================================
class TestQueryAudit:
    """Proxy logs all queries for audit trail."""

    def test_query_logged(self):
        """Executed queries are logged for audit."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        proxy.log_query("SELECT id FROM users", user_id="user-1", allowed=True)
        logs = proxy.get_query_log()
        assert len(logs) > 0
        assert logs[-1]["sql"] == "SELECT id FROM users"
        assert logs[-1]["user_id"] == "user-1"
        assert logs[-1]["allowed"] is True

    def test_blocked_query_logged(self):
        """Blocked queries are also logged."""
        from app.services.db_access_proxy import DBAccessProxy
        proxy = DBAccessProxy()
        proxy.log_query("DROP TABLE users", user_id="user-1", allowed=False, reason="dangerous")
        logs = proxy.get_query_log()
        assert logs[-1]["allowed"] is False
