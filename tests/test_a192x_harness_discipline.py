"""A192x (P4-1) 装置纪律静态针 — 事件循环钉死后的三条红线，出现即红点名。

背景：pyproject 把 test/fixture loop scope 钉到 session（pytest-asyncio ≥0.26
对遗留 session 级 ``event_loop`` fixture 的受支持替身）。钉死后全套件共用一条
loop + 一条 StaticPool 连接，两条新的套件级不变量必须被机器守住：

1. **禁止未分代的全局任务 gather**：session loop 上 ``asyncio.all_tasks()``
   是全套件视图，gather 全部 pending 会被长寿守护任务（egress_audit
   ``_writer_loop`` 等）永挂——55% 位置三轮全量同位 hang 的实证根因。
   合法形态只有两种：conftest 的 ``_task_generation`` 基线快照本身；
   以及消费该 fixture 做代际过滤的用例（A192x 收口 2026-10-30，
   ci/known-failures.md A192x 行）。
2. **loop scope 钉位不得静默丢失**：两个 ini 键缺一不可，丢了就退回
   每用例新循环 + StaticPool 跨循环死连接的老 A192x 形态。
3. **legacy session 级 ``event_loop`` fixture 不得复活**：pytest-asyncio 1.x
   根本不采纳它，留着只会误导下一个排障者（本仓库 A192x 烧掉三个波次的
   起点就是它）。
"""
import ast
from pathlib import Path

import tomllib

TESTS = Path(__file__).resolve().parent
PYPROJECT = TESTS.parent / "pyproject.toml"


def _attr_names(tree: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def _names(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def test_no_unfiltered_all_tasks_consumers_outside_generation_filter():
    """除 conftest 的 `_task_generation` 快照外，tests/ 内出现 all_tasks()
    的文件必须引用 `_task_generation` 做代际过滤，否则红——点名这条纪律。"""
    offenders: list[str] = []
    for f in sorted(TESTS.glob("*.py")):
        if f.name == "conftest.py" or f.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法坏的文件归收集期管，不归这条针
            continue
        if "all_tasks" in _attr_names(tree) | _names(tree) and "_task_generation" not in _names(tree):
            offenders.append(f.name)
    assert not offenders, (
        "A192x 装置纪律：以下文件在 session 级 loop 上裸消费 asyncio.all_tasks()，"
        "会 gather 到前置用例的长寿后台任务并永挂（egress_audit._writer_loop 实证）。"
        "请请求 conftest 的 _task_generation fixture 做代际过滤: " + ", ".join(offenders)
    )


def test_loop_scope_pins_present_in_pyproject():
    ini = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    assert ini.get("asyncio_default_test_loop_scope") == "session", (
        "A192x：asyncio_default_test_loop_scope 丢失——退回每用例新循环会让 "
        "StaticPool 单连接跨循环复用，复活波 1 的 a192d ERROR 形态"
    )
    assert ini.get("asyncio_default_fixture_loop_scope") == "session", "A192x：同上，fixture 侧钉位丢失"


def test_conftest_has_no_legacy_event_loop_fixture():
    tree = ast.parse((TESTS / "conftest.py").read_text(encoding="utf-8"))
    legacy = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "event_loop"
    ]
    assert not legacy, (
        "A192x：legacy session 级 event_loop fixture 被 pytest-asyncio 1.x 静默忽略，"
        "受支持替身是 pyproject 的两个 loop-scope ini 键；不得复活"
    )
