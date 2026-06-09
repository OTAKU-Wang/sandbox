"""Task State Machine — manages sandbox task lifecycle transitions.

Valid states:
    QUEUED → CODE_SCANNING → PREPARING → RUNNING → OUTPUT_INSPECTING → COMPLETED

Error states (can transition from any state):
    FAILED, CANCELLED, REJECTED

State transitions are validated to prevent illegal jumps.
"""
import logging
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Valid task statuses."""
    QUEUED = "queued"
    CODE_SCANNING = "code_scanning"
    PREPARING = "preparing"
    RUNNING = "running"
    OUTPUT_INSPECTING = "output_inspecting"
    COMPLETED = "completed"

    # Terminal error states
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# Valid state transitions
VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.CODE_SCANNING, TaskStatus.CANCELLED},
    TaskStatus.CODE_SCANNING: {TaskStatus.PREPARING, TaskStatus.REJECTED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.PREPARING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.OUTPUT_INSPECTING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.OUTPUT_INSPECTING: {TaskStatus.COMPLETED, TaskStatus.REJECTED, TaskStatus.FAILED},
    TaskStatus.COMPLETED: set(),  # Terminal state
    TaskStatus.FAILED: set(),     # Terminal state
    TaskStatus.CANCELLED: set(),  # Terminal state
    TaskStatus.REJECTED: set(),   # Terminal state
}

# Non-terminal states (task is still active)
ACTIVE_STATES = {TaskStatus.QUEUED, TaskStatus.CODE_SCANNING, TaskStatus.PREPARING,
                 TaskStatus.RUNNING, TaskStatus.OUTPUT_INSPECTING}

# Terminal states
TERMINAL_STATES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED}


@dataclass
class TransitionResult:
    """Result of a state transition attempt."""
    success: bool
    from_status: TaskStatus
    to_status: TaskStatus
    error: str | None = None
    timestamp: datetime | None = None


class TaskStateMachine:
    """Validates and manages task state transitions.

    Usage:
        sm = TaskStateMachine()
        result = sm.transition(TaskStatus.QUEUED, TaskStatus.CODE_SCANNING)
        assert result.success
    """

    def validate_transition(self, current: TaskStatus, target: TaskStatus) -> TransitionResult:
        """Check if a state transition is valid without executing it.

        Args:
            current: Current task status
            target: Desired target status

        Returns:
            TransitionResult with success/failure details
        """
        allowed = VALID_TRANSITIONS.get(current, set())
        if target in allowed:
            return TransitionResult(
                success=True,
                from_status=current,
                to_status=target,
                timestamp=datetime.now(timezone.utc),
            )
        else:
            return TransitionResult(
                success=False,
                from_status=current,
                to_status=target,
                error=f"Invalid transition: {current.value} → {target.value}. "
                      f"Allowed: {[s.value for s in allowed]}",
            )

    def transition(self, current: TaskStatus, target: TaskStatus) -> TransitionResult:
        """Execute a state transition.

        Args:
            current: Current task status
            target: Desired target status

        Returns:
            TransitionResult with success/failure details

        Raises:
            ValueError: If the transition is invalid (for strict mode)
        """
        result = self.validate_transition(current, target)
        if not result.success:
            logger.warning(f"Invalid transition attempted: {current.value} → {target.value}")
        return result

    def get_allowed_transitions(self, current: TaskStatus) -> set[TaskStatus]:
        """Get all valid transitions from current state.

        Args:
            current: Current task status

        Returns:
            Set of allowed target statuses
        """
        return VALID_TRANSITIONS.get(current, set())

    def is_terminal(self, status: TaskStatus) -> bool:
        """Check if a status is terminal (no further transitions)."""
        return status in TERMINAL_STATES

    def is_active(self, status: TaskStatus) -> bool:
        """Check if a status is active (task is still in progress)."""
        return status in ACTIVE_STATES

    def get_state_description(self, status: TaskStatus) -> str:
        """Get human-readable description of a task status."""
        descriptions = {
            TaskStatus.QUEUED: "Task is queued for execution",
            TaskStatus.CODE_SCANNING: "Scanning code for security violations",
            TaskStatus.PREPARING: "Preparing sandbox environment",
            TaskStatus.RUNNING: "Executing task in sandbox",
            TaskStatus.OUTPUT_INSPECTING: "Inspecting output for PII and policy violations",
            TaskStatus.COMPLETED: "Task completed successfully",
            TaskStatus.FAILED: "Task failed due to an error",
            TaskStatus.CANCELLED: "Task was cancelled by user",
            TaskStatus.REJECTED: "Task was rejected (code scan or output inspection failed)",
        }
        return descriptions.get(status, "Unknown status")

    def get_progress_percentage(self, status: TaskStatus) -> int:
        """Get approximate progress percentage for a status."""
        progress = {
            TaskStatus.QUEUED: 0,
            TaskStatus.CODE_SCANNING: 15,
            TaskStatus.PREPARING: 30,
            TaskStatus.RUNNING: 60,
            TaskStatus.OUTPUT_INSPECTING: 85,
            TaskStatus.COMPLETED: 100,
            TaskStatus.FAILED: 0,
            TaskStatus.CANCELLED: 0,
            TaskStatus.REJECTED: 0,
        }
        return progress.get(status, 0)


# Singleton
task_state_machine = TaskStateMachine()
