"""ocr A5: 锚定/验真三处自相矛盾（audit.py）。

钉死的判定：
1. verification_note 必须跟随 verified 结论——锚存在但重算根不一致时，
   面向监管的文案不得写「校验通过」（前端只读文案就会误判）；
2. 已锚定行不得被重锚定改写 tx_hash——/verify 是按当前 tx_hash 反查兄弟
   记录重算根的，把成员从旧锚点里抽走会让「未篡改」的旧成员验不过（假阳性）。
   重锚定请求只处理未锚定行；全部已锚定 ⇒ 409；混合请求诚实披露跳过数；
3. AnchorRequest.record_ids 有长度上限——巨型 IN + 同步建树不得跑在事件循环里。
"""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

pytestmark = pytest.mark.asyncio

BASE_TIME = datetime(2026, 10, 18, 9, 0, 0, tzinfo=timezone.utc)


async def _seed(db: AsyncSession, prefix: str, count: int) -> list[AuditLog]:
    rows = []
    for i in range(count):
        row = AuditLog(
            id=uuid.uuid4(),
            user_id=None,
            session_id=None,
            action=f"a5.integrity.{prefix}{i}",
            resource_type="a5_integrity_probe",
            resource_id=f"a5-{prefix}-{i}",
            detail={"probe": prefix},
            created_at=BASE_TIME,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


async def _verify(client: AsyncClient, headers: dict, row: AuditLog) -> dict:
    resp = await client.get(f"/api/v1/audit/verify/{row.id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_verification_note_follows_outcome(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict
):
    """verified=False 时 verification_note 不得宣称「校验通过」。"""
    rows = await _seed(db_session, "note-", 2)
    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(r.id) for r in rows]},
        headers=operator_headers,
    )
    assert resp.status_code == 200, resp.text

    body = await _verify(client, operator_headers, rows[0])
    assert body["verified"] is True
    assert "校验通过" in body["verification_note"]

    # 篡改其一（leaf 字节含 action，重算根必然偏离锚定值）
    rows[1].action = "a5.integrity.TAMPERED"
    await db_session.flush()

    body = await _verify(client, operator_headers, rows[1])
    assert body["verified"] is False
    note = body["verification_note"]
    assert "校验通过" not in note, f"校验失败但文案仍宣称通过: {note!r}"


async def test_reanchor_does_not_steal_anchored_members(
    client: AsyncClient, db_session: AsyncSession, operator_headers: dict
):
    """重锚定不得把已锚定行从旧锚点抽走（旧成员必须仍可验真）。"""
    r1, r2, r3 = await _seed(db_session, "rea-", 3)
    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(r1.id), str(r2.id)]},
        headers=operator_headers,
    )
    assert resp.status_code == 200
    tx1 = resp.json()["tx_hash"]
    assert tx1

    # 全部已锚定的重放请求 ⇒ 拒绝，且不动任何 tx_hash
    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(r1.id), str(r2.id)]},
        headers=operator_headers,
    )
    assert resp.status_code == 409, resp.text

    # 混合请求：只有未锚定的 r3 被锚定，r1 不得被改写
    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(r1.id), str(r3.id)]},
        headers=operator_headers,
    )
    assert resp.status_code == 200, resp.text
    mixed = resp.json()
    assert mixed["anchored"] == 1
    assert mixed["skipped_already_anchored"] == 1
    assert mixed["tx_hash"] != tx1

    await db_session.refresh(r1)
    await db_session.refresh(r2)
    await db_session.refresh(r3)
    assert r1.blockchain_tx_hash == tx1
    assert r2.blockchain_tx_hash == tx1
    assert r3.blockchain_tx_hash == mixed["tx_hash"]

    # 关键回归：旧锚点成员集合未变，r1/r2 依旧验证通过（不是「未篡改却验不过」）
    for row in (r1, r2):
        body = await _verify(client, operator_headers, row)
        assert body["verified"] is True, body
    body = await _verify(client, operator_headers, r3)
    assert body["verified"] is True, body


async def test_anchor_rejects_oversized_batch(
    client: AsyncClient, operator_headers: dict
):
    """record_ids 无上限 ⇒ 巨型 IN + 事件循环内同步建树；必须有长度闸门。"""
    resp = await client.post(
        "/api/v1/audit/anchor",
        json={"record_ids": [str(uuid.uuid4()) for _ in range(1001)]},
        headers=operator_headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"
