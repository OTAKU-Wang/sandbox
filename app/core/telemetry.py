"""Request telemetry middleware (W1, parity plan).

- RequestIDMiddleware: propagate/issue ``X-Request-ID`` on every request; the
  ID is stored on ``request.state.request_id`` (consumed by the W2 error
  contract) and echoed in the response header.
- MetricsMiddleware: per-request counters/histograms on the CDS registry.
  Disabled entirely when TESTING=1 (TestClient asserts stay deterministic) or
  METRICS_ENABLED=false. ``/metrics`` and ``/health`` are not measured.
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = logging.getLogger(__name__)

_UNMEASURED_PATHS = {"/metrics", "/health"}


def _route_template_from_params(request: Request) -> str | None:
    """Re-template the concrete path from its matched path_params.

    ``/api/v1/sandbox-sessions/<uuid>`` + ``{"session_id": "<uuid>"}`` ->
    ``/api/v1/sandbox-sessions/{session_id}``. Version-independent: survives
    flattened route copies as well as _IncludedRouter wrappers, and keeps the
    cardinality red line (labels are templates, never concrete ids).
    """
    params = request.scope.get("path_params")
    if not isinstance(params, dict) or not params:
        return None
    out = []
    for segment in request.url.path.split("/"):
        replacement = None
        for name, value in params.items():
            if segment and segment == str(value):
                replacement = "{" + str(name) + "}"
                break
        out.append(replacement or segment)
    template = "/".join(out)
    return template if "{" in template else None


def _resolve_route_template(request: Request) -> str:
    # 1. path_params re-templating (version-independent).
    try:
        template = _route_template_from_params(request)
        if template:
            return template
    except Exception:  # pragma: no cover - defensive
        pass
    # 2. router-set route (some versions put the route on the scope).
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return "unmatched"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Issue or propagate X-Request-ID; outermost middleware (W1)."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        # Accept only sane hex-ish ids from callers; ignore junk.
        if incoming and 8 <= len(incoming) <= 64 and all(c.isalnum() or c == "-" for c in incoming):
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Count + time every request on the CDS Prometheus registry (W1)."""

    def __init__(self, app, enabled: bool = True):
        super().__init__(app)
        self._enabled = enabled

    async def dispatch(self, request: Request, call_next):
        if not self._enabled or request.url.path in _UNMEASURED_PATHS:
            return await call_next(request)

        method = request.method
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            status = 500
            raise
        finally:
            route_template = _resolve_route_template(request)
            duration = time.perf_counter() - start
            try:
                HTTP_REQUESTS.labels(method=method, route=route_template, status=str(status)).inc()
                HTTP_LATENCY.labels(method=method, route=route_template).observe(duration)
            except Exception:  # pragma: no cover - metrics must never break requests
                logger.warning("[metrics] failed to record request metrics", exc_info=True)
