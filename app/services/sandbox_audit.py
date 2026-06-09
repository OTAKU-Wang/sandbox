"""Sandbox Activity Audit — captures in-sandbox events for compliance audit.

Architecture:
  Sandbox (bwrap) → tmpfs JSON log → Collector → ClickHouse sandbox_activity_events

Three event categories:
1. Data Access: DuckDB queries, file reads/writes, table operations
2. Network Access: outbound connections, DNS lookups, bandwidth
3. Application Activity: process starts, tool calls, code execution

Log injection:
  - Python: CDS_AUDIT_LOG env var points to shared tmpfs log file
  - Shell: CDS_AUDIT_LOG + helper script for shell command logging
  - The sandbox process writes JSON lines to the log file
  - Collector reads on session end or periodically for long sessions
"""
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ActivityCategory(str, Enum):
    DATA_ACCESS = "data_access"
    NETWORK = "network"
    APPLICATION = "application"


class DataAccessType(str, Enum):
    QUERY = "query"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    TABLE_CREATE = "table_create"
    TABLE_DROP = "table_drop"
    IMPORT = "import"
    EXPORT = "export"


class NetworkAccessType(str, Enum):
    CONNECTION = "connection"
    DNS_LOOKUP = "dns_lookup"
    BANDWIDTH = "bandwidth"


class ApplicationActivityType(str, Enum):
    PROCESS_START = "process_start"
    PROCESS_END = "process_end"
    TOOL_CALL = "tool_call"
    CODE_EXECUTION = "code_execution"
    ERROR = "error"
    # Application-level (REST API / service)
    HTTP_REQUEST = "http_request"
    API_ACCESS = "api_access"
    DATA_QUERY = "data_query"
    APP_LOG = "app_log"
    APP_START = "app_start"
    APP_STOP = "app_stop"


