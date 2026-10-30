"""OCR A147c: 容器状态对账必须**周期**运行，而不只在启动期收敛一次。

A147 的前半（启动期一次性收敛）与可外部触发的 `reconcile_container_state` 已由
A147b 交付；本文件只钉剩下的那半腿：lifespan 里真的装了周期循环、周期可由配置调节、
且循环调用的确实是那个会写审计的对账函数（而不是另一个空转任务）。
"""

import ast
import pathlib

MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _lifespan_src() -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            return ast.get_source_segment(MAIN.read_text(encoding="utf-8"), node) or ""
    raise AssertionError("app/main.py 里找不到 lifespan")


def test_reconcile_interval_is_configurable():
    from app.core.config import Settings

    field = Settings.model_fields.get("SESSION_RECONCILE_INTERVAL_SECONDS")
    assert field is not None, "A147: 周期对账间隔必须走 CDS_ 前缀配置，不能硬编码在 main.py"
    assert isinstance(field.default, int) and field.default >= 30, (
        f"对账间隔默认值过小会持续给节点面加压：{field.default!r}"
    )


def _loop_src() -> str:
    """取 lifespan 里那个周期协程的函数体（失败/间隔门只能看它本身）。"""
    src = _lifespan_src()
    start = src.index("async def _container_state_reconcile_loop")
    end = src.index("_container_reconcile_task = _asyncio.create_task", start)
    return src[start:end]


def test_lifespan_installs_periodic_reconcile():
    src = _lifespan_src()
    assert "reconcile_container_state" in src, (
        "A147: lifespan 未安装容器状态周期对账——启动期收敛只覆盖重启，"
        "运行期的「行活/容器没」与「行终/容器活」孤儿会长期残留"
    )
    assert "create_task" in src and "SESSION_RECONCILE_INTERVAL_SECONDS" in src, (
        "A147: 对账必须是随 lifespan 启动的周期任务，且间隔读配置"
    )
    # 守卫形态必须与兄弟任务一致：TESTING 下不启动，避免测试进程里冒出常驻协程
    src = _lifespan_src()
    start = src.index("async def _container_state_reconcile_loop")
    guard = 'if os.environ.get("TESTING") != "1":'
    gi = src.rindex(guard, 0, start)
    between = src[gi + len(guard):start]
    assert between.strip() == "" or between.strip().startswith("#"), (
        "A147: 周期对账必须直接挂在 TESTING != \"1\" 守卫下（与 session_cleanup_loop 同形）"
    )
    assert start < src.index("create_task(_container_state_reconcile_loop())")


def test_reconcile_failure_does_not_kill_the_loop():
    """对账失败必须被捕获并响亮披露：一次节点面抖动不该让整条循环消失。"""
    body = _loop_src()
    assert "while True" in body, "A147: 不是周期循环"
    assert "try:" in body, "A147: 周期对账体没有 try —— 一次异常会让任务静默终止"
    assert "except Exception" in body and "logger.error" in body, (
        "A147: 对账失败只被吞掉，没有响亮披露"
    )
    assert "await _asyncio.sleep(settings.SESSION_RECONCILE_INTERVAL_SECONDS)" in body


def test_shutdown_cancels_the_reconcile_task():
    """泄漏的后台任务会让测试与热重启留下悬挂协程。"""
    src = MAIN.read_text(encoding="utf-8")
    tail = src[src.index("reconcile_container_state"):]
    assert "_container_reconcile_task" in tail, (
        "A147: 关闭序列没有取消对账任务（应与 _compute_scheduler_task 同一取消回路）"
    )
