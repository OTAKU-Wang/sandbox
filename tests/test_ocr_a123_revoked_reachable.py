"""A123 (ocr CR 2026-10-18): `REVOKED` 终端态不可达，且全仓无人写入。

修复前 `VALID_TRANSITIONS` 没有任何一个状态的目标集含 `REVOKED`，而它同时被列为
`TERMINAL_STATES` 并带对外文案（"Session revoked (key revoked)"）⇒ 一个对外可见的
枚举值没有实现路径：谁去转这个态，`validate_transition` 永远失败、
`require_transition` 抛 `ValueError`。

修复 = 给 REVOKED 补入边。入边来源不是拍脑袋：密钥吊销级联
`sandbox_runtime.terminate_sessions_by_key` 查询的正是
RUNNING/PROVISIONING/READY/PENDING/KEY_DISTRIBUTING/SUSPENDED 六个活跃态，
也就是「吊销可能击中」的集合，所以这六个都允许 →REVOKED；已落定的终端态不得
再被吊销改写历史。全部断言都是表级不变量，不依赖硬件/运行面。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.sandbox_session import SessionStatus as S
from app.services.sandbox_manager import is_session_expired
from app.services.session_state_machine import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    session_state_machine,
)

ALL_STATUSES = set(S)
LIVE_VALUES = sorted((s.value for s in ACTIVE_STATES))
TERMINAL_VALUES = sorted((s.value for s in TERMINAL_STATES))


def _reachable_from(start: S) -> set[S]:
    seen = {start}
    frontier = [start]
    while frontier:
        nxt = []
        for state in frontier:
            for target in session_state_machine.get_allowed_transitions(state):
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        frontier = nxt
    return seen


class TestRevokedIsReachable:
    @pytest.mark.parametrize("current", LIVE_VALUES)
    def test_every_live_state_may_be_revoked(self, current):
        result = session_state_machine.validate_transition(S(current), S.REVOKED)
        assert result.success, result.error
        assert S.REVOKED in session_state_machine.get_allowed_transitions(S(current))

    def test_require_transition_accepts_revoke(self):
        session_state_machine.require_transition(S.RUNNING, S.REVOKED)
        session_state_machine.require_transition(S.PENDING, S.REVOKED)

    def test_revoked_reachable_from_the_initial_state(self):
        seen = _reachable_from(S.PENDING)
        assert S.REVOKED in seen, (
            f"REVOKED 在转换表里不可达，可达集={sorted(s.value for s in seen)}"
        )

    def test_every_advertised_status_is_implemented(self):
        seen = _reachable_from(S.PENDING)
        assert set(VALID_TRANSITIONS) == ALL_STATUSES
        assert seen == ALL_STATUSES, (
            f"不可达枚举值={sorted(s.value for s in ALL_STATUSES - seen)}"
        )


class TestRevokedStaysTerminal:
    def test_revoked_has_no_outgoing_edges(self):
        assert session_state_machine.is_terminal(S.REVOKED) is True
        assert session_state_machine.get_allowed_transitions(S.REVOKED) == set()

    @pytest.mark.parametrize("terminal", TERMINAL_VALUES)
    def test_no_terminal_state_may_enter_revoked(self, terminal):
        """已落定的态（含 REVOKED 自身）不得再转入 REVOKED —— 收紧只加在活跃态上。"""
        assert S.REVOKED not in session_state_machine.get_allowed_transitions(S(terminal))
        assert session_state_machine.validate_transition(S(terminal), S.REVOKED).success is False

    def test_advertised_description_matches_the_state(self):
        assert "revok" in session_state_machine.get_state_description(S.REVOKED).lower()


class TestNoCollateralDamage:
    @pytest.mark.parametrize("current", LIVE_VALUES)
    def test_terminate_still_allowed_from_live_states(self, current):
        result = session_state_machine.validate_transition(S(current), S.TERMINATED)
        assert result.success, result.error

    def test_revoked_is_not_added_to_active_states(self):
        assert S.REVOKED not in ACTIVE_STATES
        assert S.REVOKED in TERMINAL_STATES


def _long_dead_session(status: str):
    """一个创建时间远超 timeout 的 session double（只喂 is_session_expired 读到的字段）。"""
    return SimpleNamespace(
        status=status,
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
        timeout_seconds=60,
        extended_seconds=0,
    )


class TestExpiryHonoursRevoked:
    """`session_lifecycle.ACTIVE_SESSION_STATUSES` 的注释一直宣称「completed/failed/
terminated/revoked 都会被 `is_session_expired` 跳过」，但修复前它的终态短路元组里
    没有 `REVOKED`：一条已吊销的会话只要创建时间超过 timeout 就会被判成 expired，
    对外文案与清理逻辑于是对同一行给出两种互相矛盾的说法。"""

    @pytest.mark.parametrize("terminal", TERMINAL_VALUES)
    def test_no_terminal_state_reports_expired(self, terminal):
        assert is_session_expired(_long_dead_session(terminal)) is False, terminal

    @pytest.mark.parametrize("current", LIVE_VALUES)
    def test_live_state_past_timeout_still_expires(self, current):
        """收紧不得外溢：活跃态超时照旧要能回收，否则这是一次放宽。"""
        assert is_session_expired(_long_dead_session(current)) is True, current
