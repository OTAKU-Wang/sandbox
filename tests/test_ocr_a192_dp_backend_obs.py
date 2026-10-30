"""OCR A192f / A192g —— 「接上了一个坏后端」既不能谎报权威，也不能只能读开机日志。

A192f：`DPBudgetLedger.attach_redis()` 先 `self._redis = client` 再 `client.ping()`，
ping 失败只记一条错误，坏客户端仍留在后端位上 ⇒ `budget_backend_health()` 继续报
`backend=redis authoritative=True`。ε 预算的「权威」含义是「这份数字跨副本共享」，
而一个连 ping 都没过的客户端从来没有共享过任何东西 —— 谎报权威（口径同 A146/A192d）。
本文件钉住：未通过 ping 的接上 = 非权威，且**不许**因此退回进程内猜测
（退回缓存会重开 A192 ③ 关掉的「无预算 = 免费 DP」旁路，
`tests/test_ocr_a192_dp_budget_cross_process.py` 已钉过，这里再钉一次）。

A192g：`budget_backend_health()` 与启动披露里那份 HSM 后端读数此前只有开机日志一个读者，
运维在运行时没有任何只读面可查（A94/A192d 都只写日志）。这里钉对外端点
`GET /api/v1/monitoring/backend-health`：走同文件既有鉴权依赖、只读、不含任何密钥
（redis 驱动的异常文本会回显整条连接串 ⇒ 必须经 `redact_url`，A38）。
"""
import uuid

import fakeredis
import pytest

from app.core.scopes import check_scope, resolve_group
from app.services.dp_budget import DPBudgetLedger, dp_budget_ledger

HEALTH_PATH = "/api/v1/monitoring/backend-health"


class _UnverifiedRedis:
    """接得上对象、ping 通不过的后端（连接被拒 / 鉴权失败 / 集群降级的等价物）。

    读写也一并失败：真实的坏后端不会「ping 不行但 GET 能过」。
    """

    def ping(self):
        raise ConnectionError("redis:6379 connection refused")

    def get(self, key):
        raise ConnectionError("redis:6379 connection refused")

    def set(self, key, value):
        raise ConnectionError("redis:6379 connection refused")

    exists = get

    def eval(self, script, numkeys, *args):
        raise ConnectionError("redis:6379 connection refused")


class _CredentialEchoingRedis(_UnverifiedRedis):
    """ping 失败并把整条连接串回显出来的驱动异常（A38 的取证对象）。"""

    def ping(self):
        raise ConnectionError(
            "Error connecting to redis://:S3cr3tP4ss@redis.internal:6379/0: Connection refused"
        )


