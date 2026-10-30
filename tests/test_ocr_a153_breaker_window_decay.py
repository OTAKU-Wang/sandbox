"""OCR A153 — `TaskCircuitBreaker.record_failure` 先写 `last_failure_time = now`，再用
`now - stats.last_failure_time > failure_window_seconds` 判窗口 ⇒ 差值恒为 0，衰减分支
不可达，`failure_window_seconds` 完全失效：跨进程生命周期的零星失败会永久累积到阈值，
把"5 次 / 300s"实际变成"5 次 / 永久"。台账见
docs/ocr-review-2026-10-18/ocr-verified-findings.md §A153。

本文件把模块内的 `time` 换成假时钟（不碰全局 time 模块），只用公开 API 推进失败序列，
钉三件事：① 窗口外的失败必须让计数归零；② 窗口内的失败仍然照常累积到阈值（防止
"修成永不熔断"这种假绿）；③ 边界以**上一次**失败的时刻为准（差 1 秒都不许衰减）。
"""
from types import SimpleNamespace

import pytest

from app.services import task_circuit_breaker as tcb
from app.services.task_circuit_breaker import CircuitConfig, CircuitState, TaskCircuitBreaker


class _Clock:
    def __init__(self, t0: float = 1_760_000_000.0):
        self.now = t0

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(tcb, "time", SimpleNamespace(time=lambda: c.now))
    return c


def test_a153_failures_spread_outside_the_window_never_open_the_circuit(clock):
    """5 次失败、彼此相隔 400s（> 300s 窗口）：按配置永远不该熔断。"""
    br = TaskCircuitBreaker(CircuitConfig(failure_threshold=5, failure_window_seconds=300))
    states = []
    for _ in range(5):
        states.append(br.record_failure("s-spread", "boom"))
        clock.advance(400)
    assert all(s is CircuitState.CLOSED for s in states), f"窗口外的零星失败被累计成熔断: {states}"
    assert br.get_stats("s-spread").failure_count == 1, (
        "每次失败都应先按窗口衰减再计数，最后一次之后计数应为 1"
    )
    assert br.can_execute("s-spread")[0] is True, "零星失败被拒后不该拒绝执行"


def test_a153_failures_inside_the_window_still_trip_the_breaker(clock):
    """非空跑对照：窗口内 3 次必须真的熔断（防止把衰减改成"永不计数"式假绿）。"""
    br = TaskCircuitBreaker(CircuitConfig(failure_threshold=3, failure_window_seconds=300))
    state = None
    for _ in range(3):
        state = br.record_failure("s-inside", "boom")
        clock.advance(10)
    assert state is CircuitState.OPEN, "窗口内达到阈值必须熔断"
    ok, reason = br.can_execute("s-inside")
    assert ok is False, f"熔断后应拒绝执行: {reason}"
    assert br.get_stats("s-inside").failure_count == 3


def test_a153_decay_is_measured_from_the_previous_failure(clock):
    """窗口边界以“上一次失败”为准：相邻间隔 299s 不衰减，间隔 301s 才归零。"""
    br = TaskCircuitBreaker(CircuitConfig(failure_threshold=5, failure_window_seconds=300))
    br.record_failure("s-edge")
    clock.advance(299)
    br.record_failure("s-edge")
    assert br.get_stats("s-edge").failure_count == 2, (
        "距上次失败 299s 仍在窗口内，计数应为 2（若这里得到 1，说明衰减拿的是刚写入的时间戳）"
    )
    clock.advance(301)
    br.record_failure("s-edge")
    assert br.get_stats("s-edge").failure_count == 1, (
        "距上次失败 301s 应触发窗口衰减后重新计数"
    )
    assert br.get_stats("s-edge").total_failures == 3
