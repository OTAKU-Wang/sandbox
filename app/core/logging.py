"""Central logging setup (W1, parity plan).

Default behaviour is UNCHANGED (plain stdlib logging, existing log-text
assertions keep passing). When ``CDS_LOG_JSON=true`` a structlog JSON
pipeline is installed with ``request_id`` merged from the request contextvar
set by RequestIDMiddleware.
"""
from __future__ import annotations

import logging
import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("cds_request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Inject the current request_id into every LogRecord (json + text mode)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging(json_mode: bool) -> None:
    """Install the JSON pipeline when requested; otherwise keep stdlib config."""
    root = logging.getLogger()

    if not any(isinstance(f, RequestIdFilter) for f in root.filters):
        root.addFilter(RequestIdFilter())

    if not json_mode:
        return

    try:
        import structlog
    except ImportError:  # pragma: no cover - structlog is in requirements
        logging.getLogger(__name__).warning(
            "LOG_JSON=true but structlog is not installed; keeping plain logging"
        )
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(message)s")  # structlog renders the JSON line itself
    )
    root.handlers = [handler]
    root.setLevel(logging.INFO)
