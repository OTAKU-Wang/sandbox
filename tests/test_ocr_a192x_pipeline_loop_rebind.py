"""A192x: lifespan 关停不得打穿共享 DB 连接 / 单例不得留死循环任务（OCR 2026-10-18 补充轮）。

现场：全量回归里 `tests/test_agent_api.py` 之后的
`tests/test_ocr_a192d_dp_backend_assembly.py::test_lifespan_assembles_the_cross_process_backend`
在自己的 teardown 里炸 `no active connection` / `Cannot operate on a closed database`，单跑却绿。
最小复现 = `test_agent_api.py` + 该用例。插桩栈定到 `app/main.py` 关停段的
`await engine.dispose()`：测试用 SQLite StaticPool 只有一条共享连接，
启动披露类用例进出真实 lifespan 时 dispose 会把别人已 checkout 的会话一起关掉。

同一段关停还暴露第二处：`task_pipeline` 是模块级单例，`start()` 见 `_running_flag`
为真就早退 —— 上一个（已关闭的）事件循环留下的 poll 任务再没人取消，
它会继续读写 DB。产品同型场景：进程内重载 / 同进程多个 ASGI app。
"""

import asyncio

from sqlalchemy import text

from app.services.task_pipeline import task_pipeline


def _forge_stale_binding():
    async def _idle_poll():
        await asyncio.sleep(30)

    async def _boot():
        task = asyncio.ensure_future(_idle_poll())
        await asyncio.sleep(0)
        return task

    loop = asyncio.new_event_loop()
    try:
        stale = loop.run_until_complete(_boot())
    finally:
        loop.close()

    task_pipeline._running_flag = True
    task_pipeline._poll_task = stale
    return stale


async def _scenario_start_rebinds(stale):
    running = asyncio.get_running_loop()
    try:
        await task_pipeline.start()
        assert task_pipeline._poll_task is not None, "start() 之后没有 poll 任务"
        assert task_pipeline._poll_task.get_loop() is running, (
            "A192x: poll 任务仍绑在已关闭的旧循环上 —— 它会继续读写 DB，"
            "把后续用例/请求打成 no active connection"
        )
        assert stale.get_loop().is_closed()
    finally:
        await task_pipeline.stop()


def test_start_rebinds_poll_task_to_current_loop():
    stale = _forge_stale_binding()
    asyncio.run(_scenario_start_rebinds(stale))


async def _scenario_stop_tolerates_foreign_task():
    await task_pipeline.stop()
    assert task_pipeline._poll_task is None, "陈旧绑定未解除"
    assert task_pipeline._running_flag is False


def test_stop_does_not_touch_foreign_loop_task():
    _forge_stale_binding()
    asyncio.run(_scenario_stop_tolerates_foreign_task())


async def test_lifespan_shutdown_keeps_checked_out_connections_usable():
    """跨 lifespan 关停持有连接时，关停后的 dispose 不得把它关掉。

    诚实说明：本针在修复前后**均绿** —— SQLAlchemy 的 `dispose()` 不关已 checkout
    的连接（归还时才关）。它的价值是把“关停段 dispose 是 teardown 炸点”这个
    假说钉死为假（见 `ci/known-failures.md` A192x 行），不得当作已修证明引用。
    """
    from app.core.database import engine
    from app.main import app, lifespan, settings

    original_debug = settings.DEBUG
    settings.DEBUG = True
    try:
        async with engine.connect() as held:
            assert (await held.execute(text("SELECT 1"))).scalar() == 1
            async with lifespan(app):
                pass
            await held.execute(text("SELECT 1"))
    finally:
        settings.DEBUG = original_debug
