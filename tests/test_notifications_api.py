"""Task 12（2026-11-03 realignment 计划）：聚合通知中心 API.

计划草案的聚合口径按代码实测派生：
- 「待签署合约」：真实状态机里没有 ``PENDING_APPROVAL``——待签 = 我是合约当事方
  且我方 ``*_signed_at`` 为空、状态仍在 draft/negotiating。
- 「待审批出域」：不存在 OutputControl 请求表；真实的出域审批承载是
  ``SandboxTask.status == output_review``（任务生命周期注释即此语义），
  审批人是 operator/admin，所以这一类待办只对运营角色出现。
- 合成待办项的 id 是 ``contract:<uuid>`` / ``task:<uuid>`` 前缀形态，
  不可标记已读（它们随真实状态流转消失，不落 UserNotification 行）。
持久化通知 POST 的 user_id 由后端强制为当前用户——接口不提供代发他人能力。
"""
import uuid
from datetime import datetime, timezone

from app.models.contract import Contract
from app.models.sandbox_session import SandboxSession
from app.models.sandbox_task import SandboxTask, TaskStatus


async def _mk_contract(db, provider_id, buyer_id, **kw):
    payload = {
        "contract_no": f"CT-{uuid.uuid4().hex[:12]}",
        "contract_type": "data_usage",
        "status": "negotiating",
        "provider_id": uuid.UUID(str(provider_id)),
        "buyer_id": uuid.UUID(str(buyer_id)),
        "title": "通知中心测试合约",
        "product_ids": [str(uuid.uuid4())],
    }
    payload.update(kw)
    contract = Contract(**payload)
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


class TestNotificationCrud:
    async def test_create_list_and_mark_read(self, client, db_session, make_user):
        headers, _ = await make_user(role="buyer", prefix="ntf_u")
        resp = await client.post(
            "/api/v1/monitoring/notifications",
            json={
                "category": "todo",
                "title": "数据产品上架待审批",
                "content": "请尽快审核「企业纳税信用产品」",
                "target_path": "/data-products",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        notif = resp.json()
        assert notif["is_read"] is False
        assert notif["category"] == "todo"

        lst = await client.get("/api/v1/monitoring/notifications", headers=headers)
        assert lst.status_code == 200
        data = lst.json()
        assert data["unread_count"] >= 1
        assert any(item["id"] == notif["id"] for item in data["items"])
        assert {"items", "total", "unread_count", "todo_count"} <= set(data)

        read = await client.post(f"/api/v1/monitoring/notifications/{notif['id']}/read", headers=headers)
        assert read.status_code == 200
        assert read.json()["is_read"] is True

        lst2 = await client.get("/api/v1/monitoring/notifications", headers=headers)
        mine = [i for i in lst2.json()["items"] if i["id"] == notif["id"]]
        assert mine and mine[0]["is_read"] is True

    async def test_other_user_cannot_read_mine(self, client, db_session, make_user):
        headers_a, _ = await make_user(role="buyer", prefix="ntf_a")
        headers_b, _ = await make_user(role="buyer", prefix="ntf_b")
        resp = await client.post(
            "/api/v1/monitoring/notifications", json={"category": "system", "title": "私有提醒"}, headers=headers_a
        )
        nid = resp.json()["id"]
        foreign = await client.post(f"/api/v1/monitoring/notifications/{nid}/read", headers=headers_b)
        assert foreign.status_code == 404
        lst_b = await client.get("/api/v1/monitoring/notifications", headers=headers_b)
        assert not any(i["id"] == nid for i in lst_b.json()["items"])


class TestLiveTodos:
    async def test_unsigned_contract_shows_as_todo_for_party(self, client, db_session, make_user):
        headers, provider_id = await make_user(role="data_provider", prefix="ntf_p")
        _, buyer_id = await make_user(role="buyer", prefix="ntf_b2")
        contract = await _mk_contract(db_session, provider_id, buyer_id)

        data = (await client.get("/api/v1/monitoring/notifications", headers=headers)).json()
        contract_todos = [i for i in data["items"] if i["id"] == f"contract:{contract.id}"]
        assert contract_todos, "未签署的 negotiating 合约必须进我的待办"
        assert data["todo_count"] >= 1

        # 签完即消失：待办是实时聚合，不落表
        contract.provider_signed_at = datetime.now(timezone.utc)
        await db_session.flush()
        data2 = (await client.get("/api/v1/monitoring/notifications", headers=headers)).json()
        assert not any(i["id"] == f"contract:{contract.id}" for i in data2["items"])

    async def test_output_review_task_todo_only_for_operator(self, client, db_session, make_user):
        headers_op, op_id = await make_user(role="operator", prefix="ntf_op")
        headers_buyer, buyer_id = await make_user(role="buyer", prefix="ntf_b3")
        from app.models.user import User
        from sqlalchemy import select

        buyer = (await db_session.execute(select(User).where(User.id == uuid.UUID(str(buyer_id))))).scalar_one()
        session = SandboxSession(
            user_id=buyer.id,
            data_product_id=uuid.uuid4(),
            sandbox_level="L1",
            status="ready",
            container_id=f"ntf-{uuid.uuid4().hex[:8]}",
            timeout_seconds=600,
        )
        db_session.add(session)
        await db_session.flush()
        task = SandboxTask(
            task_id=f"ntf-task-{uuid.uuid4().hex[:8]}",
            session_id=session.id,
            user_id=buyer.id,
            status=TaskStatus.OUTPUT_REVIEW.value,
            purpose="出域待审",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
        await db_session.flush()

        data_op = (await client.get("/api/v1/monitoring/notifications", headers=headers_op)).json()
        assert any(i["id"] == f"task:{task.id}" for i in data_op["items"])
        data_buyer = (await client.get("/api/v1/monitoring/notifications", headers=headers_buyer)).json()
        assert not any(i["id"] == f"task:{task.id}" for i in data_buyer["items"])
