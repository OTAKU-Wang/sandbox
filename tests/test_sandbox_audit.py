"""Sandbox audit logging tests — in-sandbox event collection and flush."""
import json
import logging
import os
import pytest
from pathlib import Path

from app.services.sandbox_audit import (
    SandboxActivityEvent,
    SandboxAuditLogger,
    SandboxAuditCollector,
    CDSAuditSDK,
    assess_http_risk,
    create_audit_http_server,
    CDSAuditLogHandler,
    ActivityCategory,
    assess_query_risk,
    assess_network_risk,
    SHELL_AUDIT_HELPER,
)


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / "audit.jsonl")


@pytest.fixture
def collector():
    return SandboxAuditCollector()


# === SandboxAuditLogger ===

def test_logger_writes_data_access(log_path):
    logger = SandboxAuditLogger(log_path=log_path, session_id="s1", user_id="u1")
    logger.log_data_access("query", {"sql": "SELECT * FROM t"}, rows_affected=10)
    logger.close()

    with open(log_path) as f:
        lines = f.readlines()
    assert len(lines) == 1
    event = SandboxActivityEvent.from_json(lines[0])
    assert event.category == "data_access"
    assert event.event_type == "query"
    assert event.rows_affected == 10
    assert event.session_id == "s1"


def test_logger_writes_network(log_path):
    logger = SandboxAuditLogger(log_path=log_path, session_id="s1")
    logger.log_network("connection", {"host": "example.com", "port": 443})
    logger.close()

    with open(log_path) as f:
        event = SandboxActivityEvent.from_json(f.readline())
    assert event.category == "network"
    assert event.event_type == "connection"


def test_logger_writes_application(log_path):
    logger = SandboxAuditLogger(log_path=log_path, session_id="s1")
    logger.log_application("code_execution", {"language": "python"}, duration_ms=150)
    logger.close()

    with open(log_path) as f:
        event = SandboxActivityEvent.from_json(f.readline())
    assert event.category == "application"
    assert event.duration_ms == 150


def test_logger_multiple_events(log_path):
    logger = SandboxAuditLogger(log_path=log_path, session_id="s1")
    logger.log_data_access("query", {"sql": "SELECT 1"})
    logger.log_network("dns_lookup", {"domain": "example.com"})
    logger.log_application("process_start", {"cmd": "python3"})
    logger.close()

    with open(log_path) as f:
        lines = f.readlines()
    assert len(lines) == 3


def test_logger_no_path_no_crash():
    """Logger with no log path should not crash."""
    logger = SandboxAuditLogger(log_path="", session_id="s1")
    logger.log_data_access("query", {"sql": "SELECT 1"})
    logger.close()


def test_logger_env_var_init(log_path, monkeypatch):
    """Logger reads from CDS_AUDIT_LOG env var."""
    monkeypatch.setenv("CDS_AUDIT_LOG", log_path)
    monkeypatch.setenv("CDS_SESSION_ID", "env-session")
    logger = SandboxAuditLogger()
    logger.log_data_access("query", {"sql": "test"})
    logger.close()

    with open(log_path) as f:
        event = SandboxActivityEvent.from_json(f.readline())
    assert event.session_id == "env-session"


# === SandboxActivityEvent ===

def test_event_json_roundtrip():
    event = SandboxActivityEvent(
        session_id="s1",
        category="data_access",
        event_type="query",
        detail={"sql": "SELECT 1"},
        rows_affected=5,
        risk_level="low",
    )
    json_str = event.to_json()
    restored = SandboxActivityEvent.from_json(json_str)
    assert restored.session_id == "s1"
    assert restored.rows_affected == 5
    assert restored.detail == {"sql": "SELECT 1"}


# === SandboxAuditCollector ===

def test_collector_reads_events(log_path):
    logger = SandboxAuditLogger(log_path=log_path, session_id="s1")
    logger.log_data_access("query", {"sql": "SELECT 1"})
    logger.log_network("connection", {"host": "example.com"})
    logger.close()

    collector = SandboxAuditCollector()
    events = collector.collect_from_file(log_path)
    assert len(events) == 2
    assert events[0].category == "data_access"
    assert events[1].category == "network"


