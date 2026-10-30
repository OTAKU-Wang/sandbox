"""A146: 网关配额——读不到用量当「没用量」，被拒的请求照样计数，Redis 抖动永久转本地。

三条各自可测：
① 用量读失败不能再被当成 0 用量放行（gateway_service.check_quota 必须 fail-closed）；
② 超限被拒的请求不得抬高日计数器（Redis 与本地回退两条路径都要「先校后加」）；
③ 转本地计数后不得永久停留：Redis 恢复时把本地累计的增量回写，并把降级事件计数暴露出来。
"""
import uuid

import fakeredis.aioredis
import pytest

from app.services.gateway_service import AppCredential, GatewayService
from app.services.quota_manager import QuotaManager


def _unavailable_cls():
    from app.services.quota_manager import QuotaBackendUnavailable

    return QuotaBackendUnavailable


class _BrokenRedis:
    """可调用但每次都抛错的 Redis 替身（连接层抖动的形态）。"""

    def pipeline(self):
        raise ConnectionError("redis is down")

    async def eval(self, *args, **kwargs):
        raise ConnectionError("redis is down")

    async def mget(self, *args, **kwargs):
        raise ConnectionError("redis is down")

    async def get(self, *args, **kwargs):
        raise ConnectionError("redis is down")


class _FlakyRedis:
    """先坏后好的 Redis 替身：用于验证恢复后的回写。"""

    def __init__(self, inner):
        self.inner = inner
        self.broken = True

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if self.broken:
            raise ConnectionError("redis is down")
        return attr

    def pipeline(self):
        if self.broken:
            raise ConnectionError("redis is down")
        return self.inner.pipeline()


@pytest.fixture()
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_denied_request_does_not_inflate_daily_counter(redis):
    manager = QuotaManager(redis_client=redis)

    for _ in range(2):
        allowed, _reason = await manager.check_gateway_quota(
            "app-1", rows_increment=4, rows_limit=10
        )
        assert allowed is True

    allowed, reason = await manager.check_gateway_quota("app-1", rows_increment=4, rows_limit=10)
    assert allowed is False
    assert "exceeded" in reason

    usage = await manager.get_gateway_usage("app-1")
    assert usage["rows"] == 8, f"被拒的 4 行也被计数了：{usage}"


@pytest.mark.asyncio
async def test_denied_request_does_not_inflate_local_fallback_counter():
    manager = QuotaManager(redis_client=None)

    for _ in range(2):
        allowed, _reason = await manager.check_gateway_quota(
            "app-2", rows_increment=4, rows_limit=10
        )
        assert allowed is True

    allowed, _reason = await manager.check_gateway_quota("app-2", rows_increment=4, rows_limit=10)
    assert allowed is False

    usage = await manager.get_gateway_usage("app-2")
    assert usage["rows"] == 8, f"本地回退把被拒的请求也计了：{usage}"


@pytest.mark.asyncio
async def test_usage_read_failure_is_not_reported_as_zero_usage(redis):
    manager = QuotaManager(redis_client=_BrokenRedis())

    with pytest.raises(_unavailable_cls()):
        await manager.get_gateway_usage("app-3", strict=True)

    degraded = await manager.get_gateway_usage("app-3")
    assert degraded["unavailable"] is True


@pytest.mark.asyncio
async def test_gateway_check_quota_fails_closed_when_usage_unreadable(monkeypatch):
    from app.services import gateway_service as gs

    monkeypatch.setattr(gs.quota_manager, "get_gateway_usage", _raise_unavailable)

    allowed, reason = await GatewayService().check_quota(None, _cred("app-4"))

    assert allowed is False, "用量读不出来却当 0 用量放行，配额门在 Redis 抖动时静默消失"
    assert "unverifiable" in reason


@pytest.mark.asyncio
async def test_gateway_check_quota_still_allows_healthy_backend(redis, monkeypatch):
    from app.services import gateway_service as gs

    monkeypatch.setattr(gs.quota_manager, "_redis", redis)

    allowed, reason = await GatewayService().check_quota(None, _cred("app-4b"))

    assert allowed is True, reason


def _cred(app_id: str) -> AppCredential:
    return AppCredential(
        app_id=app_id,
        app_secret_hash="deadbeef",
        contract_id=uuid.uuid4(),
        consumer_id=uuid.uuid4(),
        status="active",
        rate_limit=60,
        quota_rows=1000,
        quota_bytes=1_000_000,
    )


async def _raise_unavailable(app_id, *, strict=False):
    raise _unavailable_cls()("usage backend unavailable")


@pytest.mark.asyncio
async def test_local_deltas_replayed_after_redis_recovers(redis):
    flaky = _FlakyRedis(redis)
    manager = QuotaManager(redis_client=flaky)

    allowed, _reason = await manager.check_gateway_quota("app-5", rows_increment=3, rows_limit=100)
    assert allowed is True
    health = manager.quota_health()
    assert health["local_fallback_events"] == 1
    assert health["pending_deltas"] == 1

    flaky.broken = False
    allowed, _reason = await manager.check_gateway_quota("app-5", rows_increment=2, rows_limit=100)
    assert allowed is True

    usage = await manager.get_gateway_usage("app-5")
    assert usage["rows"] == 5, f"降级期间的 3 行没回写：{usage}"
    assert manager.quota_health()["pending_deltas"] == 0
