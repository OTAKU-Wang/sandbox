"""Session State Machine — manages sandbox session lifecycle transitions.

Valid states (from SS-03 spec):
    PENDING → KEY_DISTRIBUTING → PROVISIONING → READY → RUNNING → COMPLETED

Error/terminal states:
    FAILED, TERMINATED, REVOKED, SUSPENDED

State transitions are validated to prevent illegal jumps.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.sandbox_session import SessionStatus

logger = logging.getLogger(__name__)

# Re-export for convenience
SessionState = SessionStatus

# Valid state transitions
VALID_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.PENDING: {
        SessionStatus.KEY_DISTRIBUTING,
        SessionStatus.PROVISIONING,
        SessionStatus.FAILED,
        SessionStatus.TERMINATED,
    },
    SessionStatus.KEY_DISTRIBUTING: {
        SessionStatus.PROVISIONING,
        SessionStatus.FAILED,
        SessionStatus.TERMINATED,
    },
    SessionStatus.PROVISIONING: {
        SessionStatus.READY,
        SessionStatus.RUNNING,
        SessionStatus.FAILED,
        SessionStatus.TERMINATED,
    },
    SessionStatus.READY: {
        SessionStatus.RUNNING,
        SessionStatus.FAILED,
        SessionStatus.TERMINATED,
        SessionStatus.SUSPENDED,
    },
    SessionStatus.RUNNING: {
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.TERMINATED,
        SessionStatus.SUSPENDED,
    },
    SessionStatus.SUSPENDED: {
        SessionStatus.READY,
        SessionStatus.RUNNING,
        SessionStatus.TERMINATED,
        SessionStatus.FAILED,
    },
    # Terminal states
    SessionStatus.COMPLETED: set(),
    SessionStatus.FAILED: set(),
    SessionStatus.TERMINATED: set(),
    SessionStatus.REVOKED: set(),
}

# Non-terminal states
ACTIVE_STATES = {
    SessionStatus.PENDING, SessionStatus.KEY_DISTRIBUTING,
    SessionStatus.PROVISIONING, SessionStatus.READY,
    SessionStatus.RUNNING, SessionStatus.SUSPENDED,
}

# Terminal states
TERMINAL_STATES = {
    SessionStatus.COMPLETED, SessionStatus.FAILED,
    SessionStatus.TERMINATED, SessionStatus.REVOKED,
}


@dataclass
class TransitionResult:
    """Result of a state transition attempt."""
    success: bool
    from_state: SessionStatus
    to_state: SessionStatus
    error: str | None = None
    timestamp: datetime | None = None


class SessionStateMachine:
    """Validates and manages session state transitions.

    Uses SessionStatus from app.models.sandbox_session as the canonical enum.

    Usage:
        from app.services.session_state_machine import session_state_machine
        from app.models.sandbox_session import SessionStatus
        result = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.PROVISIONING)
        assert result.success
    """

    def validate_transition(self, current: SessionStatus, target: SessionStatus) -> TransitionResult:
        """Check if a state transition is valid."""
        allowed = VALID_TRANSITIONS.get(current, set())
        if target in allowed:
            return TransitionResult(
                success=True,
                from_state=current,
                to_state=target,
                timestamp=datetime.now(timezone.utc),
            )
        return TransitionResult(
            success=False,
            from_state=current,
            to_state=target,
            error=f"Invalid session transition: {current.value} → {target.value}. "
                  f"Allowed: {[s.value for s in allowed]}",
        )

    def require_transition(self, current: SessionStatus, target: SessionStatus) -> None:
        """Validate a transition and raise ValueError if invalid."""
        result = self.validate_transition(current, target)
        if not result.success:
            raise ValueError(result.error)

    def get_allowed_transitions(self, current: SessionStatus) -> set[SessionStatus]:
        """Get all valid transitions from current state."""
        return VALID_TRANSITIONS.get(current, set())

    def is_terminal(self, status: SessionStatus) -> bool:
        """Check if a status is terminal."""
        return status in TERMINAL_STATES

    def is_active(self, status: SessionStatus) -> bool:
        """Check if a status is active."""
        return status in ACTIVE_STATES

    def get_state_description(self, status: SessionStatus) -> str:
        """Get human-readable description of a session status."""
        descriptions = {
            SessionStatus.PENDING: "Session created, waiting to start",
            SessionStatus.KEY_DISTRIBUTING: "Distributing encryption key to sandbox",
            SessionStatus.PROVISIONING: "Provisioning sandbox container",
            SessionStatus.READY: "Sandbox ready, waiting for task",
            SessionStatus.RUNNING: "Executing task in sandbox",
            SessionStatus.COMPLETED: "Session completed successfully",
            SessionStatus.FAILED: "Session failed due to an error",
            SessionStatus.TERMINATED: "Session terminated by user or system",
            SessionStatus.REVOKED: "Session revoked (key revoked)",
            SessionStatus.SUSPENDED: "Session suspended (resource limit or pause)",
        }
        return descriptions.get(status, "Unknown status")


# Singleton
session_state_machine = SessionStateMachine()
