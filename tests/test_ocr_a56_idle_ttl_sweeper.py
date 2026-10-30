"""A56 — DuckDB 引擎的 TTL 回收不得依赖请求到达；空闲进程的明文/DEK 也要按时消失。

台账：ocr-verified-findings.md A56（TTL 清扫的调用点/驻留问题）。
"""
import ast
import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import app.api.sandbox_db as sandbox_db
import app.main as main_module

DB = "/api/v1/sandbox-db"


def _age(engine, seconds: int = 7200) -> None:
    engine._created_at = datetime.now(timezone.utc) - timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_sweep_is_callable_without_any_request(client, auth_headers):
    """A56：清扫必须有一个公开入口，不靠「下一个请求」才回收过期引擎。"""
    sid = f"a56-{uuid.uuid4().hex[:8]}"
    resp = await client.post(
        f"{DB}/create-table",
        json={"session_id": sid, "table_name": "t", "data": [{"a": 1}]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    _age(sandbox_db.get_engine(sid))

    swept = sandbox_db.sweep_expired_duckdb_engines()

    assert swept >= 1
    assert sid not in sandbox_db._engines
    assert sandbox_db.get_engine(sid) is None


@pytest.mark.asyncio
async def test_sweep_keeps_fresh_engines_and_their_ownership(client, auth_headers):
    """A56：清扫只回收到期引擎；未到期引擎的归属仍然可判定。"""
    stale = f"a56-stale-{uuid.uuid4().hex[:8]}"
    fresh = f"a56-fresh-{uuid.uuid4().hex[:8]}"
    for sid in (stale, fresh):
        resp = await client.post(
            f"{DB}/create-table",
            json={"session_id": sid, "table_name": "t", "data": [{"a": 1}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
    _age(sandbox_db.get_engine(stale))
    owner = sandbox_db.get_engine(fresh).owner_user_id

    sandbox_db.sweep_expired_duckdb_engines()

    assert stale not in sandbox_db._engines
    assert sandbox_db.get_engine(fresh).owner_user_id == owner


def _lifespan_call_names() -> set[str]:
    tree = ast.parse(inspect.getsource(main_module.lifespan.__wrapped__))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_boot_wires_the_engine_sweeper_and_cancels_it():
    """A56：lifespan 必须启动清扫循环并在停机时取消它，而不是留给下一个请求。"""
    names = _lifespan_call_names()
    assert "_start_duckdb_engine_sweeper" in names, sorted(n for n in names if "sweep" in n or "start" in n)

    src = inspect.getsource(main_module.lifespan.__wrapped__)
    started = [
        target
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name) and node.value.func.id == "_start_duckdb_engine_sweeper"
        for target in node.targets if isinstance(target, ast.Name)
    ]
    assert started, "sweeper task 必须被具名保存，才能在 shutdown 取消"
    assert all(f"{name.id}.cancel()" in src for name in started)
