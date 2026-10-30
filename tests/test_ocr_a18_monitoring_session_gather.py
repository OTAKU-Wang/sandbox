"""OCR A18 - monitoring 在同一个 AsyncSession 上 asyncio.gather（medium-high）。

`app/api/monitoring.py` 的 `/stats` 与 `/policy-rejection` 把多条查询交给
`asyncio.gather(db.execute(...), db.execute(...))`。一个 `AsyncSession` 不是可并发
使用的对象：并发 `execute` 会在真实 asyncpg/aiosqlite 栈上抛
`InvalidRequestError: This session is already running a query`（或被驱动层串行化后
拿到交叉结果）。台账判定这两处目前被缓存 30s/60s 遮住，属潜伏缺陷，修法就是顺序 await。

本文件直接调用两个 handler，用一个「并发重入即记录」的假 session：`execute` 在读到
结果前让出事件循环，因此 gather 形态必然观察到重叠，顺序 await 形态必然为 0。
"""
import asyncio

import pytest

from app.api.monitoring import get_monitoring_stats, get_policy_rejection


class _FakeResult:
    def scalar(self) -> int:
        return 0

    def all(self) -> list:
        return []


class _OverrunSession:
    """AsyncSession stand-in that records concurrent execute() calls."""

    def __init__(self) -> None:
        self.calls = 0
        self.overlaps = 0
        self._busy = False

    async def execute(self, statement):
        self.calls += 1
        if self._busy:
            self.overlaps += 1
        self._busy = True
        try:
            await asyncio.sleep(0)
            return _FakeResult()
        finally:
            self._busy = False


@pytest.mark.parametrize(
    "handler,expected_calls",
    [(get_monitoring_stats, 7), (get_policy_rejection, 3)],
)
async def test_queries_do_not_share_one_session_concurrently(handler, expected_calls):
    db = _OverrunSession()

    payload = await handler(db=db, current_user=None)

    assert isinstance(payload, dict) and payload, f"handler 没有产出响应体: {payload!r}"
    assert db.calls == expected_calls, (
        f"预期 {expected_calls} 条查询，实际 {db.calls} 条 - 断言失去意义"
    )
    assert db.overlaps == 0, (
        f"{handler.__name__} 在同一个 AsyncSession 上并发发出 "
        f"{db.overlaps + 1} 条查询（asyncio.gather 形态）"
    )
