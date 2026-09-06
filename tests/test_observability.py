"""W1 observability tests — /metrics, Request-ID, route-template labels.

The global ``client`` fixture runs with MetricsMiddleware disabled (TESTING),
so counter/route-label assertions use a fresh app with the middleware enabled
explicitly; the /metrics endpoint and Request-ID behaviour are verified on
both the global client and fresh apps.
"""
import re
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.telemetry import MetricsMiddleware, RequestIDMiddleware


def _fresh_app(include_sessions: bool = False) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware, enabled=True)
    app.add_middleware(RequestIDMiddleware)
    from app.api import metrics as metrics_api

    app.include_router(metrics_api.router)
    if include_sessions:
        from app.api import sandbox_sessions

        app.include_router(sandbox_sessions.router, prefix="/api/v1/sandbox-sessions")
    return app


@pytest.mark.asyncio
async def test_metrics_endpoint_reachable(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_metrics_token_required(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "METRICS_API_TOKEN", "secret-token")
    resp = await client.get("/metrics")
    assert resp.status_code == 401
    resp = await client.get("/metrics", headers={"Authorization": "Bearer secret-token"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_disabled_returns_404(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "METRICS_ENABLED", False)
    resp = await client.get("/metrics")
    assert resp.status_code == 404


def test_metrics_counts_requests_with_route_template_label():
    app = _fresh_app(include_sessions=True)
    with TestClient(app) as c:
        c.get("/health")
        # Unknown session id -> 404 via HTTPException, but the ROUTE matched,
        # so the metrics route label must be the template, not the uuid.
        c.get(f"/api/v1/sandbox-sessions/{uuid.uuid4()}")
        body = c.get("/metrics").text

    assert "cds_http_requests_total" in body
    assert 'route="/api/v1/sandbox-sessions/{session_id}"' in body
    assert str(uuid.uuid4()) not in body, "concrete ids must never appear as labels"


@pytest.mark.asyncio
async def test_request_id_echo(client):
    resp = await client.get("/health", headers={"X-Request-ID": "abcdef1234567890"})
    assert resp.headers.get("X-Request-ID") == "abcdef1234567890"


@pytest.mark.asyncio
async def test_request_id_generated_is_16_hex(client):
    resp = await client.get("/health")
    rid = resp.headers.get("X-Request-ID", "")
    assert len(rid) == 16
    assert re.fullmatch(r"[0-9a-f]{16}", rid)


def test_request_id_junk_header_replaced():
    app = _fresh_app()
    with TestClient(app) as c:
        resp = c.get("/health", headers={"X-Request-ID": "bad id with spaces!!"})
        rid = resp.headers.get("X-Request-ID", "")
        assert re.fullmatch(r"[0-9a-f]{16}", rid), "junk request ids must be replaced"
