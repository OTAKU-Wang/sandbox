"""Real-time Alert Rule Engine — monitors security events and triggers alerts.

Alert Rules:
1. Consecutive Rejection: Too many output inspections rejected in a window
2. DP Budget Exhaustion: Differential privacy budget running low
3. Anomaly Detection: Unusual access patterns (burst queries, off-hours access)

Each rule evaluates events against thresholds and emits Alert objects.

A149（潜伏缺陷波修复，模块仍未接线——接不接由人拍板）：
- evaluate() 增加去重冷却（RuleConfig.cooldown_minutes，默认 5 分钟）：轮询循环
  不再把窗口内旧事件每轮重报一遍；
- 夜间访问判定改用事件自身时间戳（旧实现看 evaluate 调用时刻的墙钟，白天评估
  必漏报夜间行为），近窗从 config.window_minutes 取（旧实现硬编码 5 分钟）；
- 事件表有界化：record 时裁剪超过 event_retention_minutes 的旧事件并遵守
  max_events 上限（旧实现单例列表无界增长）；
- 可变共享状态加 RLock（record/evaluate/clear 复合操作）；
- record_* 接受可选 event_time（naive 视为 UTC）——摄取管道可携带真实事件时间，
  测试可回填历史；
- DP 快照披露 snapshot_age_seconds；check_dp_budget 非数值入参收口为 None+警告。
"""
import logging
import threading
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
    # A149: re-fire suppression for the same (type, session, user) — polling loops
    # used to re-emit identical alerts on every evaluate() call.
    cooldown_minutes: int = 5


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

    def __init__(
        self,
        configs: dict[AlertType, RuleConfig] | None = None,
        event_retention_minutes: int = 60,
        max_events: int = 10000,
    ):
        self._configs = configs if configs is not None else dict(self.DEFAULT_CONFIGS)
        # A149: the old singleton grew its event lists without bound and had no
        # locking around record/evaluate/clear compound mutations.
        self._lock = threading.RLock()
        self._event_retention = timedelta(minutes=event_retention_minutes)
        self._max_events = max_events
        # Event tracking
        self._rejection_events: list[tuple[datetime, str, str | None]] = []  # (ts, session_id, user_id)
        self._query_events: list[tuple[datetime, str | None]] = []  # (ts, user_id)
        self._dp_budget_snapshots: dict[str, tuple[datetime, float, float, str | None]] = {}
        self._fired_alerts: list[Alert] = []
        self._last_fired: dict[tuple, datetime] = {}

    @staticmethod
    def _norm_ts(ts: datetime | None) -> datetime:
        """A149: event timestamps are compared against aware cutoffs — normalize."""
        if ts is None:
            return datetime.now(timezone.utc)
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

    def _prune(self) -> None:
        """Drop events outside the retention window; cap list sizes (A149)."""
        cutoff = datetime.now(timezone.utc) - self._event_retention
        self._rejection_events = [e for e in self._rejection_events if e[0] >= cutoff][-self._max_events:]
        self._query_events = [e for e in self._query_events if e[0] >= cutoff][-self._max_events:]

    def configure(self, alert_type: AlertType, config: RuleConfig) -> None:
        """Update configuration for a specific rule."""
        with self._lock:
            self._configs[alert_type] = config

    def record_rejection(
        self, session_id: str, user_id: str | None = None,
        event_time: datetime | None = None,
    ) -> None:
        """Record an output inspection rejection event (A149: event_time honored)."""
        with self._lock:
            self._rejection_events.append((self._norm_ts(event_time), session_id, user_id))
            self._prune()

    def record_query(self, user_id: str | None = None, event_time: datetime | None = None) -> None:
        """Record a data query event (A149: event_time honored)."""
        with self._lock:
            self._query_events.append((self._norm_ts(event_time), user_id))
            self._prune()

    def record_dp_budget(
        self,
        session_id: str,
        remaining_epsilon: float,
        total_epsilon: float,
        user_id: str | None = None,
    ) -> None:
        """Record the latest DP budget snapshot for automatic evaluation."""
        with self._lock:
            self._dp_budget_snapshots[session_id] = (
                datetime.now(timezone.utc),
                float(remaining_epsilon),
                float(total_epsilon),
                user_id,
            )
            self._prune_dp_snapshots()

    def _prune_dp_snapshots(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._event_retention
        for sid in [s for s, (ts, *_rest) in self._dp_budget_snapshots.items() if ts < cutoff]:
            del self._dp_budget_snapshots[sid]

    def evaluate(self) -> list[Alert]:
        """Evaluate all rules and return new alerts.

        Returns:
            List of newly triggered alerts (not previously fired)
        """
        new_alerts: list[Alert] = []

        with self._lock:
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

            # A149: dedup cooldown — evaluate() is a polling entry point; without
            # suppression every poll re-emitted the still-in-window condition as
            # fresh alerts and _fired_alerts grew unbounded with poll frequency.
            now = datetime.now(timezone.utc)
            suppressed: list[Alert] = []
            fresh: list[Alert] = []
            for alert in new_alerts:
                key = (alert.alert_type, alert.session_id, alert.user_id)
                config = self._configs.get(alert.alert_type)
                cooldown = getattr(config, "cooldown_minutes", 0) or 0
                last = self._last_fired.get(key)
                if cooldown > 0 and last is not None and (now - last) < timedelta(minutes=cooldown):
                    suppressed.append(alert)
                    continue
                self._last_fired[key] = now
                fresh.append(alert)
            self._fired_alerts.extend(fresh)
            return fresh

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

        Uses the latest snapshots recorded with ``record_dp_budget``. A149: the
        snapshot timestamp was recorded but discarded ("_"), so stale snapshots
        re-alerted forever with no freshness disclosure.
        """
        alerts: list[Alert] = []
        now = datetime.now(timezone.utc)
        for session_id, (ts, remaining, total, user_id) in self._dp_budget_snapshots.items():
            alert = self._build_dp_budget_alert(
                session_id, remaining, total, config, user_id=user_id,
                extra_metadata={"snapshot_age_seconds": round((now - ts).total_seconds(), 3)},
            )
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

        # A149: non-numeric input used to raise TypeError straight at the caller.
        try:
            remaining_epsilon = float(remaining_epsilon)
            total_epsilon = float(total_epsilon)
        except (TypeError, ValueError):
            logger.warning(
                "alert_engine: check_dp_budget got non-numeric epsilon "
                f"(remaining={remaining_epsilon!r}, total={total_epsilon!r}) — no alert (A149)"
            )
            return None

        return self._build_dp_budget_alert(session_id, remaining_epsilon, total_epsilon, config)

    def _build_dp_budget_alert(
        self,
        session_id: str,
        remaining_epsilon: float,
        total_epsilon: float,
        config: RuleConfig,
        user_id: str | None = None,
        extra_metadata: dict | None = None,
    ) -> Alert | None:
        if total_epsilon <= 0:
            return None

        ratio = remaining_epsilon / total_epsilon
        if ratio > config.threshold:
            return None

        metadata = {"remaining_epsilon": remaining_epsilon, "total_epsilon": total_epsilon, "ratio": ratio}
        if extra_metadata:
            metadata.update(extra_metadata)
        return Alert(
            alert_type=AlertType.DP_BUDGET_EXHAUSTION,
            severity=AlertSeverity.CRITICAL if ratio <= 0.01 else AlertSeverity.HIGH,
            message=f"DP budget critically low: {remaining_epsilon:.2f}/{total_epsilon:.2f} ({ratio:.1%})",
            user_id=user_id,
            session_id=session_id,
            metadata=metadata,
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
        """Check for off-hours access.

        A149: the old implementation looked at the wall-clock hour of the
        *evaluate()* call (daytime polls silently missed last night's behavior)
        and hard-coded a 5-minute lookback ignoring config.window_minutes.
        Now every query event's own timestamp decides, within the configured window
        (window_minutes <= 0 → no window bound, matching the rule's default).
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=config.window_minutes) if config.window_minutes > 0 else None

        alerts = []
        seen_users: set = set()
        for ts, uid in list(self._query_events):
            if cutoff is not None and ts < cutoff:
                continue
            hour = ts.astimezone(timezone.utc).hour
            if not (hour >= self.OFF_HOURS_START or hour < self.OFF_HOURS_END):
                continue
            if uid not in seen_users:
                seen_users.add(uid)
                alerts.append(Alert(
                    alert_type=AlertType.ANOMALY_OFF_HOURS,
                    severity=AlertSeverity.MEDIUM,
                    message=(
                        f"Off-hours access by user {uid or 'unknown'} at "
                        f"{ts.astimezone(timezone.utc).strftime('%H:%M')} UTC (event time, A149)"
                    ),
                    user_id=uid,
                    metadata={"hour": hour, "event_time": ts.isoformat()},
                ))
        return alerts

    def get_fired_alerts(self) -> list[Alert]:
        """Return all previously fired alerts."""
        return list(self._fired_alerts)

    def clear_fired(self) -> None:
        """Clear fired alerts history (and the cooldown ledger with it)."""
        with self._lock:
            self._fired_alerts.clear()
            self._last_fired.clear()

    def clear_events(self) -> None:
        """Clear all recorded events."""
        with self._lock:
            self._rejection_events.clear()
            self._query_events.clear()
            self._dp_budget_snapshots.clear()


# Singleton
alert_engine = AlertRuleEngine()
