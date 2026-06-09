"""Sprint 4 tests — CDC Agent, Column Encryption, RLS Engine, DB Access Proxy."""
import pytest
import hashlib
from datetime import datetime, timezone, timedelta

from app.services.cdc_agent import (
    CDCAgent, CDCConnectorType, CDCStatus,
    CDCChangeEvent, CDCConnectorConfig, CDCTableMapping,
)
from app.services.column_encryption import (
    ColumnEncryption, EncryptionMode, EncryptedColumn,
)
from app.services.rls_engine import (
    RLSEngine, RLSPolicy, RLSContext, PolicyType, SensitivityLevel,
)
from app.services.db_access_proxy import (
    DBAccessProxy, ProxyAction, ColumnEncryptionRule,
)


# ── CDC Agent Tests ──────────────────────────────────────────────


def test_cdc_connector_status_enum():
    """CDCStatus covers expected states."""
    assert CDCStatus.PENDING.value == "pending"
    assert CDCStatus.RUNNING.value == "running"
    assert CDCStatus.PAUSED.value == "paused"
    assert CDCStatus.FAILED.value == "failed"
    assert CDCStatus.STOPPED.value == "stopped"


def test_cdc_connector_type_enum():
    """CDCConnectorType covers expected types."""
    assert CDCConnectorType.DEBEZIUM_POSTGRES.value == "debezium-postgres"
    assert CDCConnectorType.KAFKA_SINK_CLICKHOUSE.value == "kafka-sink-clickhouse"


def test_cdc_agent_generate_postgres_config():
    """CDCAgent generates valid Debezium PostgreSQL connector config."""
    agent = CDCAgent()
    config = agent.generate_postgres_connector_config(
        database_url="postgresql://cds:secret@localhost:5432/cds",
        tables=["public.data_products", "public.audit_logs"],
        connector_name="test-cdc",
    )
    assert config.connector_name == "test-cdc"
    assert config.database_hostname == "localhost"
    assert config.database_port == 5432
    assert config.database_user == "cds"
    assert config.database_dbname == "cds"
    assert len(config.table_include_list) == 2


def test_cdc_agent_connector_lifecycle():
    """CDCAgent manages connector lifecycle (start/pause/stop)."""
    agent = CDCAgent()
    agent.generate_postgres_connector_config(
        database_url="postgresql://cds:secret@localhost:5432/cds",
        tables=["public.data_products"],
        connector_name="lifecycle-test",
    )
    assert agent.get_connector_status("lifecycle-test") == CDCStatus.PENDING

    import asyncio
    asyncio.run(agent.start_connector("lifecycle-test"))
    assert agent.get_connector_status("lifecycle-test") == CDCStatus.RUNNING

    asyncio.run(agent.pause_connector("lifecycle-test"))
    assert agent.get_connector_status("lifecycle-test") == CDCStatus.PAUSED

    asyncio.run(agent.stop_connector("lifecycle-test"))
    assert agent.get_connector_status("lifecycle-test") == CDCStatus.STOPPED


def test_cdc_change_event_to_kafka_message():
    """CDCChangeEvent serializes to Kafka message format."""
    event = CDCChangeEvent(
        event_id="test-001",
        source_table="public.data_products",
        operation="u",
        timestamp=datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc),
        before={"name": "old-name"},
        after={"name": "new-name"},
    )
    msg = event.to_kafka_message()
    assert msg["event_id"] == "test-001"
    assert msg["operation"] == "u"
    assert msg["after"]["name"] == "new-name"


def test_cdc_table_mapping():
    """CDCTableMapping stores column mappings correctly."""
    agent = CDCAgent()
    mapping = agent.register_table_mapping(
        source_table="public.users",
        target_table="cds_audit.users",
        column_mappings={"id": "user_id", "name": "user_name"},
    )
    assert mapping.source_table == "public.users"
    assert mapping.target_table == "cds_audit.users"
    assert mapping.column_mappings["id"] == "user_id"


def test_cdc_metrics():
    """CDCAgent tracks metrics correctly."""
    agent = CDCAgent()
    agent.generate_postgres_connector_config(
        database_url="postgresql://cds:secret@localhost:5432/cds",
        tables=["public.data_products"],
        connector_name="metrics-test",
    )
    metrics = agent.get_metrics()
    assert metrics["total_connectors"] == 1
    assert metrics["running_connectors"] == 0
    assert metrics["total_events_produced"] == 0


# ── Column Encryption Tests ──────────────────────────────────────


def test_column_encryption_mode_enum():
    """EncryptionMode covers expected modes."""
    assert EncryptionMode.DETERMINISTIC.value == "sm4-siv"
    assert EncryptionMode.RANDOMIZED.value == "sm4-gcm"


