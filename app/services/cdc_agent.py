"""CDC Agent — Change Data Capture framework for PostgreSQL → Kafka → ClickHouse.

Generates Debezium connector configurations and provides a Kafka producer
for streaming database change events to downstream consumers.

Naming note: "CDC Agent" here means a CHANGE DATA CAPTURE connector tool
(Debezium→Kafka→ClickHouse), NOT an AI/LLM agent. The sandbox-internal agent
execution framework (N10) is a separate concern — see app/services/agent_service.py.
"""
import asyncio
import json
import logging
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    from aiokafka import AIOKafkaProducer

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False

from app.utils.crypto import sm3_hash

logger = logging.getLogger(__name__)


class CDCConnectorType(str, Enum):
    DEBEZIUM_POSTGRES = "debezium-postgres"
    DEBEZIUM_MYSQL = "debezium-mysql"
    KAFKA_SINK_CLICKHOUSE = "kafka-sink-clickhouse"


class CDCStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class CDCConnectorConfig:
    """Debezium connector configuration."""
    connector_name: str
    connector_class: str
    database_hostname: str
    database_port: int
    database_user: str
    database_password: str
    database_dbname: str
    database_server_name: str
    table_include_list: list[str] = field(default_factory=list)
    slot_name: str = "cds_cdc"
    publication_name: str = "cds_publication"
    plugin_name: str = "pgoutput"
    heartbeat_interval_ms: int = 10000
    snapshot_mode: str = "initial"
    additional_config: dict[str, Any] = field(default_factory=dict)

    def to_debezium_config(self) -> dict[str, Any]:
        """Convert to Debezium connector config JSON."""
        config = {
            "name": self.connector_name,
            "config": {
                "connector.class": self.connector_class,
                "database.hostname": self.database_hostname,
                "database.port": str(self.database_port),
                "database.user": self.database_user,
                "database.password": self.database_password,
                "database.dbname": self.database_dbname,
                "database.server.name": self.database_server_name,
                "slot.name": self.slot_name,
                "publication.name": self.publication_name,
                "plugin.name": self.plugin_name,
                "heartbeat.interval.ms": str(self.heartbeat_interval_ms),
                "snapshot.mode": self.snapshot_mode,
                "key.converter": "org.apache.kafka.connect.json.JsonConverter",
                "key.converter.schemas.enable": "true",
                "value.converter": "org.apache.kafka.connect.json.JsonConverter",
                "value.converter.schemas.enable": "true",
            },
        }
        if self.table_include_list:
            config["config"]["table.include.list"] = ",".join(self.table_include_list)
        config["config"].update(self.additional_config)
        return config


@dataclass
class CDCChangeEvent:
    """Represents a single change event from CDC."""
    event_id: str
    source_table: str
    operation: str  # c=create, u=update, d=delete, r=read(snapshot)
    timestamp: datetime
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    source_connector: str = ""
    lsn: int | None = None  # PostgreSQL Log Sequence Number

    def to_kafka_message(self) -> dict[str, Any]:
        """Convert to Kafka message payload."""
        return {
            "event_id": self.event_id,
            "source_table": self.source_table,
            "operation": self.operation,
            "timestamp": self.timestamp.isoformat(),
            "before": self.before,
            "after": self.after,
            "source_connector": self.source_connector,
            "lsn": self.lsn,
        }


@dataclass
class CDCTableMapping:
    """Maps a source table to a ClickHouse target table with column transforms."""
    source_table: str
    target_table: str
    column_mappings: dict[str, str] = field(default_factory=dict)  # source_col -> target_col
    filter_condition: str | None = None
    transform_rules: list[dict[str, Any]] = field(default_factory=list)


