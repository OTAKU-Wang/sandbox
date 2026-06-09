"""Tests for Task State Machine — lifecycle transitions."""
import pytest
from app.services.task_state_machine import (
    TaskStateMachine, TaskStatus, TransitionResult,
    ACTIVE_STATES, TERMINAL_STATES,
)


@pytest.fixture
def sm():
    return TaskStateMachine()


# ── Valid Transitions ─────────────────────────────────────────────

class TestValidTransitions:
    def test_queued_to_code_scanning(self, sm):
        result = sm.transition(TaskStatus.QUEUED, TaskStatus.CODE_SCANNING)
        assert result.success is True

    def test_code_scanning_to_preparing(self, sm):
        result = sm.transition(TaskStatus.CODE_SCANNING, TaskStatus.PREPARING)
        assert result.success is True

    def test_preparing_to_running(self, sm):
        result = sm.transition(TaskStatus.PREPARING, TaskStatus.RUNNING)
        assert result.success is True

    def test_running_to_output_inspecting(self, sm):
        result = sm.transition(TaskStatus.RUNNING, TaskStatus.OUTPUT_INSPECTING)
        assert result.success is True

    def test_output_inspecting_to_completed(self, sm):
        result = sm.transition(TaskStatus.OUTPUT_INSPECTING, TaskStatus.COMPLETED)
        assert result.success is True

    def test_full_happy_path(self, sm):
        """Full lifecycle: QUEUED → COMPLETED."""
        states = [
            TaskStatus.QUEUED,
            TaskStatus.CODE_SCANNING,
            TaskStatus.PREPARING,
            TaskStatus.RUNNING,
            TaskStatus.OUTPUT_INSPECTING,
            TaskStatus.COMPLETED,
        ]
        for i in range(len(states) - 1):
            result = sm.transition(states[i], states[i + 1])
            assert result.success is True, f"Failed: {states[i].value} → {states[i+1].value}"


# ── Error Transitions ─────────────────────────────────────────────

class TestErrorTransitions:
    def test_queued_to_cancelled(self, sm):
        result = sm.transition(TaskStatus.QUEUED, TaskStatus.CANCELLED)
        assert result.success is True

    def test_code_scanning_to_rejected(self, sm):
        result = sm.transition(TaskStatus.CODE_SCANNING, TaskStatus.REJECTED)
        assert result.success is True

    def test_code_scanning_to_failed(self, sm):
        result = sm.transition(TaskStatus.CODE_SCANNING, TaskStatus.FAILED)
        assert result.success is True

    def test_running_to_failed(self, sm):
        result = sm.transition(TaskStatus.RUNNING, TaskStatus.FAILED)
        assert result.success is True

    def test_output_inspecting_to_rejected(self, sm):
        result = sm.transition(TaskStatus.OUTPUT_INSPECTING, TaskStatus.REJECTED)
        assert result.success is True

    def test_preparing_to_cancelled(self, sm):
        result = sm.transition(TaskStatus.PREPARING, TaskStatus.CANCELLED)
        assert result.success is True


# ── Invalid Transitions ───────────────────────────────────────────

class TestInvalidTransitions:
    def test_queued_to_running(self, sm):
        result = sm.transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
        assert result.success is False
        assert "Invalid transition" in result.error

    def test_completed_to_any(self, sm):
        """Completed is terminal — no transitions allowed."""
        for target in TaskStatus:
            if target == TaskStatus.COMPLETED:
                continue
            result = sm.transition(TaskStatus.COMPLETED, target)
            assert result.success is False

    def test_failed_to_any(self, sm):
        """Failed is terminal — no transitions allowed."""
        for target in TaskStatus:
            if target == TaskStatus.FAILED:
                continue
            result = sm.transition(TaskStatus.FAILED, target)
            assert result.success is False

    def test_cancelled_to_any(self, sm):
        """Cancelled is terminal — no transitions allowed."""
        for target in TaskStatus:
            if target == TaskStatus.CANCELLED:
                continue
            result = sm.transition(TaskStatus.CANCELLED, target)
            assert result.success is False

    def test_rejected_to_any(self, sm):
        """Rejected is terminal — no transitions allowed."""
        for target in TaskStatus:
            if target == TaskStatus.REJECTED:
                continue
            result = sm.transition(TaskStatus.REJECTED, target)
            assert result.success is False

    def test_running_to_completed(self, sm):
        """Must go through OUTPUT_INSPECTING first."""
        result = sm.transition(TaskStatus.RUNNING, TaskStatus.COMPLETED)
        assert result.success is False

    def test_code_scanning_to_running(self, sm):
        """Must go through PREPARING first."""
        result = sm.transition(TaskStatus.CODE_SCANNING, TaskStatus.RUNNING)
        assert result.success is False