@dataclass
class SandboxActivityEvent:
    """A single sandbox activity event."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str = ""
    user_id: str = ""
    sandbox_level: str = ""
    category: str = ""  # ActivityCategory value
    event_type: str = ""  # specific type within category
    detail: dict = field(default_factory=dict)
    # Metrics
    duration_ms: int = 0
    bytes_read: int = 0
    bytes_written: int = 0
    rows_affected: int = 0
    # Risk assessment
    risk_level: str = "low"  # low, medium, high, critical
    blocked: bool = False

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "SandboxActivityEvent":
        data = json.loads(line)
        return cls(**data)


# === In-Sandbox Logger (injected into bwrap) ===

class SandboxAuditLogger:
    """Lightweight logger for use inside the sandbox process.

    Writes JSON events to a shared tmpfs log file.
    Initialized via CDS_AUDIT_LOG env var.
    """

    def __init__(self, log_path: str | None = None, session_id: str = "",
                 user_id: str = "", sandbox_level: str = ""):
        self._log_path = log_path or os.environ.get("CDS_AUDIT_LOG", "")
        self._session_id = session_id or os.environ.get("CDS_SESSION_ID", "")
        self._user_id = user_id or os.environ.get("CDS_USER_ID", "")
        self._sandbox_level = sandbox_level or os.environ.get("CDS_SANDBOX_LEVEL", "")
        self._fh = None
        if self._log_path:
            try:
                self._fh = open(self._log_path, "a", encoding="utf-8")
            except OSError:
                self._fh = None

    def _write(self, event: SandboxActivityEvent):
        if self._fh:
            self._fh.write(event.to_json() + "\n")
            self._fh.flush()

    def log_data_access(self, access_type: str, detail: dict,
                        rows_affected: int = 0, bytes_read: int = 0,
                        bytes_written: int = 0, risk_level: str = "low"):
        event = SandboxActivityEvent(
            session_id=self._session_id,
            user_id=self._user_id,
            sandbox_level=self._sandbox_level,
            category=ActivityCategory.DATA_ACCESS.value,
            event_type=access_type,
            detail=detail,
            rows_affected=rows_affected,
            bytes_read=bytes_read,
            bytes_written=bytes_written,
            risk_level=risk_level,
        )
        self._write(event)

    def log_network(self, access_type: str, detail: dict,
                    risk_level: str = "low", blocked: bool = False):
        event = SandboxActivityEvent(
            session_id=self._session_id,
            user_id=self._user_id,
            sandbox_level=self._sandbox_level,
            category=ActivityCategory.NETWORK.value,
            event_type=access_type,
            detail=detail,
            risk_level=risk_level,
            blocked=blocked,
        )
        self._write(event)

    def log_application(self, activity_type: str, detail: dict,
                        duration_ms: int = 0, risk_level: str = "low"):
        event = SandboxActivityEvent(
            session_id=self._session_id,
            user_id=self._user_id,
            sandbox_level=self._sandbox_level,
            category=ActivityCategory.APPLICATION.value,
            event_type=activity_type,
            detail=detail,
            duration_ms=duration_ms,
            risk_level=risk_level,
        )
        self._write(event)

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None


# === Collector: reads sandbox log → ClickHouse ===

class SandboxAuditCollector:
    """Collects sandbox activity events from tmpfs log files and writes to ClickHouse."""

    def __init__(self):
        self._clickhouse_client = None

    def _get_clickhouse(self):
        if self._clickhouse_client is None:
            try:
                from clickhouse_driver import Client
                from app.core.config import get_settings
                settings = get_settings()
                url = settings.CLICKHOUSE_URL
                host, port, user, password, database = "localhost", 9000, "default", "", "cds_audit"
                if "://" in url:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    host = parsed.hostname or "localhost"
                    port = parsed.port or 9000
                    user = parsed.username or "default"
                    password = parsed.password or ""
                    database = parsed.path.lstrip("/") or "cds_audit"
                elif ":" in url:
                    h, p = url.rsplit(":", 1)
                    host, port = h, int(p)
                else:
                    host = url
                self._clickhouse_client = Client(
                    host=host, port=port, database=database,
                    user=user, password=password,
                )
            except Exception as e:
                logger.warning(f"[sandbox-audit] ClickHouse unavailable: {e}")
                self._clickhouse_client = None
        return self._clickhouse_client

    def collect_from_file(self, log_path: str) -> list[SandboxActivityEvent]:
        """Read all events from a sandbox log file."""
        events = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(SandboxActivityEvent.from_json(line))
                        except (json.JSONDecodeError, TypeError) as e:
                            logger.warning(f"[sandbox-audit] Bad log line: {e}")
        except FileNotFoundError:
            pass
        return events

    def flush_to_clickhouse(self, events: list[SandboxActivityEvent]) -> int:
        """Write collected events to ClickHouse sandbox_activity_events table."""
        if not events:
            return 0
        client = self._get_clickhouse()
        if not client:
            logger.warning("[sandbox-audit] ClickHouse unavailable, events dropped")
            return 0

        rows = []
        for ev in events:
            detail_json = json.dumps(ev.detail, ensure_ascii=False)
            rows.append([
                ev.event_id,
                ev.timestamp,
                ev.session_id,
                ev.user_id,
                ev.sandbox_level,
                ev.category,
                ev.event_type,
                detail_json,
                ev.duration_ms,
                ev.bytes_read,
                ev.bytes_written,
                ev.rows_affected,
                ev.risk_level,
                ev.blocked,
            ])

        try:
            client.execute(
                """INSERT INTO sandbox_activity_events
                   (event_id, timestamp, session_id, user_id, sandbox_level,
                    category, event_type, detail, duration_ms,
                    bytes_read, bytes_written, rows_affected,
                    risk_level, blocked)
                   VALUES""",
                rows,
            )
            logger.info(f"[sandbox-audit] Flushed {len(events)} events to ClickHouse")
            return len(events)
        except Exception as e:
            logger.error(f"[sandbox-audit] ClickHouse flush failed: {e}")
            return 0

    def collect_and_flush(self, log_path: str) -> int:
        """Collect events from log file and flush to ClickHouse."""
        events = self.collect_from_file(log_path)
        return self.flush_to_clickhouse(events)


# === Risk Assessment Helpers ===

def assess_query_risk(sql: str) -> str:
    """Assess risk level of a SQL query."""
    sql_upper = sql.upper().strip()
    # Critical: DROP, DELETE, TRUNCATE, ALTER
    if any(kw in sql_upper for kw in ("DROP ", "DELETE ", "TRUNCATE ", "ALTER ")):
        return "critical"
    # High: INSERT, UPDATE, CREATE
    if any(kw in sql_upper for kw in ("INSERT ", "UPDATE ", "CREATE ")):
        return "high"
    # Medium: SELECT with many rows or joins
    if "SELECT" in sql_upper and ("JOIN" in sql_upper or "UNION" in sql_upper):
        return "medium"
    return "low"


def assess_network_risk(host: str, port: int) -> str:
    """Assess risk level of a network connection."""
    # High: non-standard ports
    if port not in (80, 443, 53):
        return "high"
    # Medium: non-HTTPS
    if port == 80:
        return "medium"
    return "low"


# === Helper Script for Shell Audit ===

SHELL_AUDIT_HELPER = '''#!/bin/sh
# CDS Sandbox Audit Helper — logs shell commands to audit log
# Injected into sandbox via tmpfs
_log() {
    _ts=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z" 2>/dev/null || echo "unknown")
    _detail=$(printf '{"cmd":"%s","cwd":"%s","exit":%d}' "$1" "$(pwd)" "$2")
    printf '{"event_id":"%s","timestamp":"%s","session_id":"%s","user_id":"%s","sandbox_level":"%s","category":"application","event_type":"code_execution","detail":%s,"duration_ms":0,"bytes_read":0,"bytes_written":0,"rows_affected":0,"risk_level":"low","blocked":false}\n' \
        "$(cat /proc/sys/kernel/random/uuid 2>/dev/null || echo '00000000-0000-0000-0000-000000000000')" \
        "$_ts" "$CDS_SESSION_ID" "$CDS_USER_ID" "$CDS_SANDBOX_LEVEL" "$_detail" >> "$CDS_AUDIT_LOG"
}
# Wrap: log command, execute, log result
_cds_exec() {
    _cmd="$*"
    _start=$(date +%s%N 2>/dev/null || echo 0)
    eval "$_cmd"
    _rc=$?
    _end=$(date +%s%N 2>/dev/null || echo 0)
    _dur=$(( (_end - _start) / 1000000 ))
    _log "$_cmd" $_rc
    return $_rc
}
'''

# === Python Audit Context Manager ===

class audit_context:
    """Context manager for auditing sandbox operations in Python code.

    Usage:
        from app.services.sandbox_audit import audit_context
        with audit_context("query", {"sql": sql}) as ctx:
            result = engine.execute_query(sql)
            ctx.set_rows(result.row_count)
    """

    def __init__(self, event_type: str, detail: dict,
                 category: str = "data_access", risk_level: str = "low"):
        self._logger = SandboxAuditLogger()
        self._event_type = event_type
        self._detail = detail
        self._category = category
        self._risk_level = risk_level
        self._start_time = 0.0
        self._rows = 0
        self._bytes_read = 0
        self._bytes_written = 0

    def set_rows(self, n: int):
        self._rows = n

    def set_bytes(self, read: int = 0, written: int = 0):
        self._bytes_read = read
        self._bytes_written = written

    def __enter__(self):
        self._start_time = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.monotonic() - self._start_time) * 1000)
        if exc_type:
            self._detail["error"] = str(exc_val)
            self._risk_level = "high"

        if self._category == ActivityCategory.DATA_ACCESS.value:
            self._logger.log_data_access(
                self._event_type, self._detail,
                rows_affected=self._rows,
                bytes_read=self._bytes_read,
                bytes_written=self._bytes_written,
                risk_level=self._risk_level,
            )
        elif self._category == ActivityCategory.NETWORK.value:
            self._logger.log_network(
                self._event_type, self._detail,
                risk_level=self._risk_level,
            )
        else:
            self._logger.log_application(
                self._event_type, self._detail,
                duration_ms=duration_ms,
                risk_level=self._risk_level,
            )
        self._logger.close()
        return False  # Don't suppress exceptions


# === Singleton collector ===
_collector: SandboxAuditCollector | None = None


def get_sandbox_audit_collector() -> SandboxAuditCollector:
    global _collector
    if _collector is None:
        _collector = SandboxAuditCollector()
    return _collector


# === Application Audit SDK (for apps deployed in sandbox) ===

def assess_http_risk(method: str, path: str, status_code: int) -> str:
    """Assess risk level of an HTTP request."""
    # Critical: admin/delete endpoints with success
    if status_code < 400 and any(kw in path.lower() for kw in ("/admin", "/delete", "/drop", "/truncate")):
        return "critical"
    # High: write operations (POST/PUT/PATCH/DELETE)
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return "high"
    # Medium: auth/sensitive endpoints
    if any(kw in path.lower() for kw in ("/auth", "/login", "/token", "/password", "/key")):
        return "medium"
    # High: server errors
    if status_code >= 500:
        return "high"
    return "low"


class CDSAuditSDK:
    """Lightweight SDK for applications deployed in the sandbox.

    Applications can use this to log their activity to the sandbox audit trail.
    Works with any Python framework (FastAPI, Flask, Django) and any language
    via the HTTP audit endpoint.

    Usage (Python):
        from cds_audit import CDSAuditSDK
        audit = CDSAuditSDK()

        # Log a REST API request
        audit.log_http_request("GET", "/api/data", 200, duration_ms=45)

        # Log a data query
        audit.log_data_query("SELECT * FROM users", rows=100)

        # Log application lifecycle
        audit.log_app_start("my-service", port=8080)

    Usage (Java/Node.js via HTTP):
        POST http://localhost:9877/audit
        {"category":"application","event_type":"http_request",
         "detail":{"method":"GET","path":"/api/data","status":200}}
    """

    def __init__(self, log_path: str | None = None, session_id: str | None = None,
                 app_name: str = ""):
        self._log_path = log_path or os.environ.get("CDS_AUDIT_LOG", "")
        self._session_id = session_id or os.environ.get("CDS_SESSION_ID", "")
        self._user_id = os.environ.get("CDS_USER_ID", "")
        self._sandbox_level = os.environ.get("CDS_SANDBOX_LEVEL", "")
        self._app_name = app_name
        self._fh = None
        if self._log_path:
            try:
                self._fh = open(self._log_path, "a", encoding="utf-8")
            except OSError:
                self._fh = None

    def _write_event(self, category: str, event_type: str, detail: dict,
                     duration_ms: int = 0, risk_level: str = "low",
                     rows_affected: int = 0, bytes_read: int = 0,
                     bytes_written: int = 0, blocked: bool = False):
        if not self._fh:
            return
        detail["app"] = self._app_name
        event = SandboxActivityEvent(
            session_id=self._session_id,
            user_id=self._user_id,
            sandbox_level=self._sandbox_level,
            category=category,
            event_type=event_type,
            detail=detail,
            duration_ms=duration_ms,
            bytes_read=bytes_read,
            bytes_written=bytes_written,
            rows_affected=rows_affected,
            risk_level=risk_level,
            blocked=blocked,
        )
        self._fh.write(event.to_json() + "\n")
        self._fh.flush()

    def log_http_request(self, method: str, path: str, status_code: int,
                         duration_ms: int = 0, client_ip: str = "",
                         request_size: int = 0, response_size: int = 0,
                         user_agent: str = ""):
        """Log an HTTP request handled by the deployed application."""
        detail = {
            "method": method,
            "path": path,
            "status_code": status_code,
            "client_ip": client_ip,
            "request_size": request_size,
            "response_size": response_size,
            "user_agent": user_agent,
        }
        risk = assess_http_risk(method, path, status_code)
        self._write_event(
            ActivityCategory.APPLICATION.value,
            ApplicationActivityType.HTTP_REQUEST.value,
            detail, duration_ms=duration_ms, risk_level=risk,
            bytes_read=request_size, bytes_written=response_size,
        )

    def log_api_access(self, endpoint: str, method: str, params: dict | None = None,
                       auth_type: str = "", duration_ms: int = 0):
        """Log API endpoint access with parameters."""
        detail = {
            "endpoint": endpoint,
            "method": method,
            "params": params or {},
            "auth_type": auth_type,
        }
        self._write_event(
            ActivityCategory.APPLICATION.value,
            ApplicationActivityType.API_ACCESS.value,
            detail, duration_ms=duration_ms,
        )

    def log_data_query(self, query: str, source: str = "app",
                       rows: int = 0, duration_ms: int = 0):
        """Log a data query made by the application."""
        detail = {
            "query": query[:2000],
            "source": source,
        }
        risk = assess_query_risk(query)
        self._write_event(
            ActivityCategory.DATA_ACCESS.value,
            ApplicationActivityType.DATA_QUERY.value,
            detail, duration_ms=duration_ms, rows_affected=rows, risk_level=risk,
        )

    def log_app_event(self, event_type: str, detail: dict,
                      duration_ms: int = 0, risk_level: str = "low"):
        """Log a generic application event."""
        self._write_event(
            ActivityCategory.APPLICATION.value,
            event_type, detail, duration_ms=duration_ms, risk_level=risk_level,
        )

    def log_app_start(self, app_name: str, port: int = 0, framework: str = ""):
        """Log application startup."""
        self._write_event(
            ActivityCategory.APPLICATION.value,
            ApplicationActivityType.APP_START.value,
            {"app_name": app_name, "port": port, "framework": framework},
        )

    def log_app_stop(self, app_name: str, reason: str = ""):
        """Log application shutdown."""
        self._write_event(
            ActivityCategory.APPLICATION.value,
            ApplicationActivityType.APP_STOP.value,
            {"app_name": app_name, "reason": reason},
        )

    def log_app_log(self, level: str, message: str, logger_name: str = ""):
        """Log an application log entry (for forwarding to audit trail)."""
        risk = "high" if level in ("ERROR", "CRITICAL") else "low"
        self._write_event(
            ActivityCategory.APPLICATION.value,
            ApplicationActivityType.APP_LOG.value,
            {"level": level, "message": message[:4000], "logger": logger_name},
            risk_level=risk,
        )

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None


# === FastAPI/Flask Middleware for Auto-Audit ===

class AuditMiddleware:
    """ASGI/WSGI middleware for automatic HTTP request audit logging.

    For FastAPI:
        from app.services.sandbox_audit import AuditMiddleware
        app.add_middleware(AuditMiddleware, app_name="my-service")

    For Flask:
        from app.services.sandbox_audit import FlaskAuditMiddleware
        FlaskAuditMiddleware(app, app_name="my-service")
    """

    def __init__(self, app, app_name: str = ""):
        self.app = app
        self._sdk = CDSAuditSDK(app_name=app_name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        start_time = time.monotonic()
        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            client_ip = ""
            for name, value in scope.get("headers", []):
                if name == b"x-forwarded-for":
                    client_ip = value.decode().split(",")[0].strip()
                    break
            self._sdk.log_http_request(
                method, path, status_code,
                duration_ms=duration_ms, client_ip=client_ip,
            )


class FlaskAuditMiddleware:
    """Flask middleware for automatic HTTP request audit logging.

    Usage:
        from app.services.sandbox_audit import FlaskAuditMiddleware
        FlaskAuditMiddleware(app, app_name="my-service")
    """

    def __init__(self, app, app_name: str = ""):
        self._sdk = CDSAuditSDK(app_name=app_name)
        app.before_request(self._before)
        app.after_request(self._after)
        self._start_time = 0.0

    def _before(self):
        self._start_time = time.monotonic()

    def _after(self, response):
        from flask import request
        duration_ms = int((time.monotonic() - self._start_time) * 1000)
        self._sdk.log_http_request(
            request.method, request.path, response.status_code,
            duration_ms=duration_ms,
            client_ip=request.remote_addr or "",
            request_size=request.content_length or 0,
            response_size=response.content_length or 0,
            user_agent=request.user_agent.string or "",
        )
        return response


# === HTTP Audit Endpoint (for Java/Node.js apps) ===

def create_audit_http_server(host: str = "127.0.0.1", port: int = 9877):
    """Create a lightweight HTTP server for receiving audit events from non-Python apps.

    Runs inside the sandbox. Java/Node.js apps POST audit events to this endpoint.

    POST /audit
    Content-Type: application/json
    {
        "category": "application",
        "event_type": "http_request",
        "detail": {"method": "GET", "path": "/api/data", "status": 200},
        "duration_ms": 45,
        "risk_level": "low"
    }

    POST /audit/batch
    Content-Type: application/json
    [{"category": "...", "event_type": "...", "detail": {...}}, ...]

    GET /health
    Returns: {"status": "ok"}
    """
    import asyncio
    from http.server import HTTPServer, BaseHTTPRequestHandler

    sdk = CDSAuditSDK(app_name="audit-sidecar")

    class AuditHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/audit":
                self._handle_single()
            elif self.path == "/audit/batch":
                self._handle_batch()
            else:
                self.send_error(404)

        def do_GET(self):
            if self.path == "/health":
                self._send_json({"status": "ok"})
            else:
                self.send_error(404)

        def _handle_single(self):
            try:
                body = self._read_body()
                sdk._write_event(
                    category=body.get("category", "application"),
                    event_type=body.get("event_type", "unknown"),
                    detail=body.get("detail", {}),
                    duration_ms=body.get("duration_ms", 0),
                    risk_level=body.get("risk_level", "low"),
                    rows_affected=body.get("rows_affected", 0),
                    blocked=body.get("blocked", False),
                )
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        def _handle_batch(self):
            try:
                body = self._read_body()
                if not isinstance(body, list):
                    self._send_json({"ok": False, "error": "Expected JSON array"}, 400)
                    return
                count = 0
                for item in body:
                    sdk._write_event(
                        category=item.get("category", "application"),
                        event_type=item.get("event_type", "unknown"),
                        detail=item.get("detail", {}),
                        duration_ms=item.get("duration_ms", 0),
                        risk_level=item.get("risk_level", "low"),
                    )
                    count += 1
                self._send_json({"ok": True, "count": count})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        def _read_body(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            return json.loads(raw)

        def _send_json(self, data: dict, status: int = 200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass  # Suppress default logging

    return HTTPServer((host, port), AuditHandler)


# === Python Audit Logger Hook (for standard logging module) ===

class CDSAuditLogHandler(logging.Handler):
    """Logging handler that forwards log records to the CDS audit trail.

    Usage:
        import logging
        from cds_audit import CDSAuditLogHandler

        handler = CDSAuditLogHandler(app_name="my-service")
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.WARNING)  # Only WARN+ to audit
    """

    def __init__(self, app_name: str = "", level: int = logging.WARNING):
        super().__init__(level=level)
        self._sdk = CDSAuditSDK(app_name=app_name)

    def emit(self, record: logging.LogRecord):
        try:
            self._sdk.log_app_log(
                level=record.levelname,
                message=self.format(record),
                logger_name=record.name,
            )
        except Exception:
            pass


# === Collector: tmpfs → ClickHouse ===

def collect_session_audit_log(session_id: str, log_path: str,
                               clickhouse_client=None, wipe: bool = True) -> int:
    """Collect sandbox audit events from tmpfs log and persist to ClickHouse.

    Called on session end or periodically for long sessions.

    Args:
        session_id: Sandbox session ID
        log_path: Path to the tmpfs JSONL log file
        clickhouse_client: clickhouse_driver.Client instance (uses gateway client if None)
        wipe: Whether to securely wipe the tmpfs file after collection

    Returns:
        Number of events collected
    """
    p = Path(log_path)
    if not p.exists():
        return 0

    events = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = SandboxActivityEvent.from_json(line)
                    events.append(event)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"[audit-collector] Skipping malformed event: {e}")
    except OSError as e:
        logger.error(f"[audit-collector] Failed to read {log_path}: {e}")
        return 0

    if not events:
        if wipe:
            _secure_wipe_log(p)
        return 0

    # Write to ClickHouse
    client = clickhouse_client or _get_clickhouse_client()
    if client:
        try:
            rows = []
            for ev in events:
                rows.append([
                    ev.event_id, ev.timestamp, ev.session_id, ev.user_id,
                    ev.sandbox_level, ev.category, ev.event_type,
                    json.dumps(ev.detail, ensure_ascii=False),
                    ev.duration_ms, ev.bytes_read, ev.bytes_written,
                    ev.rows_affected, ev.risk_level, ev.blocked,
                ])
            client.execute(
                """INSERT INTO sandbox_activity_events
                   (event_id, timestamp, session_id, user_id, sandbox_level,
                    category, event_type, detail, duration_ms,
                    bytes_read, bytes_written, rows_affected, risk_level, blocked)
                   VALUES""",
                rows,
            )
            logger.info(f"[audit-collector] Persisted {len(events)} events for session {session_id}")
        except Exception as e:
            logger.error(f"[audit-collector] ClickHouse write failed: {e}")
    else:
        logger.warning(f"[audit-collector] No ClickHouse client — {len(events)} events lost")

    if wipe:
        _secure_wipe_log(p)

    return len(events)


def _secure_wipe_log(path: Path) -> None:
    """Securely wipe a tmpfs log file."""
    try:
        from app.services.sandbox_security import secure_wipe_file
        secure_wipe_file(path)
    except ImportError:
        # Fallback: simple delete
        path.unlink(missing_ok=True)


def _get_clickhouse_client():
    """Get a ClickHouse client from gateway_service or direct config."""
    try:
        from app.services.gateway_service import gateway_service
        return gateway_service._get_clickhouse()
    except Exception:
        return None
