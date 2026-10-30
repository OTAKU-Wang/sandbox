"""A149 — alert_engine.py 潜伏缺陷钉（三引擎之二；模块不接线）。

分组：
  E1  evaluate() 无去重/冷却：轮询循环每调一次就把窗口内旧事件重报一遍 ⇒ cooldown
  E2  _check_off_hours 用墙钟（datetime.now 的小时）而不是事件时间戳；且硬编码 5 分钟
      近期窗、无视 config.window_minutes ⇒ 逐事件 ts 判定 + 窗从配置
  E3  单例事件表无界增长 ⇒ 保留窗裁剪 + max_events 上限
  E4  可变共享状态无锁（record/evaluate 复合态） ⇒ RLock
  E5  DP 快照时间戳存而不用：陈旧快照每轮重报 ⇒ cooldown 去重 + 快照龄披露
  E6  事件不可回填时间（测试无法造历史事件；也意味着摄取管道只能“到达即事件时间”）
      ⇒ record_* 接受 event_time（naive 视为 UTC）
  E7  check_dp_budget 非数值入参 TypeError 直冲调用方 ⇒ 收口为 None + 警告
"""
import threading
from datetime import datetime, timezone, timedelta

import pytest

from app.services.alert_engine import (
    AlertRuleEngine, AlertType, AlertSeverity, RuleConfig,
)


def _yesterday_off_hours(now=None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=1)).replace(hour=23, minute=30, second=0, microsecond=0)


class TestE1Cooldown:
    def test_repeat_evaluate_suppressed_within_cooldown(self):
        engine = AlertRuleEngine()
        for _ in range(3):
            engine.record_rejection("sess-1", "user-1")
        first = engine.evaluate()
        assert len(first) >= 1
        second = engine.evaluate()
        # 同一 (type, session, user) 在冷却窗内不再重复轰炸
        assert [a for a in second if a.session_id == "sess-1"] == []
        # 但历史仍可查
        assert len(engine.get_fired_alerts()) >= 1

    def test_new_distinct_target_still_fires(self):
        engine = AlertRuleEngine()
        for _ in range(3):
            engine.record_rejection("sess-1", "user-1")
        engine.evaluate()
        for _ in range(3):
            engine.record_rejection("sess-2", "user-2")
        second = engine.evaluate()
        assert any(a.session_id == "sess-2" for a in second)


class TestE2EventTimestampOffHours:
    def test_off_hours_event_alerts_even_in_daytime_eval(self):
        engine = AlertRuleEngine(event_retention_minutes=60 * 26)
        engine.record_query("night-owl", event_time=_yesterday_off_hours())
        alerts = [a for a in engine.evaluate() if a.alert_type == AlertType.ANOMALY_OFF_HOURS]
        # 旧实现看 evaluate 调用时刻的墙钟：白天评估 ⇒ 夜间行为漏报
        assert len(alerts) == 1
        assert alerts[0].user_id == "night-owl"

    def test_business_hours_event_not_alerted(self):
        engine = AlertRuleEngine(event_retention_minutes=60 * 26)
        now = datetime.now(timezone.utc)
        biz = (now - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        engine.record_query("early-bird", event_time=biz)
        alerts = [a for a in engine.evaluate() if a.alert_type == AlertType.ANOMALY_OFF_HOURS]
        assert alerts == []

    def test_window_minutes_honored_not_hardcoded_5(self):
        engine = AlertRuleEngine(event_retention_minutes=60 * 26)
        engine.configure(AlertType.ANOMALY_OFF_HOURS,
                         RuleConfig(enabled=True, threshold=1, window_minutes=10))
        engine.record_query("old-owl", event_time=_yesterday_off_hours())
        alerts = [a for a in engine.evaluate() if a.alert_type == AlertType.ANOMALY_OFF_HOURS]
        # 窗设 10 分钟 ⇒ 昨天的事件出窗，不报（旧实现无视配置硬用 5 分钟墙钟）
        assert alerts == []


class TestE3BoundedState:
    def test_stale_events_pruned(self):
        engine = AlertRuleEngine(event_retention_minutes=60)
        engine.configure(AlertType.ANOMALY_BURST, RuleConfig(threshold=2, window_minutes=120))
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        for _ in range(5):
            engine.record_query("ghost", event_time=old)
        # 超过保留窗的事件在记录时即裁剪，不留无界内存
        assert len(engine._query_events) == 0
        assert engine.evaluate() == []

    def test_max_events_cap(self):
        engine = AlertRuleEngine(max_events=10)
        for _ in range(50):
            engine.record_query("flood")
        assert len(engine._query_events) <= 10


class TestE4Concurrency:
    def test_concurrent_records_exact_count(self):
        engine = AlertRuleEngine()
        def hammer():
            for _ in range(25):
                engine.record_rejection("s-1", "u-1")
        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(engine._rejection_events) == 100


class TestE5DpSnapshotFreshness:
    def test_snapshot_age_disclosed_and_not_respammed(self):
        engine = AlertRuleEngine()
        engine.record_dp_budget("sess-1", remaining_epsilon=0.5, total_epsilon=10.0)
        first = [a for a in engine.evaluate() if a.alert_type == AlertType.DP_BUDGET_EXHAUSTION]
        assert len(first) == 1
        assert "snapshot_age_seconds" in first[0].metadata
        second = [a for a in engine.evaluate() if a.alert_type == AlertType.DP_BUDGET_EXHAUSTION]
        assert second == []


class TestE7NumericGuard:
    def test_non_numeric_budget_returns_none(self):
        engine = AlertRuleEngine()
        assert engine.check_dp_budget("s", "not-a-number", 10.0) is None

    def test_numeric_strings_coerced(self):
        engine = AlertRuleEngine()
        alert = engine.check_dp_budget("s", "0.05", "10.0")
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL
