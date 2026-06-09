"""ClickHouse Schema — declarative schema definitions for audit tables.

Provides schema introspection, validation, and migration support for
the expanded 30+ column ClickHouse audit schema.
"""
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ColumnDef:
    """ClickHouse column definition."""
    name: str
    dtype: str
    default: str = ""
    comment: str = ""


@dataclass
class IndexDef:
    """ClickHouse index definition (bloom filter, set, etc.)."""
    name: str
    expr: str
    type: str = "bloom_filter"
    granularity: int = 3


@dataclass
class TableDef:
    """ClickHouse table definition."""
    name: str
    engine: str = "MergeTree()"
    partition_key: str = ""
    order_by: str = ""
    ttl: str = ""
    columns: list[ColumnDef] = field(default_factory=list)
    indexes: list[IndexDef] = field(default_factory=list)


class ClickHouseSchema:
    """Declarative ClickHouse schema manager.

    Defines the expanded audit_events schema (30+ columns) and related tables.
    Provides introspection, validation, and migration support.
    """

    def __init__(self):
        self._tables: dict[str, TableDef] = {}
        self._init_audit_events()
        self._init_policy_decisions()
        self._init_cdc_events()
        self._init_data_product_versions()
        self._init_certificate_events()
        self._init_sandbox_activity_events()
        self._init_gateway_metering()

    def _init_audit_events(self) -> None:
        """Initialize audit_events table schema with 30+ columns."""
        columns = [
            # Core identification
            ColumnDef("event_id", "UUID", "generateUUIDv4()"),
            ColumnDef("timestamp", "DateTime64(3, 'UTC')"),
            ColumnDef("event_date", "Date", "toDate(timestamp)"),
            # User/session context
            ColumnDef("user_id", "UUID"),
            ColumnDef("session_id", "UUID"),
            ColumnDef("user_role", "LowCardinality(String)", "''"),
            ColumnDef("user_region", "LowCardinality(String)", "''"),
            # Action details
            ColumnDef("action", "LowCardinality(String)"),
            ColumnDef("action_category", "LowCardinality(String)", "''"),
            ColumnDef("resource_type", "LowCardinality(String)"),
            ColumnDef("resource_id", "String", "''"),
            ColumnDef("resource_name", "String", "''"),
            # Request context
            ColumnDef("ip_address", "IPv4", "toIPv4('0.0.0.0')"),
            ColumnDef("user_agent", "String", "''"),
            ColumnDef("request_method", "LowCardinality(String)", "''"),
            ColumnDef("request_path", "String", "''"),
            ColumnDef("request_id", "UUID", "generateUUIDv4()"),
            # Cryptographic attestation
            ColumnDef("sm2_signature", "String", "''"),
            ColumnDef("integrity_hash", "String", "''"),
            ColumnDef("merkle_root", "String", "''"),
            ColumnDef("blockchain_tx_hash", "String", "''"),
            # Data product context
            ColumnDef("data_product_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("data_product_version", "UInt32", "0"),
            ColumnDef("data_product_type", "LowCardinality(String)", "''"),
            ColumnDef("product_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("product_type", "LowCardinality(String)", "''"),
            ColumnDef("product_version", "UInt32", "0"),
            # Sandbox/compute context
            ColumnDef("sandbox_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("sandbox_type", "LowCardinality(String)", "''"),
            ColumnDef("sandbox_level", "LowCardinality(String)", "''"),
            ColumnDef("sandbox_exit_code", "Int32", "0"),
            ColumnDef("sandbox_duration_ms", "UInt64", "0"),
            ColumnDef("compute_duration_ms", "UInt64", "0"),
            # Data metrics
            ColumnDef("rows_affected", "UInt64", "0"),
            ColumnDef("bytes_read", "UInt64", "0"),
            ColumnDef("bytes_written", "UInt64", "0"),
            # Policy/contract context
            ColumnDef("contract_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("contract_type", "LowCardinality(String)", "''"),
            ColumnDef("policy_decision", "LowCardinality(String)", "''"),
            ColumnDef("policy_reason", "String", "''"),
            # Output control
            ColumnDef("output_format", "LowCardinality(String)", "''"),
            ColumnDef("output_rows", "UInt64", "0"),
            ColumnDef("output_size_bytes", "UInt64", "0"),
            # Federation
            ColumnDef("federation_space_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("federation_trust_level", "LowCardinality(String)", "''"),
            # Security context
            ColumnDef("security_level", "LowCardinality(String)", "''"),
            ColumnDef("encryption_key_id", "String", "''"),
            ColumnDef("column_encrypted_fields", "Array(String)", "[]"),
            # Training/AI context
            ColumnDef("training_job_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("training_model", "String", "''"),
            ColumnDef("training_loss", "Float64", "0.0"),
            ColumnDef("training_epochs", "UInt32", "0"),
            ColumnDef("model_id", "String", "''"),
            # Differential privacy
            ColumnDef("dp_epsilon", "Float64", "0.0"),
            ColumnDef("dp_delta", "Float64", "0.0"),
            ColumnDef("dp_mechanism", "LowCardinality(String)", "''"),
            ColumnDef("dp_sensitivity", "Float64", "0.0"),
            # Chain attestation
            ColumnDef("chain_tx_hash", "String", "''"),
            ColumnDef("chain_block_number", "UInt64", "0"),
            ColumnDef("chain_contract_address", "String", "''"),
            # Error tracking
            ColumnDef("error_code", "LowCardinality(String)", "''"),
            ColumnDef("error_message", "String", "''"),
            # Flexible payload
            ColumnDef("detail", "String", "'{}'"),
            ColumnDef("tags", "Array(String)", "[]"),
        ]

        self._tables["audit_events"] = TableDef(
            name="audit_events",
            engine="MergeTree()",
            partition_key="toYYYYMM(timestamp)",
            order_by="(timestamp, user_id, action, resource_type)",
            ttl="timestamp + INTERVAL 7 YEAR",
            columns=columns,
            indexes=[
                IndexDef("idx_session_id", "session_id", "bloom_filter"),
                IndexDef("idx_contract_id", "contract_id", "bloom_filter"),
                IndexDef("idx_sandbox_id", "sandbox_id", "bloom_filter"),
                IndexDef("idx_action", "action", "bloom_filter"),
                IndexDef("idx_resource_type", "resource_type", "bloom_filter"),
            ],
        )

    def _init_policy_decisions(self) -> None:
        columns = [
            ColumnDef("decision_id", "UUID", "generateUUIDv4()"),
            ColumnDef("timestamp", "DateTime64(3, 'UTC')"),
            ColumnDef("user_id", "UUID"),
            ColumnDef("contract_id", "UUID"),
            ColumnDef("operation", "String"),
            ColumnDef("sandbox_mode", "String"),
            ColumnDef("decision", "LowCardinality(String)"),
            ColumnDef("reason", "String"),
            ColumnDef("fields_requested", "Array(String)"),
            ColumnDef("fields_allowed", "Array(String)"),
            ColumnDef("fields_denied", "Array(String)"),
            ColumnDef("rls_policies_applied", "Array(String)", "[]"),
            ColumnDef("column_encryption_applied", "Array(String)", "[]"),
            ColumnDef("sensitivity_level", "LowCardinality(String)", "''"),
            ColumnDef("user_region", "LowCardinality(String)", "''"),
            ColumnDef("user_clearance", "LowCardinality(String)", "''"),
        ]
        self._tables["policy_decisions"] = TableDef(
            name="policy_decisions",
            engine="MergeTree()",
            partition_key="toYYYYMM(timestamp)",
            order_by="(timestamp, user_id, contract_id)",
            columns=columns,
        )

    def _init_cdc_events(self) -> None:
        columns = [
            ColumnDef("event_id", "UUID", "generateUUIDv4()"),
            ColumnDef("timestamp", "DateTime64(3, 'UTC')"),
            ColumnDef("source_table", "String"),
            ColumnDef("operation", "LowCardinality(String)"),
            ColumnDef("lsn", "UInt64", "0"),
            ColumnDef("before_data", "String", "'{}'"),
            ColumnDef("after_data", "String", "'{}'"),
            ColumnDef("connector_name", "String", "''"),
            ColumnDef("kafka_topic", "String", "''"),
            ColumnDef("kafka_offset", "UInt64", "0"),
            ColumnDef("processed", "Boolean", "false"),
        ]
        self._tables["cdc_events"] = TableDef(
            name="cdc_events",
            engine="MergeTree()",
            partition_key="toYYYYMM(timestamp)",
            order_by="(timestamp, source_table, operation)",
            columns=columns,
        )

    def _init_data_product_versions(self) -> None:
        columns = [
            ColumnDef("version_id", "UUID", "generateUUIDv4()"),
            ColumnDef("timestamp", "DateTime64(3, 'UTC')"),
            ColumnDef("data_product_id", "UUID"),
            ColumnDef("version", "UInt32"),
            ColumnDef("parent_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("change_summary", "String", "''"),
            ColumnDef("user_id", "UUID"),
            ColumnDef("action", "LowCardinality(String)"),
            ColumnDef("schema_snapshot", "String", "'{}'"),
        ]
        self._tables["data_product_versions"] = TableDef(
            name="data_product_versions",
            engine="MergeTree()",
            partition_key="toYYYYMM(timestamp)",
            order_by="(timestamp, data_product_id, version)",
            columns=columns,
        )

    def _init_certificate_events(self) -> None:
        columns = [
            ColumnDef("event_id", "UUID", "generateUUIDv4()"),
            ColumnDef("timestamp", "DateTime64(3, 'UTC')"),
            ColumnDef("cert_id", "UUID"),
            ColumnDef("serial_number", "String"),
            ColumnDef("action", "LowCardinality(String)"),
            ColumnDef("subject", "String", "''"),
            ColumnDef("issuer", "String", "''"),
            ColumnDef("user_id", "UUID"),
            ColumnDef("reason", "String", "''"),
            ColumnDef("chain_valid", "Boolean", "true"),
            ColumnDef("chain_length", "UInt32", "0"),
        ]
        self._tables["certificate_events"] = TableDef(
            name="certificate_events",
            engine="MergeTree()",
            partition_key="toYYYYMM(timestamp)",
            order_by="(timestamp, cert_id, action)",
            columns=columns,
        )

    def _init_sandbox_activity_events(self) -> None:
        """Initialize sandbox_activity_events table for in-sandbox activity audit."""
        columns = [
            ColumnDef("event_id", "UUID", "generateUUIDv4()"),
            ColumnDef("timestamp", "DateTime64(3, 'UTC')"),
            ColumnDef("event_date", "Date", "toDate(timestamp)"),
            # Session context
            ColumnDef("session_id", "String", "''"),
            ColumnDef("user_id", "String", "''"),
            ColumnDef("sandbox_level", "LowCardinality(String)", "''"),
            # Event classification
            ColumnDef("category", "LowCardinality(String)"),  # data_access, network, application
            ColumnDef("event_type", "LowCardinality(String)"),  # query, connection, process_start, etc.
            # Detail payload
            ColumnDef("detail", "String", "'{}'"),
            # Metrics
            ColumnDef("duration_ms", "UInt64", "0"),
            ColumnDef("bytes_read", "UInt64", "0"),
            ColumnDef("bytes_written", "UInt64", "0"),
            ColumnDef("rows_affected", "UInt64", "0"),
            # Risk assessment
            ColumnDef("risk_level", "LowCardinality(String)", "'low'"),
            ColumnDef("blocked", "Boolean", "false"),
        ]
        self._tables["sandbox_activity_events"] = TableDef(
            name="sandbox_activity_events",
            engine="MergeTree()",
            partition_key="toYYYYMM(timestamp)",
            order_by="(timestamp, session_id, category, event_type)",
            ttl="timestamp + INTERVAL 3 YEAR",
            columns=columns,
            indexes=[
                IndexDef("idx_session_id", "session_id", "bloom_filter"),
                IndexDef("idx_category", "category", "bloom_filter"),
                IndexDef("idx_event_type", "event_type", "bloom_filter"),
            ],
        )

    def _init_gateway_metering(self) -> None:
        """Initialize gateway_metering table for contract gateway access metering."""
        columns = [
            ColumnDef("request_id", "UUID", "generateUUIDv4()"),
            ColumnDef("timestamp", "DateTime64(3, 'UTC')"),
            ColumnDef("event_date", "Date", "toDate(timestamp)"),
            # Contract context
            ColumnDef("contract_id", "UUID"),
            ColumnDef("app_id", "String"),
            ColumnDef("consumer_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            ColumnDef("product_id", "UUID", "toUUID('00000000-0000-0000-0000-000000000000')"),
            # Request details
            ColumnDef("operation", "LowCardinality(String)"),  # query, access, export
            # Metering
            ColumnDef("rows_returned", "UInt64", "0"),
            ColumnDef("bytes_returned", "UInt64", "0"),
            ColumnDef("duration_ms", "UInt64", "0"),
            ColumnDef("status_code", "UInt16", "200"),
        ]
        self._tables["gateway_metering"] = TableDef(
            name="gateway_metering",
            engine="SummingMergeTree()",
            partition_key="toYYYYMM(timestamp)",
            order_by="(timestamp, contract_id, app_id, product_id, operation)",
            ttl="timestamp + INTERVAL 3 YEAR",
            columns=columns,
        )

    def get_columns(self, table_name: str) -> list[str]:
        """Get column names for a table."""
        table = self._tables.get(table_name)
        if not table:
            return []
        return [col.name for col in table.columns]

    def get_table_info(self, table_name: str) -> dict[str, Any]:
        """Get table metadata (engine, partition key, etc.)."""
        table = self._tables.get(table_name)
        if not table:
            return {}
        return {
            "name": table.name,
            "engine": table.engine,
            "partition_key": table.partition_key,
            "order_by": table.order_by,
            "ttl": table.ttl,
            "column_count": len(table.columns),
        }

    def list_tables(self) -> list[str]:
        """List all defined table names."""
        return list(self._tables.keys())

    def validate_schema_completeness(self, table_name: str) -> dict[str, Any]:
        """Validate that a table schema is complete.

        Returns dict with 'valid' boolean and 'missing_columns' list.
        """
        table = self._tables.get(table_name)
        if not table:
            return {"valid": False, "error": f"Table {table_name} not found", "missing_columns": []}

        missing = []
        for col in table.columns:
            if not col.name or not col.dtype:
                missing.append(col.name or "(unnamed)")

        return {
            "valid": len(missing) == 0,
            "table": table_name,
            "column_count": len(table.columns),
            "missing_columns": missing,
        }

    def generate_create_sql(self, table_name: str) -> str:
        """Generate CREATE TABLE SQL for a table."""
        table = self._tables.get(table_name)
        if not table:
            return ""

        col_defs = []
        for col in table.columns:
            parts = f"    {col.name} {col.dtype}"
            if col.default:
                parts += f" DEFAULT {col.default}"
            col_defs.append(parts)

        cols_str = ",\n".join(col_defs)
        # Add index definitions if present
        if table.indexes:
            idx_parts = []
            for idx in table.indexes:
                idx_parts.append(
                    f"    INDEX {idx.name} {idx.expr} TYPE {idx.type} GRANULARITY {idx.granularity}"
                )
            cols_str += ",\n" + ",\n".join(idx_parts)

        sql = f"CREATE TABLE IF NOT EXISTS cds_audit.{table_name}\n(\n{cols_str}\n)\nENGINE = {table.engine}"
        if table.partition_key:
            sql += f"\nPARTITION BY {table.partition_key}"
        if table.order_by:
            sql += f"\nORDER BY {table.order_by}"
        if table.ttl:
            sql += f"\nTTL {table.ttl}"
        return sql


# Singleton
clickhouse_schema = ClickHouseSchema()
