"""A122（medium）— get_allowed_transitions 返回转换表内部持有的 set（别名地雷）。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A122

`session_state_machine.get_allowed_transitions()` 的命中分支返回的是模块级
`VALID_TRANSITIONS[current]` 本体（只有未命中的默认分支才是新建集合）。任何调用方
对返回值做一次 `.add()/.discard()/.clear()` 就永久改写全局状态机，并跨请求共享。
本文件钉住两条契约：返回值必须是副本；调用方改返回值不得影响转换表、
`validate_transition` 或后续调用。
"""

from __future__ import annotations

from typing import cast

import pytest

from app.models.sandbox_session import SessionStatus
from app.services.session_state_machine import VALID_TRANSITIONS, session_state_machine


def test_returns_a_copy_for_every_table_state():
    for state in VALID_TRANSITIONS:
        got = session_state_machine.get_allowed_transitions(state)
        assert got == VALID_TRANSITIONS[state]
        assert got is not VALID_TRANSITIONS[state], (
            f"get_allowed_transitions({state}) leaked the table's internal set"
        )


def test_unknown_state_still_returns_a_fresh_empty_set():
    unknown = cast(SessionStatus, cast(object, "NOT_A_STATE"))
    first = session_state_machine.get_allowed_transitions(unknown)
    second = session_state_machine.get_allowed_transitions(unknown)
    assert first == set()
    assert first is not second


def test_caller_mutation_cannot_corrupt_the_state_machine():
    pristine = set(VALID_TRANSITIONS[SessionStatus.READY])
    try:
        got = session_state_machine.get_allowed_transitions(SessionStatus.READY)
        got.add(SessionStatus.REVOKED)
        got.discard(SessionStatus.RUNNING)
        got.clear()

        assert VALID_TRANSITIONS[SessionStatus.READY] == pristine
        assert session_state_machine.get_allowed_transitions(SessionStatus.READY) == pristine
        assert session_state_machine.validate_transition(
            SessionStatus.READY, SessionStatus.RUNNING
        ).success is True
        assert session_state_machine.validate_transition(
            SessionStatus.COMPLETED, SessionStatus.REVOKED
        ).success is False
    finally:
        VALID_TRANSITIONS[SessionStatus.READY] = set(pristine)
