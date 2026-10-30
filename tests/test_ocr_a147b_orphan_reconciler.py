"""A147b (backend-20 转交的后半腿): 状态漂移之后没人收敛。

A147 把「容器已拉起后的异常」这条路堵住了（`contract_fulfillment` 会销毁容器并把会话
写成 FAILED），但账本明写的另一半还在：会话行停在非终态而容器早没了（节点重启、进程被
OOM 杀掉、外部 `docker rm`），或者反过来——行已是终态而容器还在跑（泄漏的容器占着
`cds-data-<session_id>` 卷）。此前没有任何一方收敛这两种漂移：唯一会扫活跃行的
`cleanup_expired_sessions` 只看时间，不看运行面真相。

要求（父会话原话）：**周期性 reconciler** 要「可被外部触发（不靠 sleep 常驻）、有幂等
测试、运行痕迹可审计」，并且禁止用固定 sleep 或常驻协程糊测试。

本文件因此只调用被触发的入口 `sandbox_runtime.reconcile_container_state(db)`，用假
`get_status` / 假 `terminate` 当作运行面探针边界，不启动任何协程、不 sleep。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.user import User
from app.services.sandbox_runtime import sandbox_runtime

pytestmark = pytest.mark.asyncio

RECONCILE_ACTION = "session.container_state_reconciled"


async def _user(db_session: AsyncSession) -> uuid.UUID:
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"a147b_{unique}", email=f"a147b_{unique}@example.com",
        hashed_password="x", role="data_scientist",
    )
    db_session.add(user)
    await db_session.flush()
    return user.id


async def _seed(db_session: AsyncSession, user_id: uuid.UUID, status: str,
                container: str | None) -> SandboxSession:
    row = SandboxSession(
        id=uuid.uuid4(), user_id=user_id, data_product_id=uuid.uuid4(),
        sandbox_level="L0", sandbox_mode="structured_query",
        status=status, container_id=container,
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _fake_status(live: set[str]):
    def _probe(container_id: str) -> str:
        return "running" if container_id in live else "terminated"

    return _probe


async def _audit_rows(db_session: AsyncSession) -> list[AuditLog]:
    result = await db_session.execute(select(AuditLog).where(AuditLog.action == RECONCILE_ACTION))
    return list(result.scalars().all())


async def test_reconciler_closes_rows_whose_container_is_gone(db_session, monkeypatch):
    user_id = await _user(db_session)
    live_states = [
        SessionStatus.PENDING.value, SessionStatus.PROVISIONING.value,
        SessionStatus.READY.value, SessionStatus.RUNNING.value,
        SessionStatus.SUSPENDED.value,
    ]
    rows = [await _seed(db_session, user_id, st, f"l0-{uuid.uuid4().hex}") for st in live_states]
    monkeypatch.setattr(sandbox_runtime, "get_status", lambda cid: "terminated")

    report = await sandbox_runtime.reconcile_container_state(db_session)

    assert report["closed"] == len(live_states), f"应收敛 {len(live_states)} 行，实得 {report}"
    for row in rows:
        await db_session.refresh(row)
        assert row.status == SessionStatus.TERMINATED.value, (
            f"A147b: 容器已消失的会话行 {row.id} 仍停在 {row.status}"
        )
        assert row.ended_at is not None
        assert "reconcil" in (row.error_message or "").lower()

    audits = await _audit_rows(db_session)
    assert len(audits) == 1, f"一轮收敛应留下 1 条审计痕迹，实得 {len(audits)}"
    detail = audits[0].detail or {}
    assert detail.get("closed") == len(live_states)
    assert sorted(detail.get("closed_session_ids") or []) == sorted(str(r.id) for r in rows), (
        "审计痕迹必须逐行列出被收敛的会话，否则事后无法复盘是谁动的"
    )


async def test_reconciler_is_idempotent(db_session, monkeypatch):
    user_id = await _user(db_session)
    row = await _seed(db_session, user_id, SessionStatus.RUNNING.value, f"l0-{uuid.uuid4().hex}")
    monkeypatch.setattr(sandbox_runtime, "get_status", lambda cid: "terminated")

    first = await sandbox_runtime.reconcile_container_state(db_session)
    second = await sandbox_runtime.reconcile_container_state(db_session)

    assert first["closed"] == 1
    assert second["closed"] == 0, f"第二轮仍写入收敛，reconciler 不幂等: {second}"
    assert second["containers_reclaimed"] == 0, f"第二轮重复回收，reconciler 不幂等: {second}"
    await db_session.refresh(row)
    assert row.status == SessionStatus.TERMINATED.value
    assert len(await _audit_rows(db_session)) == 1, "重复触发不得重复写审计痕迹"


async def test_reconciler_reclaims_containers_of_terminal_rows(db_session, monkeypatch):
    user_id = await _user(db_session)
    leaked = f"l0-{uuid.uuid4().hex}"
    live = {leaked}
    row = await _seed(db_session, user_id, SessionStatus.TERMINATED.value, leaked)
    monkeypatch.setattr(sandbox_runtime, "get_status", _fake_status(live))

    killed: list[tuple[str, str]] = []

    def _fake_terminate(cid: str, session_id=None) -> bool:
        killed.append((cid, str(session_id)))
        live.discard(cid)
        return True

    monkeypatch.setattr(sandbox_runtime, "terminate", _fake_terminate)

    report = await sandbox_runtime.reconcile_container_state(db_session)

    assert report["containers_reclaimed"] == 1, f"泄漏容器未被回收: {report}"
    assert killed == [(leaked, str(row.id))], (
        f"回收容器必须带上会话 id（卷名 cds-data-<session_id>），实得 {killed}"
    )
    audits = await _audit_rows(db_session)
    assert len(audits) == 1
    assert (audits[0].detail or {}).get("containers_reclaimed") == 1

    second = await sandbox_runtime.reconcile_container_state(db_session)
    assert second["containers_reclaimed"] == 0, f"回收腿不幂等: {second}"


async def test_reconciler_does_not_close_rows_when_container_is_live(db_session, monkeypatch):
    user_id = await _user(db_session)
    alive = f"l0-{uuid.uuid4().hex}"
    row = await _seed(db_session, user_id, SessionStatus.RUNNING.value, alive)
    monkeypatch.setattr(sandbox_runtime, "get_status", _fake_status({alive}))

    killed: list[str] = []
    monkeypatch.setattr(sandbox_runtime, "terminate", lambda cid, session_id=None: killed.append(cid) or True)

    report = await sandbox_runtime.reconcile_container_state(db_session)

    assert report == {"scanned": 1, "closed": 0, "containers_reclaimed": 0,
                      "skipped_unknown": 0, "closed_session_ids": [],
                      "reclaimed_session_ids": [], "skipped_unknown_session_ids": []}, (
        f"容器存活时 reconciler 不该有任何动作: {report}"
    )
    await db_session.refresh(row)
    assert row.status == SessionStatus.RUNNING.value
    assert killed == []
    assert await _audit_rows(db_session) == []


async def test_reconciler_refuses_to_guess_on_unknown_status(db_session, monkeypatch):
    user_id = await _user(db_session)
    row = await _seed(db_session, user_id, SessionStatus.RUNNING.value, f"nosuch-{uuid.uuid4().hex}")
    monkeypatch.setattr(sandbox_runtime, "get_status", lambda cid: "unknown")

    report = await sandbox_runtime.reconcile_container_state(db_session)

    assert report["skipped_unknown"] == 1, f"unknown 应计入披露而不是动作: {report}"
    assert report["closed"] == 0
    assert report["skipped_unknown_session_ids"] == [str(row.id)]
    await db_session.refresh(row)
    assert row.status == SessionStatus.RUNNING.value
    assert await _audit_rows(db_session) == [], "只披露不动作的轮次不该写收敛痕迹"
