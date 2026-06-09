"""Tests for Quota Manager (Redis Lua atomic counters)."""
import uuid
import pytest

from app.services.quota_manager import (
    QuotaManager, QuotaType, QuotaCheckResult,
)


@pytest.fixture
def manager():
    return QuotaManager(redis_client=None)  # Uses local fallback


class TestQuotaManagerLocal:
    @pytest.mark.asyncio
    async def test_increment_within_limit(self, manager):
        session_id = uuid.uuid4()
        result = await manager.check_and_increment(
            session_id, QuotaType.ROWS, amount=100, limit=1000
        )
        assert result.allowed is True
        assert result.current == 100
        assert result.remaining == 900

    @pytest.mark.asyncio
    async def test_increment_exceeds_limit(self, manager):
        session_id = uuid.uuid4()
        result = await manager.check_and_increment(
            session_id, QuotaType.ROWS, amount=1500, limit=1000
        )
        assert result.allowed is False
        assert result.current == 0  # Not incremented

    @pytest.mark.asyncio
    async def test_increment_accumulates(self, manager):
        session_id = uuid.uuid4()
        await manager.check_and_increment(session_id, QuotaType.BYTES, amount=500, limit=1000)
        result = await manager.check_and_increment(
            session_id, QuotaType.BYTES, amount=400, limit=1000
        )
        assert result.allowed is True
        assert result.current == 900
        assert result.remaining == 100

    @pytest.mark.asyncio
    async def test_increment_at_exact_limit(self, manager):
        session_id = uuid.uuid4()
        await manager.check_and_increment(session_id, QuotaType.API_CALLS, amount=999, limit=1000)
        result = await manager.check_and_increment(
            session_id, QuotaType.API_CALLS, amount=1, limit=1000
        )
        assert result.allowed is True
        assert result.current == 1000
        assert result.remaining == 0

    @pytest.mark.asyncio
    async def test_zero_limit_means_unlimited(self, manager):
        session_id = uuid.uuid4()
        result = await manager.check_and_increment(
            session_id, QuotaType.GPU_SECONDS, amount=999999, limit=0
        )
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_get_usage(self, manager):
        session_id = uuid.uuid4()
        await manager.check_and_increment(session_id, QuotaType.ROWS, amount=42, limit=1000)
        usage = await manager.get_usage(session_id, QuotaType.ROWS)
        assert usage == 42

    @pytest.mark.asyncio
    async def test_get_usage_empty(self, manager):
        session_id = uuid.uuid4()
        usage = await manager.get_usage(session_id, QuotaType.ROWS)
        assert usage == 0

    @pytest.mark.asyncio
    async def test_reset(self, manager):
        session_id = uuid.uuid4()
        await manager.check_and_increment(session_id, QuotaType.ROWS, amount=500, limit=1000)
        await manager.check_and_increment(session_id, QuotaType.BYTES, amount=200, limit=500)
        await manager.reset(session_id)
        assert await manager.get_usage(session_id, QuotaType.ROWS) == 0
        assert await manager.get_usage(session_id, QuotaType.BYTES) == 0

    @pytest.mark.asyncio
    async def test_reset_prefills_contract_limits_locally(self, manager):
        session_id = uuid.uuid4()
        await manager.reset(session_id, contract_limits={
            "max_output_rows": 25,
            "dp_epsilon_budget": 1.5,
            "max_duration_seconds": 60,
            "max_requests_per_second": 7,
        })

        assert await manager.get_limit(session_id, QuotaType.ROWS) == 25
        assert await manager.get_limit(session_id, QuotaType.DP_EPSILON) == 15000
        assert await manager.get_limit(session_id, QuotaType.DURATION_SECONDS) == 60
        assert await manager.get_limit(session_id, QuotaType.API_CALLS) == 7

    @pytest.mark.asyncio
    async def test_separate_sessions_independent(self, manager):
        s1 = uuid.uuid4()
        s2 = uuid.uuid4()
        await manager.check_and_increment(s1, QuotaType.ROWS, amount=500, limit=1000)
        result = await manager.check_and_increment(s2, QuotaType.ROWS, amount=600, limit=1000)
        assert result.allowed is True
        assert result.current == 600  # Independent of s1

    @pytest.mark.asyncio
    async def test_batch_check_all_pass(self, manager):
        session_id = uuid.uuid4()
        quotas = {
            QuotaType.ROWS: (100, 1000),
            QuotaType.BYTES: (200, 500),
        }
        results = await manager.check_batch(session_id, quotas)
        assert results[QuotaType.ROWS].allowed is True
        assert results[QuotaType.BYTES].allowed is True
        # Verify consumed
        assert await manager.get_usage(session_id, QuotaType.ROWS) == 100
        assert await manager.get_usage(session_id, QuotaType.BYTES) == 200

    @pytest.mark.asyncio
    async def test_batch_check_one_fails_none_consumed(self, manager):
        session_id = uuid.uuid4()
        quotas = {
            QuotaType.ROWS: (100, 1000),
            QuotaType.BYTES: (600, 500),  # Would exceed
        }
        results = await manager.check_batch(session_id, quotas)
        assert results[QuotaType.ROWS].allowed is True  # Individually ok
        assert results[QuotaType.BYTES].allowed is False
        # Verify NOTHING was consumed (atomic rollback)
        assert await manager.get_usage(session_id, QuotaType.ROWS) == 0
        assert await manager.get_usage(session_id, QuotaType.BYTES) == 0


class TestGatewayQuota:
    """Tests for gateway daily quota (no Redis = always allow)."""

    @pytest.fixture
    def manager(self):
        return QuotaManager(redis_client=None)

    @pytest.mark.asyncio
    async def test_gateway_quota_no_redis_enforces_local_limit(self, manager):
        """Without Redis, gateway quota uses the single-process local fallback."""
        allowed, reason = await manager.check_gateway_quota(
            "app-1", rows_increment=1000, rows_limit=500
        )
        assert allowed is False
        assert "exceeded" in reason

    @pytest.mark.asyncio
    async def test_gateway_quota_no_redis_bytes(self, manager):
        allowed, reason = await manager.check_gateway_quota(
            "app-2", bytes_increment=999999, bytes_limit=100
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_gateway_usage_no_redis_returns_local_usage(self, manager):
        await manager.check_gateway_quota("app-3", rows_increment=2, bytes_increment=10)
        usage = await manager.get_gateway_usage("app-3")
        assert usage == {"rows": 2, "bytes": 10}

    @pytest.mark.asyncio
    async def test_gateway_quota_zero_increment_skip(self, manager):
        """Zero increment should not trigger quota check."""
        allowed, reason = await manager.check_gateway_quota(
            "app-4", rows_increment=0, rows_limit=100
        )
        assert allowed is True

    def test_gateway_key_format(self, manager):
        """Gateway key includes app_id and date."""
        key = manager._gateway_key("app-abc", "rows")
        assert "gwquota:app-abc:" in key
        assert ":rows" in key

    def test_gateway_key_daily_partition(self, manager):
        """Keys include UTC date for daily partitioning."""
        key = manager._gateway_key("app-x", "bytes")
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        assert today in key
