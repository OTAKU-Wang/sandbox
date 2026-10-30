"""A319（本轮新发现，未进 OCR 队列）— `sandbox.execute` 这条审计从来没有落过库。

`BwrapAdapter.execute()` 收尾处调 `audit_service.log(db=None, action="sandbox.execute", ...)`
（`app/services/sandbox_runtime.py:1296` 起）。`audit_service.log()` 不校验 `db`，一路走到
最后一行 `db.add(entry)` 才对 None 抛 `AttributeError: 'NoneType' object has no attribute 'add'`，
而该站点外层是 `except Exception: pass` ⇒ **零行、零报错、零日志**：每次 L3 执行的审计事实
（谁、哪个会话、退出码、输出字节数）从未进入审计漏斗，也就从不进 Merkle 叶子。

要求（父会话三条硬判据）：
1. 这种调用必须**响亮失败** —— `log(db=None)` 明确抛错并点名仓内既有分工
   （失败/拒绝类留痕走 `log_standalone()`，见 `app/api/AGENTS.md`）；
2. `sandbox.execute` 必须真的产生**可查审计行**；
3. 不许用「捕获后打个 warning」糊过去 —— 那还是静默丢失。

取证边界（诚实声明）：本文件不打桩审计链路（打了就正是 A68 记的「绿灯套件走不到真实落库
路径」），执行面跑真 bwrap（本机 /usr/bin/bwrap），不伪造 TEE/MicroVM。
"""
from __future__ import annotations

import logging
import uuid
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services.audit_service import audit_service
from app.services.sandbox_runtime import BwrapAdapter

pytestmark = pytest.mark.asyncio

WORKSPACE_ROOT = "/tmp/cds-a319-audit-test"


@pytest.mark.asyncio
async def test_audit_log_with_none_db_fails_loudly_and_names_the_standalone_path(caplog):
    """改前：None 走到 db.add 才炸 AttributeError（且被站点吞掉）⇒ 这里要的是 TypeError + 指路。"""
    orphan_db = cast(AsyncSession, cast(object, None))
    with caplog.at_level(logging.ERROR, logger="app.services.audit_service"):
        with pytest.raises(TypeError) as exc:
            await audit_service.log(
                orphan_db,
                action="sandbox.execute",
                resource_type="sandbox_session",
                session_id=uuid.uuid4(),
            )
    assert "log_standalone" in str(exc.value), (
        f"报错必须点名既有分工 log_standalone()，实得: {exc.value}"
    )
    errors = [r for r in caplog.records
              if r.levelno >= logging.ERROR and r.name.startswith("app.services")]
    assert errors, "响亮失败 = 异常 + ERROR 日志两条通道；只抛不记在调用方 try/except 下仍是静默丢失"
    assert any("sandbox.execute" in r.getMessage() for r in errors), (
        f"ERROR 日志必须点名被丢的 action，实得 {[r.getMessage() for r in errors]}"
    )


@pytest.mark.asyncio
async def test_l3_execute_writes_a_queryable_sandbox_execute_audit_row(
    db_session: AsyncSession, make_user,
):
    """改前指纹：执行成功、返回 200 语义，但 `sandbox.execute` 审计行数恒为 0 且无任何报错。"""
    _headers, uid = await make_user("data_scientist", "a319exec")
    session_row = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(uid),
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session_row)
    # log_standalone 自己开会话并提交：种子行必须先可见，否则会被同连接的第二个会话带走。
    await db_session.commit()
    session_id = str(session_row.id)

    adapter = BwrapAdapter(workspace_root=WORKSPACE_ROOT)
    prov = adapter.provision(session_id, "", timeout=60, user_id=uid)
    try:
        result = await adapter.execute(prov["container_id"], "print(2+3)", "python")
        assert result["exit_code"] == 0, f"执行本身要能过: {result}"

        rows = (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "sandbox.execute")
            )
        ).scalars().all()
        assert len(rows) == 1, (
            f"A319: sandbox.execute 审计从未落库（改前恒 0 行且零报错），实得 {len(rows)} 行"
        )
        assert str(rows[0].session_id) == session_id, "审计行必须回链到该沙箱会话"
        assert str(rows[0].user_id) == uid, "审计行必须留下执行人"
        detail = rows[0].detail or {}
        assert detail.get("exit_code") == 0, f"审计要留下退出码: {detail}"
        assert detail.get("output_size", -1) >= 0, f"审计要留下输出量: {detail}"
    finally:
        adapter.terminate(prov["container_id"])
