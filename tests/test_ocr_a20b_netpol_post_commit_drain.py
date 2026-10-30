"""A20b（方案 A）— 网络策略落库成功之前，强制面一个字节都不许收到。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A20 的第二半边
（父级裁决 = 方案 A：路由只登记 db.info["cds_netpol_pending"]，由
app/core/database.py::get_db 在 commit 成功之后 drain 再推引擎，响应如实返回
enforcement="queued"，不谎称已生效）。

与 tests/test_ocr_a20_network_policy_unique_race.py 的分工：那条钉的是先查后插
（409）半边；本文件钉「引擎先于提交」半边。刻意**不打桩 audit_service**
（本仓曾因整只打桩审计漏掉 AuditLog.session_id 的 UUID 缺陷）——成功路径与
drain 失败路径都按真实 AuditLog 落库断言。引擎侧只替换 NetworkPolicyEngine 的
create_policy（它是宿主 netns/iptables 面，测试里必须换成记录器，这不是审计）。
"""
from __future__ import annotations

import uuid

from collections.abc import AsyncGenerator
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.network_policy import NetworkPolicyCreate, create_network_policy
from app.core.database import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services.network_policy import network_policy_engine

POLICY_URL = "/api/v1/network-policies"


class _Spy:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def install(self, monkeypatch, *, boom: bool = False) -> None:
        async def _fake_create(session_id: str, config, db=None):
            self.calls.append((session_id, config))
            if boom:
                raise RuntimeError("opa boom")
            return {"session_id": session_id, "mode": config.mode}

        async def _fake_remove(session_id: str, db=None):
            return None

        monkeypatch.setattr(network_policy_engine, "create_policy", _fake_create)
        monkeypatch.setattr(network_policy_engine, "remove_policy", _fake_remove)


async def _seed(db_session, uid: str) -> str:
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(uid),
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()
    sid = str(session.id)
    await db_session.commit()  # 真实 get_db 的新会话必须能看到 seed 行
    return sid


def _body(sid: str) -> NetworkPolicyCreate:
    return NetworkPolicyCreate(session_id=sid, mode="deny_all", allowed_ips=["10.0.0.0/8"])


