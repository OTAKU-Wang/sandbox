"""A320（父会话登记的新发现，A319 失效类）— str user_id 让审计行在 flush 期静默蒸发。

`AuditLog.user_id` 是 `UUID(as_uuid=True)`（app/models/audit_log.py），而全仓仍有
若干调用点直接递 str（`user_id=str(current_user.id)`：dev_sandbox.py:440/524/618、
network_policy.py:127、output_control.py:478、deliveries.py:526、federation.py:350、
connectors.py:526/663 …）。SQLite 的 UUID 绑定在 **flush 期** 才炸
`StatementError: 'str' object has no attribute 'hex'`，而多数调用方外层是
`try/except Exception: pass` 或只打 warning ⇒ 错误发生在离写入很远的地方、被不相关
的捕获吞掉：审计行静默丢失。这与 A319 的 `db=None` 是同一失效类。

`audit_service.log()` 已为 `session_id` 修过同一缺陷（A68：`_coerce_session_id`，
`tests/test_ocr_a68_audit_session_uuid.py` 钉住），但 `user_id` 没有对称兜底。
本文件用**真实落库**判定（不打桩审计链路）：
1. 递 str 的 user_id 必须真的产生可查行，且列值等于该 UUID；
2. 解析不出的 user_id ⇒ 行仍落库，user_id 为 None（不造假值、不截断前 32 位），
   原始值披露在 `detail._user_id_unparsed`，且 ERROR 日志点名被丢字段与 action；
3. 「报错」不得成为新的静默丢行通道 —— 解析失败路径必须落行，而不是抛。
"""
from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database as db_module
from app.models.audit_log import AuditLog
from app.services.audit_service import audit_service

pytestmark = pytest.mark.asyncio


async def _rows(db: AsyncSession, action: str) -> list[AuditLog]:
    return list(
        (await db.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all()
    )


@pytest.mark.asyncio
async def test_str_user_id_request_session_produces_queryable_row(
    db_session: AsyncSession,
):
    """改前指纹：`log(user_id=str(...))` 一路走到 flush 才炸 StatementError。

    对递 str 的调用点（外层 try/except）这就是 0 行 0 响应的静默丢行；本用例不捕获，
    所以改前在 red 树里以 flush 异常的形式响亮地红。
    """
    uid = uuid.uuid4()
    await audit_service.log(
        db_session,
        action="a320.str_user_request",
        resource_type="a320_probe",
        user_id=str(uid),
    )
    await db_session.commit()

    # 独立会话回查：证明行是真的落库，而不是只在写入会话的 identity map 里可见。
    async with db_module.async_session() as probe_db:
        rows = await _rows(probe_db, "a320.str_user_request")
    assert len(rows) == 1, (
        f"A320: 递 str 的 user_id 必须产生可查审计行（改前恒 0 行或被 flush 异常吞掉），"
        f"实得 {len(rows)} 行"
    )
    assert rows[0].user_id == uid, (
        f"str 必须解析为该 UUID，实得 {rows[0].user_id!r}"
    )


@pytest.mark.asyncio
async def test_str_user_id_standalone_write_lands_row(db_session: AsyncSession):
    """真实丢行面：log_standalone 自带 try/except，改前 str user_id ⇒ 0 行 + 一条 CRITICAL。

    这条就是 A319 同类的现场形态 —— 调用方永远看不到异常。判据是行必须存在。
    """
    uid = uuid.uuid4()
    await audit_service.log_standalone(
        action="a320.str_user_standalone",
        resource_type="a320_probe",
        user_id=str(uid),
    )
    rows = await _rows(db_session, "a320.str_user_standalone")
    assert len(rows) == 1, (
        f"A320: log_standalone 递 str user_id 改前恒 0 行且零报错（异常被本方法吞掉），"
        f"实得 {len(rows)} 行"
    )
    assert rows[0].user_id == uid, f"列值必须为该 UUID，实得 {rows[0].user_id!r}"


@pytest.mark.asyncio
async def test_unparseable_user_id_still_lands_with_null_and_error_log(
    db_session: AsyncSession, caplog
):
    """解析不出 ⇒ 行仍落库 + user_id=None + detail 披露 + ERROR 点名字段与 action。

    禁止两种坏修法：造假值（把任意串编成 UUID / 截断前 32 位凑数），或让报错成为
    新的静默丢行通道（解析失败路径把整行丢掉）。
    """
    junk = "u-not-a-uuid-32chars-000000000000"  # 33 字符，非 hex，截前 32 位也解析不出
    with caplog.at_level(logging.ERROR, logger="app.services.audit_service"):
        await audit_service.log(
            db_session,
            action="a320.junk_user",
            resource_type="a320_probe",
            user_id=junk,
        )
        await db_session.commit()

    async with db_module.async_session() as probe_db:
        rows = await _rows(probe_db, "a320.junk_user")
    assert len(rows) == 1, (
        f"解析不出的 user_id 不得吞掉整行（报错=新的静默丢行），实得 {len(rows)} 行"
    )
    assert rows[0].user_id is None, (
        f"user_id 列必须为 NULL，不得造假值，实得 {rows[0].user_id!r}"
    )
    detail = rows[0].detail or {}
    assert detail.get("_user_id_unparsed") == junk, (
        f"原始值必须完整披露在 detail._user_id_unparsed（不得静默截断），实得 {detail!r}"
    )

    errors = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and r.name.startswith("app.services")
    ]
    assert any("user_id" in m and "a320.junk_user" in m for m in errors), (
        f"ERROR 日志必须点名被丢的字段与 action，实得 {errors}"
    )