def _fake_ledger(server: fakeredis.FakeServer | None = None):
    server = server or fakeredis.FakeServer()
    led = DPBudgetLedger()
    led.attach_redis(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
    return server, led


# ── A192f: attach 的 ping 门必须决定「权威」，而不是只留一行日志 ──────────────


def test_attach_that_never_passed_ping_is_not_reported_authoritative():
    """A192f 核心：ping 失败的后端不得被 health 报成 authoritative=True。"""
    led = DPBudgetLedger()
    led.attach_redis(_UnverifiedRedis())

    health = led.budget_backend_health()
    assert health["authoritative"] is False, (
        f"接上一个 ping 都没过的后端却仍报权威 = 谎报跨副本可见性：{health}"
    )
    assert health.get("verified") is False, health
    assert health["redis_errors"] >= 1, health
    assert health["last_error"], health


def test_unverified_backend_never_falls_back_to_the_process_guess():
    """不谎报权威 ≠ 退回进程内猜测：读写仍须打到后端并显式失败。

    这一条是把 A192f 的修法与「直接 detach」区分开的地方：detach 之后
    `budget_state_sync` 会报 `source=in-process / tracked=False`，
    `output_inspection` 就重新拿到「无预算 = 免费 DP」旁路（A192 ③ 已关）。
    """
    led = DPBudgetLedger()
    led.attach_redis(_UnverifiedRedis())
    sid = f"a192f-{uuid.uuid4().hex[:12]}"

    state = led.budget_state_sync(sid)
    assert state["source"] == "unverified", state
    assert state["tracked"] is True, "读不到却报没预算 = 重开免费 DP 旁路"
    assert state["authoritative"] is False, state
    assert led.consume_sync(sid, 1.0) is False, state
    with pytest.raises(Exception) as exc:
        led.allocate_sync(sid, 5.0)
    assert "backend" in str(exc.value).lower() or "redis" in str(exc.value).lower()


def test_verified_backend_is_still_authoritative():
    """反向护栏：ping 通过的后端不得被一并染成降级（真后端不能被骗成模拟）。"""
    _server, led = _fake_ledger()
    health = led.budget_backend_health()
    assert health["backend"] == "redis", health
    assert health["authoritative"] is True, health
    assert health.get("verified") is True, health

    sid = f"a192f-ok-{uuid.uuid4().hex[:12]}"
    led.allocate_sync(sid, 4.0)
    assert led.budget_state_sync(sid)["source"] == "redis"
    assert led.get_remaining_sync(sid) == pytest.approx(4.0)


def test_authority_returns_only_after_a_ping_verified_attach():
    """坏 → 好：权威位必须由**这次**接上的、ping 通过的那个客户端决定。"""
    led = DPBudgetLedger()
    led.attach_redis(_UnverifiedRedis())
    assert led.budget_backend_health()["authoritative"] is False

    server = fakeredis.FakeServer()
    led.attach_redis(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
    assert led.budget_backend_health()["authoritative"] is True

    sid = f"a192f-re-{uuid.uuid4().hex[:12]}"
    led.allocate_sync(sid, 2.0)
    replica_b = DPBudgetLedger()
    replica_b.attach_redis(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
    assert replica_b.get_remaining_sync(sid) == pytest.approx(2.0)


def test_detach_reports_in_process_and_non_authoritative():
    led = DPBudgetLedger()
    led.attach_redis(None)
    health = led.budget_backend_health()
    assert health["backend"] == "in-process", health
    assert health["authoritative"] is False, health
    assert health.get("verified") is not True, health


# ── A192g: 运行时只读观测面 ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_singleton_backend():
    """端点读的是进程级单例：进出抹平，不把 fakeredis/坏后端漏给同座次其它测试。"""
    led = dp_budget_ledger
    names = ("_redis", "_backend_probe_at", "_backend_last_error", "_backend_verified_client")
    saved = {name: getattr(led, name, None) for name in names}
    events = dict(led._backend_events)
    led._redis = None
    led._backend_probe_at = None
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(led, name, value)
        led._backend_events.clear()
        led._backend_events.update(events)


# AGENTS.md 钉住「启动披露的 app.state 写入全仓保持 2」＝ main.py 的写入 + monitoring.py
# 的只读出口；测试侧统一走下面的 setattr/getattr/delattr，不再多开一处字面量。
_HSM_STATE_KEY = "hsm_backend"


def _get_hsm_disclosure(main):
    return getattr(main.app.state, _HSM_STATE_KEY, None)


def _set_hsm_disclosure(main, value) -> None:
    setattr(main.app.state, _HSM_STATE_KEY, value)


def _clear_hsm_disclosure(main) -> None:
    """抹掉启动披露位（Starlette 不同版本对缺失属性分别报 AttributeError/KeyError）。"""
    try:
        delattr(main.app.state, _HSM_STATE_KEY)
    except (AttributeError, KeyError):
        pass


async def test_backend_health_endpoint_discloses_both_backends(client, operator_headers):
    """运维在运行时可查：DP 预算后端 + HSM 后端，一个只读 GET。"""
    import app.main as main

    saved_hsm = _get_hsm_disclosure(main)
    _set_hsm_disclosure(main, {"backend": "software-fallback", "is_simulation": True})
    server = fakeredis.FakeServer()
    dp_budget_ledger.attach_redis(
        fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    )
    try:
        resp = await client.get(HEALTH_PATH, headers=operator_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dp_budget_backend"]["backend"] == "redis", data
        assert data["dp_budget_backend"]["authoritative"] is True, data
        assert data["hsm_backend"]["backend"] == "software-fallback", data
        assert data["hsm_backend"]["is_simulation"] is True, data
        assert data["generated_at"], data
    finally:
        if saved_hsm is None:
            _clear_hsm_disclosure(main)
        else:
            _set_hsm_disclosure(main, saved_hsm)


async def test_backend_health_endpoint_reports_missing_disclosure_honestly(
    client, operator_headers
):
    """没有启动披露（未走完整 lifespan）时报 unknown 并按模拟对待，不得 500、不得默认硬件。"""
    import app.main as main

    saved_hsm = _get_hsm_disclosure(main)
    _clear_hsm_disclosure(main)
    try:
        resp = await client.get(HEALTH_PATH, headers=operator_headers)
        assert resp.status_code == 200, resp.text
        hsm = resp.json()["hsm_backend"]
        assert hsm["backend"] == "unknown", hsm
        assert hsm["is_simulation"] is True, hsm
    finally:
        if saved_hsm is not None:
            _set_hsm_disclosure(main, saved_hsm)


async def test_backend_health_endpoint_requires_auth(client):
    """不得新增无鉴权面：照抄同文件既有 `require_roles` 口径。"""
    resp = await client.get(HEALTH_PATH)
    assert resp.status_code in (401, 403), f"未鉴权却返回 {resp.status_code}: {resp.text}"


async def test_backend_health_endpoint_rejects_non_operator_roles(client, make_user):
    headers, _ = await make_user(role="data_provider", prefix="dpobs")
    resp = await client.get(HEALTH_PATH, headers=headers)
    assert resp.status_code == 403, f"数据方角色越权读到后端健康态: {resp.status_code}"


async def test_backend_health_endpoint_is_read_only(client, operator_headers):
    resp = await client.post(HEALTH_PATH, headers=operator_headers)
    assert resp.status_code == 405, f"该端点接受了写方法: {resp.status_code} {resp.text}"


async def test_backend_health_endpoint_never_leaks_credentials(client, operator_headers):
    """redis 驱动异常会回显整条连接串：对外必须抹掉凭据（A38），但保留可排障的错误。"""
    dp_budget_ledger.attach_redis(_CredentialEchoingRedis())
    resp = await client.get(HEALTH_PATH, headers=operator_headers)
    assert resp.status_code == 200, resp.text
    assert "S3cr3tP4ss" not in resp.text, "对外端点回显了连接串里的密码"
    assert "redis" in resp.text, "抹凭据不能把错误本身一起抹掉"


def test_monitoring_prefix_is_already_scope_mapped():
    """A192g 不需要改 `app/core/scopes.py`：/api/v1/monitoring 已在 audit 组内。"""
    group = resolve_group(HEALTH_PATH)
    assert group == "audit", group
    assert check_scope(["audit:read"], group, "read") is True
    assert check_scope(["data:read"], group, "read") is False