# ── Validation ────────────────────────────────────────────────────

class TestValidation:
    def test_validate_transition_returns_timestamp(self, sm):
        result = sm.validate_transition(TaskStatus.QUEUED, TaskStatus.CODE_SCANNING)
        assert result.timestamp is not None

    def test_validate_transition_error_message(self, sm):
        result = sm.validate_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
        assert "Invalid transition" in result.error
        assert "completed" in result.error


# ── Allowed Transitions ───────────────────────────────────────────

class TestAllowedTransitions:
    def test_queued_allowed(self, sm):
        allowed = sm.get_allowed_transitions(TaskStatus.QUEUED)
        assert allowed == {TaskStatus.CODE_SCANNING, TaskStatus.CANCELLED}

    def test_code_scanning_allowed(self, sm):
        allowed = sm.get_allowed_transitions(TaskStatus.CODE_SCANNING)
        assert TaskStatus.PREPARING in allowed
        assert TaskStatus.REJECTED in allowed
        assert TaskStatus.FAILED in allowed

    def test_completed_allowed(self, sm):
        allowed = sm.get_allowed_transitions(TaskStatus.COMPLETED)
        assert len(allowed) == 0

    def test_all_states_have_entries(self, sm):
        for status in TaskStatus:
            allowed = sm.get_allowed_transitions(status)
            assert isinstance(allowed, set)


# ── Terminal/Active States ────────────────────────────────────────

class TestStateClassification:
    def test_terminal_states(self, sm):
        for status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED]:
            assert sm.is_terminal(status) is True
            assert sm.is_active(status) is False

    def test_active_states(self, sm):
        for status in [TaskStatus.QUEUED, TaskStatus.CODE_SCANNING, TaskStatus.PREPARING,
                       TaskStatus.RUNNING, TaskStatus.OUTPUT_INSPECTING]:
            assert sm.is_active(status) is True
            assert sm.is_terminal(status) is False

    def test_terminal_active_mutually_exclusive(self):
        assert TERMINAL_STATES.isdisjoint(ACTIVE_STATES)
        assert TERMINAL_STATES | ACTIVE_STATES == set(TaskStatus)


# ── Descriptions ──────────────────────────────────────────────────

class TestDescriptions:
    def test_all_states_have_descriptions(self, sm):
        for status in TaskStatus:
            desc = sm.get_state_description(status)
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_queued_description(self, sm):
        desc = sm.get_state_description(TaskStatus.QUEUED)
        assert "queued" in desc.lower()


# ── Progress ──────────────────────────────────────────────────────

class TestProgress:
    def test_queued_is_zero(self, sm):
        assert sm.get_progress_percentage(TaskStatus.QUEUED) == 0

    def test_completed_is_100(self, sm):
        assert sm.get_progress_percentage(TaskStatus.COMPLETED) == 100

    def test_progress_increases(self, sm):
        states = [
            TaskStatus.QUEUED,
            TaskStatus.CODE_SCANNING,
            TaskStatus.PREPARING,
            TaskStatus.RUNNING,
            TaskStatus.OUTPUT_INSPECTING,
            TaskStatus.COMPLETED,
        ]
        percentages = [sm.get_progress_percentage(s) for s in states]
        assert percentages == sorted(percentages)

    def test_error_states_zero(self, sm):
        for status in [TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED]:
            assert sm.get_progress_percentage(status) == 0


# ── TransitionResult ──────────────────────────────────────────────

class TestTransitionResult:
    def test_result_fields(self, sm):
        result = sm.transition(TaskStatus.QUEUED, TaskStatus.CODE_SCANNING)
        assert isinstance(result, TransitionResult)
        assert result.from_status == TaskStatus.QUEUED
        assert result.to_status == TaskStatus.CODE_SCANNING
        assert result.error is None

    def test_error_result_fields(self, sm):
        result = sm.transition(TaskStatus.COMPLETED, TaskStatus.RUNNING)
        assert result.success is False
        assert result.error is not None