def test_collector_handles_missing_file():
    collector = SandboxAuditCollector()
    events = collector.collect_from_file("/nonexistent/path/audit.jsonl")
    assert events == []


def test_collector_handles_malformed_lines(log_path):
    with open(log_path, "w") as f:
        f.write("not valid json\n")
        f.write('{"event_id":"test","timestamp":"2026-01-01","session_id":"s1","category":"data_access","event_type":"query"}\n')
        f.write("\n")  # empty line

    collector = SandboxAuditCollector()
    events = collector.collect_from_file(log_path)
    assert len(events) == 1  # Only valid line


def test_collect_and_flush_no_clickhouse(log_path, collector):
    """collect_and_flush works even when ClickHouse is unavailable."""
    logger = SandboxAuditLogger(log_path=log_path, session_id="s1")
    logger.log_data_access("query", {"sql": "SELECT 1"})
    logger.close()

    # ClickHouse not configured — should return 0 but not crash
    count = collector.collect_and_flush(log_path)
    # count is 0 because ClickHouse is unavailable in test env
    assert count >= 0


# === Risk Assessment ===

def test_assess_query_risk():
    assert assess_query_risk("SELECT * FROM t") == "low"
    assert assess_query_risk("SELECT * FROM t JOIN t2 ON t.id=t2.id") == "medium"
    assert assess_query_risk("INSERT INTO t VALUES (1)") == "high"
    assert assess_query_risk("DROP TABLE t") == "critical"
    assert assess_query_risk("DELETE FROM t WHERE id=1") == "critical"
    assert assess_query_risk("UPDATE t SET x=1") == "high"


def test_assess_network_risk():
    assert assess_network_risk("example.com", 443) == "low"
    assert assess_network_risk("example.com", 80) == "medium"
    assert assess_network_risk("example.com", 53) == "low"
    assert assess_network_risk("example.com", 8080) == "high"
    assert assess_network_risk("example.com", 22) == "high"


# === Shell Audit Helper ===

def test_shell_audit_helper_contains_log_function():
    assert "_log()" in SHELL_AUDIT_HELPER
    assert "CDS_AUDIT_LOG" in SHELL_AUDIT_HELPER
    assert "CDS_SESSION_ID" in SHELL_AUDIT_HELPER


# === Application Audit SDK ===

