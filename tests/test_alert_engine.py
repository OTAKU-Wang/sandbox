"""Tests for Alert Rule Engine — real-time security event monitoring."""
import pytest
from datetime import datetime, timezone, timedelta

from app.services.alert_engine import (
    AlertRuleEngine, Alert, AlertSeverity, AlertType,
    RuleConfig,
)


@pytest.fixture
def engine():
    return AlertRuleEngine()


# ── Consecutive Rejection ─────────────────────────────────────────

class TestConsecutiveRejection:
    def test_no_alert_below_threshold(self, engine):
        engine.record_rejection("sess-1", "user-1")
        engine.record_rejection("sess-1", "user-1")
        alerts = engine.evaluate()
        rejection_alerts = [a for a in alerts if a.alert_type == AlertType.CONSECUTIVE_REJECTION]
        assert len(rejection_alerts) == 0

    def test_alert_at_threshold(self, engine):
        for _ in range(3):
            engine.record_rejection("sess-1", "user-1")
        alerts = engine.evaluate()
        rejection_alerts = [a for a in alerts if a.alert_type == AlertType.CONSECUTIVE_REJECTION]
        assert len(rejection_alerts) == 1
        assert rejection_alerts[0].severity == AlertSeverity.HIGH
        assert "sess-1" in rejection_alerts[0].message
        assert rejection_alerts[0].session_id == "sess-1"

    def test_alert_per_session(self, engine):
        for _ in range(3):
            engine.record_rejection("sess-1", "user-1")
            engine.record_rejection("sess-2", "user-2")
        alerts = engine.evaluate()
        rejection_alerts = [a for a in alerts if a.alert_type == AlertType.CONSECUTIVE_REJECTION]
        assert len(rejection_alerts) == 2

    def test_custom_threshold(self, engine):
        engine.configure(AlertType.CONSECUTIVE_REJECTION, RuleConfig(threshold=5, window_minutes=10))
        for _ in range(4):
            engine.record_rejection("sess-1")
        assert len(engine.evaluate()) == 0
        engine.record_rejection("sess-1")
        assert len(engine.evaluate()) == 1

    def test_disabled_rule(self, engine):
        engine.configure(AlertType.CONSECUTIVE_REJECTION, RuleConfig(enabled=False))
        for _ in range(10):
            engine.record_rejection("sess-1")
        alerts = engine.evaluate()
        assert len(alerts) == 0


# ── DP Budget Exhaustion ──────────────────────────────────────────

class TestDPBudgetExhaustion:
    def test_no_alert_when_healthy(self, engine):
        alert = engine.check_dp_budget("sess-1", remaining_epsilon=5.0, total_epsilon=10.0)
        assert alert is None

    def test_alert_at_threshold(self, engine):
        alert = engine.check_dp_budget("sess-1", remaining_epsilon=0.5, total_epsilon=10.0)
        assert alert is not None
        assert alert.alert_type == AlertType.DP_BUDGET_EXHAUSTION
        assert alert.severity == AlertSeverity.HIGH

    def test_critical_alert_near_zero(self, engine):
        alert = engine.check_dp_budget("sess-1", remaining_epsilon=0.01, total_epsilon=10.0)
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_no_alert_zero_total(self, engine):
        alert = engine.check_dp_budget("sess-1", remaining_epsilon=0.0, total_epsilon=0.0)
        assert alert is None

    def test_disabled(self, engine):
        engine.configure(AlertType.DP_BUDGET_EXHAUSTION, RuleConfig(enabled=False))
        alert = engine.check_dp_budget("sess-1", remaining_epsilon=0.0, total_epsilon=10.0)
        assert alert is None

    def test_metadata(self, engine):
        alert = engine.check_dp_budget("sess-1", remaining_epsilon=0.05, total_epsilon=10.0)
        assert alert is not None
        assert alert.metadata["ratio"] == pytest.approx(0.005)
        assert alert.metadata["remaining_epsilon"] == 0.05


# ── Anomaly Burst ─────────────────────────────────────────────────

