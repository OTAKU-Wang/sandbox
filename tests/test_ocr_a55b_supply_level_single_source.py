"""OCR A55wf — `workflow_service.pick_test_sandbox_level` 不得再留第二份档位阶梯。

A55 把「承载供数的档位强度序」落成唯一权威
`capability_matrix.SUPPLY_DATA_LEVELS` + `supply_data_level_rank()` /
`can_carry_supply_data()`，dev-sandbox（`app/api/dev_sandbox.py`）与生产会话
（`app/api/sandbox_sessions.py`）两条入口都已改为消费它。
`pick_test_sandbox_level`（测试/生产会话的选档腿）却仍带私有副本 `("l2c", "L3")`：
权威将来增删一档（矩阵里 per-VM TEE 的 L2' 变体在排期、L3/bwrap 属可退役档），
这里不会跟着动 —— 同一张合约的两条腿会选出不同的档，而「哪一档能装密态供数」
本来就是平台不变量，不该有两处真相。

本文件只钉结构与语义，不重复 `tests/test_ss15_flow_unit.py` 的既有档位断言：
  1. 结构：函数体里不得再出现「装了 ≥2 个档位字面量的容器」，且必须引用权威符号；
  2. 语义：monkeypatch 权威阶梯（退一档 / 加一档），pick 的结果必须跟着动 ——
     证明选档没有第二份真相（打桩只对**调用时**读取权威的写法生效）。
另附一条等价性读数：权威未被打桩时，逐条款的返回值与重构前钉死的今天行为一致
（行为必须等价，任何选档结果的变化都算越界）。
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "app" / "services" / "workflow_service.py"


def _contract(levels):
    return SimpleNamespace(allowed_sandbox_levels=levels)


def _pick_source_and_fn() -> tuple[str, ast.FunctionDef]:
    src = SRC.read_text(encoding="utf-8")
    fn = next(
        (
            node
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.FunctionDef) and node.name == "pick_test_sandbox_level"
        ),
        None,
    )
    assert fn is not None, f"{SRC} 里找不到 pick_test_sandbox_level"
    return src, fn


def _level_literals(fn: ast.FunctionDef) -> list[list[str]]:
    """函数体里每个「容器字面量」中的档位字面量清单（容器 ≥2 个档位字面量 = 第二份阶梯）。"""
    from app.services import capability_matrix

    vocabulary = {lv.lower() for lv in capability_matrix.LEVEL_ORDER}
    containers: list[list[str]] = []
    for node in ast.walk(fn):
        if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            continue
        members = [
            el.value
            for el in node.elts
            if isinstance(el, ast.Constant) and isinstance(el.value, str) and el.value.lower() in vocabulary
        ]
        if len(members) >= 2:
            containers.append(members)
    return containers


def _referenced_names(fn: ast.FunctionDef) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_pick_does_not_define_a_second_level_ladder():
    """结构门：私有阶梯不得存在——档位强度序的唯一权威在 capability_matrix。"""
    offenders = _level_literals(_pick_source_and_fn()[1])
    assert offenders == [], (
        "pick_test_sandbox_level 里出现了第二份档位阶梯 "
        f"{offenders}：请改为消费 capability_matrix.SUPPLY_DATA_LEVELS（A55 唯一权威）"
    )


def test_pick_consumes_the_authoritative_constant():
    """结构门：函数必须显式引用权威常量/函数，否则「去重」只是换了个私有写法。"""
    names = _referenced_names(_pick_source_and_fn()[1])
    authority = {"SUPPLY_DATA_LEVELS", "supply_data_level_rank", "can_carry_supply_data"}
    assert names & authority, (
        f"pick_test_sandbox_level 没有引用权威符号（期望命中 {sorted(authority)} 之一），"
        f"实际引用 {sorted(names)}"
    )


def test_pick_follows_the_authority_when_a_level_retires(monkeypatch):
    """语义门（退一档）：权威把 L3 移出可承载供数的阶梯后，选档不得再选 L3。

    私有副本会在权威已退役 L3 之后继续返回 L3 —— 测试/生产会话建在一个已经不允许
    承载密态供数的档上。这里只打桩权威常量，验证没有第二份真相。
    """
    from app.services import capability_matrix, workflow_service as wf

    monkeypatch.setattr(capability_matrix, "SUPPLY_DATA_LEVELS", ("l2c",))
    assert wf.pick_test_sandbox_level(_contract("L1, L3")) == "l2c"


def test_pick_follows_the_authority_when_a_level_is_added(monkeypatch):
    """语义门（加一档）：权威新增更强的档后，条款放行它就必须选它；无条款时回最强档。"""
    from app.services import capability_matrix, workflow_service as wf

    monkeypatch.setattr(capability_matrix, "SUPPLY_DATA_LEVELS", ("l2c-tee", "l2c", "L3"))
    assert wf.pick_test_sandbox_level(_contract("l2c-tee, L3")) == "l2c-tee"
    assert wf.pick_test_sandbox_level(None) == "l2c-tee"
    # 条款里一档都不在阶梯内 → 回阶梯最强档，由建会话处诚实报 RUNTIME_UNAVAILABLE
    assert wf.pick_test_sandbox_level(_contract("L9")) == "l2c-tee"


# 重构前实测钉死的今天行为（与 tests/test_ss15_flow_unit.py 的既有断言同表，不放宽）
TODAY_BEHAVIOUR = [
    (None, "l2c"),
    ("", "l2c"),
    ("L3", "L3"),
    ("L1, L3, l2c", "l2c"),
    ("L0", "l2c"),
    ("L1", "l2c"),
    ("L2, L1", "l2c"),
    ("L0, L3", "L3"),
    ("L1,L3", "L3"),
    ("L9", "l2c"),
]


@pytest.mark.parametrize("clause,expected", TODAY_BEHAVIOUR)
def test_behaviour_equivalence_on_the_live_authority(clause, expected):
    """行为等价门：不打桩时，逐条款的选档结果必须与重构前逐字一致。"""
    from app.services import workflow_service as wf

    assert wf.pick_test_sandbox_level(_contract(clause) if clause is not None else None) == expected