def test_column_encryption_deterministic():
    """SM4-SIV deterministic encryption: same plaintext → same ciphertext."""
    dek = hashlib.sha256(b"test-key").digest()[:16]
    enc = ColumnEncryption(dek=dek, key_id="test")

    ct1 = enc.encrypt_deterministic("hello world", "email")
    ct2 = enc.encrypt_deterministic("hello world", "email")
    assert ct1.ciphertext == ct2.ciphertext  # Deterministic

    ct3 = enc.encrypt_deterministic("hello world", "name")
    assert ct1.ciphertext != ct3.ciphertext  # Different column → different IV


def test_column_encryption_randomized():
    """SM4-GCM randomized encryption: same plaintext → different ciphertext."""
    dek = hashlib.sha256(b"test-key").digest()[:16]
    enc = ColumnEncryption(dek=dek, key_id="test")

    ct1 = enc.encrypt_randomized("hello world", "secret")
    ct2 = enc.encrypt_randomized("hello world", "secret")
    assert ct1.ciphertext != ct2.ciphertext  # Random nonce each time


def test_column_encryption_hash_for_index():
    """ColumnEncryption.hash_for_index produces consistent hashes."""
    dek = hashlib.sha256(b"test-key").digest()[:16]
    enc = ColumnEncryption(dek=dek, key_id="test")

    h1 = enc.hash_for_index("test@example.com", "email")
    h2 = enc.hash_for_index("test@example.com", "email")
    assert h1 == h2

    h3 = enc.hash_for_index("other@example.com", "email")
    assert h1 != h3


def test_column_encryption_invalid_key():
    """ColumnEncryption rejects invalid key sizes."""
    with pytest.raises(ValueError, match="DEK must be 16 bytes"):
        ColumnEncryption(dek=b"short", key_id="test")


def test_column_encryption_encrypt_column_dispatch():
    """encrypt_column dispatches to correct mode."""
    dek = hashlib.sha256(b"test-key").digest()[:16]
    enc = ColumnEncryption(dek=dek, key_id="test")

    det = enc.encrypt_column("test", "col", EncryptionMode.DETERMINISTIC)
    assert det.mode == EncryptionMode.DETERMINISTIC

    rand = enc.encrypt_column("test", "col", EncryptionMode.RANDOMIZED)
    assert rand.mode == EncryptionMode.RANDOMIZED


# ── RLS Engine Tests ─────────────────────────────────────────────


def test_sensitivity_level_clearance_rank():
    """SensitivityLevel clearance ranks are ordered correctly."""
    assert SensitivityLevel.clearance_rank("public") < SensitivityLevel.clearance_rank("internal")
    assert SensitivityLevel.clearance_rank("internal") < SensitivityLevel.clearance_rank("confidential")
    assert SensitivityLevel.clearance_rank("confidential") < SensitivityLevel.clearance_rank("secret")


def test_rls_region_policy():
    """Region policy generates correct SQL conditions."""
    engine = RLSEngine()
    policy = RLSPolicy(
        policy_id="region-cn",
        name="China Region",
        policy_type=PolicyType.REGION,
        conditions={"allowed_regions": ["CN", "HK"], "region_column": "data_region"},
    )
    engine.add_policy(policy)

    # User in allowed region
    context = RLSContext(user_region="CN")
    where = engine.evaluate_sql(context)
    assert where is not None
    assert "data_region = 'CN'" in where

    # User in denied region
    context = RLSContext(user_region="US")
    where = engine.evaluate_sql(context)
    assert where == "FALSE"


def test_rls_time_policy():
    """Time policy generates correct SQL conditions."""
    engine = RLSEngine()
    policy = RLSPolicy(
        policy_id="time-embargo",
        name="24h Embargo",
        policy_type=PolicyType.TIME,
        conditions={"time_column": "published_at", "embargo_hours": 24},
    )
    engine.add_policy(policy)

    context = RLSContext()
    where = engine.evaluate_sql(context)
    assert where is not None
    assert "published_at <=" in where


def test_rls_sensitivity_policy():
    """Sensitivity policy filters by clearance level."""
    engine = RLSEngine()
    policy = RLSPolicy(
        policy_id="sensitivity-filter",
        name="Confidential Filter",
        policy_type=PolicyType.SENSITIVITY,
        conditions={"max_allowed_level": "confidential", "sensitivity_column": "security_level"},
    )
    engine.add_policy(policy)

    # User with secret clearance — no filtering
    context = RLSContext(user_clearance="secret")
    where = engine.evaluate_sql(context)
    assert where is None

    # User with public clearance — filtered
    context = RLSContext(user_clearance="public")
    where = engine.evaluate_sql(context)
    assert where is not None
    assert "security_level IN" in where


