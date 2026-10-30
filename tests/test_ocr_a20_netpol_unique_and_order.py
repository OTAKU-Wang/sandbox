"""A20（medium）— 网络策略：唯一约束冲突的领域码 + 强制面必须在 commit 之后。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A20
落点: app/api/network_policy.py（create 的冲突出口、update/delete 的强制面时机）

与兄弟文件的分工（父级裁决）：
- `tests/test_ocr_a20_network_policy_unique_race.py`（兄弟轨 backend-netpol-drain-a 持有）
  钉「先查后插撞约束必须是 409 而不是 500」与 POST 的 drain 时机；
- `tests/test_ocr_a20b_netpol_post_commit_drain.py` 钉 POST 半边「commit 之前引擎不得收到字节」；
- **本文件**钉 A20 剩下的两条：
  1. 冲突响应必须带**语义错误码** `NETWORK_POLICY_EXISTS`（W2 体 `{code,message,detail,request_id}`），
     而不是 `app/core/error_handlers.py::http_exception_handler` 兜底出来的 `HTTP_409`
     —— 照 `deliveries.py::_slug_conflict`（`ENDPOINT_SLUG_TAKEN`）的口径：保存点包住那条
     INSERT，`IntegrityError` 转成领域错误，**不 rollback 请求会话**；
  2. 同一个文件里的 PATCH / DELETE 也不得在 commit 之前动强制面：登记到
     `session.info[NETPOL_PENDING_KEY]`，由 `get_db` 在 commit 成功后 drain（本文件用真实
     `get_db` 驱动，因此 drain 真的会跑）。提交失败 ⇒ 强制面一个字节都收不到。

边界（如实）：强制面（宿主 netns/iptables/DNS 代理）在测试机上必须换成记录器，这不是审计；
审计与数据库都走真实落库路径。「真 iptables 是否装成功」是硬件面，本机不可证。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.network_policy as np_module
from app.api.network_policy import (
    NetworkPolicyCreate,
    NetworkPolicyUpdate,
    create_network_policy,
    delete_network_policy,
    update_network_policy,
)
from app.core.database import get_db
from app.core.errors import CDSError
from app.main import app
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services.network_policy import network_policy_engine

POLICY_URL = "/api/v1/network-policies"


class _EngineSpy:
    """强制面记录器：宿主 netns/iptables 面在测试里必须是它，否则测试就在改宿主网络。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_create(session_id: str, config, db=None):
            self.calls.append(("create", session_id, config.mode))
            return {"session_id": session_id, "mode": config.mode}

        async def _fake_remove(session_id: str, db=None):
            self.calls.append(("remove", session_id))

        monkeypatch.setattr(network_policy_engine, "create_policy", _fake_create)
        monkeypatch.setattr(network_policy_engine, "remove_policy", _fake_remove)


