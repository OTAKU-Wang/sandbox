"""Central logging setup (W1, parity plan).

Default behaviour is UNCHANGED (plain stdlib logging, existing log-text
assertions keep passing). When ``CDS_LOG_JSON=true`` every root handler gets a
JSON formatter that carries the request-scoped context (timestamp / level /
logger / message / request_id / exception).

A43 (2026-10-18): the previous JSON branch was dead weight and actively lossy
— ``structlog.configure`` without a ``LoggerFactory`` and without
``ProcessorFormatter``/``capture_logs`` never routed anything through structlog
(no module in the repo calls ``structlog.get_logger``), while
``root.handlers = [StreamHandler("%(message)s")]`` threw away whatever
handler uvicorn/dictConfig had installed and stripped every stdlib record to
its bare message — no timestamp, no level, no logger name, no request_id, and
not JSON either. ``structlog`` is not even installed in the gate venv, so the
branch degraded to "warn once, keep plain logging" there.

Now the JSON pipeline is plain stdlib (``JsonFormatter``), attaches to the
handlers that already exist, and never overrides an explicitly configured
root level. ``request_id`` reaches the record through the contextvar written by
``RequestIDContextMiddleware`` — a ``logging.Filter`` on the root logger cannot
do it, because stdlib applies filters only on the *originating* logger, never
on ancestors, so child-logger records never see ``RequestIdFilter``.
"""
from __future__ import annotations

import json
import logging
import sys
import contextvars
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("cds_request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Inject the current request_id into every LogRecord (json + text mode)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# LogRecord attributes that are part of the stdlib schema — everything else on
# the record is an `extra=` field and belongs in the JSON payload.
_STANDARD_RECORD_KEYS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for stdlib loggers (no structlog required)."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id,
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS or key == "request_id" or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif getattr(record, "exc_text", None):
            payload["exception"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:  # pragma: no cover - logging must never raise
            return json.dumps(
                {"timestamp": payload["timestamp"], "level": record.levelname,
                 "logger": record.name, "message": str(record.msg),
                 "request_id": request_id},
                ensure_ascii=False, default=str,
            )


class RequestIDContextMiddleware:
    """Bind ``request_id_var`` for the lifetime of one request.

    Raw ASGI (same shape as main.py's SS-12 gate): the id is the one
    ``RequestIDMiddleware`` already stamped on ``scope["state"]``, or the
    inbound ``X-Request-ID`` header when the request has not passed through
    that middleware (WS/ASGI mounts, direct app calls).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = (scope.get("state") or {}).get("request_id") or ""
        if not request_id:
            for key, value in scope.get("headers") or []:
                if key == b"x-request-id":
                    candidate = value.decode("latin-1").strip()
                    if candidate and 8 <= len(candidate) <= 64 and all(
                        c.isalnum() or c == "-" for c in candidate
                    ):
                        request_id = candidate
                    break
        token = request_id_var.set(request_id or "-")
        try:
            await self.app(scope, receive, send)
        finally:
            request_id_var.reset(token)


def setup_logging(json_mode: bool) -> None:
    """Install the JSON pipeline when requested; otherwise keep stdlib config."""
    root = logging.getLogger()

    if not any(isinstance(f, RequestIdFilter) for f in root.filters):
        root.addFilter(RequestIdFilter())

    if not json_mode:
        return

    formatter = JsonFormatter()
    if not root.handlers:
        # Nothing installed yet (bare `python -c`, some workers): own a handler
        # so logs are not silently swallowed by the "lastResort" fallback.
        root.addHandler(logging.StreamHandler(sys.stderr))

    # Attach — never replace. Existing handlers (uvicorn/gunicorn/dictConfig,
    # a file sink, a collector agent) keep their place and just change shape.
    for handler in list(root.handlers):
        handler.setFormatter(formatter)

    # Root level: surface INFO only where nothing was configured (NOTSET makes
    # the effective level WARNING). An explicit level — looser OR stricter —
    # is the operator's call and is left alone.
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
