"""CDS Prometheus metrics (W1, parity plan).

Cardinality red lines (binding for every future edit):
- labels must NEVER include unbounded dimensions (session_id, contract_id,
  user_id, filename, ...);
- the ``route`` label uses the route TEMPLATE (e.g.
  ``/api/v1/sandbox-sessions/{session_id}``), never the concrete path;
- session status enums are bounded sets and are allowed.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "cds_http_requests_total", "HTTP requests handled", ["method", "route", "status"], registry=REGISTRY
)
HTTP_LATENCY = Histogram(
    "cds_http_request_duration_seconds", "HTTP request latency", ["method", "route"], registry=REGISTRY
)
SESSIONS_ACTIVE = Gauge(
    "cds_sessions_active", "Currently active sandbox sessions by status", ["status"], registry=REGISTRY
)
SESSIONS_TOTAL = Counter(
    "cds_sessions_total", "Sandbox session status transitions", ["transition"], registry=REGISTRY
)
TASKS_ACTIVE = Gauge(
    "cds_tasks_active", "Currently active sandbox tasks by status", ["status"], registry=REGISTRY
)
TASKS_TOTAL = Counter(
    "cds_tasks_total", "Sandbox task completions by result", ["result"], registry=REGISTRY
)
OUTPUT_INSPECTIONS = Counter(
    "cds_output_inspections_total", "Output gateway inspection verdicts", ["verdict"], registry=REGISTRY
)
KMS_DISTRIBUTIONS = Counter(
    "cds_kms_key_distribution_total", "Session key distribution outcomes", ["result"], registry=REGISTRY
)
RATE_LIMITED = Counter(
    "cds_rate_limited_total", "Requests rejected by rate limiting", ["scope"], registry=REGISTRY
)


def render_metrics() -> bytes:
    """Render the CDS registry in the Prometheus text exposition format."""
    return generate_latest(REGISTRY)


def record_session_transition(old_status: str | None, new_status: str) -> None:
    """Instrument one session status transition (bounded status enums only)."""
    SESSIONS_TOTAL.labels(transition=f"{old_status or 'none'}->{new_status}").inc()
    if old_status:
        try:
            SESSIONS_ACTIVE.labels(status=old_status).dec()
        except ValueError:
            pass  # counter below zero — Gauge.dec raises only on misuse
    SESSIONS_ACTIVE.labels(status=new_status).inc()


def record_output_inspection(verdict: str) -> None:
    """Verdict is bounded: blocked | redacted | passed."""
    OUTPUT_INSPECTIONS.labels(verdict=verdict).inc()


def record_kms_distribution(result: str) -> None:
    """Result is bounded: success | rejected | failed."""
    KMS_DISTRIBUTIONS.labels(result=result).inc()