async def _audit_rows(session, action_like: str) -> list[AuditLog]:
    rows = (
        await session.execute(
            select(AuditLog).where(
                AuditLog.action == action_like,
                AuditLog.resource_type == "network_policy",
            )
        )
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_handler_defers_push_until_get_db_commit(db_session, make_user, monkeypatch):
    """登记≠推送：路由返回时引擎必须还一无所知；commit 成功 drain 之后才收到。"""
    spy = _Spy()
    spy.install(monkeypatch)
    _headers, uid = await make_user("data_provider", "a20b1")
    sid = await _seed(db_session, uid)

    agen = cast(AsyncGenerator[AsyncSession, None], get_db())
    db = await agen.__anext__()
    try:
        user = await db.get(User, uuid.UUID(uid))
        assert user is not None
        resp = await create_network_policy(_body(sid), db=db, current_user=user)

        assert getattr(resp, "enforcement", None) == "queued", (
            f"响应必须如实披露 enforcement=queued，实际: "
            f"{resp.model_dump() if hasattr(resp, 'model_dump') else resp}"
        )
        assert spy.calls == [], (
            "A20: 路由在 commit 之前就推了强制面 —— 提交失败时引擎里会留下一条"
            f"数据库里不存在的策略: {spy.calls}"
        )

        exhausted = False
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            exhausted = True
        assert exhausted
        assert [c[0] for c in spy.calls] == [sid], (
            f"commit 成功之后 drain 必须把登记的策略推给引擎，实际: {spy.calls}"
        )
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass

    from app.core.database import async_session as session_factory

    async with session_factory() as check:
        row = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalar_one_or_none()
        assert row is not None, "policy 行必须真的 commit 了"
        audits = await _audit_rows(check, "network_policy.create")
        assert audits, "不打桩审计：成功路径必须留下真实 network_policy.create 审计行"
        detail = audits[-1].detail or {}
        assert detail.get("enforcement") == "queued", (
            f"成功审计必须如实记录 queued，实际 {detail}"
        )


@pytest.mark.asyncio
async def test_failed_commit_leaves_enforcement_untouched(db_session, make_user, monkeypatch):
    """A20 的本体：提交失败 ⇒ 引擎里不得留下一条数据库里不存在的策略。

    用实例级 before_commit 监听器让 **commit 本身**真实抛错（登记成功后、
    commit 落地前），复现「引擎已装、事务没成」的原始窗口。
    """
    spy = _Spy()
    spy.install(monkeypatch)
    _headers, uid = await make_user("data_provider", "a20b2")
    sid = await _seed(db_session, uid)

    agen = cast(AsyncGenerator[AsyncSession, None], get_db())
    db = await agen.__anext__()
    try:
        user = await db.get(User, uuid.UUID(uid))
        assert user is not None
        await create_network_policy(_body(sid), db=db, current_user=user)
        assert spy.calls == []

        def _boom(session):
            raise RuntimeError("commit boom")

        event.listen(db.sync_session, "before_commit", _boom)
        with pytest.raises(RuntimeError, match="commit boom"):
            await agen.__anext__()
    finally:
        try:
            await agen.aclose()
        except Exception:
            pass

    assert spy.calls == [], (
        "A20: 提交失败后引擎仍收到了策略 —— 强制侧与记录侧分叉"
        f"（{spy.calls}）"
    )

    from app.core.database import async_session as session_factory

    async with session_factory() as check:
        row = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalar_one_or_none()
        assert row is None, "提交失败的策略行不得存在"


@pytest.mark.asyncio
async def test_http_create_returns_queued_and_drains_after_commit(
    db_session, make_user, monkeypatch
):
    """经真实 get_db 的 HTTP 全链：201 + enforcement=queued，drain 在响应完成前落地。"""
    spy = _Spy()
    spy.install(monkeypatch)
    headers, uid = await make_user("data_provider", "a20b3")
    sid = await _seed(db_session, uid)

    app.dependency_overrides.pop(get_db, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            POLICY_URL,
            json={"session_id": sid, "mode": "deny_all", "allowed_ips": ["10.0.0.0/8"]},
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body.get("enforcement") == "queued", (
        f"响应必须如实披露 enforcement=queued（不谎称已生效），实际: {body}"
    )
    assert body.get("session_id") == sid
    assert [c[0] for c in spy.calls] == [sid], (
        f"HTTP 请求收尾时（get_db commit 后）drain 必须已把策略推给引擎: {spy.calls}"
    )


@pytest.mark.asyncio
async def test_drain_failure_keeps_committed_response_and_audits(
    db_session, make_user, monkeypatch
):
    """drain 推送失败：响应不得伪报失败（数据行已 commit），但必须留真实失败审计。"""
    spy = _Spy()
    spy.install(monkeypatch, boom=True)
    headers, uid = await make_user("data_provider", "a20b4")
    sid = await _seed(db_session, uid)

    app.dependency_overrides.pop(get_db, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            POLICY_URL,
            json={"session_id": sid, "mode": "deny_all", "allowed_ips": ["10.0.0.0/8"]},
            headers=headers,
        )
    assert resp.status_code == 201, (
        f"记录面已提交，响应不得因强制面失败伪报 5xx（如实 queued 即可）: "
        f"{resp.status_code} {resp.text[:200]}"
    )
    assert resp.json().get("enforcement") == "queued"
    assert [c[0] for c in spy.calls] == [sid], "drain 确实尝试过推送"

    from app.core.database import async_session as session_factory

    async with session_factory() as check:
        row = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalar_one_or_none()
        assert row is not None, "drain 失败不得回滚已提交的策略行（禁请求会话 rollback）"
        failed = [
            r
            for r in (await check.execute(select(AuditLog))).scalars().all()
            if r.resource_type == "network_policy" and "fail" in (r.action or "")
        ]
        assert failed, "drain 失败必须留真实审计（不打桩）"
        assert (failed[-1].detail or {}).get("error"), "失败审计必须写明原因"
