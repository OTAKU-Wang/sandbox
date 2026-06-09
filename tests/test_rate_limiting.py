"""I5 Rate Limiting tests — TokenBucket + NetworkPolicyEngine rate limit integration."""
import time
import pytest
import asyncio

from app.services.network_policy import (
    _TokenBucket,
    NetworkPolicyEngine,
    NetworkPolicyConfig,
)


class TestTokenBucket:
    """Tests for _TokenBucket rate limiter."""

    def test_allows_within_capacity(self):
        bucket = _TokenBucket(rate=10.0, capacity=10.0)
        # Should allow up to capacity tokens
        for _ in range(10):
            assert bucket.allow(now=0.0) is True

    def test_rejects_over_capacity(self):
        bucket = _TokenBucket(rate=10.0, capacity=5.0)
        for _ in range(5):
            assert bucket.allow(now=0.0) is True
        assert bucket.allow(now=0.0) is False

    def test_refills_over_time(self):
        bucket = _TokenBucket(rate=10.0, capacity=10.0)
        # Exhaust all tokens
        for _ in range(10):
            bucket.allow(now=0.0)
        assert bucket.allow(now=0.0) is False

        # After 0.1s at rate=10, should have 1 token
        assert bucket.allow(now=0.1) is True
        assert bucket.allow(now=0.1) is False

    def test_refill_capped_at_capacity(self):
        bucket = _TokenBucket(rate=10.0, capacity=5.0)
        # Wait a long time (10s at rate=10 = 100 tokens, but capacity=5)
        for _ in range(5):
            assert bucket.allow(now=10.0) is True
        assert bucket.allow(now=10.0) is False

    def test_burst_handling(self):
        bucket = _TokenBucket(rate=1.0, capacity=5.0)
        # Burst of 5
        for _ in range(5):
            assert bucket.allow(now=0.0) is True
        assert bucket.allow(now=0.0) is False
        # After 1s, 1 token refills
        assert bucket.allow(now=1.0) is True

    def test_zero_rate_blocks_after_capacity(self):
        bucket = _TokenBucket(rate=0.0, capacity=3.0)
        for _ in range(3):
            assert bucket.allow(now=0.0) is True
        # No refill with rate=0
        assert bucket.allow(now=100.0) is False

    def test_high_rate_sustained(self):
        bucket = _TokenBucket(rate=1000.0, capacity=1.0)
        # At t=0, capacity=1
        assert bucket.allow(now=0.0) is True
        assert bucket.allow(now=0.0) is False
        # At t=0.001, 1 token refilled
        assert bucket.allow(now=0.001) is True
        # At t=0.002, another token
        assert bucket.allow(now=0.002) is True

    def test_exact_boundary(self):
        bucket = _TokenBucket(rate=10.0, capacity=10.0)
        # Exhaust
        for _ in range(10):
            bucket.allow(now=0.0)
        # At exactly 0.1s, should have exactly 1 token
        assert bucket.allow(now=0.1) is True
        assert bucket.allow(now=0.1) is False

    def test_monotonic_clock_default(self):
        """Test that allow() works with no explicit time (uses monotonic clock)."""
        bucket = _TokenBucket(rate=100.0, capacity=5.0)
        # Should not raise
        result = bucket.allow()
        assert isinstance(result, bool)

    def test_fractional_tokens(self):
        bucket = _TokenBucket(rate=0.5, capacity=1.0)
        # Exhaust
        bucket.allow(now=0.0)
        # After 1s: 0.5 tokens (not enough)
        assert bucket.allow(now=1.0) is False
        # After 2s: 1.0 tokens
        assert bucket.allow(now=2.0) is True


class TestNetworkPolicyRateLimit:
    """Tests for NetworkPolicyEngine.check_rate_limit integration."""

    def _make_engine(self) -> NetworkPolicyEngine:
        return NetworkPolicyEngine()

    def test_no_policy_allows(self):
        engine = self._make_engine()
        assert engine.check_rate_limit("session-1") is True

    def test_zero_max_connections_no_limiter(self):
        engine = self._make_engine()
        config = NetworkPolicyConfig(mode="deny_all", max_connections_per_second=0)
        asyncio.get_event_loop().run_until_complete(
            engine.create_policy("session-2", config)
        )
        # No limiter created, always allow
        assert engine.check_rate_limit("session-2") is True

    def test_rate_limit_created_for_positive_rate(self):
        engine = self._make_engine()
        config = NetworkPolicyConfig(mode="deny_all", max_connections_per_second=10)
        asyncio.get_event_loop().run_until_complete(
            engine.create_policy("session-3", config)
        )
        # Limiter should exist
        assert "session-3" in engine._rate_limiters

    def test_rate_limit_within_bounds(self):
        engine = self._make_engine()
        config = NetworkPolicyConfig(mode="deny_all", max_connections_per_second=100)
        asyncio.get_event_loop().run_until_complete(
            engine.create_policy("session-4", config)
        )
        # Should allow many connections within burst (capacity = rate * 2 = 200)
        for _ in range(200):
            assert engine.check_rate_limit("session-4") is True

    def test_rate_limit_exceeded(self):
        engine = self._make_engine()
        config = NetworkPolicyConfig(mode="deny_all", max_connections_per_second=2)
        asyncio.get_event_loop().run_until_complete(
            engine.create_policy("session-5", config)
        )
        # capacity = 2 * 2 = 4
        for _ in range(4):
            engine.check_rate_limit("session-5")
        assert engine.check_rate_limit("session-5") is False

    def test_rate_limit_session_independence(self):
        engine = self._make_engine()
        config_a = NetworkPolicyConfig(mode="deny_all", max_connections_per_second=1)
        config_b = NetworkPolicyConfig(mode="deny_all", max_connections_per_second=100)
        asyncio.get_event_loop().run_until_complete(
            engine.create_policy("session-a", config_a)
        )
        asyncio.get_event_loop().run_until_complete(
            engine.create_policy("session-b", config_b)
        )
        # Exhaust session-a (capacity=2)
        for _ in range(2):
            engine.check_rate_limit("session-a")
        assert engine.check_rate_limit("session-a") is False
        # session-b should still work
        assert engine.check_rate_limit("session-b") is True

    def test_cleanup_removes_limiter(self):
        engine = self._make_engine()
        config = NetworkPolicyConfig(mode="deny_all", max_connections_per_second=10)
        asyncio.get_event_loop().run_until_complete(
            engine.create_policy("session-cleanup", config)
        )
        assert "session-cleanup" in engine._rate_limiters
        asyncio.get_event_loop().run_until_complete(
            engine.remove_policy("session-cleanup")
        )
        assert "session-cleanup" not in engine._rate_limiters
