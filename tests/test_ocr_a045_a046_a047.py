"""OCR 复核轮 A45/A46/A47 —— `app/core/security.py` 限流与体积门的回归测试。

三处的现场（回读逐字确认）：

- A45 `_resolve_scope` 只返回**一个**桶：只要未验签 JWT 里解出 `sub` 就整条请求
  走 `user` 桶，**IP 桶完全不查** ⇒ 一个攻击者签无数伪造 token（`sub` 自便）即可
  把每 IP 上限变成每伪造身份上限。
- A46 `_local` 只清桶内过期时间戳，**空键永不回收、键数无上限**；这条路径正是
  Redis 不可用时的回退，长期运行键空间随伪造身份数线性增长。
- A47 `RequestSizeLimitMiddleware` 只看 `content-length`，**没有这个头就什么都不拦**
  ⇒ `Transfer-Encoding: chunked` 可绕过 10MB 上限。
"""
import base64
import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.security import RateLimitMiddleware, RequestSizeLimitMiddleware


def _jwt(sub: str) -> str:
    def b64(obj):
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'sub': sub})}.sig"


class _Settings:
    RATE_LIMIT_ENABLED = True
    RATE_LIMIT_AUTH_PER_MINUTE = 100
    RATE_LIMIT_PER_MINUTE_USER = 500
    RATE_LIMIT_PER_MINUTE_IP = 2


@pytest.fixture
def rate_app(monkeypatch):
    import app.core.redis as redis_mod

    async def no_redis():
        raise RuntimeError("redis down")

    monkeypatch.setattr(redis_mod, "get_redis", no_redis)
    import app.core.security as security

    monkeypatch.setattr(security, "get_settings", lambda: _Settings())
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, requests_per_minute=None)

    @app.get("/api/v1/ping")
    async def ping():
        return {"ok": True}

    return app


def test_a045_forged_jwt_still_consumes_the_ip_bucket(rate_app):
    """伪造 `sub` 只能让它多一个自己的桶，不能把 IP 桶整条绕开。"""
    client = TestClient(rate_app)
    codes = [client.get("/api/v1/ping", headers={"Authorization": f"Bearer {_jwt('attacker-%d' % i)}"}).status_code
             for i in range(6)]
    assert 429 in codes, codes
    blocked = client.get("/api/v1/ping", headers={"Authorization": f"Bearer {_jwt('brand-new-sub')}"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["scope"] == "ip"


def test_a046_local_fallback_bounds_key_space(rate_app):
    """Redis 回退路径的进程内桶必须有键数上限。"""
    mw = RateLimitMiddleware(rate_app)
    for i in range(5000):
        mw._local_hit("ip", f"10.0.0.{i}", 100)
    assert len(mw._local) <= RateLimitMiddleware._MAX_LOCAL_BUCKETS


def test_a047_chunked_body_over_limit_is_not_delivered_intact():
    """没有 content-length 的 chunked 请求也必须受 max_size_bytes 约束。"""
    app = FastAPI()
    app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=1024)

    @app.post("/ingest")
    async def ingest(request: Request):
        body = await request.body()
        return {"received": len(body)}

    client = TestClient(app)
    small = client.post("/ingest", content=b"x" * 200)
    assert small.status_code == 200
    assert small.json()["received"] == 200

    def oversized():
        for _ in range(40):
            yield b"y" * 100

    try:
        resp = client.post("/ingest", content=oversized())
    except Exception:
        return
    assert resp.status_code in (400, 413) or resp.json().get("received", 0) <= 1024, resp.text[:200]
