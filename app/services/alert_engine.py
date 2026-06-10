"""Real-time Alert Rule Engine — monitors security events and triggers alerts.

Alert Rules:
1. Consecutive Rejection: Too many output inspections rejected in a window
2. DP Budget Exhaustion: Differential privacy budget running low
3. Anomaly Detection: Unusual access patterns (burst queries, off-hours access)

Each rule evaluates events against thresholds and emits Alert objects.
"""
import logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertType(str, Enum):
    CONSECUTIVE_REJECTION = "consecutive_rejection"
    DP_BUDGET_EXHAUSTION = "dp_budget_exhaustion"
    ANOMALY_BURST = "anomaly_burst"
    ANOMALY_OFF_HOURS = "anomaly_off_hours"


@dataclass
class Alert:
    """A triggered alert."""
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    user_id: str | None = None
    session_id: str | None = None
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RuleConfig:
    """Configuration for an alert rule."""
    enabled: bool = True
    threshold: int | float = 0
    window_minutes: int = 10


class AlertRuleEngine:
    """Evaluates security events against alert rules.

    Usage:
        engine = AlertRuleEngine()
        engine.record_rejection("user-1", "session-1")
        alerts = engine.evaluate()
    """

    DEFAULT_CONFIGS = {
        AlertType.CONSECUTIVE_REJECTION: RuleConfig(enabled=True, threshold=3, window_minutes=10),
        AlertType.DP_BUDGET_EXHAUSTION: RuleConfig(enabled=True, threshold=0.1, window_minutes=0),
        AlertType.ANOMALY_BURST: RuleConfig(enabled=True, threshold=50, window_minutes=5),
        AlertType.ANOMALY_OFF_HOURS: RuleConfig(enabled=True, threshold=1, window_minutes=0),
    }

    # Off-hours: 22:00 - 06:00 UTC
    OFF_HOURS_START = 22
    OFF_HOURS_END = 6

    def __init__(self, configs: dict[AlertType, RuleConfig] | None = None):
        self._configs = configs if configs is not None else dict(self.DEFAULT_CONFIGS)
        # Event tracking
        self._rejection_events: list[tuple[datetime, str, str | None]] = []  # (ts, session_id, user_id)
        self._query_events: list[tuple[datetime, str | None]] = []  # (ts, user_id)
        self._dp_budget_snapshots: dict[str, tuple[datetime, float, float, str | None]] = {}
        self._fired_alerts: list[Alert] = []

    def configure(self, alert_type: AlertType, config: RuleConfig) -> None:
        """Update configuration for a specific rule."""
        self._configs[alert_type] = config

    def record_rejection(self, session_id: str, user_id: str | None = None) -> None:
        """Record an output inspection rejection event."""
        self._rejection_events.append((datetime.now(timezone.utc), session_id, user_id))

    def record_query(self, user_id: str | None = None) -> None:
        """Record a data query event."""
        self._query_events.append((datetime.now(timezone.utc), user_id))

    def record_dp_budget(
        self,
        session_id: str,
        remaining_epsilon: float,
        total_epsilon: float,
        user_id: str | None = None,
    ) -> None:
        """Record the latest DP budget snapshot for automatic evaluation."""
        self._dp_budget_snapshots[session_id] = (
            datetime.now(timezone.utc),
            float(remaining_epsilon),
            float(total_epsilon),
            user_id,
        )

    def evaluate(self) -> list[Alert]:
        """Evaluate all rules and return new alerts.

        Returns:
            List of newly triggered alerts (not previously fired)
        """
        new_alerts: list[Alert] = []

        for alert_type, config in self._configs.items():
            if not config.enabled:
                continue

            if alert_type == AlertType.CONSECUTIVE_REJECTION:
                alerts = self._check_consecutive_rejection(config)
            elif alert_type == AlertType.DP_BUDGET_EXHAUSTION:
                alerts = self._check_dp_exhaustion(config)
            elif alert_type == AlertType.ANOMALY_BURST:
                alerts = self._check_anomaly_burst(config)
            elif alert_type == AlertType.ANOMALY_OFF_HOURS:
                alerts = self._check_off_hours(config)
            else:
                continue

            new_alerts.extend(alerts)

        self._fired_alerts.extend(new_alerts)
        return new_alerts

    def _check_consecutive_rejection(self, config: RuleConfig) -> list[Alert]:
        """Check for too many rejections in the time window."""
        if not self._rejection_events:
            return []

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=config.window_minutes)
        recent = [(ts, sid, uid) for ts, sid, uid in self._rejection_events if ts >= cutoff]

        if len(recent) < config.threshold:
            return []

        # Group by session to find which sessions are being rejected
        by_session: dict[str, list] = {}
        for ts, sid, uid in recent:
            by_session.setdefault(sid, []).append((ts, uid))

        alerts = []
        for sid, events in by_session.items():
            if len(events) >= config.threshold:
                user_id = events[0][1]  # uid from first event
                alerts.append(Alert(
                    alert_type=AlertType.CONSECUTIVE_REJECTION,
                    severity=AlertSeverity.HIGH,
                    message=f"Session {sid} had {len(events)} consecutive rejections within {config.window_minutes}min",
                    user_id=user_id,
                    session_id=sid,
                    metadata={"rejection_count": len(events), "window_minutes": config.window_minutes},
                ))
        return alerts

    def _check_dp_exhaustion(self, config: RuleConfig) -> list[Alert]:
        """Check if DP budget is below threshold.

        Uses the latest snapshots recorded with ``record_dp_budget``.
        """
        alerts: list[Alert] = []
        for session_id, (_, remaining, total, user_id) in self._dp_budget_snapshots.items():
            alert = self._build_dp_budget_alert(session_id, remaining, total, config, user_id=user_id)
            if alert:
                alerts.append(alert)
        return alerts

    def check_dp_budget(self, session_id: str, remaining_epsilon: float, total_epsilon: float) -> Alert | None:
        """Check if a session's DP budget is critically low.

        Args:
            session_id: Session to check
            remaining_epsilon: Remaining DP epsilon
            total_epsilon: Total allocated epsilon

        Returns:
            Alert if budget is below threshold, None otherwise
        """
        config = self._configs.get(AlertType.DP_BUDGET_EXHAUSTION)
        if not config or not config.enabled:
            return None

        return self._build_dp_budget_alert(session_id, remaining_epsilon, total_epsilon, config)

    def _build_dp_budget_alert(
        self,
        session_id: str,
        remaining_epsilon: float,
        total_epsilon: float,
        config: RuleConfig,
        user_id: str | None = None,
    ) -> Alert | None:
        if total_epsilon <= 0:
            return None

        ratio = remaining_epsilon / total_epsilon
        if ratio > config.threshold:
            return None

        return Alert(
            alert_type=AlertType.DP_BUDGET_EXHAUSTION,
            severity=AlertSeverity.CRITICAL if ratio <= 0.01 else AlertSeverity.HIGH,
            message=f"DP budget critically low: {remaining_epsilon:.2f}/{total_epsilon:.2f} ({ratio:.1%})",
            user_id=user_id,
            session_id=session_id,
            metadata={"remaining_epsilon": remaining_epsilon, "total_epsilon": total_epsilon, "ratio": ratio},
        )

    def _check_anomaly_burst(self, config: RuleConfig) -> list[Alert]:
        """Check for burst query patterns."""
        if not self._query_events:
            return []

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=config.window_minutes)

        # Group by user
        by_user: dict[str | None, int] = {}
        for ts, uid in self._query_events:
            if ts >= cutoff:
                by_user[uid] = by_user.get(uid, 0) + 1

        alerts = []
        for uid, count in by_user.items():
            if count >= config.threshold:
                alerts.append(Alert(
                    alert_type=AlertType.ANOMALY_BURST,
                    severity=AlertSeverity.HIGH,
                    message=f"User {uid or 'unknown'} executed {count} queries within {config.window_minutes}min",
                    user_id=uid,
                    metadata={"query_count": count, "window_minutes": config.window_minutes},
                ))
        return alerts

    def _check_off_hours(self, config: RuleConfig) -> list[Alert]:
        """Check for off-hours access."""
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Check if current time is in off-hours window
        is_off_hours = hour >= self.OFF_HOURS_START or hour < self.OFF_HOURS_END
        if not is_off_hours:
            return []

        # Check if there are recent queries
        recent_cutoff = now - timedelta(minutes=5)
        recent_queries = [(ts, uid) for ts, uid in self._query_events if ts >= recent_cutoff]

        if not recent_queries:
            return []

        alerts = []
        seen_users = set()
        for ts, uid in recent_queries:
            if uid not in seen_users:
                seen_users.add(uid)
                alerts.append(Alert(
                    alert_type=AlertType.ANOMALY_OFF_HOURS,
                    severity=AlertSeverity.MEDIUM,
                    message=f"Off-hours access by user {uid or 'unknown'} at {now.strftime('%H:%M')} UTC",
                    user_id=uid,
                    metadata={"hour": hour},
                ))
        return alerts

    def get_fired_alerts(self) -> list[Alert]:
        """Return all previously fired alerts."""
        return list(self._fired_alerts)

    def clear_fired(self) -> None:
        """Clear fired alerts history."""
        self._fired_alerts.clear()

    def clear_events(self) -> None:
        """Clear all recorded events."""
        self._rejection_events.clear()
        self._query_events.clear()
        self._dp_budget_snapshots.clear()


# Singleton
alert_engine = AlertRuleEngine()