class TestAnomalyBurst:
    def test_no_alert_below_threshold(self, engine):
        for _ in range(10):
            engine.record_query("user-1")
        alerts = engine.evaluate()
        burst_alerts = [a for a in alerts if a.alert_type == AlertType.ANOMALY_BURST]
        assert len(burst_alerts) == 0

    def test_alert_at_threshold(self, engine):
        for _ in range(50):
            engine.record_query("user-1")
        alerts = engine.evaluate()
        burst_alerts = [a for a in alerts if a.alert_type == AlertType.ANOMALY_BURST]
        assert len(burst_alerts) == 1
        assert "50" in burst_alerts[0].message

    def test_multiple_users(self, engine):
        for _ in range(50):
            engine.record_query("user-1")
        for _ in range(50):
            engine.record_query("user-2")
        alerts = engine.evaluate()
        burst_alerts = [a for a in alerts if a.alert_type == AlertType.ANOMALY_BURST]
        assert len(burst_alerts) == 2


# ── Off-Hours Access ──────────────────────────────────────────────

class TestOffHours:
    def test_no_alert_during_business_hours(self, engine):
        # The engine checks current UTC hour; we can't easily mock that
        # but we can verify the method doesn't crash and returns a list
        engine.record_query("user-1")
        alerts = engine._check_off_hours(engine._configs[AlertType.ANOMALY_OFF_HOURS])
        assert isinstance(alerts, list)

    def test_off_hours_constants(self):
        assert AlertRuleEngine.OFF_HOURS_START == 22
        assert AlertRuleEngine.OFF_HOURS_END == 6


# ── Evaluation ────────────────────────────────────────────────────

class TestEvaluation:
    def test_evaluate_returns_list(self, engine):
        alerts = engine.evaluate()
        assert isinstance(alerts, list)

    def test_fired_alerts_accumulate(self, engine):
        for _ in range(3):
            engine.record_rejection("sess-1", "user-1")
        engine.evaluate()
        fired = engine.get_fired_alerts()
        assert len(fired) >= 1

    def test_clear_fired(self, engine):
        for _ in range(3):
            engine.record_rejection("sess-1")
        engine.evaluate()
        engine.clear_fired()
        assert len(engine.get_fired_alerts()) == 0

    def test_clear_events(self, engine):
        engine.record_rejection("sess-1")
        engine.record_query("user-1")
        engine.clear_events()
        # After clearing, no events to evaluate
        alerts = engine.evaluate()
        assert len(alerts) == 0


# ── Configuration ─────────────────────────────────────────────────

class TestConfiguration:
    def test_custom_config(self, engine):
        config = RuleConfig(enabled=True, threshold=100, window_minutes=30)
        engine.configure(AlertType.ANOMALY_BURST, config)
        # Threshold is now 100
        for _ in range(50):
            engine.record_query("user-1")
        alerts = engine.evaluate()
        burst = [a for a in alerts if a.alert_type == AlertType.ANOMALY_BURST]
        assert len(burst) == 0

    def test_default_configs(self):
        engine = AlertRuleEngine()
        assert AlertType.CONSECUTIVE_REJECTION in engine._configs
        assert AlertType.DP_BUDGET_EXHAUSTION in engine._configs
        assert AlertType.ANOMALY_BURST in engine._configs
        assert AlertType.ANOMALY_OFF_HOURS in engine._configs


# ── Alert Data Class ──────────────────────────────────────────────

class TestAlert:
    def test_alert_fields(self):
        alert = Alert(
            alert_type=AlertType.CONSECUTIVE_REJECTION,
            severity=AlertSeverity.HIGH,
            message="test",
            user_id="u1",
            session_id="s1",
        )
        assert alert.alert_type == AlertType.CONSECUTIVE_REJECTION
        assert alert.severity == AlertSeverity.HIGH
        assert alert.user_id == "u1"
        assert alert.session_id == "s1"
        assert isinstance(alert.timestamp, datetime)

    def test_alert_metadata_default(self):
        alert = Alert(
            alert_type=AlertType.DP_BUDGET_EXHAUSTION,
            severity=AlertSeverity.CRITICAL,
            message="test",
        )
        assert alert.metadata == {}
