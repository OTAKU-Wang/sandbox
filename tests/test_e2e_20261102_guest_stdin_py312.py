"""2026-11-02 本地全栈 e2e 实锤：A96 带外通道的 stdin 所有权在 py3.12 上是执行面地雷。

`GuestSecrets.deliver()` 写完即 `stdin.close()`，但 `proc.stdin` 仍挂在 Popen 上；
py3.12 的 `subprocess._communicate` 对挂着的 stdin 无条件 `flush()` →
`ValueError: I/O operation on closed file.`——dev-sandbox/仿真执行梯在 3.12 生产镜像上
每次执行必炸。本机 py3.14 该 flush 被 guard 吞掉，测试装置（ASGITransport，不跑镜像）
永远看不见。修复 = deliver 后 `proc.stdin = None`（所有权移交显式化）。

这里钉两层：
1. 行为层：用复刻 3.12 语义的假 Popen 走 `_run_guest_argv` 真函数——修复缺席即红。
2. 结构层：firecracker 仿真腿的 deliver→communicate 站点必须有同一移交（AST 静态针，
   与 test_a192x_harness_discipline 同族纪律）。
"""
import ast
import subprocess
from pathlib import Path

import pytest

from app.services import sandbox_runtime as sr
from app.services.sandbox_security import GuestSecrets

REPO = Path(__file__).resolve().parent.parent


class _ClosedAwareStdin:
    """TextIOWrapper 替身：close() 后 closed=True。"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:  # deliver 空通道不会走到，防御性提供
        raise OSError("no fd")


class _Popen312:
    """复刻 py3.12 subprocess 语义的最小假进程。

    3.12 的 `_communicate()` 会先 `self.stdin.flush()`；对已关闭的 TextIOWrapper
    flush 抛 ValueError——这正是当年生产镜像上每次执行都炸的那一行。
    """

    def __init__(self, argv, **kwargs) -> None:
        self.stdin = _ClosedAwareStdin() if kwargs.get("stdin") == subprocess.PIPE else None
        self.stdout = None
        self.stderr = None
        self.returncode = 0
        self.pid = 4242

    def communicate(self, input=None, timeout=None):  # noqa: A002 - 同名参数是接口形状
        if self.stdin is not None and self.stdin.closed:
            raise ValueError("I/O operation on closed file.")
        return ("ok", "")


def test_run_guest_argv_hands_off_stdin_before_communicate(monkeypatch):
    """空通道也必须安全：deliver 关写端后 stdin 必须从 Popen 摘除。"""
    monkeypatch.setattr(sr.subprocess, "Popen", _Popen312)
    secrets = GuestSecrets()  # 空 pairs：deliver 只关写端
    result = sr._run_guest_argv(["true"], secrets)
    assert result.returncode == 0


def test_run_guest_argv_real_semantics_sanity():
    """真 subprocess 冒烟：走一次真实的 echo，确认摘除逻辑没伤正常路径。"""
    secrets = GuestSecrets()
    result = sr._run_guest_argv(["echo", "guest_ok"], secrets, capture_output=True, text=True)
    assert result.returncode == 0
    assert "guest_ok" in result.stdout


def _stdin_none_between_deliver_and_communicate(tree: ast.AST) -> list[str]:
    """找每个函数体内 deliver(...) 与 communicate(...) 之间是否有 `<x>.stdin = None`。"""
    violations = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        events = []  # (kind, target) kind in {deliver, stdin_none, communicate}
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = node.func.attr
                if name == "deliver":
                    events.append((node.lineno, "deliver"))
                elif name == "communicate":
                    events.append((node.lineno, "communicate"))
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (isinstance(tgt, ast.Attribute) and tgt.attr == "stdin"
                            and isinstance(tgt.value, ast.Name) and tgt.value.id == "proc"):
                        events.append((node.lineno, "stdin_none"))
        by_line = {}
        for lineno, kind in events:
            by_line.setdefault(lineno, set()).add(kind)
        lines = sorted(by_line)
        for i, ln in enumerate(lines):
            if "deliver" not in by_line[ln]:
                continue
            # deliver 之后、最近的 communicate 之前，必须出现 stdin_none
            comm_after = next((c for c in lines[i + 1:] if "communicate" in by_line[c]), None)
            if comm_after is None:
                continue
            handed = any("stdin_none" in by_line[c] for c in lines[i + 1:] if c < comm_after)
            if not handed:
                violations.append(f"{fn.name}@L{ln}")
    return violations


@pytest.mark.parametrize("rel", [
    "app/services/firecracker_runtime.py",
    "app/services/sandbox_runtime.py",
])
def test_deliver_communicate_sites_handed_off_stdin(rel):
    """结构针：deliver→communicate 站点之间必须有 proc.stdin = None（或走 _run_guest_argv）。"""
    src = (REPO / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = _stdin_none_between_deliver_and_communicate(tree)
    assert not bad, (
        f"{rel}: 这些 deliver→communicate 站点缺 `proc.stdin = None` 移交（py3.12 必炸，"
        f"见本文件 docstring）：{bad}"
    )