class TestCDSAuditSDK:
    """Test the application-level audit SDK."""

    def test_sdk_http_request(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1", app_name="my-api")
        sdk.log_http_request("GET", "/api/users", 200, duration_ms=45, client_ip="10.0.0.1")
        sdk.close()

        event = json.loads(log_file.read_text().strip())
        assert event["category"] == "application"
        assert event["event_type"] == "http_request"
        assert event["detail"]["method"] == "GET"
        assert event["detail"]["path"] == "/api/users"
        assert event["detail"]["status_code"] == 200
        assert event["detail"]["app"] == "my-api"
        assert event["duration_ms"] == 45
        assert event["risk_level"] == "low"

    def test_sdk_http_request_write_op_high_risk(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1", app_name="my-api")
        sdk.log_http_request("POST", "/api/users", 201, duration_ms=100)
        sdk.close()

        event = json.loads(log_file.read_text().strip())
        assert event["risk_level"] == "high"

    def test_sdk_http_request_admin_critical(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1")
        sdk.log_http_request("DELETE", "/admin/users/1", 200)
        sdk.close()

        event = json.loads(log_file.read_text().strip())
        assert event["risk_level"] == "critical"

    def test_sdk_api_access(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1", app_name="data-service")
        sdk.log_api_access("/api/v1/query", "POST", params={"table": "users"}, auth_type="jwt")
        sdk.close()

        event = json.loads(log_file.read_text().strip())
        assert event["event_type"] == "api_access"
        assert event["detail"]["endpoint"] == "/api/v1/query"
        assert event["detail"]["auth_type"] == "jwt"

    def test_sdk_data_query(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1", app_name="analytics")
        sdk.log_data_query("SELECT * FROM orders JOIN items ON orders.id=items.order_id",
                          rows=500, duration_ms=120)
        sdk.close()

        event = json.loads(log_file.read_text().strip())
        assert event["category"] == "data_access"
        assert event["event_type"] == "data_query"
        assert event["rows_affected"] == 500
        assert event["risk_level"] == "medium"  # JOIN query

    def test_sdk_app_lifecycle(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1", app_name="my-flask-app")
        sdk.log_app_start("my-flask-app", port=5000, framework="flask")
        sdk.log_app_stop("my-flask-app", reason="shutdown")
        sdk.close()

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        start = json.loads(lines[0])
        stop = json.loads(lines[1])
        assert start["event_type"] == "app_start"
        assert start["detail"]["port"] == 5000
        assert stop["event_type"] == "app_stop"

    def test_sdk_app_log(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1")
        sdk.log_app_log("ERROR", "Database connection failed", logger_name="db")
        sdk.close()

        event = json.loads(log_file.read_text().strip())
        assert event["event_type"] == "app_log"
        assert event["detail"]["level"] == "ERROR"
        assert event["risk_level"] == "high"

    def test_sdk_app_log_info_low_risk(self, tmp_path):
        log_file = tmp_path / "sdk_audit.jsonl"
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path=str(log_file), session_id="s1")
        sdk.log_app_log("INFO", "Server started")
        sdk.close()

        event = json.loads(log_file.read_text().strip())
        assert event["risk_level"] == "low"

    def test_sdk_no_path_no_crash(self):
        from app.services.sandbox_audit import CDSAuditSDK

        sdk = CDSAuditSDK(log_path="", session_id="s1")
        sdk.log_http_request("GET", "/test", 200)
        sdk.log_data_query("SELECT 1")
        sdk.close()


class TestHTTPRiskAssessment:
    """Test HTTP request risk assessment."""

    def test_get_low_risk(self):
        from app.services.sandbox_audit import assess_http_risk
        assert assess_http_risk("GET", "/api/users", 200) == "low"

    def test_post_high_risk(self):
        from app.services.sandbox_audit import assess_http_risk
        assert assess_http_risk("POST", "/api/users", 201) == "high"
        assert assess_http_risk("DELETE", "/api/users/1", 200) == "high"
        assert assess_http_risk("PUT", "/api/users/1", 200) == "high"

    def test_admin_endpoint_critical(self):
        from app.services.sandbox_audit import assess_http_risk
        assert assess_http_risk("DELETE", "/admin/users/1", 200) == "critical"
        assert assess_http_risk("POST", "/api/drop-table", 200) == "critical"

    def test_auth_endpoint_medium(self):
        from app.services.sandbox_audit import assess_http_risk
        assert assess_http_risk("GET", "/auth/login", 200) == "medium"
        assert assess_http_risk("GET", "/api/token", 200) == "medium"

    def test_server_error_high(self):
        from app.services.sandbox_audit import assess_http_risk
        assert assess_http_risk("GET", "/api/data", 500) == "high"
        assert assess_http_risk("GET", "/api/data", 503) == "high"

    def test_client_error_not_high(self):
        from app.services.sandbox_audit import assess_http_risk
        assert assess_http_risk("GET", "/api/data", 404) == "low"
        assert assess_http_risk("GET", "/api/data", 400) == "low"


class TestAuditHTTPServer:
    """Test the HTTP audit endpoint for non-Python apps."""

    def test_server_creation(self):
        from app.services.sandbox_audit import create_audit_http_server
        server = create_audit_http_server(host="127.0.0.1", port=0)
        assert server is not None
        server.server_close()


class TestCDSAuditLogHandler:
    """Test the Python logging handler."""

    def test_handler_creation(self):
        from app.services.sandbox_audit import CDSAuditLogHandler
        handler = CDSAuditLogHandler(app_name="test-app")
        assert handler.level == logging.WARNING