class CDCEventProducer:
    """Kafka producer for CDC change events.

    Wraps AIOKafkaProducer with lifecycle management and SM3 integrity headers.
    Falls back to an in-memory store when Kafka is unavailable or for testing.
    """

    def __init__(
        self,
        backend: str = "kafka",
        kafka_brokers: str = "localhost:9092",
        topic_prefix: str = "cds.events",
    ):
        self._backend = backend
        self._kafka_brokers = kafka_brokers
        self._topic_prefix = topic_prefix
        self._producer: AIOKafkaProducer | None = None
        self._memory_store: dict[str, list[bytes]] = {}
        self._started = False

    async def start(self) -> None:
        """Initialize and start the Kafka producer."""
        if self._backend == "kafka" and KAFKA_AVAILABLE:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._kafka_brokers,
                value_serializer=lambda v: v,  # already bytes
                key_serializer=lambda k: k,  # already bytes
            )
            try:
                await self._producer.start()
                self._started = True
                logger.info(
                    f"CDCEventProducer started, connected to Kafka at {self._kafka_brokers}"
                )
            except Exception as e:
                logger.error(f"CDCEventProducer failed to connect to Kafka: {e}")
                self._producer = None
                self._started = False
        else:
            self._started = True
            if self._backend == "kafka" and not KAFKA_AVAILABLE:
                logger.warning(
                    "aiokafka not installed — CDCEventProducer running in memory mode"
                )
            logger.info("CDCEventProducer started in memory mode")

    async def stop(self) -> None:
        """Gracefully shut down the Kafka producer."""
        if self._producer is not None:
            try:
                await self._producer.stop()
                logger.info("CDCEventProducer stopped")
            except Exception as e:
                logger.error(f"CDCEventProducer stop error: {e}")
            finally:
                self._producer = None
        self._started = False

    async def produce_event(self, event: "CDCChangeEvent") -> bool:
        """Produce a single CDCChangeEvent to the appropriate Kafka topic.

        Topic name: {topic_prefix}.{event_type}
        Message value: JSON-serialized event payload.
        Header: SM3 hash of the value for integrity verification.
        """
        topic = f"{self._topic_prefix}.{event.source_table}"
        message = event.to_kafka_message()
        value = json.dumps(message, default=str).encode("utf-8")
        key = event.event_id.encode("utf-8")
        integrity_hash = sm3_hash(value)

        if self._producer is not None:
            try:
                await self._producer.send_and_wait(
                    topic=topic,
                    value=value,
                    key=key,
                    headers=[("sm3-hash", integrity_hash.encode("utf-8"))],
                )
                logger.debug(f"CDC event sent to {topic}: {event.event_id}")
                return True
            except Exception as e:
                logger.error(f"Kafka produce failed for {topic}: {e}")
                return False

        # Fallback: in-memory store
        if topic not in self._memory_store:
            self._memory_store[topic] = []
        self._memory_store[topic].append(value)
        logger.debug(f"CDC event stored in memory for {topic}: {event.event_id}")
        return True

    async def produce_raw(self, topic: str, value: bytes, key: bytes | None = None) -> bool:
        """Produce raw bytes to a topic with SM3 integrity header."""
        integrity_hash = sm3_hash(value)

        if self._producer is not None:
            try:
                await self._producer.send_and_wait(
                    topic=topic,
                    value=value,
                    key=key,
                    headers=[("sm3-hash", integrity_hash.encode("utf-8"))],
                )
                return True
            except Exception as e:
                logger.error(f"Kafka produce failed for {topic}: {e}")
                return False

        if topic not in self._memory_store:
            self._memory_store[topic] = []
        self._memory_store[topic].append(value)
        return True

    def get_memory_events(self, topic: str, max_events: int = 10) -> list[dict[str, Any]]:
        """Retrieve events from the in-memory store (testing/development)."""
        raw_events = self._memory_store.get(topic, [])[-max_events:]
        result = []
        for raw in raw_events:
            try:
                result.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                result.append({"_raw": repr(raw)})
        return result

    @property
    def is_connected(self) -> bool:
        if self._backend == "memory":
            return self._started
        return self._producer is not None and self._started