def test_rls_combined_policies():
    """Multiple policies are AND-combined."""
    engine = RLSEngine()
    engine.add_policy(RLSPolicy(
        policy_id="region",
        name="Region",
        policy_type=PolicyType.REGION,
        conditions={"allowed_regions": ["CN"], "region_column": "region"},
    ))
    engine.add_policy(RLSPolicy(
        policy_id="time",
        name="Time",
        policy_type=PolicyType.TIME,
        conditions={"time_column": "created_at", "max_age_hours": 720},
    ))

    context = RLSContext(user_region="CN")
    where = engine.evaluate_sql(context)
    assert where is not None
    assert "AND" in where


def test_rls_inject_where():
    """RLSEngine.inject_rls_where correctly injects into SQL."""
    engine = RLSEngine()

    # SELECT with existing WHERE
    sql = "SELECT * FROM users WHERE active = true"
    result = engine.inject_rls_where(sql, "region = 'CN'")
    assert "region = 'CN'" in result
    assert "active = true" in result

    # SELECT without WHERE
    sql = "SELECT * FROM users"
    result = engine.inject_rls_where(sql, "region = 'CN'")
    assert "WHERE" in result
    assert "region = 'CN'" in result

    # SELECT with GROUP BY
    sql = "SELECT count(*) FROM users GROUP BY region"
    result = engine.inject_rls_where(sql, "region = 'CN'")
    assert "WHERE" in result
    assert "GROUP BY" in result


def test_rls_validate_policy():
    """RLSEngine.validate_policy catches configuration errors."""
    engine = RLSEngine()

    # Valid region policy
    policy = RLSPolicy(
        policy_id="test",
        name="Test",
        policy_type=PolicyType.REGION,
        conditions={"allowed_regions": ["CN"]},
    )
    errors = engine.validate_policy(policy)
    assert len(errors) == 0

    # Invalid: missing required conditions
    bad_policy = RLSPolicy(
        policy_id="test",
        name="Test",
        policy_type=PolicyType.REGION,
        conditions={},
    )
    errors = engine.validate_policy(bad_policy)
    assert len(errors) > 0


# ── DB Access Proxy Tests ────────────────────────────────────────


def test_proxy_blocked_pattern():
    """DBAccessProxy blocks dangerous SQL patterns."""
    proxy = DBAccessProxy()
    proxy.add_blocked_pattern(r"DROP\s+TABLE")
    proxy.add_blocked_pattern(r"TRUNCATE")

    context = RLSContext()

    result = proxy.evaluate_sql("DROP TABLE users", context)
    assert result.action == ProxyAction.DENY

    result = proxy.evaluate_sql("TRUNCATE audit_logs", context)
    assert result.action == ProxyAction.DENY

    result = proxy.evaluate_sql("SELECT * FROM users", context)
    assert result.action != ProxyAction.DENY


def test_proxy_rls_injection():
    """DBAccessProxy injects RLS WHERE clauses."""
    proxy = DBAccessProxy()
    engine = RLSEngine()
    engine.add_policy(RLSPolicy(
        policy_id="region",
        name="Region",
        policy_type=PolicyType.REGION,
        conditions={"allowed_regions": ["CN"], "region_column": "region"},
    ))
    # Monkey-patch the rls_engine singleton
    import app.services.db_access_proxy as proxy_module
    original_engine = proxy_module.rls_engine
    proxy_module.rls_engine = engine

    try:
        context = RLSContext(user_region="CN")
        result = proxy.evaluate_sql("SELECT * FROM data_products", context)
        assert result.action == ProxyAction.REWRITE
        assert "region = 'CN'" in result.rewritten_sql
        assert "rls" in result.applied_policies
    finally:
        proxy_module.rls_engine = original_engine


def test_proxy_encryption_rules():
    """DBAccessProxy manages encryption rules."""
    proxy = DBAccessProxy()
    proxy.register_encryption_rule("users", "email", EncryptionMode.DETERMINISTIC)
    proxy.register_encryption_rule("users", "phone", EncryptionMode.RANDOMIZED)

    rules = proxy.get_encryption_rules()
    assert len(rules) == 2
    assert rules[0]["table"] == "users"
    assert rules[0]["mode"] == "sm4-siv"


def test_proxy_metrics():
    """DBAccessProxy tracks query metrics."""
    proxy = DBAccessProxy()
    context = RLSContext()

    proxy.evaluate_sql("SELECT 1", context)
    proxy.evaluate_sql("SELECT 2", context)

    metrics = proxy.get_metrics()
    assert metrics["total_queries"] == 2
    assert metrics["denied_queries"] == 0
