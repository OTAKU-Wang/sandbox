"""A108/A109 (ocr CR 2026-10-18): 资源限额 fail-open 与租户配额的非原子记账。

A108: get_resource_limits 返回共享字典本体（一次就地写污染全进程默认值）；
validate_resource_limits 对 0/负数原样采纳、对字符串在 min() 里抛 TypeError
（用户可稳定把会话创建打成 500）。修复 = 返回深拷贝 + 三个键强制正整数
（非正/非 int 直接 422 VALIDATION_ERROR）。

A109: 配额写入是「整快照 HSET 覆盖」——多副本后写者把别人的累加抹掉；
_persist_tenant_quota 的 quota is None 分支执行破坏性 redis.delete；
release 的 max(0,…) 静默截断把账目错误藏起来。修复 = 按字段 HINCRBY 增量
（多副本叠加）、删除分支去掉、截断打警告、并提供检查即预留的原子原语
try_reserve_tenant_usage（Redis Lua，回退进程内临界区）。
"""
import logging
import uuid

import pytest
import fakeredis.aioredis

from app.core.errors import CDSError
from app.services import sandbox_manager
from app.services.sandbox_manager import (
    RESOURCE_LIMITS,
    TENANT_QUOTA_KEY_PREFIX,
    _persist_tenant_quota,
    check_tenant_quota,
    flush_quota_persistence,
    get_resource_limits,
    get_tenant_quota,
    release_tenant_usage,
    update_tenant_usage,
    validate_resource_limits,
)

try:  # TEMP-RED: 修复后删除本块，恢复严格导入
    from app.services.sandbox_manager import try_reserve_tenant_usage
except ImportError:  # pragma: no cover
    try_reserve_tenant_usage = None

pytestmark = pytest.mark.asyncio


# ── A108: 资源限额校验 ──────────────────────────────────────────────


class TestGetResourceLimitsCopy:
    def test_returns_copy_not_shared_dict(self):
        limits = get_resource_limits("L3")
        assert limits == RESOURCE_LIMITS["L3"]
        assert limits is not RESOURCE_LIMITS["L3"]

    def test_mutating_returned_dict_cannot_poison_defaults(self):
        original = RESOURCE_LIMITS["L3"]["cpu_cores"]
        try:
            limits = get_resource_limits("L3")
            limits["cpu_cores"] = 9999
            assert RESOURCE_LIMITS["L3"]["cpu_cores"] == original
            assert get_resource_limits("L3")["cpu_cores"] == original
        finally:
            RESOURCE_LIMITS["L3"]["cpu_cores"] = original

    def test_validate_none_does_not_return_shared_dict(self):
        limits = validate_resource_limits("L3", None)
        assert limits == RESOURCE_LIMITS["L3"]
        assert limits is not RESOURCE_LIMITS["L3"]


class TestValidateResourceLimitsPositiveInt:
    @pytest.mark.parametrize("bad", [0, -1, "8", 1.5, True, None])
    def test_non_positive_or_non_int_rejected_422(self, bad):
        with pytest.raises(CDSError) as exc:
            validate_resource_limits("L1", {"cpu_cores": bad})
        err = exc.value
        assert err.http_status == 422
        assert err.code == "VALIDATION_ERROR"
        assert "cpu_cores" in err.message or "cpu_cores" in str(err.detail)

    def test_zero_on_any_key_rejected(self):
        for key in ("cpu_cores", "memory_mb", "disk_mb"):
            with pytest.raises(CDSError):
                validate_resource_limits("L1", {key: 0})

    def test_legit_narrowing_and_clamping_keep_working(self):
        """收紧不误伤合法路径：范围内保留、超上限钳制、None 走默认。"""
        limits = validate_resource_limits("L2", {"cpu_cores": 1, "memory_mb": 256})
        assert limits["cpu_cores"] == 1 and limits["memory_mb"] == 256
        clamped = validate_resource_limits("L3", {"cpu_cores": 100, "memory_mb": 99999})
        assert clamped["cpu_cores"] == RESOURCE_LIMITS["L3"]["cpu_cores"]
        assert clamped["memory_mb"] == RESOURCE_LIMITS["L3"]["memory_mb"]
        none = validate_resource_limits("L1", None)
        assert none == RESOURCE_LIMITS["L1"]

    def test_unknown_keys_do_not_bypass_validation(self):
        """未识别键照旧被忽略，不得借别名塞进 effective。"""
        limits = validate_resource_limits("L3", {"gpu_count": 9999, "memory_mb": 128})
        assert limits["memory_mb"] == 128
        assert "gpu_count" not in limits


# ── A109: 租户配额原子记账 ─────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _fake_redis_and_clean_state(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "_redis_client", fake)
    sandbox_manager._tenant_quotas.clear()
    sandbox_manager._pending_deltas.clear()
    yield fake
    sandbox_manager._tenant_quotas.clear()
    sandbox_manager._pending_deltas.clear()


