"""OCR A192d —— 跨进程 ε 预算后端必须在应用装配路径上接上，接不上要如实披露。

owner-output-chain 把跨进程 DP 预算落在 `app/services/dp_budget.py`（`attach_redis` /
`budget_backend_health` / `budget_state_sync` / 四个 `*_sync`），但生产装配点没人调
`attach_redis`：真部署里「多副本共享同一份 ε 预算」从来没生效过，读数却照旧以权威口径
外报。所以这里经**真实 lifespan** 取证：
1. 装配后 `budget_backend_health()` 报 backend=redis / authoritative=True，且同一份预算在
   第二个账本实例（模拟另一副本，共用一个 redis server）上可见；
2. 后端接不上时 health 必须报 backend!=redis / authoritative=False 并留 WARNING ——
   不得静默退回进程内账本再照旧报预算（owner-output-chain 已定口径）；
3. 装配确实发生在 lifespan 启动路径上（源码级护栏 + 第 1 条真跑）。
"""
import inspect
import uuid

import fakeredis
import pytest

import app.main as main
from app.services.dp_budget import DPBudgetLedger, dp_budget_ledger


@pytest.fixture(autouse=True)
def _isolated_ledger_backend():
    """装配写的是进程级单例：进出各自抹平，不污染同座次的其它测试。"""
    led = dp_budget_ledger
    saved = (
        led._redis,
        led._backend_probe_at,
        led._backend_last_error,
        dict(led._backend_events),
    )
    led._redis = None
    led._backend_probe_at = None
    yield
    led._redis, led._backend_probe_at, led._backend_last_error, saved_events = saved
    led._backend_events.clear()
    led._backend_events.update(saved_events)


def _fake_server():
    server = fakeredis.FakeServer()

    def _client():
        return fakeredis.FakeStrictRedis(server=server, decode_responses=True)

    return server, _client


def test_wiring_makes_the_budget_visible_on_another_replica():
    server, client = _fake_server()
    health = main.dp_budget_backend_wiring(client)
    assert health["backend"] == "redis", health
    assert health["authoritative"] is True, health

    sid = f"a192d-{uuid.uuid4().hex[:12]}"
    dp_budget_ledger.allocate_sync(sid, 5.0)

    replica_b = DPBudgetLedger()
    replica_b.attach_redis(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
    assert replica_b.has_budget_sync(sid) is True, "另一副本看不见这份预算 ⇒ 跨进程装配没生效"
    assert replica_b.get_remaining_sync(sid) == pytest.approx(5.0)


def test_wiring_failure_is_disclosed_and_not_authoritative(caplog):
    import logging

    def _down():
        raise ConnectionError("redis:6379 refused")

    with caplog.at_level(logging.INFO, logger="app.main"):
        health = main.dp_budget_backend_wiring(_down)

    assert health["backend"] != "redis", health
    assert health["authoritative"] is False, "接不上后端却仍报 authoritative=True 就是谎报"
    hits = [r for r in caplog.records if "DP ε-budget backend" in r.getMessage()]
    assert hits, [r.getMessage() for r in caplog.records]
    assert hits[-1].levelno >= logging.WARNING, "后端缺失必须留痕，不能只在健康态里躺着"


async def test_lifespan_assembles_the_cross_process_backend(monkeypatch):
    server, client = _fake_server()
    monkeypatch.setattr(main.settings, "DEBUG", True, raising=False)
    monkeypatch.setattr(main, "_dp_budget_sync_client", client)
    async with main.lifespan(main.app):
        health = dp_budget_ledger.budget_backend_health()
        assert health["backend"] == "redis", (
            f"走完真实启动路径仍未接上跨进程后端：{health}"
        )
        sid = f"a192d-boot-{uuid.uuid4().hex[:12]}"
        dp_budget_ledger.allocate_sync(sid, 3.0)
        replica_b = DPBudgetLedger()
        replica_b.attach_redis(
            fakeredis.FakeStrictRedis(server=server, decode_responses=True)
        )
        assert replica_b.has_budget_sync(sid) is True
        assert replica_b.get_remaining_sync(sid) == pytest.approx(3.0)


def test_lifespan_source_wires_the_backend():
    src = inspect.getsource(getattr(main.lifespan, "__wrapped__", main.lifespan))
    assert "dp_budget_backend_wiring()" in src, (
        "A192d: attach_redis 必须由应用装配路径调用，否则真部署里跨副本预算不可见"
    )
