"""A169: webhook 派发窗口不得被已暂停订阅的滞留行卡住（订阅状态下推进 SELECT）。

暂停订阅（MAX_CONSECUTIVE_FAILURES 自动降级）的 pending 行若占满 limit 窗口，
活跃订阅永远排不到；订阅已删除的行仍须保留死信分支。deliver_once 被替换为
记录桩——被测对象是窗口选取，不是 HTTP/签名路径。
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.models.user import User, UserRole
from app.models.webhook import WebhookStatus, WebhookSubscription
from app.models.webhook_delivery import DeliveryStatus, WebhookDelivery
from app.services import delivery_service as dv


async def _mk(db, *, status: str, n: int, t0: datetime):
    user = User(username=f"a169-{uuid.uuid4().hex[:8]}", email=f"a169-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password="x", role=UserRole.BUYER)
    db.add(user)
    await db.flush()
    sub = WebhookSubscription(user_id=user.id, name="s", url="http://receiver.invalid/hook",
                              events=["compute_run.succeeded"], status=status)
    db.add(sub)
    await db.flush()
    rows = []
    for i in range(n):
        d = WebhookDelivery(subscription_id=sub.id, event_type="compute_run.succeeded",
                            event_id=uuid.uuid4().hex, payload={}, status=DeliveryStatus.PENDING.value,
                            created_at=t0 + timedelta(minutes=i))
        db.add(d)
        rows.append(d)
    await db.flush()
    return sub, rows


async def test_paused_backlog_does_not_starve_active_subscription(db_session, monkeypatch):
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    paused_sub, paused_rows = await _mk(db_session, status=WebhookStatus.PAUSED.value, n=2, t0=t0)
    active_sub, active_rows = await _mk(db_session, status=WebhookStatus.ACTIVE.value, n=1,
                                        t0=t0 + timedelta(hours=1))

    attempted = []

    async def fake_deliver(db, delivery, subscription):
        attempted.append(str(delivery.subscription_id))
        delivery.status = DeliveryStatus.DELIVERED.value

    monkeypatch.setattr(dv, "deliver_once", fake_deliver)
    counters = await dv.dispatch_pending(db_session, limit=2)

    assert counters["attempted"] == 1
    assert attempted == [str(active_sub.id)]
    assert active_rows[0].status == DeliveryStatus.DELIVERED.value
    for row in paused_rows:
        assert row.status == DeliveryStatus.PENDING.value


async def test_orphan_delivery_still_dead_letters(db_session):
    orphan = WebhookDelivery(subscription_id=None, event_type="compute_run.succeeded",
                             event_id=uuid.uuid4().hex, payload={}, status=DeliveryStatus.PENDING.value)
    db_session.add(orphan)
    await db_session.flush()

    counters = await dv.dispatch_pending(db_session, limit=5)

    assert counters["attempted"] == 0
    assert orphan.status == DeliveryStatus.DEAD.value
