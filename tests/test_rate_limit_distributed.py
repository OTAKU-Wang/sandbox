"""W3: distributed rate limiting — Redis fixed window, per-scope buckets.

Covers docs/cubesandbox-parity-plan.md W3 test matrix:
- same-IP burst hits the IP bucket (61st request -> 429 + Retry-After + RATE_LIMITED)
- distinct IPs count independently
- login endpoint uses the dedicated (stricter) auth bucket per IP
- Redis outage degrades to the in-process window (fail-open) with a WARNING
- authenticated requests land in per-user buckets keyed by the (unverified) JWT sub
"""
import base64
import json
import uuid

import pytest
import pytest_asyncio
import fakeredis.aioredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.security import RateLimitMiddleware


def _make_fake_redis(monkeypatch) -> fakeredis.aioredis.FakeRedis:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "_redis_client", fake)
    return fake


def _bearer(sub: str) -> str:
    def b64(obj: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
        return raw

    return f"Bearer {b64({'alg': 'none'})}.{b64({'sub': sub})}.sig"


@pytest_asyncio.fixture
async def limited_app(monkeypatch, ip="10.0.0.1"):
    """Minimal app with the rate-limit middleware active (tests run with
    TESTING=1 so the real app does not attach it)."""
    _make_fake_redis(monkeypatch)
    test_app = FastAPI()

    @test_app.get("/ping")
    async def ping():
        return {"ok": True}

    @test_app.post("/api/v1/auth/login")
    async def login():
        return {"ok": True}

    test_app.add_middleware(RateLimitMiddleware)
    return test_app


def _client(app: FastAPI, ip: str = "10.0.0.1") -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 51234)), base_url="http://test")


@pytest.mark.asyncio
async def test_ip_bucket_returns_429_with_contract_shape(limited_app):
    async with _client(limited_app) as ac:
        last = None
        for _ in range(61):
            last = await ac.get("/ping")
        assert last.status_code == 429
        body = last.json()
        assert body["code"] == "RATE_LIMITED"
        assert body["request_id"]
        retry_after = last.headers["Retry-After"]
        assert retry_after.isdigit() and 1 <= int(retry_after) <= 60
        assert last.headers["X-Request-ID"] == body["request_id"]


@pytest.mark.asyncio
async def test_distinct_ips_count_independently(limited_app):
    async with _client(limited_app, ip="10.0.0.7") as ac_a, _client(limited_app, ip="10.0.0.8") as ac_b:
        for _ in range(60):
            assert (await ac_a.get("/ping")).status_code == 200
        assert (await ac_b.get("/ping")).status_code == 200
        assert (await ac_a.get("/ping")).status_code == 429


@pytest.mark.asyncio
async def test_login_endpoint_uses_stricter_auth_bucket(limited_app):
    async with _client(limited_app) as ac:
        for i in range(10):
            resp = await ac.post("/api/v1/auth/login")
            assert resp.status_code == 200, f"request {i + 1} unexpectedly limited"
        resp = await ac.post("/api/v1/auth/login")
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_user_scope_keyed_by_jwt_sub(limited_app):
    async with _client(limited_app) as ac:
        headers_a = {"Authorization": _bearer("11111111-1111-1111-1111-111111111111")}
        headers_b = {"Authorization": _bearer("22222222-2222-2222-2222-222222222222")}
        for _ in range(600):
            resp = await ac.get("/ping", headers=headers_a)
        assert resp.status_code == 200
        assert (await ac.get("/ping", headers=headers_a)).status_code == 429
        assert (await ac.get("/ping", headers=headers_b)).status_code == 200
        # anonymous IP bucket unaffected by the user buckets
        assert (await ac.get("/ping")).status_code == 200


@pytest.mark.asyncio
async def test_redis_outage_fails_open_with_warning(limited_app, monkeypatch, caplog):
    import app.core.redis as redis_module

    async def _broken_get_redis():
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis_module, "get_redis", _broken_get_redis)
    with caplog.at_level("WARNING"):
        async with _client(limited_app) as ac:
            for _ in range(5):
                assert (await ac.get("/ping")).status_code == 200
    assert any("fail" in r.message.lower() or "unavailable" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_rate_limit_disabled_passes_through(monkeypatch):
    from app.core.config import get_settings

    _make_fake_redis(monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    test_app = FastAPI()

    @test_app.get("/ping")
    async def ping():
        return {"ok": True}

    test_app.add_middleware(RateLimitMiddleware)
    async with _client(test_app) as ac:
        for _ in range(200):
            assert (await ac.get("/ping")).status_code == 200


@pytest.mark.asyncio
async def test_redis_window_shared_across_middleware_instances(monkeypatch):
    """Two replicas (separate middleware instances) share one Redis window."""
    _make_fake_redis(monkeypatch)
    app_a, app_b = FastAPI(), FastAPI()
    for test_app in (app_a, app_b):
        @test_app.get("/ping")
        async def ping():
            return {"ok": True}

        test_app.add_middleware(RateLimitMiddleware)

    ip = f"10.1.{uuid.uuid4().int % 200}.{uuid.uuid4().int % 200}"
    async with _client(app_a, ip=ip) as ac_a, _client(app_b, ip=ip) as ac_b:
        for _ in range(60):
            assert (await ac_a.get("/ping")).status_code == 200
        assert (await ac_b.get("/ping")).status_code == 429