class TestDeltaBasedPersistence:
    async def test_cross_replica_updates_compose_not_clobber(
        self, _fake_redis_and_clean_state,
    ):
        """副本 B 的写入必须叠加到副本 A 的累计上，而不是整快照覆盖。"""
        update_tenant_usage("tenant-a", cpu_cores=2, memory_mb=1024, disk_mb=4096)
        await flush_quota_persistence()

        # 模拟另一副本：本地 dict 是启动时的旧状态（这里为空），它只知道自己 +5
        sandbox_manager._tenant_quotas.clear()
        update_tenant_usage("tenant-a", cpu_cores=5, memory_mb=2048, disk_mb=8192)
        await flush_quota_persistence()

        stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-a")
        assert stored["used_cpu_cores"] == "7"
        assert stored["used_memory_mb"] == "3072"
        assert stored["used_disk_mb"] == "12288"

    async def test_failed_flush_keeps_delta_for_retry(self, _fake_redis_and_clean_state, monkeypatch):
        """Redis 抖动不得把增量弄丢：失败保留 pending，恢复后补写。"""
        import app.core.redis as redis_module

        async def broken(*a, **k):
            raise ConnectionError("redis down")

        monkeypatch.setattr(redis_module, "get_redis", broken)
        update_tenant_usage("tenant-b", cpu_cores=3)
        await flush_quota_persistence()  # 不得抛出

        stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-b")
        assert stored.get("used_cpu_cores") is None  # 当时确实没写进去

        monkeypatch.undo()  # 恢复 fake redis
        await flush_quota_persistence()
        stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-b")
        assert stored["used_cpu_cores"] == "3"

    async def test_persist_of_missing_local_entry_never_deletes_redis_state(
        self, _fake_redis_and_clean_state,
    ):
        """A109 地雷拆除：本地无此租户条目时，绝对快照写入必须是无操作，
        而不是把多副本累计出来的 Redis 用量删掉。"""
        key = TENANT_QUOTA_KEY_PREFIX + "tenant-c"
        await _fake_redis_and_clean_state.hset(key, mapping={"used_cpu_cores": "6"})
        sandbox_manager._tenant_quotas.pop("tenant-c", None)

        await _persist_tenant_quota("tenant-c")

        stored = await _fake_redis_and_clean_state.hgetall(key)
        assert stored.get("used_cpu_cores") == "6"


class TestHonestRelease:
    async def test_over_release_is_clamped_and_logged(
        self, _fake_redis_and_clean_state, caplog,
    ):
        update_tenant_usage("tenant-d", cpu_cores=1, memory_mb=0, disk_mb=0)
        await flush_quota_persistence()

        with caplog.at_level(logging.WARNING, logger="app.services.sandbox_manager"):
            release_tenant_usage("tenant-d", cpu_cores=5, memory_mb=0, disk_mb=0)
        quota = get_tenant_quota("tenant-d")
        assert quota["used_cpu_cores"] == 0
        assert any(
            "used_cpu_cores" in r.getMessage()
            for r in caplog.records if r.levelno == logging.WARNING
        ), "静默截断必须变成可见警告（账目错误要可发现）"

    async def test_normal_release_still_subtracts(self, _fake_redis_and_clean_state):
        update_tenant_usage("tenant-e", cpu_cores=4, memory_mb=1024, disk_mb=2048)
        release_tenant_usage("tenant-e", cpu_cores=4, memory_mb=1024, disk_mb=2048)
        await flush_quota_persistence()
        stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-e")
        assert stored["used_cpu_cores"] == "0"
        assert stored["used_memory_mb"] == "0"


class TestTryReserve:
    async def test_reserve_allows_within_quota_and_applies_everywhere(
        self, _fake_redis_and_clean_state,
    ):
        allowed, reason = await try_reserve_tenant_usage("tenant-f", cpu_cores=2, memory_mb=1024, disk_mb=2048)
        assert allowed is True and reason == ""
        assert get_tenant_quota("tenant-f")["used_cpu_cores"] == 2
        stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-f")
        assert stored["used_cpu_cores"] == "2"

    async def test_reserve_atomic_across_replicas_rejects_second(
        self, _fake_redis_and_clean_state,
    ):
        """并发两副本同时预留最后一份 ⇒ 恰好一个成功、超额者零副作用。"""
        maxes = sandbox_manager.TENANT_DEFAULTS
        cpu = maxes["max_cpu_cores"]  # 全部额度一次拿走
        first, _ = await try_reserve_tenant_usage("tenant-g", cpu_cores=cpu, memory_mb=0, disk_mb=0)
        assert first is True

        # 模拟副本 B：本地 dict 全新（看不到 A 的预留），走同一 Redis 原子预留
        sandbox_manager._tenant_quotas.clear()
        ok, reason = await try_reserve_tenant_usage("tenant-g", cpu_cores=1, memory_mb=0, disk_mb=0)
        assert ok is False
        assert "CPU" in reason or "cpu" in reason.lower()

        stored = await _fake_redis_and_clean_state.hgetall(TENANT_QUOTA_KEY_PREFIX + "tenant-g")
        assert stored["used_cpu_cores"] == str(cpu)  # 被拒者回滚，没留下增量

    async def test_reserve_rejected_locally_never_touches_redis(
        self, _fake_redis_and_clean_state,
    ):
        allowed, reason = await try_reserve_tenant_usage(
            "tenant-h", cpu_cores=10_000, memory_mb=0, disk_mb=0,
        )
        assert allowed is False
        assert reason
        exists = await _fake_redis_and_clean_state.exists(TENANT_QUOTA_KEY_PREFIX + "tenant-h")
        assert not exists

    async def test_reserve_falls_back_to_in_process_when_redis_down(
        self, _fake_redis_and_clean_state, monkeypatch,
    ):
        """Redis 不可用时不静默放行：退化为进程内原子预留并告警。"""
        import app.core.redis as redis_module

        async def broken(*a, **k):
            raise ConnectionError("redis down")

        monkeypatch.setattr(redis_module, "get_redis", broken)
        maxes = sandbox_manager.TENANT_DEFAULTS

        ok1, _ = await try_reserve_tenant_usage("tenant-i", cpu_cores=maxes["max_cpu_cores"], memory_mb=0, disk_mb=0)
        assert ok1 is True
        ok2, reason2 = await try_reserve_tenant_usage("tenant-i", cpu_cores=1, memory_mb=0, disk_mb=0)
        assert ok2 is False and reason2
