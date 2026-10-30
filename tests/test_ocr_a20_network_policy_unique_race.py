"""A20（medium）— POST /network-policies：唯一约束上的先查后插 + 强制面时机。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A20
落点: app/api/network_policy.py::create_network_policy（先查、add+flush、登记强制面）、
      app/core/database.py::get_db / drain_post_commit_hooks（commit 成功后才推引擎）

钉住三条要求（HEAD 全红）：
1. `network_policies.session_id` 是 unique（app/models/network_policy.py:24）。并发窗口里
   第二个 worker 的检查扑空、flush 撞唯一约束 ⇒ IntegrityError 冒到 get_db ⇒ 500。
   必须收成与 :73-76 同一文案的 409。
2. （A20b 方案 A 的时机契约）强制面只在**落库 commit 成功之后**收到字节：路由只把操作登记到
   `db.info["cds_netpol_pending"]`，响应如实 `enforcement="queued"`；异常路径走 get_db 的
   rollback 分支 ⇒ 唯一约束竞态的**输家**一个字节都推不出去，且赢家已提交的记录不受波及。
   —— 本条取代旧契约「handler 内直推、失败 502」：方案 A 下 commit 前根本不存在推送时机，
   而「commit 前就推」正是 A20 的强制侧/记录侧分叉本体。
3. 强制面失败必须留审计，且写在请求事务之外（`audit_service.log_standalone`）。数据行此刻
   已提交，因此失败既不得把请求伪报成 5xx，也不得回滚请求会话（本仓实测：请求会话 rollback
   会 expire 整个 session），并且不得把这条审计寄存在可能被吞的请求会话里。

边界（如实）：强制面侧只替换 `NetworkPolicyEngine.create_policy/remove_policy`（宿主
netns/iptables 面在测试里必须是记录器，这不是审计）；审计**只包裹不替换**（按会话身份分流
通道），因此成功/失败两条 AuditLog 都按真实落库行断言。「真 iptables/真 netns 是否装成功」
是硬件面，本机不可证 —— 见证据文件 HARDWARE-GATED。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.network_policy as np_module
from app.main import app
from app.core.database import get_db
from app.models.audit_log import AuditLog
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services.network_policy import network_policy_engine

POLICY_URL = "/api/v1/network-policies"


async def _seed_session(db_session: AsyncSession, user_id: str, *, commit: bool = False) -> str:
    """最小 seeding（形状取自 tests/test_egress_audit.py:117-126）。

    `commit=True` 用于驱动**真实 get_db** 的用例：新开的事务看不见未提交行。
    """
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_id),
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=SessionStatus.RUNNING.value,
    )
    db_session.add(session)
    await db_session.flush()
    if commit:
        await db_session.commit()
    return str(session.id)


def _body(session_id: str):
    """与 _post 同一份请求体，用于直接调用路由函数（驱动真实 get_db 的用例）。"""
    return np_module.NetworkPolicyCreate(
        session_id=session_id, mode="deny_all", allowed_ips=["10.0.0.0/8"]
    )


def _check_session():
    """另一个会话：按真实落库状态复核（conftest 已把 async_session 指向测试引擎）。"""
    from app.core.database import async_session

    return async_session()


class _EngineSpy:
    """强制面记录器：宿主 netns/iptables 面必须换成它，否则测试就在改宿主网络。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch, *, boom: bool = False) -> None:
        async def _fake_create(session_id: str, config, db=None):
            self.calls.append((session_id, config))
            if boom:
                raise RuntimeError("opa boom")
            return {"session_id": session_id, "mode": config.mode}

        async def _fake_remove(session_id: str, db=None):
            self.calls.append((f"remove:{session_id}", None))

        monkeypatch.setattr(network_policy_engine, "create_policy", _fake_create)
        monkeypatch.setattr(network_policy_engine, "remove_policy", _fake_remove)


async def _post(client: AsyncClient, headers: dict[str, str], session_id: str):
    return await client.post(
        POLICY_URL,
        json={"session_id": session_id, "mode": "deny_all", "allowed_ips": ["10.0.0.0/8"]},
        headers=headers,
    )


