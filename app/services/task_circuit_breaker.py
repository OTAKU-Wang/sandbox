"""Task Circuit Breaker — dual-layer timeout + exception accumulation.

Implements:
1. Task-level timeout: individual task hard limit
2. Session-level exception accumulation: circuit opens after N failures
3. Graceful degradation: suspend session on circuit open
"""
import time
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit tripped, rejecting requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5       # Failures before opening
    failure_window_seconds: int = 300  # Window for counting failures
    recovery_timeout_seconds: int = 60  # Time before half-open
    task_timeout_seconds: int = 3600    # Per-task hard timeout


@dataclass
class CircuitStats:
    """Circuit breaker statistics."""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0
    last_success_time: float = 0
    total_failures: int = 0
    total_successes: int = 0
    circuit_opened_count: int = 0


class TaskCircuitBreaker:
    """Dual-layer circuit breaker for sandbox tasks.

    Layer 1: Task-level timeout — each task has a hard time limit.
    Layer 2: Session-level circuit — opens after accumulating too many failures.
    """

    def __init__(self, config: CircuitConfig | None = None):
        self.config = config or CircuitConfig()
        self._sessions: dict[str, CircuitStats] = {}

    def _get_stats(self, session_id: str) -> CircuitStats:
        if session_id not in self._sessions:
            self._sessions[session_id] = CircuitStats()
        return self._sessions[session_id]

    def can_execute(self, session_id: str) -> tuple[bool, str]:
        """Check if a task can be executed in this session."""
        stats = self._get_stats(session_id)

        if stats.state == CircuitState.CLOSED:
            return True, "Circuit closed — normal operation"

        if stats.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            elapsed = time.time() - stats.last_failure_time
            if elapsed >= self.config.recovery_timeout_seconds:
                stats.state = CircuitState.HALF_OPEN
                return True, "Circuit half-open — testing recovery"
            return False, (
                f"Circuit open — {stats.failure_count} failures in window. "
                f"Recovery in {int(self.config.recovery_timeout_seconds - elapsed)}s"
            )

        if stats.state == CircuitState.HALF_OPEN:
            return True, "Circuit half-open — allowing single test"

        return False, "Unknown circuit state"

    def record_success(self, session_id: str) -> None:
        """Record a successful task execution."""
        stats = self._get_stats(session_id)
        stats.last_success_time = time.time()
        stats.total_successes += 1

        if stats.state == CircuitState.HALF_OPEN:
            # Recovery confirmed
            stats.state = CircuitState.CLOSED
            stats.failure_count = 0
            logger.info(f"Circuit closed for session {session_id} — recovery confirmed")

    def record_failure(self, session_id: str, error: str = "") -> CircuitState:
        """Record a failed task execution. Returns new circuit state."""
        stats = self._get_stats(session_id)
        now = time.time()
        stats.last_failure_time = now
        stats.total_failures += 1

        # Reset count if outside window
        if now - stats.last_failure_time > self.config.failure_window_seconds:
            stats.failure_count = 0

        stats.failure_count += 1

        if stats.state == CircuitState.HALF_OPEN:
            # Failed during recovery → re-open
            stats.state = CircuitState.OPEN
            stats.circuit_opened_count += 1
            logger.warning(f"Circuit re-opened for session {session_id}: {error}")
            return stats.state

        if stats.failure_count >= self.config.failure_threshold:
            stats.state = CircuitState.OPEN
            stats.circuit_opened_count += 1
            logger.warning(
                f"Circuit opened for session {session_id}: "
                f"{stats.failure_count} failures (threshold: {self.config.failure_threshold})"
            )
            return stats.state

        return stats.state

    def check_task_timeout(self, started_at: float) -> tuple[bool, str]:
        """Check if a task has exceeded its timeout."""
        elapsed = time.time() - started_at
        if elapsed > self.config.task_timeout_seconds:
            return False, f"Task timeout: {int(elapsed)}s > {self.config.task_timeout_seconds}s"
        return True, f"Task running: {int(elapsed)}s / {self.config.task_timeout_seconds}s"

    def get_stats(self, session_id: str) -> CircuitStats:
        """Get circuit stats for a session."""
        return self._get_stats(session_id)

    def reset(self, session_id: str) -> None:
        """Reset circuit breaker for a session."""
        self._sessions.pop(session_id, None)

    def get_all_open_circuits(self) -> list[str]:
        """Get all sessions with open circuits."""
        return [
            sid for sid, stats in self._sessions.items()
            if stats.state == CircuitState.OPEN
        ]


# Singleton
task_circuit_breaker = TaskCircuitBreaker()
