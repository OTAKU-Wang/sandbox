"""Tests for CDC Agent framework.

SS-09#1: Debezium configuration generation + Kafka producer stub.
Ensures CDC pipeline configuration is correct and data change events are produced.
"""
import pytest
import json


# ============================================================
# Debezium Configuration
# ============================================================
class TestDebeziumConfig:
    """CDC agent generates correct Debezium connector configurations."""

    def test_generate_postgres_config(self):
        """Generate Debezium config for PostgreSQL source."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent()
        config = agent.generate_debezium_config(
            db_type="postgresql",
            host="postgres",
            port=5432,
            database="cds",
            username="cdc_user",
            password="secret",
            table_include_list=["public.audit_logs", "public.contracts"],
        )
        assert config["name"] is not None
        assert "postgresql" in config["config"].get("connector.class", "").lower() or \
               "postgres" in json.dumps(config).lower()
        assert config["config"]["database.hostname"] == "postgres"
        assert config["config"]["database.port"] == 5432
        assert "public.audit_logs" in config["config"]["table.include.list"]

    def test_config_has_kafka_settings(self):
        """Config includes Kafka bootstrap servers."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent()
        config = agent.generate_debezium_config(
            db_type="postgresql",
            host="postgres",
            port=5432,
            database="cds",
            username="cdc_user",
            password="secret",
            table_include_list=["public.audit_logs"],
        )
        assert "kafka" in json.dumps(config).lower() or "bootstrap" in json.dumps(config).lower()

    def test_config_enables_snapshot(self):
        """Config includes initial snapshot mode."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent()
        config = agent.generate_debezium_config(
            db_type="postgresql",
            host="postgres",
            port=5432,
            database="cds",
            username="cdc_user",
            password="secret",
            table_include_list=["public.audit_logs"],
        )
        snapshot = config["config"].get("snapshot.mode", "")
        assert snapshot in ("initial", "always", "never", "initial_only")


# ============================================================
# Kafka Producer Stub
# ============================================================
class TestKafkaProducer:
    """CDC agent produces change events to Kafka topics."""

    def test_produce_change_event(self):
        """Agent produces a change event to the configured topic."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent(backend="memory")  # in-memory stub for testing
        event = {
            "op": "c",  # create
            "source": {"table": "audit_logs", "db": "cds"},
            "after": {"id": "123", "action": "login", "user_id": "u1"},
        }
        result = agent.produce("cds.public.audit_logs", event)
        assert result["status"] == "ok"
        assert result["topic"] == "cds.public.audit_logs"

    def test_consume_change_event(self):
        """Agent can consume events from a topic."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent(backend="memory")
        event = {"op": "u", "source": {"table": "contracts"}, "after": {"status": "active"}}
        agent.produce("cds.public.contracts", event)
        events = agent.consume("cds.public.contracts", max_events=10)
        assert len(events) >= 1
        assert events[-1]["op"] == "u"

    def test_produce_multiple_events(self):
        """Agent handles multiple events in sequence."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent(backend="memory")
        for i in range(5):
            agent.produce("cds.public.audit_logs", {"op": "c", "after": {"id": str(i)}})
        events = agent.consume("cds.public.audit_logs", max_events=10)
        assert len(events) == 5


# ============================================================
# CDC Pipeline Integration
# ============================================================
class TestCDCPipeline:
    """Full CDC pipeline: source → Debezium → Kafka → sink."""

    def test_pipeline_status(self):
        """Pipeline reports correct status."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent(backend="memory")
        status = agent.get_pipeline_status()
        assert status["backend"] == "memory"
        assert "topics" in status or "status" in status

    def test_pipeline_topic_list(self):
        """Pipeline lists active topics."""
        from app.services.cdc_agent import CDCAgent
        agent = CDCAgent(backend="memory")
        agent.produce("topic-a", {"op": "c"})
        agent.produce("topic-b", {"op": "c"})
        topics = agent.list_topics()
        assert "topic-a" in topics
        assert "topic-b" in topics
