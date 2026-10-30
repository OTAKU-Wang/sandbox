"""A25: WS 一次性票据的消费必须是单命令原子的（`_consume_ticket` 服务层口径）。

台账：`consumed = await redis.get(key)` 后 `await redis.delete(key)` —— 两条命令之间的
窗口内两个并发连接可双双通过 ⇒ single-use 语义可被竞态绕过（修法一行：`getdel`）。

本文件从服务层直接打 `_consume_ticket`（绕过 WS 握手与 HTTP 主闸），钉三件事：
1. 并发消费同一张票据，只有 1 个连接能拿到 claims —— 替身如实复刻两种命令的语义差别：
   `GET` 是两次往返（值先回到客户端，DELETE 尚未发出 ⇒ 中间可被调度切走），
   `GETDEL` 是服务端单命令（读删之间不让出）；
2. 顺序重放（拿到过 claims 的那张票再消费一次）必须被拒，且 Redis 键已烧掉；
3. 伪造 type / 过期 / 无 jti / Redis 不可用一律 fail-closed 返回 None（拒发流，不放行）。
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import jwt as pyjwt
import pytest

from app.api import session_stream as stream
from app.core.config import get_settings


class _RacingRedis:
    """GET 与 GETDEL 的往返语义差别替身（见模块 docstring 第 1 条）。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def get(self, key):
        value = self.store.get(key)
        await asyncio.sleep(0)
        return value

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0


def _fake_redis_factory(monkeypatch, client):
    async def _get():
        return client

    monkeypatch.setattr(stream, "get_redis", _get)


async def _mint(racing: _RacingRedis, monkeypatch) -> str:
    _fake_redis_factory(monkeypatch, racing)
    issued = await stream.issue_ws_ticket(str(uuid.uuid4()), "buyer")
    assert "ticket" in issued, issued
    return issued["ticket"]


async def test_concurrent_consumers_get_single_use_semantics(monkeypatch):
    racing = _RacingRedis()
    ticket = await _mint(racing, monkeypatch)

    claims = await asyncio.gather(*(stream._consume_ticket(ticket) for _ in range(3)))

    winners = [c for c in claims if c is not None]
    assert len(winners) == 1, f"single-use 被竞态绕过：{len(winners)}/3 个连接同时通过"
    assert racing.store == {}, "票据键必须已烧掉"


async def test_sequential_replay_is_refused(monkeypatch):
    server = fakeredis.FakeServer()
    real = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    _fake_redis_factory(monkeypatch, real)

    issued = await stream.issue_ws_ticket(str(uuid.uuid4()), "buyer")
    ticket = issued["ticket"]

    first = await stream._consume_ticket(ticket)
    assert first is not None
    jti = first["jti"]
    assert await real.get(f"cds:ws-ticket:{jti}") is None
    assert await stream._consume_ticket(ticket) is None


def _signed(**overrides) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(uuid.uuid4()),
        "role": "buyer",
        "jti": uuid.uuid4().hex,
        "type": "ws-ticket",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=30)).timestamp()),
    }
    payload.update(overrides)
    return pyjwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


async def test_forged_or_expired_tickets_are_refused(monkeypatch):
    racing = _RacingRedis()
    _fake_redis_factory(monkeypatch, racing)
    past = datetime.now(timezone.utc) - timedelta(seconds=120)

    cases = {
        "expired": _signed(iat=int(past.timestamp()), exp=int(past.timestamp() + 5)),
        "wrong_type": _signed(type="access"),
        "no_jti": _signed(jti=""),
        "not_a_jwt": "gsueh.gsheu.gsehu",
    }
    for name, ticket in cases.items():
        assert await stream._consume_ticket(ticket) is None, f"{name} 票据被放行"


async def test_ticket_burned_before_claims_are_returned(monkeypatch):
    racing = _RacingRedis()
    ticket = await _mint(racing, monkeypatch)
    jti = pyjwt.decode(ticket, get_settings().JWT_SECRET_KEY,
                       algorithms=[get_settings().JWT_ALGORITHM])["jti"]
    assert f"cds:ws-ticket:{jti}" in racing.store

    claims = await stream._consume_ticket(ticket)
    assert claims is not None
    assert f"cds:ws-ticket:{jti}" not in racing.store


async def test_redis_outage_fails_closed(monkeypatch):
    class _Down:
        async def getdel(self, key):
            raise ConnectionError("redis is down")

        async def get(self, key):
            raise ConnectionError("redis is down")

    racing = _RacingRedis()
    ticket = await _mint(racing, monkeypatch)
    _fake_redis_factory(monkeypatch, _Down())

    assert await stream._consume_ticket(ticket) is None


async def test_mint_refuses_without_redis(monkeypatch):
    async def _boom():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(stream, "get_redis", _boom)
    issued = await stream.issue_ws_ticket(str(uuid.uuid4()), "buyer")
    assert issued == {"error": "ws-ticket service unavailable"}