class _EmptyResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _StaleSnapshot:
    """把**第一条**命中 network_policies 的 SELECT 装作空结果。

    = 「先查」这一步在并发窗口里扑空（另一个 worker 刚提交），后面的 add+flush 因此
    必须自己去撞唯一约束。其余调用全部转发给真实 session。
    """

    def __init__(self, inner: AsyncSession) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_blinded", False)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def execute(self, statement, *args, **kwargs):
        result = await self._inner.execute(statement, *args, **kwargs)
        if not self._blinded and "network_policies" in str(statement):
            object.__setattr__(self, "_blinded", True)
            return _EmptyResult()
        return result


async def test_concurrent_second_policy_returns_409_not_500(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    """A20-1: 检查扑空、flush 撞唯一约束时必须是 409，而不是把 IntegrityError 冒出去。"""
    headers, uid = await make_user("data_provider", "a20race")
    sid = await _seed_session(db_session, uid)

    # 另一个 worker 已提交的行（同一事务里落盘，唯一索引真实存在）
    db_session.add(
        NetworkPolicy(session_id=sid, user_id=uid, mode="deny_all", allowed_ips=[], allowed_domains=[])
    )
    await db_session.flush()

    original = app.dependency_overrides[get_db]

    async def stale_override():
        async for session in original():
            yield _StaleSnapshot(session)

    app.dependency_overrides[get_db] = stale_override
    try:
        try:
            resp = await _post(client, headers, sid)
        except Exception as exc:  # noqa: BLE001 - 红读数要落在断言行上，不是异常栈里
            pytest.fail(
                "A20: 唯一约束冲突没有被路由接住，异常直接逸出 "
                f"{type(exc).__name__}: {str(exc)[:180]}"
            )
    finally:
        app.dependency_overrides[get_db] = original

    assert resp.status_code == 409, (
        f"A20: 并发第二条应收成 409（与 :73-76 同文案），实际 {resp.status_code}: {resp.text[:200]}"
    )
    assert "already exists" in resp.text, resp.text


async def test_unique_race_loser_never_reaches_enforcement(
    db_session: AsyncSession,
    make_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A20-2（时机契约）：唯一约束竞态的**输家**一个字节都不许推给强制面。

    旧用例钉的是「handler 内直推、装配失败 502」；方案 A 之后 commit 前不存在推送时机，
    于是这一半的正确形状变成：输家的请求异常必须交回**真实 get_db** 走 rollback 分支
    （而不是 drain 分支），引擎收到 0 次，赢家已提交的策略行原样存在。
    """
    spy = _EngineSpy()
    spy.install(monkeypatch)
    _headers, uid = await make_user("data_provider", "a20eng")
    sid = await _seed_session(db_session, uid, commit=True)

    # 赢家：并发窗口里先提交的那条（唯一索引真实存在）
    db_session.add(
        NetworkPolicy(
            session_id=sid,
            user_id=uid,
            mode="deny_all",
            allowed_ips=["192.168.0.0/16"],
            allowed_domains=[],
        )
    )
    await db_session.commit()

    agen = cast(AsyncGenerator[AsyncSession, None], get_db())
    raw = await agen.__anext__()
    db = _StaleSnapshot(raw)  # 「先查」扑空 = 另一个 worker 刚提交
    try:
        user = await db.get(User, uuid.UUID(uid))
        assert user is not None
        try:
            await np_module.create_network_policy(_body(sid), db=db, current_user=user)
        except HTTPException as exc:
            loser_status = exc.status_code
            # FastAPI 把端点异常投回 yield 依赖：get_db 的 except → rollback → raise。
            with pytest.raises(HTTPException) as propagated:
                await agen.athrow(exc)
            assert propagated.value is exc, (
                "A20b: 输家异常没有被 get_db 原样抛出（异常路径必须只 rollback，不得 drain）"
            )
        else:
            pytest.fail("A20: 唯一约束竞态的输家既没收成 409 也没抛异常")

        assert loser_status == 409, (
            f"A20: 输家应收成 409，实际 {loser_status}"
        )
        assert spy.calls == [], (
            "A20b: 竞态输家把强制面推进了引擎 —— 异常路径必须走 get_db 的 rollback 分支，"
            f"绝不能 drain（引擎收到 {spy.calls}）"
        )
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 - 已经 athrow 结束的生成器
            pass

    async with _check_session() as check:
        rows = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalars().all()
        assert len(rows) == 1, (
            f"A20: 输家不得留下一条记录面行：{[(r.user_id, r.allowed_ips) for r in rows]}"
        )
        assert rows[0].allowed_ips == ["192.168.0.0/16"], (
            "A20: 输家的 rollback 波及了赢家已提交的策略行"
        )


async def test_drain_failure_is_audited_outside_the_request_transaction(
    db_session: AsyncSession,
    make_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A20-3（时机契约）：commit 成功之后的推送失败 —— 如实 queued + 请求事务外留真实审计。

    旧用例钉的是「handler 内失败 ⇒ 502 + standalone」；方案 A 下推送发生在 commit 之后，
    记录面已经落了库，所以失败面既不得把请求伪报成 5xx，也不得回滚请求会话，而审计仍
    必须写在请求事务之外（寄存进请求会话的失败审计在提交链任一环失败时都会一起消失）。
    """
    spy = _EngineSpy()
    spy.install(monkeypatch, boom=True)
    _headers, uid = await make_user("data_provider", "a20aud")
    sid = await _seed_session(db_session, uid, commit=True)

    audit_svc = np_module.audit_service
    real_log = audit_svc.log
    real_standalone = audit_svc.log_standalone
    request_channel: list[tuple[str | None, dict | None]] = []
    standalone_channel: list[tuple[str | None, dict | None]] = []

    agen = cast(AsyncGenerator[AsyncSession, None], get_db())
    raw = await agen.__anext__()

    async def _spy_log(db, *args, **kwargs):  # noqa: ANN001 - 透明包裹真实实现
        action = kwargs.get("action") or (args[0] if args else None)
        detail = kwargs.get("detail")
        # log_standalone 内部复用 self.log(own_db, ...) ⇒ 按**会话身份**分通道，不按函数名
        bucket = request_channel if db is raw else standalone_channel
        bucket.append((action, detail))
        return await real_log(db, *args, **kwargs)

    async def _spy_standalone(**kwargs):  # noqa: ANN001
        standalone_channel.append((kwargs.get("action"), kwargs.get("detail")))
        return await real_standalone(**kwargs)

    monkeypatch.setattr(audit_svc, "log", _spy_log)
    monkeypatch.setattr(audit_svc, "log_standalone", _spy_standalone)

    try:
        user = await raw.get(User, uuid.UUID(uid))
        assert user is not None
        resp = await np_module.create_network_policy(_body(sid), db=raw, current_user=user)

        assert getattr(resp, "enforcement", None) == "queued", (
            "A20b: 强制面失败时响应也不得谎称已生效 —— 必须如实 enforcement=queued，"
            f"实际: {resp}"
        )
        assert spy.calls == [], (
            f"A20b: 登记≠推送，commit 之前引擎不得收到任何字节: {spy.calls}"
        )

        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()  # commit 成功 → drain 推引擎（炸）→ 收尾
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 - 已自然结束的生成器
            pass

    assert [c[0] for c in spy.calls] == [sid], (
        f"A20b: commit 成功后 drain 必须真的尝试推送: {spy.calls}"
    )
    failed_standalone = [c for c in standalone_channel if c[0] and "fail" in c[0]]
    assert failed_standalone, (
        "A20: 强制面失败没有任何请求事务外的审计"
        f"（standalone={standalone_channel}, request={request_channel}）"
    )
    _action, detail = failed_standalone[-1]
    assert detail and detail.get("error"), f"A20: 失败审计没有记录原因: {detail}"
    assert detail.get("stage") == "post_commit_drain", (
        f"A20b: 失败审计必须如实写明发生在 commit 后的 drain: {detail}"
    )
    assert not [c for c in request_channel if c[0] and "fail" in c[0]], (
        "A20: 失败审计寄存进了请求会话——提交链任一环失败就会与它一起消失: "
        f"{request_channel}"
    )

    # 不打桩落库：审计只包裹未替换，所以真实 AuditLog 行必须自己站住
    async with _check_session() as check:
        policy_row = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalar_one_or_none()
        assert policy_row is not None, "A20b: drain 失败不得回滚已提交的记录面（禁请求会话 rollback）"
        rows = (
            await check.execute(
                select(AuditLog).where(AuditLog.resource_type == "network_policy")
            )
        ).scalars().all()
        failed_rows = [r for r in rows if "fail" in (r.action or "")]
        assert failed_rows, (
            f"A20: 失败审计没有真实落库（network_policy 审计行={[r.action for r in rows]}）"
        )
        assert (failed_rows[-1].detail or {}).get("error"), (
            f"A20: 落库的失败审计没有原因: {failed_rows[-1].detail}"
        )
