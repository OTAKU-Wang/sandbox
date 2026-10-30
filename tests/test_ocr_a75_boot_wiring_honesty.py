"""A75 — 启动接线必须诚实：持久队列（W16）的半成品状态要披露，生产 create_all 门要显式可测。

台账：ocr-verified-findings.md A75（持久队列归属守卫缺失，非活路径；main.py 只在建表处导入）。
NEEDS-OWNER: app/models/task_queue.py —— complete()/fail() 的 claimed_by 守卫与
requeue_stale() 的 attempts/run_after 守卫在该文件内，不在本轨道可改文件里。
"""
import ast
import inspect
import subprocess
from pathlib import Path

import pytest

import app.main as main_module

REPO = Path(main_module.__file__).resolve().parent.parent


def _lifespan_src() -> str:
    return inspect.getsource(main_module.lifespan.__wrapped__)


class _Cfg:
    def __init__(self, debug: bool = False, url: str = "postgresql+asyncpg://cds:pw@pg:5432/cds"):
        self.DEBUG = debug
        self.DATABASE_URL = url


def test_prod_never_create_all_and_dev_still_does():
    """A75：生产（非 DEBUG、非 sqlite、无 TESTING）绝不能靠 create_all 建表；dev/test 保持不变。"""
    assert main_module._create_all_allowed(_Cfg(), {}) is False
    assert main_module._create_all_allowed(_Cfg(debug=True), {}) is True
    assert main_module._create_all_allowed(_Cfg(), {"TESTING": "1"}) is True
    assert main_module._create_all_allowed(_Cfg(url="sqlite+aiosqlite:///./dev.db"), {}) is True


def test_lifespan_uses_the_create_all_gate():
    """A75：门必须在 lifespan 里被真正调用，且拒绝时保留原有的「跳过建表」分支。"""
    src = _lifespan_src()
    assert "_create_all_allowed(" in src
    assert "create_all" in src
    assert "Skipping Base.metadata.create_all" in src, "生产跳过建表的披露分支必须保留"


def test_boot_discloses_durable_queue_state(caplog):
    """A75：持久队列表会建，但没有生产消费者、终态写回无归属守卫 —— 启动时必须说出来。"""
    with caplog.at_level("WARNING"):
        main_module._announce_boot_wiring()

    line = next((r.getMessage() for r in caplog.records if "durable task queue" in r.getMessage()), "")
    assert line, [r.getMessage() for r in caplog.records]
    assert "production_consumer=False" in line
    assert "terminal_write_guard=none" in line
    assert "stale_requeue_guard=none" in line


def test_boot_wiring_announced_from_lifespan():
    """A75：披露必须挂在启动路径上（T12：启用的降级/半成品不得静默）。"""
    tree = ast.parse(_lifespan_src())
    names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_announce_boot_wiring" in names


def test_durable_queue_really_has_no_production_consumer():
    """A75：披露内容与被披露对象保持一致 —— app/ 里除 main 建表导入外无人消费该队列。"""
    out = subprocess.run(
        ["grep", "-rln", "--include=*.py", "-E", r"models import task_queue|models\.task_queue", "app/"],
        cwd=REPO, capture_output=True, text=True, check=False,
    ).stdout.split()
    assert sorted(out) == ["app/main.py"], out