class _EmptyResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _StaleSnapshot:
    """把**第一条**命中 network_policies 的 SELECT 装作空结果 = 并发窗口里「先查」扑空。

    其余调用原样转发，因此后面的 add+flush 必须自己去撞真实唯一约束。
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


class _RollbackSpy:
    """数请求会话被 rollback 的次数（A20 铁律：冲突出口不得自己回滚请求事务）。"""

    def __init__(self, inner: AsyncSession) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "rollbacks", 0)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def rollback(self) -> None:
        object.__setattr__(self, "rollbacks", self.rollbacks + 1)
        await self._inner.rollback()


async def _seed_session(db_session: AsyncSession, user_id: str, *, commit: bool = False) -> str:
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


async def _seed_policy(
    db_session: AsyncSession, user_id: str, *, mode: str = "deny_all"
) -> tuple[str, uuid.UUID]:
    """真实 get_db 的新会话看得见 seed 行 ⇒ 必须 commit。"""
    sid = await _seed_session(db_session, user_id, commit=False)
    policy = NetworkPolicy(
        session_id=sid,
        user_id=user_id,
        mode=mode,
        allowed_ips=[],
        allowed_domains=[],
        allowed_ports=[443],
        active=True,
    )
    db_session.add(policy)
    await db_session.commit()
    return sid, uuid.UUID(str(policy.id))


async def _open_real_get_db(user_id: str):
    """真实 get_db（会 commit、会 drain），不是 conftest 那个既不提交也不回滚的 override。"""
    agen = cast(AsyncGenerator[AsyncSession, None], get_db())
    db = await agen.__anext__()
    user = await db.get(User, uuid.UUID(user_id))
    assert user is not None
    return agen, db, user


async def _finish(agen) -> None:
    """把生成器跑到 commit（异常按原样抛出，让用例自己判定）。"""
    try:
        await agen.__anext__()
    except StopAsyncIteration:
        pass


@pytest.mark.asyncio
async def test_unique_conflict_response_carries_semantic_code(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    """A20-1: 先查扑空、INSERT 撞唯一约束 ⇒ W2 体必须是领域码，不是兜底的 HTTP_409。"""
    headers, uid = await make_user("data_provider", "a20code")
    sid = await _seed_session(db_session, uid)
    # 赢家：并发窗口里先提交的那条（真实唯一索引 network_policies.session_id unique）
    db_session.add(
        NetworkPolicy(
            session_id=sid, user_id=uid, mode="deny_all", allowed_ips=[], allowed_domains=[]
        )
    )
    await db_session.flush()

    original = app.dependency_overrides[get_db]

    async def stale_override():
        async for session in original():
            yield _StaleSnapshot(session)

    app.dependency_overrides[get_db] = stale_override
    try:
        resp = await client.post(
            POLICY_URL,
            json={"session_id": sid, "mode": "deny_all", "allowed_ips": ["10.0.0.0/8"]},
            headers=headers,
        )
    finally:
        app.dependency_overrides[get_db] = original

    assert resp.status_code == 409, f"A20: 冲突必须是 409，实际 {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body.get("code") == "NETWORK_POLICY_EXISTS", (
        "A20: 唯一约束冲突没有领域错误码（客户端只能按 HTTP_409 猜）——W2 要求 "
        f"{{code,message,detail,request_id}}，实际: {body}"
    )
    assert "already exists" in (body.get("message") or ""), (
        f"A20: 冲突文案必须与先查分支同一句（客户端按同一提示走），实际: {body}"
    )


@pytest.mark.asyncio
async def test_conflict_handler_does_not_roll_back_request_session(
    db_session: AsyncSession,
    make_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A20-1b: 冲突出口只准吞掉那条 INSERT —— 处理器自身不得 rollback 请求会话。

    本仓铁律（`app/core/AGENTS.md`）：「Isolate a single statement with a SAVEPOINT,
    never session.rollback()」—— 请求会话 rollback 会 expire 整个 session，后续
    同步属性读取会 MissingGreenlet。同时核对强制面一个字节都未收到、
    请求尾回滚后记录面干净。

    如实边界（不是缺陷）：flush 失败后 ORM 仍会把会话标成 PendingRollback，
    「冲突后还能继续查该会话」不是可依赖的属性；真实链路里请求尾必由 get_db 统一回滚。
    """
    spy = _EngineSpy()
    spy.install(monkeypatch)
    _headers, uid = await make_user("data_provider", "a20tx")
    sid, _winner_id = await _seed_policy(db_session, uid)  # 赢家：已 commit 的 session_id 唯一行
    other_sid = await _seed_session(db_session, uid, commit=True)

    agen, db, user = await _open_real_get_db(uid)
    counted = _RollbackSpy(db)
    blinded = cast(AsyncSession, cast(object, _StaleSnapshot(counted)))
    try:
        # 同一请求事务里的另一条业务写入（之后随请求尾回滚一起丢弃）
        counted.add(
            NetworkPolicy(
                session_id=other_sid,
                user_id=uid,
                mode="deny_all",
                allowed_ips=[],
                allowed_domains=[],
                allowed_ports=[443],
            )
        )
        await counted.flush()

        with pytest.raises(CDSError) as raised:
            await create_network_policy(
                NetworkPolicyCreate(session_id=sid, mode="deny_all", allowed_ips=["10.0.0.0/8"]),
                db=blinded,
                current_user=user,
            )
        assert raised.value.code == "NETWORK_POLICY_EXISTS", (
            f"A20: 冲突没走领域错误，而是 legacy HTTPException: {raised.value!r}"
        )
        assert raised.value.http_status == 409
        assert counted.rollbacks == 0, (
            f"A20: 冲突出口自己 rollback 了请求会话（铁律禁止）: {counted.rollbacks} 次"
        )
        assert spy.calls == [], f"A20: 冲突请求仍动了强制面: {spy.calls}"

        with pytest.raises(CDSError):
            await agen.athrow(raised.value)  # 请求尾：get_db 统一回滚并原样抛出
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 - 已自然结束的生成器
            pass

    from app.core.database import async_session as session_factory

    async with session_factory() as check:
        winner = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalars().all()
        assert len(winner) == 1, f"A20: 输家动了赢家的记录面行（{len(winner)} 行）"
        assert winner[0].allowed_ips in ([], None), (
            f"输家的值不得覆盖赢家: {winner[0].allowed_ips}"
        )
        discarded = (
            await check.execute(
                select(NetworkPolicy).where(NetworkPolicy.session_id == other_sid)
            )
        ).scalars().all()
        assert discarded == [], (
            "A20: 失败请求的半途写入泄漏到了库里（应随请求尾回滚一起丢弃）"
        )


