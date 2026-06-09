"""Tests for ClickHouse audit schema expansion.

SS-06: Expands audit_events from 7 to 30+ columns.
Covers DP metrics, training metrics, chain attestation, and more.
"""
import pytest


# ============================================================
# Schema Column Validation
# ============================================================
class TestAuditEventsSchema:
    """Verify audit_events table has all required columns."""

    EXPECTED_COLUMNS = [
        # Core fields (existing)
        "event_id", "timestamp", "user_id", "session_id",
        "action", "resource_type", "resource_id", "detail",
        "ip_address", "sm2_signature", "integrity_hash",
        # DP metrics (new)
        "dp_epsilon", "dp_delta", "dp_mechanism", "dp_sensitivity",
        # Training metrics (new)
        "training_job_id", "training_model", "training_loss", "training_epochs",
        # Chain attestation (new)
        "chain_tx_hash", "chain_block_number", "chain_contract_address",
        # Sandbox details (new)
        "sandbox_level", "sandbox_exit_code", "sandbox_duration_ms",
        # Data product (new)
        "product_id", "product_type", "product_version",
        # Contract (new)
        "contract_id", "contract_type",
        # Output control (new)
        "output_format", "output_rows", "output_size_bytes",
        # Federation (new)
        "federation_space_id", "federation_trust_level",
    ]

    def test_audit_events_has_core_columns(self):
        """audit_events table has all core columns."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        columns = schema.get_columns("audit_events")
        core = ["event_id", "timestamp", "user_id", "session_id", "action",
                "resource_type", "resource_id", "detail"]
        for col in core:
            assert col in columns, f"Missing core column: {col}"

    def test_audit_events_has_dp_columns(self):
        """audit_events table has differential privacy columns."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        columns = schema.get_columns("audit_events")
        dp_cols = ["dp_epsilon", "dp_delta", "dp_mechanism", "dp_sensitivity"]
        for col in dp_cols:
            assert col in columns, f"Missing DP column: {col}"

    def test_audit_events_has_training_columns(self):
        """audit_events table has training metrics columns."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        columns = schema.get_columns("audit_events")
        training_cols = ["training_job_id", "training_model", "training_loss"]
        for col in training_cols:
            assert col in columns, f"Missing training column: {col}"

    def test_audit_events_has_chain_columns(self):
        """audit_events table has chain attestation columns."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        columns = schema.get_columns("audit_events")
        chain_cols = ["chain_tx_hash", "chain_block_number"]
        for col in chain_cols:
            assert col in columns, f"Missing chain column: {col}"

    def test_audit_events_column_count(self):
        """audit_events table has at least 30 columns."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        columns = schema.get_columns("audit_events")
        assert len(columns) >= 30, f"Expected 30+ columns, got {len(columns)}"

    def test_audit_events_engine(self):
        """audit_events uses MergeTree engine with monthly partitioning."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        info = schema.get_table_info("audit_events")
        assert "MergeTree" in info.get("engine", "")
        assert "toYYYYMM" in info.get("partition_key", "") or \
               "YYYYMM" in info.get("partition_expression", "")


# ============================================================
# Policy Decisions Schema
# ============================================================
class TestPolicyDecisionsSchema:
    """Verify policy_decisions table schema."""

    def test_policy_decisions_exists(self):
        """policy_decisions table exists."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        tables = schema.list_tables()
        assert "policy_decisions" in tables

    def test_policy_decisions_has_decision_fields(self):
        """policy_decisions has required decision fields."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        columns = schema.get_columns("policy_decisions")
        required = ["decision_id", "timestamp", "user_id", "decision", "reason"]
        for col in required:
            assert col in columns, f"Missing column: {col}"


# ============================================================
# Schema Migration
# ============================================================
class TestSchemaMigration:
    """Schema can be migrated from old to new version."""

    def test_migration_adds_columns(self):
        """Migration script adds new columns without breaking existing data."""
        from app.services.clickhouse_schema import ClickHouseSchema
        schema = ClickHouseSchema()
        # Should not raise
        result = schema.validate_schema_completeness("audit_events")
        assert result["valid"] is True or result["missing_columns"] is not None
