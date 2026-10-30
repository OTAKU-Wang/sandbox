"""A123b (ocr CR 2026-10-18 第二半): 级联写入方仍硬编码 TERMINATED，REVOKED 无人写入。

A123 的前半（backend-19b）给六个活跃态补了 →REVOKED 入边，但唯一的密钥吊销级联
`sandbox_runtime.terminate_sessions_by_key` 仍把每一行写成 `TERMINATED`，
`session.error_message` 也写「terminated」⇒ 对外可见的 `revoked` 枚举 + 文案
（"Session revoked (key revoked)"）在盘上仍然零写入方，前端已就绪的
「已撤销」标签永远不出现。

修法语义（按台账「在吊销时真用」）：级联的原因决定终态——
吊销类原因（`key_revoked` / `certificate_revoked:<serial>`）写 REVOKED，
非吊销的关闭（`key_ttl_expired`，密钥 TTL 到期）仍是 TERMINATED。
容器驱逐腿与审计键 `terminated_sessions` 不变（A121 的内存驱逐、
test_p0_security_gaps 的响应键断言都不受影响）。
"""
import uuid

import pytest

from app.models.sandbox_session import SandboxSession, SessionStatus as S
from app.services.sandbox_runtime import sandbox_runtime

pytestmark = pytest.mark.asyncio


async def _user(db_session) -> uuid.UUID:
    from app.models.user import User

    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"a123b_{unique}", email=f"a123b_{unique}@example.com",
        hashed_password="x", role="data_scientist",
    )
    db_session.add(user)
    await db_session.flush()
    return user.id


async def _seed(db_session, user_id, key_id: str, status: str, container: str | None = None):
    row = SandboxSession(
        id=uuid.uuid4(), user_id=user_id, data_product_id=uuid.uuid4(),
        sandbox_level="L0", sandbox_mode="structured_query",
        status=status, container_id=container, session_key_id=key_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _status_by_id(db_session, rows) -> dict[str, str]:
    out = {}
    for row in rows:
        await db_session.refresh(row)
        out[str(row.id)] = row.status
    return out


async def test_revocation_cascade_lands_live_sessions_in_revoked(db_session, monkeypatch):
    user_id = await _user(db_session)
    key_id = f"key-{uuid.uuid4().hex}"
    live = [
        S.PENDING.value, S.KEY_DISTRIBUTING.value, S.PROVISIONING.value,
        S.READY.value, S.RUNNING.value, S.SUSPENDED.value,
    ]
    rows = [await _seed(db_session, user_id, key_id, st) for st in live]

    count = await sandbox_runtime.terminate_sessions_by_key(key_id, db_session, reason="key_revoked")

    assert count == len(live)
    statuses = await _status_by_id(db_session, rows)
    assert sorted(statuses.values()) == sorted([S.REVOKED.value] * len(live)), (
        f"A123b: 吊销级联写入的终态={sorted(set(statuses.values()))}，应为 revoked"
    )
    for row in rows:
        assert row.ended_at is not None
        assert "key_revoked" in (row.error_message or "")


async def test_revoked_is_reachable_in_db_by_the_production_route(client, db_session, admin_headers):
    resp = await client.post(
        "/api/v1/kms/keys", params={"product_id": str(uuid.uuid4())}, headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    key_id = resp.json()["key_id"]
    user_id = await _user(db_session)
    session = await _seed(db_session, user_id, key_id, S.RUNNING.value)

    revoked = await client.request(
        "DELETE", f"/api/v1/kms/keys/{key_id}",
        json={"reason": "A123b cascade probe", "ticket_id": "T-1"}, headers=admin_headers,
    )
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["status"] == "revoked"
    assert body["terminated_sessions"] == 1

    await db_session.refresh(session)
    assert session.status == S.REVOKED.value, (
        f"A123b: DELETE /kms/keys 之后会话落在 {session.status}，对外文案 revoked 仍无写入方"
    )


async def test_non_revocation_close_stays_terminated(db_session):
    user_id = await _user(db_session)
    key_id = f"key-{uuid.uuid4().hex}"
    session = await _seed(db_session, user_id, key_id, S.RUNNING.value)

    await sandbox_runtime.terminate_sessions_by_key(key_id, db_session, reason="key_ttl_expired")

    await db_session.refresh(session)
    assert session.status == S.TERMINATED.value, "TTL 到期不是吊销，不得伪装成 revoked"


async def test_terminal_rows_are_never_rewritten_by_a_later_revocation(db_session):
    user_id = await _user(db_session)
    key_id = f"key-{uuid.uuid4().hex}"
    settled = [
        (S.COMPLETED.value, "completed"), (S.FAILED.value, "failed"),
        (S.TERMINATED.value, "terminated"), (S.REVOKED.value, "revoked"),
    ]
    rows = {status: await _seed(db_session, user_id, key_id, status) for status, _ in settled}

    count = await sandbox_runtime.terminate_sessions_by_key(key_id, db_session, reason="key_revoked")

    assert count == 0
    for status, expected in settled:
        await db_session.refresh(rows[status])
        assert rows[status].status == expected


async def test_container_eviction_leg_still_runs_for_revoked_sessions(db_session, monkeypatch):
    user_id = await _user(db_session)
    key_id = f"key-{uuid.uuid4().hex}"
    session = await _seed(db_session, user_id, key_id, S.RUNNING.value, container="zz-no-such-adapter")
    seen: list[str] = []
    monkeypatch.setattr(sandbox_runtime, "terminate", lambda cid: seen.append(cid) or True)

    await sandbox_runtime.terminate_sessions_by_key(key_id, db_session, reason="key_revoked")

    assert seen == [session.container_id], "改成 REVOKED 不得丢掉容器驱逐腿"
    await db_session.refresh(session)
    assert session.status == S.REVOKED.value
