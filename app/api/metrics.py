"""/metrics endpoint (W1, parity plan) — Prometheus scrape target.

Mounted WITHOUT the /api/v1 prefix (Prometheus convention). Optional bearer
token via CDS_METRICS_API_TOKEN for deployments that cannot network-isolate
the scrape path.
"""
from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import get_settings
from app.core.metrics import render_metrics

router = APIRouter()


@router.get("/metrics")
async def metrics_endpoint(request: Request):
    settings = get_settings()
    if not settings.METRICS_ENABLED:
        return Response(status_code=404)
    if settings.METRICS_API_TOKEN:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {settings.METRICS_API_TOKEN}":
            return Response(
                content='{"detail": "metrics token required"}',
                status_code=401,
                media_type="application/json",
            )
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8")
