"""Tests for session state machine — validates all transition rules."""
import pytest
from app.models.sandbox_session import SessionStatus
from app.services.session_state_machine import (
    session_state_machine, SessionState, TransitionResult,
    VALID_TRANSITIONS, ACTIVE_STATES, TERMINAL_STATES,
)


class TestSessionStateAlias:
    def test_session_state_is_session_status(self):
        assert SessionState is SessionStatus


class TestValidTransitions:
    def test_pending_to_provisioning(self):
        r = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.PROVISIONING)
        assert r.success

    def test_pending_to_key_distributing(self):
        r = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.KEY_DISTRIBUTING)
        assert r.success

    def test_pending_to_failed(self):
        r = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.FAILED)
        assert r.success

    def test_pending_to_terminated(self):
        r = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.TERMINATED)
        assert r.success

    def test_provisioning_to_ready(self):
        r = session_state_machine.validate_transition(SessionStatus.PROVISIONING, SessionStatus.READY)
        assert r.success

    def test_provisioning_to_running(self):
        r = session_state_machine.validate_transition(SessionStatus.PROVISIONING, SessionStatus.RUNNING)
        assert r.success

    def test_ready_to_running(self):
        r = session_state_machine.validate_transition(SessionStatus.READY, SessionStatus.RUNNING)
        assert r.success

    def test_ready_to_suspended(self):
        r = session_state_machine.validate_transition(SessionStatus.READY, SessionStatus.SUSPENDED)
        assert r.success

    def test_running_to_completed(self):
        r = session_state_machine.validate_transition(SessionStatus.RUNNING, SessionStatus.COMPLETED)
        assert r.success

    def test_running_to_suspended(self):
        r = session_state_machine.validate_transition(SessionStatus.RUNNING, SessionStatus.SUSPENDED)
        assert r.success

    def test_suspended_to_ready(self):
        r = session_state_machine.validate_transition(SessionStatus.SUSPENDED, SessionStatus.READY)
        assert r.success

    def test_suspended_to_running(self):
        r = session_state_machine.validate_transition(SessionStatus.SUSPENDED, SessionStatus.RUNNING)
        assert r.success

    def test_suspended_to_terminated(self):
        r = session_state_machine.validate_transition(SessionStatus.SUSPENDED, SessionStatus.TERMINATED)
        assert r.success

    def test_key_distributing_to_provisioning(self):
        r = session_state_machine.validate_transition(SessionStatus.KEY_DISTRIBUTING, SessionStatus.PROVISIONING)
        assert r.success


class TestInvalidTransitions:
    def test_completed_is_terminal(self):
        for target in SessionStatus:
            r = session_state_machine.validate_transition(SessionStatus.COMPLETED, target)
            assert not r.success, f"COMPLETED → {target.value} should be invalid"

    def test_failed_is_terminal(self):
        for target in SessionStatus:
            r = session_state_machine.validate_transition(SessionStatus.FAILED, target)
            assert not r.success, f"FAILED → {target.value} should be invalid"

    def test_terminated_is_terminal(self):
        for target in SessionStatus:
            r = session_state_machine.validate_transition(SessionStatus.TERMINATED, target)
            assert not r.success, f"TERMINATED → {target.value} should be invalid"

    def test_revoked_is_terminal(self):
        for target in SessionStatus:
            r = session_state_machine.validate_transition(SessionStatus.REVOKED, target)
            assert not r.success, f"REVOKED → {target.value} should be invalid"

    def test_pending_to_running_invalid(self):
        r = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.RUNNING)
        assert not r.success

    def test_pending_to_completed_invalid(self):
        r = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.COMPLETED)
        assert not r.success

    def test_running_to_pending_invalid(self):
        r = session_state_machine.validate_transition(SessionStatus.RUNNING, SessionStatus.PENDING)
        assert not r.success

    def test_provisioning_to_pending_invalid(self):
        r = session_state_machine.validate_transition(SessionStatus.PROVISIONING, SessionStatus.PENDING)
        assert not r.success


class TestRequireTransition:
    def test_require_transition_valid(self):
        # Should not raise
        session_state_machine.require_transition(SessionStatus.PENDING, SessionStatus.PROVISIONING)

    def test_require_transition_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid session transition"):
            session_state_machine.require_transition(SessionStatus.COMPLETED, SessionStatus.RUNNING)


class TestTerminalAndActive:
    @pytest.mark.parametrize("state", [
        SessionStatus.COMPLETED, SessionStatus.FAILED,
        SessionStatus.TERMINATED, SessionStatus.REVOKED,
    ])
    def test_terminal_states(self, state):
        assert session_state_machine.is_terminal(state)
        assert not session_state_machine.is_active(state)

    @pytest.mark.parametrize("state", [
        SessionStatus.PENDING, SessionStatus.KEY_DISTRIBUTING,
        SessionStatus.PROVISIONING, SessionStatus.READY,
        SessionStatus.RUNNING, SessionStatus.SUSPENDED,
    ])
    def test_active_states(self, state):
        assert session_state_machine.is_active(state)
        assert not session_state_machine.is_terminal(state)


class TestAllowedTransitions:
    def test_get_allowed_from_pending(self):
        allowed = session_state_machine.get_allowed_transitions(SessionStatus.PENDING)
        assert SessionStatus.PROVISIONING in allowed
        assert SessionStatus.FAILED in allowed
        assert SessionStatus.TERMINATED in allowed
        assert SessionStatus.RUNNING not in allowed

    def test_get_allowed_from_terminal_returns_empty(self):
        for state in TERMINAL_STATES:
            allowed = session_state_machine.get_allowed_transitions(state)
            assert allowed == set()


class TestStateDescription:
    def test_all_states_have_descriptions(self):
        for state in SessionStatus:
            desc = session_state_machine.get_state_description(state)
            assert desc and desc != "Unknown status", f"Missing description for {state.value}"

    def test_description_content(self):
        assert "waiting" in session_state_machine.get_state_description(SessionStatus.PENDING).lower()
        assert "executing" in session_state_machine.get_state_description(SessionStatus.RUNNING).lower()


class TestTransitionResult:
    def test_success_result(self):
        r = session_state_machine.validate_transition(SessionStatus.PENDING, SessionStatus.PROVISIONING)
        assert isinstance(r, TransitionResult)
        assert r.success
        assert r.from_state == SessionStatus.PENDING
        assert r.to_state == SessionStatus.PROVISIONING
        assert r.error is None
        assert r.timestamp is not None

    def test_failure_result(self):
        r = session_state_machine.validate_transition(SessionStatus.COMPLETED, SessionStatus.RUNNING)
        assert not r.success
        assert r.error is not None
        assert "Invalid session transition" in r.error
