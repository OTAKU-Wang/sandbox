"""A192 ③：DP 预算的同步快路径必须是**跨进程真预算**，取不到真预算要显式失败。

HEAD 上 `has_budget_sync()` / `get_remaining_sync()` 只读进程内 `_cache`/`_loaded`
（注释自认 "survive the request, not a restart"）。后果有两个，都是「伪造预算」：
1) 另一个副本（或重启后的本进程）看不见已分配的 ε ⇒ `output_inspection` 走
   「无预算旁路」⇒ 不记账、无限次 `dp_applied=True`；两个副本还能各花一份 ε；
2) 没有哪个读数能告诉调用方「这个数字只是本进程的猜测」。

判据：接上跨进程后端（本机用 fakeredis，绝不联网）后，预算对多副本可见且**只有一份**；
后端读不到时必须显式失败并披露，绝不回落到「看起来有预算」，也不把进程内旧数当权威。
"""
import uuid

import fakeredis
import pytest

from app.services.dp_budget import DPBudgetLedger


def _sid() -> str:
    return f"sb-{uuid.uuid4().hex[:12]}"


def _pair():
    """两个 ledger = 两个 API 副本，共享同一个 Redis（fakeredis 同 server）。"""
    server = fakeredis.FakeServer()
    replicas = []
    for _ in range(2):
        led = DPBudgetLedger()
        led.attach_redis(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
        replicas.append(led)
    return replicas


class _BrokenRedis:
    """后端在线但读/写全失败（连接断、鉴权失败、集群降级都算这一类）。"""

    def get(self, key):
        raise ConnectionError("redis is down")

    exists = get
    set = get
    ping = get

    def eval(self, script, numkeys, *args):
        raise ConnectionError("redis is down")


def test_ledger_exposes_a_cross_process_backend_seam():
    led = DPBudgetLedger()
    assert hasattr(led, "attach_redis"), "没有跨进程后端的接缝：ε 预算只能是进程内的"
    health = led.budget_backend_health()
    assert health["backend"] == "in-process"
    # 进程内模式必须是**披露**的降级，而不是一行注释
    assert health["authoritative"] is False, health


def test_budget_allocated_on_one_replica_is_visible_on_another():
    a, b = _pair()
    sid = _sid()
    a.allocate_sync(sid, 5.0)

    assert b.has_budget_sync(sid) is True, (
        "第二个副本看不见预算 ⇒ output_inspection 走无预算旁路，不记账无限次加噪"
    )
    assert b.get_remaining_sync(sid) == pytest.approx(5.0)


def test_two_replicas_cannot_over_spend_one_epsilon_budget():
    a, b = _pair()
    sid = _sid()
    a.allocate_sync(sid, 5.0)

    assert a.consume_sync(sid, 3.0) is True
    assert b.consume_sync(sid, 3.0) is False, (
        "两个副本各扣各的缓存 = 一份 5.0 的预算花掉 6.0（伪造预算）"
    )
    assert b.get_remaining_sync(sid) == pytest.approx(2.0)
    assert a.get_remaining_sync(sid) == pytest.approx(2.0)


def test_unreadable_backend_does_not_open_the_no_budget_bypass():
    led = DPBudgetLedger()
    led.attach_redis(_BrokenRedis())
    sid = _sid()

    state = led.budget_state_sync(sid)
    assert state["source"] == "unverified", state
    assert state["authoritative"] is False, state
    # tracked=True 是有意的：读不到真预算时不能把会话当成「没有预算」放行，
    # 必须让它走记账路径，由 consume_sync 显式拒绝。
    assert state["tracked"] is True, state
    assert led.consume_sync(sid, 1.0) is False, "后端读不到却扣减成功 = 假记账"

    health = led.budget_backend_health()
    assert health["backend"] == "redis", health
    assert health["redis_errors"] >= 1, health
    assert health["last_error"], health


def test_stale_process_cache_is_not_returned_as_authoritative():
    server = fakeredis.FakeServer()
    led = DPBudgetLedger()
    led.attach_redis(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
    sid = _sid()
    led.allocate_sync(sid, 5.0)
    assert led.get_remaining_sync(sid) == pytest.approx(5.0)

    led.attach_redis(_BrokenRedis())
    assert led.get_remaining_sync(sid) == 0.0, (
        "后端不可读时把进程内旧数当权威回读 = 伪造预算"
    )
    assert led.budget_state_sync(sid)["source"] == "unverified"


def test_allocation_that_never_reaches_the_backend_fails_loudly():
    led = DPBudgetLedger()
    led.attach_redis(_BrokenRedis())
    with pytest.raises(Exception) as exc:
        led.allocate_sync(_sid(), 5.0)
    assert "backend" in str(exc.value).lower() or "redis" in str(exc.value).lower()
    assert led.budget_backend_health()["redis_errors"] >= 1


def test_other_replica_inspection_cannot_take_the_free_dp_bypass(monkeypatch):
    """端到端：预算在副本 A 上分配，检查跑在副本 B ⇒ 必须走记账路径并被预算拦住。

    HEAD 上 B 看不见预算，`dp_applied=True` + `dp_budget_remaining=None`（无限次免费 DP）。
    """
    from app.services import dp_budget as dp_budget_module
    from app.services import output_inspection as oi

    a, b = _pair()
    sid = _sid()
    a.allocate_sync(sid, 1.0)

    monkeypatch.setattr(dp_budget_module, "dp_budget_ledger", b)
    monkeypatch.setattr(oi, "dp_budget_ledger", b)

    result = oi.OutputInspector().inspect(
        "统计", "u1", sid, dp_epsilon=5.0, output_rows=[{"amount": 10}]
    )
    assert result.dp_applied is False, "预算只有 1.0 却报 DP 已应用 = 谎报 + 未记账"
    assert result.stage_results["differential_privacy"] is False
    assert any(f.type == "dp_budget_exhausted" for f in result.findings), result.findings