@pytest.mark.asyncio
async def test_update_defers_enforcement_until_after_commit(
    db_session: AsyncSession,
    make_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A20-2: PATCH 在 commit 之前不得动强制面；commit 成功后 drain 才按序 remove+create。"""
    spy = _EngineSpy()
    spy.install(monkeypatch)
    _headers, uid = await make_user("data_provider", "a20upd")
    sid, policy_id = await _seed_policy(db_session, uid)

    agen, db, user = await _open_real_get_db(uid)
    try:
        await update_network_policy(
            policy_id,
            NetworkPolicyUpdate(mode="allowlist", allowed_ips=["10.0.0.0/8"]),
            db=db,
            current_user=user,
        )
        assert spy.calls == [], (
            "A20: update 在 commit 之前就动了强制面 —— 提交失败时运行面会与库里已提交的"
            f"策略分叉（此处旧策略已被拆掉）: {spy.calls}"
        )

        await _finish(agen)  # commit 成功 → drain
        assert spy.calls == [("remove", sid), ("create", sid, "allowlist")], (
            f"A20: commit 成功后 drain 必须按登记顺序把新策略装上，实际: {spy.calls}"
        )
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass


@pytest.mark.asyncio
async def test_update_with_failed_commit_leaves_enforcement_untouched(
    db_session: AsyncSession,
    make_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A20-2b: 提交失败 ⇒ 引擎一个字节都收不到，运行面保持旧策略（记录侧与强制侧不分叉）。"""
    spy = _EngineSpy()
    spy.install(monkeypatch)
    _headers, uid = await make_user("data_provider", "a20updbad")
    sid, policy_id = await _seed_policy(db_session, uid)

    agen, db, user = await _open_real_get_db(uid)
    try:
        await update_network_policy(
            policy_id,
            NetworkPolicyUpdate(mode="allowlist", allowed_ips=["10.0.0.0/8"]),
            db=db,
            current_user=user,
        )
        assert spy.calls == []

        def _boom(session):
            raise RuntimeError("commit boom")

        event.listen(db.sync_session, "before_commit", _boom)
        with pytest.raises(RuntimeError, match="commit boom"):
            await agen.__anext__()
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass

    assert spy.calls == [], (
        f"A20: 提交失败后强制面仍被改动（旧策略被拆/新策略已装）: {spy.calls}"
    )

    from app.core.database import async_session as session_factory

    async with session_factory() as check:
        row = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalar_one_or_none()
        assert row is not None, "A20: 提交失败的更新丢掉了记录面行"
        assert str(row.mode) == "deny_all", f"A20: 提交失败的更新污染了记录面: {row.mode}"


@pytest.mark.asyncio
async def test_delete_defers_enforcement_until_after_commit(
    db_session: AsyncSession,
    make_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A20-3: DELETE 先拆强制面、事务却还没成 ⇒ 提交失败时策略在库里却已不生效。"""
    spy = _EngineSpy()
    spy.install(monkeypatch)
    _headers, uid = await make_user("data_provider", "a20del")
    sid, policy_id = await _seed_policy(db_session, uid)

    agen, db, user = await _open_real_get_db(uid)
    try:
        await delete_network_policy(policy_id, db=db, current_user=user)
        assert spy.calls == [], (
            "A20: delete 在 commit 之前就撤了强制面 —— 提交失败时库里的策略行还在，"
            f"运行面却已放行（零信任默认失效）: {spy.calls}"
        )

        def _boom(session):
            raise RuntimeError("commit boom")

        event.listen(db.sync_session, "before_commit", _boom)
        with pytest.raises(RuntimeError, match="commit boom"):
            await agen.__anext__()
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass

    assert spy.calls == [], f"A20: 提交失败的删除仍撤掉了强制面: {spy.calls}"

    from app.core.database import async_session as session_factory

    async with session_factory() as check:
        row = (
            await check.execute(select(NetworkPolicy).where(NetworkPolicy.session_id == sid))
        ).scalar_one_or_none()
        assert row is not None, "A20: 提交失败的删除不得丢掉记录面行"
        kept_id = uuid.UUID(str(row.id))

    agen2, db2, user2 = await _open_real_get_db(uid)
    await delete_network_policy(kept_id, db=db2, current_user=user2)
    assert spy.calls == [], "A20: delete 的强制面撤销必须等 commit（drain）"
    await _finish(agen2)
    assert spy.calls == [("remove", sid)], (
        f"A20: commit 成功后 drain 必须撤销强制面，实际: {spy.calls}"
    )
