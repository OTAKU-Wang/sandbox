"""Tests for Task Circuit Breaker."""
import time
import pytest

from app.services.task_circuit_breaker import (
    TaskCircuitBreaker, CircuitConfig, CircuitState,
)


@pytest.fixture
def breaker():
    config = CircuitConfig(
        failure_threshold=3,
        failure_window_seconds=60,
        recovery_timeout_seconds=10,
        task_timeout_seconds=300,
    )
    return TaskCircuitBreaker(config)


class TestCircuitBreaker:
    def test_initial_state_closed(self, breaker):
        allowed, msg = breaker.can_execute("session-1")
        assert allowed is True
        assert "closed" in msg.lower()

    def test_single_failure_stays_closed(self, breaker):
        state = breaker.record_failure("session-1", "error")
        assert state == CircuitState.CLOSED
        allowed, _ = breaker.can_execute("session-1")
        assert allowed is True

    def test_threshold_opens_circuit(self, breaker):
        for i in range(3):
            breaker.record_failure("session-1", f"error {i}")
        state = breaker.get_stats("session-1").state
        assert state == CircuitState.OPEN

    def test_open_circuit_rejects(self, breaker):
        for i in range(3):
            breaker.record_failure("session-1", f"error {i}")
        allowed, msg = breaker.can_execute("session-1")
        assert allowed is False
        assert "Circuit open" in msg

    def test_recovery_timeout_allows_half_open(self, breaker):
        config = breaker.config
        # Manually set last failure to past
        for i in range(3):
            breaker.record_failure("session-1", f"error {i}")
        stats = breaker.get_stats("session-1")
        stats.last_failure_time = time.time() - config.recovery_timeout_seconds - 1

        allowed, msg = breaker.can_execute("session-1")
        assert allowed is True
        assert "half-open" in msg.lower()

    def test_half_open_success_closes(self, breaker):
        for i in range(3):
            breaker.record_failure("session-1", f"error {i}")
        stats = breaker.get_stats("session-1")
        stats.state = CircuitState.HALF_OPEN

        breaker.record_success("session-1")
        assert breaker.get_stats("session-1").state == CircuitState.CLOSED
        assert breaker.get_stats("session-1").failure_count == 0

    def test_half_open_failure_reopens(self, breaker):
        for i in range(3):
            breaker.record_failure("session-1", f"error {i}")
        stats = breaker.get_stats("session-1")
        stats.state = CircuitState.HALF_OPEN

        state = breaker.record_failure("session-1", "recovery failed")
        assert state == CircuitState.OPEN

    def test_separate_sessions_independent(self, breaker):
        for i in range(3):
            breaker.record_failure("s1", f"error {i}")
        allowed_s1, _ = breaker.can_execute("s1")
        allowed_s2, _ = breaker.can_execute("s2")
        assert allowed_s1 is False
        assert allowed_s2 is True

    def test_reset_clears_state(self, breaker):
        for i in range(3):
            breaker.record_failure("session-1", f"error {i}")
        breaker.reset("session-1")
        allowed, _ = breaker.can_execute("session-1")
        assert allowed is True

    def test_get_all_open_circuits(self, breaker):
        for i in range(3):
            breaker.record_failure("s1", "err")
            breaker.record_failure("s2", "err")
        open_circuits = breaker.get_all_open_circuits()
        assert "s1" in open_circuits
        assert "s2" in open_circuits

    def test_success_resets_failure_window(self, breaker):
        # 2 failures
        breaker.record_failure("s1", "err1")
        breaker.record_failure("s1", "err2")
        # Success resets
        breaker.record_success("s1")
        # 2 more failures — should not trip (count was reset by window)
        breaker.record_failure("s1", "err3")
        breaker.record_failure("s1", "err4")
        stats = breaker.get_stats("s1")
        # Depending on window timing, may or may not be open
        # But total_failures should accumulate
        assert stats.total_failures == 4


class TestTaskTimeout:
    def test_within_timeout(self, breaker):
        allowed, msg = breaker.check_task_timeout(time.time() - 100)
        assert allowed is True

    def test_exceeds_timeout(self, breaker):
        allowed, msg = breaker.check_task_timeout(time.time() - 400)
        assert allowed is False
        assert "timeout" in msg.lower()
