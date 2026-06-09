"""Tests for Row-Level Security (RLS) strategy engine.

SS-09#4: Region, time, and sensitivity-based RLS policies.
Ensures query results are filtered based on user context and policy rules.
"""
import pytest
from datetime import datetime, timezone, timedelta


# ============================================================
# RLS Policy Types
# ============================================================
class TestRLSRegionPolicy:
    """Region-based RLS — restrict data access by geographic region."""

    def test_region_allow(self):
        """User in allowed region can access data."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "region", "allowed_regions": ["cn-east", "cn-north"]}
        row = {"region": "cn-east", "data": "test"}
        assert engine.evaluate(policy, row, user_region="cn-east") is True

    def test_region_deny(self):
        """User in denied region cannot access data."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "region", "allowed_regions": ["cn-east", "cn-north"]}
        row = {"region": "us-west", "data": "test"}
        assert engine.evaluate(policy, row, user_region="us-west") is False

    def test_region_no_user_context(self):
        """Missing user region context defaults to deny."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "region", "allowed_regions": ["cn-east"]}
        row = {"region": "cn-east", "data": "test"}
        assert engine.evaluate(policy, row, user_region=None) is False


class TestRLSTimePolicy:
    """Time-based RLS — restrict data access by time window."""

    def test_time_within_window(self):
        """Access within allowed time window succeeds."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        now = datetime.now(timezone.utc)
        policy = {
            "type": "time",
            "start_hour": 8,
            "end_hour": 18,
            "timezone": "UTC",
        }
        # Mock current hour to be within window
        assert engine.evaluate_time_policy(policy, current_hour=10) is True

    def test_time_outside_window(self):
        """Access outside allowed time window fails."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {
            "type": "time",
            "start_hour": 8,
            "end_hour": 18,
            "timezone": "UTC",
        }
        assert engine.evaluate_time_policy(policy, current_hour=22) is False

    def test_time_boundary_start(self):
        """Access at exact start hour succeeds."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "time", "start_hour": 8, "end_hour": 18}
        assert engine.evaluate_time_policy(policy, current_hour=8) is True

    def test_time_boundary_end(self):
        """Access at exact end hour fails (exclusive)."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "time", "start_hour": 8, "end_hour": 18}
        assert engine.evaluate_time_policy(policy, current_hour=18) is False


class TestRLSSensitivityPolicy:
    """Sensitivity-based RLS — restrict access by data sensitivity level."""

    def test_sensitivity_allowed(self):
        """User with sufficient clearance can access sensitive data."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "sensitivity", "max_level": 3}
        row = {"sensitivity": 2, "data": "test"}
        assert engine.evaluate(policy, row, user_clearance=3) is True

    def test_sensitivity_denied(self):
        """User with insufficient clearance cannot access sensitive data."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "sensitivity", "max_level": 3}
        row = {"sensitivity": 3, "data": "test"}
        assert engine.evaluate(policy, row, user_clearance=1) is False

    def test_sensitivity_exact_match(self):
        """User with exact clearance level can access."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policy = {"type": "sensitivity", "max_level": 3}
        row = {"sensitivity": 3, "data": "test"}
        assert engine.evaluate(policy, row, user_clearance=3) is True


# ============================================================
# Composite Policies
# ============================================================
class TestRLSCompositePolicy:
    """Multiple policies combined (AND logic)."""

    def test_all_policies_pass(self):
        """Row passes all composite policies."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policies = [
            {"type": "region", "allowed_regions": ["cn-east"]},
            {"type": "sensitivity", "max_level": 3},
        ]
        row = {"region": "cn-east", "sensitivity": 2}
        assert engine.evaluate_composite(policies, row, user_region="cn-east", user_clearance=3) is True

    def test_one_policy_fails(self):
        """Row fails if any composite policy fails."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        policies = [
            {"type": "region", "allowed_regions": ["cn-east"]},
            {"type": "sensitivity", "max_level": 3},
        ]
        row = {"region": "us-west", "sensitivity": 2}
        assert engine.evaluate_composite(policies, row, user_region="cn-east", user_clearance=3) is False


# ============================================================
# SQL Rewrite
# ============================================================
class TestRLSSQLRewrite:
    """RLS engine can inject WHERE clauses into SQL queries."""

    def test_inject_region_filter(self):
        """Region policy adds WHERE clause for region column."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        sql = "SELECT * FROM users"
        policy = {"type": "region", "column": "region", "allowed_regions": ["cn-east"]}
        rewritten = engine.inject_filter(sql, policy, user_region="cn-east")
        assert "WHERE" in rewritten.upper()
        assert "region" in rewritten.lower()

    def test_inject_sensitivity_filter(self):
        """Sensitivity policy adds WHERE clause for sensitivity column."""
        from app.services.rls_engine import RLSEngine
        engine = RLSEngine()
        sql = "SELECT * FROM medical_records"
        policy = {"type": "sensitivity", "column": "sensitivity_level", "max_level": 2}
        rewritten = engine.inject_filter(sql, policy, user_clearance=3)
        assert "WHERE" in rewritten.upper()