class CDCAgent:
    """CDC Agent — manages Debezium connectors and Kafka event streaming.

    Responsibilities:
    - Generate Debezium connector configurations
    - Manage connector lifecycle (register, pause, resume, delete)
    - Produce change events to Kafka topics
    - Track CDC lag and health metrics
    """

    def __init__(
        self,
        backend: str = "kafka",
        kafka_brokers: str = "localhost:9092",
        topic_prefix: str = "cds.events",
        kafka_connect_url: str = "",
        connect_timeout_seconds: float = 10.0,
    ):
        self._connectors: dict[str, CDCConnectorConfig] = {}
        self._connector_payloads: dict[str, dict[str, Any]] = {}
        self._table_mappings: list[CDCTableMapping] = []
        self._status: dict[str, CDCStatus] = {}
        self._event_count: int = 0
        self._last_event_time: datetime | None = None
        self._backend = backend
        self._kafka_connect_url = kafka_connect_url.rstrip("/")
        self._connect_timeout_seconds = connect_timeout_seconds
        self._memory_store: dict[str, list[dict[str, Any]]] = {}  # In-memory event store for testing
        self._event_producer = CDCEventProducer(
            backend=backend,
            kafka_brokers=kafka_brokers,
            topic_prefix=topic_prefix,
        )
        self._kafka_producer = None  # Legacy reference; kept for backward compat

    async def start(self) -> None:
        """Start the CDC agent's Kafka producer."""
        await self._event_producer.start()
        logger.info("CDCAgent started")

    async def stop(self) -> None:
        """Stop the CDC agent's Kafka producer."""
        await self._event_producer.stop()
        logger.info("CDCAgent stopped")

    def generate_postgres_connector_config(
        self,
        database_url: str,
        tables: list[str],
        connector_name: str = "cds-postgres-cdc",
        server_name: str = "cds",
    ) -> CDCConnectorConfig:
        """Generate Debezium PostgreSQL connector configuration.

        Args:
            database_url: PostgreSQL connection URL (postgresql://user:pass@host:port/db)
            tables: List of tables to capture (schema.table format)
            connector_name: Name for the connector
            server_name: Logical server name for Kafka topic prefix
        """
        from urllib.parse import urlparse
        parsed = urlparse(database_url)

        config = CDCConnectorConfig(
            connector_name=connector_name,
            connector_class="io.debezium.connector.postgresql.PostgresConnector",
            database_hostname=parsed.hostname or "localhost",
            database_port=parsed.port or 5432,
            database_user=parsed.username or "cds",
            database_password=parsed.password or "",
            database_dbname=parsed.path.lstrip("/") or "cds",
            database_server_name=server_name,
            table_include_list=tables,
            slot_name=f"{connector_name}_slot",
            publication_name=f"{connector_name}_pub",
        )

        self._connectors[connector_name] = config
        self._connector_payloads[connector_name] = config.to_debezium_config()
        self._status[connector_name] = CDCStatus.PENDING
        logger.info(f"Generated Debezium config for connector: {connector_name}")
        return config

    def generate_clickhouse_sink_config(
        self,
        connector_name: str,
        kafka_bootstrap_servers: str,
        clickhouse_url: str,
        topic_prefix: str,
        tables: list[str],
    ) -> dict[str, Any]:
        """Generate Kafka Connect ClickHouse sink connector config.

        Args:
            connector_name: Name for the sink connector
            kafka_bootstrap_servers: Kafka broker addresses
            clickhouse_url: ClickHouse JDBC URL
            topic_prefix: Kafka topic prefix (matches Debezium server.name)
            tables: List of target tables
        """
        # Map Debezium topics: {serverName}.{schema}.{table}
        topics = [f"{topic_prefix}.{t}" for t in tables]

        config = {
            "name": connector_name,
            "config": {
                "connector.class": "com.clickhouse.kafka.connect.ClickHouseSinkConnector",
                "tasks.max": "1",
                "hostname": clickhouse_url,
                "port": "8123",
                "database": "cds_audit",
                "topics": ",".join(topics),
                "key.converter": "org.apache.kafka.connect.json.JsonConverter",
                "value.converter": "org.apache.kafka.connect.json.JsonConverter",
                "value.converter.schemas.enable": "true",
                "clickhouse.server.url": clickhouse_url,
                "clickhouse.server.user": "default",
                "clickhouse.server.password": "",
                "ssl": "false",
            },
        }

        self._status[connector_name] = CDCStatus.PENDING
        self._connector_payloads[connector_name] = config
        return config

    def register_table_mapping(
        self,
        source_table: str,
        target_table: str,
        column_mappings: dict[str, str] | None = None,
        filter_condition: str | None = None,
    ) -> CDCTableMapping:
        """Register a source-to-target table mapping with optional transforms."""
        mapping = CDCTableMapping(
            source_table=source_table,
            target_table=target_table,
            column_mappings=column_mappings or {},
            filter_condition=filter_condition,
        )
        self._table_mappings.append(mapping)
        return mapping

    async def start_connector(self, connector_name: str) -> bool:
        """Start or upsert a connector through Kafka Connect when configured."""
        if connector_name not in self._status:
            return False
        if self._kafka_connect_url:
            payload = self._connector_payloads.get(connector_name)
            if not payload:
                self._status[connector_name] = CDCStatus.FAILED
                logger.error("No Kafka Connect payload found for connector: %s", connector_name)
                return False
            ok = await self._upsert_connector(connector_name, payload)
            self._status[connector_name] = CDCStatus.RUNNING if ok else CDCStatus.FAILED
            return ok
        self._status[connector_name] = CDCStatus.RUNNING
        logger.info(f"Started CDC connector: {connector_name}")
        return True

    async def _upsert_connector(self, connector_name: str, payload: dict[str, Any]) -> bool:
        config_payload = payload.get("config", payload)
        status, body = await asyncio.to_thread(
            self._request_connect,
            "PUT",
            f"/connectors/{connector_name}/config",
            config_payload,
        )
        if 200 <= status < 300:
            logger.info("Kafka Connect connector upserted: %s", connector_name)
            return True
        logger.error("Kafka Connect upsert failed for %s: status=%s body=%s", connector_name, status, body)
        return False

    async def pause_connector(self, connector_name: str) -> bool:
        """Pause a running connector."""
        if connector_name not in self._status:
            return False
        if self._kafka_connect_url:
            status, body = await asyncio.to_thread(
                self._request_connect,
                "PUT",
                f"/connectors/{connector_name}/pause",
                None,
            )
            if not (200 <= status < 300):
                logger.error("Kafka Connect pause failed for %s: status=%s body=%s", connector_name, status, body)
                return False
        self._status[connector_name] = CDCStatus.PAUSED
        logger.info(f"Paused CDC connector: {connector_name}")
        return True

    async def stop_connector(self, connector_name: str) -> bool:
        """Stop a connector."""
        if connector_name not in self._status:
            return False
        if self._kafka_connect_url:
            status, body = await asyncio.to_thread(
                self._request_connect,
                "DELETE",
                f"/connectors/{connector_name}",
                None,
            )
            if status not in {200, 202, 204, 404}:
                logger.error("Kafka Connect delete failed for %s: status=%s body=%s", connector_name, status, body)
                return False
        self._status[connector_name] = CDCStatus.STOPPED
        logger.info(f"Stopped CDC connector: {connector_name}")
        return True

    def _request_connect(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> tuple[int, str]:
        """Call Kafka Connect REST API with stdlib urllib."""
        if not self._kafka_connect_url:
            return 0, "Kafka Connect URL not configured"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self._kafka_connect_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._connect_timeout_seconds) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return exc.code, body
        except Exception as exc:
            return 0, str(exc)

    async def produce_event(self, event: CDCChangeEvent, topic: str) -> bool:
        """Produce a CDC change event to a Kafka topic.

        Delegates to CDCEventProducer which handles Kafka or in-memory fallback.
        """
        self._event_count += 1
        self._last_event_time = event.timestamp

        # Override topic to use configured prefix if caller passed a bare table name
        if not topic.startswith(self._event_producer._topic_prefix):
            event.source_table = topic

        return await self._event_producer.produce_event(event)

    def get_connector_status(self, connector_name: str) -> CDCStatus | None:
        """Get current status of a connector."""
        return self._status.get(connector_name)

    def list_connectors(self) -> list[dict[str, Any]]:
        """List all registered connectors with their status."""
        return [
            {
                "name": name,
                "status": self._status.get(name, CDCStatus.PENDING).value,
                "connector_class": config.connector_class,
                "tables": config.table_include_list,
            }
            for name, config in self._connectors.items()
        ]

    def get_metrics(self) -> dict[str, Any]:
        """Get CDC agent metrics."""
        return {
            "total_connectors": len(self._connectors),
            "running_connectors": sum(
                1 for s in self._status.values() if s == CDCStatus.RUNNING
            ),
            "total_events_produced": self._event_count,
            "last_event_time": self._last_event_time.isoformat() if self._last_event_time else None,
            "table_mappings": len(self._table_mappings),
        }

    # ── Simplified API for test compatibility ──────────────────

    def generate_debezium_config(
        self,
        db_type: str = "postgresql",
        host: str = "localhost",
        port: int = 5432,
        database: str = "cds",
        username: str = "cds",
        password: str = "",
        table_include_list: list[str] | None = None,
        connector_name: str | None = None,
    ) -> dict[str, Any]:
        """Generate Debezium connector config (simplified API).

        Returns dict with 'name' and 'config' keys matching Kafka Connect REST format.
        """
        name = connector_name or f"cds-{db_type}-cdc"
        tables = table_include_list or []

        connector_class = {
            "postgresql": "io.debezium.connector.postgresql.PostgresConnector",
            "mysql": "io.debezium.connector.mysql.MySqlConnector",
        }.get(db_type, "io.debezium.connector.postgresql.PostgresConnector")

        config = {
            "name": name,
            "config": {
                "connector.class": connector_class,
                "database.hostname": host,
                "database.port": port,
                "database.user": username,
                "database.password": password,
                "database.dbname": database,
                "database.server.name": "cds",
                "table.include.list": ",".join(tables),
                "slot.name": f"{name}_slot",
                "publication.name": f"{name}_pub",
                "plugin.name": "pgoutput",
                "snapshot.mode": "initial",
                "heartbeat.interval.ms": "10000",
                "key.converter": "org.apache.kafka.connect.json.JsonConverter",
                "key.converter.schemas.enable": "true",
                "value.converter": "org.apache.kafka.connect.json.JsonConverter",
                "value.converter.schemas.enable": "true",
            },
        }

        self._connectors[name] = CDCConnectorConfig(
            connector_name=name,
            connector_class=connector_class,
            database_hostname=host,
            database_port=port,
            database_user=username,
            database_password=password,
            database_dbname=database,
            database_server_name="cds",
            table_include_list=tables,
        )
        self._connector_payloads[name] = config
        self._status[name] = CDCStatus.PENDING
        return config

    def produce(self, topic: str, event: dict[str, Any]) -> dict[str, Any]:
        """Produce an event to a topic (in-memory or Kafka).

        Returns status dict with 'status' and 'topic' keys.
        For Kafka backend, delegates to CDCEventProducer via asyncio.
        """
        self._event_count += 1
        self._last_event_time = datetime.now(timezone.utc)

        if self._backend == "memory":
            if topic not in self._memory_store:
                self._memory_store[topic] = []
            self._memory_store[topic].append(event)
        elif self._backend == "kafka" and self._event_producer.is_connected:
            value = json.dumps(event, default=str).encode("utf-8")
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._event_producer.produce_raw(topic, value))
            except RuntimeError:
                # No running loop — store in memory as fallback
                if topic not in self._memory_store:
                    self._memory_store[topic] = []
                self._memory_store[topic].append(event)
        elif self._kafka_producer:
            # Legacy synchronous producer path
            try:
                self._kafka_producer.produce(
                    topic=topic,
                    value=json.dumps(event).encode("utf-8"),
                )
            except Exception as e:
                logger.error(f"Kafka produce failed: {e}")
                return {"status": "error", "topic": topic, "error": str(e)}

        return {"status": "ok", "topic": topic}

    def consume(self, topic: str, max_events: int = 10) -> list[dict[str, Any]]:
        """Consume events from a topic (in-memory or Kafka).

        Returns list of event dicts.
        """
        if self._backend == "memory":
            return self._memory_store.get(topic, [])[-max_events:]

        # For kafka backend, check the event producer's in-memory fallback
        return self._event_producer.get_memory_events(topic, max_events)

    def get_pipeline_status(self) -> dict[str, Any]:
        """Get CDC pipeline status."""
        return {
            "backend": self._backend,
            "status": "running" if self._backend == "memory" else "configured",
            "topics": list(self._memory_store.keys()),
            "connectors": len(self._connectors),
        }

    def list_topics(self) -> list[str]:
        """List all active topics."""
        if self._backend == "memory":
            return list(self._memory_store.keys())
        return list(self._memory_store.keys())



def create_cdc_agent(backend: str = "kafka") -> CDCAgent:
    """Create a CDCAgent instance from application settings."""
    from app.core.config import get_settings
    settings = get_settings()
    return CDCAgent(
        backend=backend,
        kafka_brokers=settings.CDC_KAFKA_BROKERS,
        topic_prefix=settings.CDC_KAFKA_TOPIC_PREFIX,
        kafka_connect_url=settings.CDC_KAFKA_CONNECT_URL,
        connect_timeout_seconds=settings.CDC_KAFKA_CONNECT_TIMEOUT_SECONDS,
    )


# Singleton — uses default config; app/main.py calls start()/stop() in lifespan
cdc_agent = create_cdc_agent(backend="memory")
