"""OCR 复核轮 A94/A153 —— HSM 回退闸门与熔断窗口衰减的回归测试。

- A94 `hsm_adapter.create_hsm_adapter`：`fallback_allowed = True` 是默认值，读取
  `HSM_SOFTWARE_FALLBACK_ALLOWED` 的那一段整个包在 `except Exception: pass` 里 ⇒
  配置读不出来时闸门自动打开，`HSM_SOFTWARE_FALLBACK_ALLOWED=false` 的
  fail-closed 承诺（gap B5/T12）失效。
- A153 `task_circuit_breaker.record_failure`：先把 `stats.last_failure_time` 赋成
  `now`，再用 `now - stats.last_failure_time > window` 判断窗口 ⇒ 差值恒为 0，
  衰减分支不可达，跨小时的零星失败会永久累积到阈值并把熔断打开。
"""
import time
from types import SimpleNamespace

import pytest

from app.services import hsm_adapter as hsm
from app.services.task_circuit_breaker import CircuitConfig, CircuitState, TaskCircuitBreaker


def _settings(**kw):
    base = dict(VAULT_ADDR="", VAULT_TOKEN="", HSM_SOFTWARE_FALLBACK_ALLOWED=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a094_unreadable_settings_must_not_open_the_fallback_gate(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(hsm, "get_settings", boom, raising=False)
    monkeypatch.setattr("app.core.config.get_settings", boom)
    with pytest.raises(RuntimeError, match="HSM_SOFTWARE_FALLBACK_ALLOWED"):
        hsm.create_hsm_adapter()


def test_a094_software_fallback_still_allowed_when_config_says_yes(monkeypatch):
    monkeypatch.setattr("app.core.config.get_settings", lambda: _settings())
    assert isinstance(hsm.create_hsm_adapter(), hsm.SoftwareHSMAdapter)


def test_a094_explicit_refusal_still_refuses(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: _settings(HSM_SOFTWARE_FALLBACK_ALLOWED=False),
    )
    with pytest.raises(RuntimeError, match="refusing to fall back"):
        hsm.create_hsm_adapter()


def test_a153_failure_count_decays_outside_the_window():
    breaker = TaskCircuitBreaker(CircuitConfig(failure_threshold=5, failure_window_seconds=60))
    breaker.record_failure("sess-a")
    stats = breaker.get_stats("sess-a")
    stats.last_failure_time = time.time() - 3600
    stats.failure_count = 4
    state = breaker.record_failure("sess-a")
    after = breaker.get_stats("sess-a")
    assert after.failure_count == 1
    assert state is CircuitState.CLOSED


def test_a153_failures_inside_the_window_still_accumulate():
    breaker = TaskCircuitBreaker(CircuitConfig(failure_threshold=5, failure_window_seconds=3600))
    breaker.record_failure("sess-b")
    breaker.record_failure("sess-b")
    assert breaker.get_stats("sess-b").failure_count == 2


def test_a153_threshold_still_opens_the_circuit():
    breaker = TaskCircuitBreaker(CircuitConfig(failure_threshold=3, failure_window_seconds=3600))
    for _ in range(3):
        state = breaker.record_failure("sess-c")
    assert state is CircuitState.OPEN
    assert breaker.can_execute("sess-c")[0] is False
