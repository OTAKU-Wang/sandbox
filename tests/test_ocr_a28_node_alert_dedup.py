"""A28 (ocr CR 2026-10-18): 向 unique 列 dedup_key 无条件 INSERT，一条重复告警卡死整轮清理。

`detect_stale_nodes` / `recover_stale_nodes`（app/api/sandbox_nodes.py）是后台
清理循环里跑的（app/services/session_lifecycle.py:298-301），而
`AlertRecord.dedup_key` 是 UNIQUE（app/models/alert.py:36）。修复前只要该 node 的
告警行已经存在而 health_state 又被人复位（unisolate / 重新上线 / recover 之后再
stale），本轮就会抛 IntegrityError：同一批里后面的 node 既没被标 stale 也没了
audit 行，之后每一轮都在同一条上再撞一次。

修复后的契约：重复信号合并进已有行（occurrence_count++ / 重新打开），一条重复
不能带走整批，函数只对真正改过的 node 计数。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api.sandbox_nodes import detect_stale_nodes, recover_stale_nodes
from app.core.config import get_settings
from app.models.alert import AlertRecord, AlertStatus
from app.models.sandbox_node import NodeStatus, SandboxNode


async def _mk_node(db, *, node_id: str, heartbeat_age_s: int, health_state: str = "unknown") -> SandboxNode:
    node = SandboxNode(
        node_id=node_id,
        hostname=f"{node_id}.example",
        ip_address="10.0.0.7",
        status=NodeStatus.ONLINE.value,
        health_state=health_state,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_s),
    )
    db.add(node)
    await db.flush()
    return node


async def _seed_alert(db, *, dedup_key: str, alert_type: str, status: str) -> AlertRecord:
    record = AlertRecord(
        dedup_key=dedup_key,
        alert_type=alert_type,
        severity="warning",
        status=status,
        message="previous occurrence",
        resource_type="sandbox_node",
        resource_id=str(uuid.uuid4()),
    )
    db.add(record)
    await db.flush()
    return record


async def _alerts(db, dedup_key: str) -> list[AlertRecord]:
    rows = await db.execute(select(AlertRecord).where(AlertRecord.dedup_key == dedup_key))
    return list(rows.scalars().all())


@pytest.fixture(autouse=True)
def _stale_window(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "NODE_STALE_SECONDS", 300)
    return settings


@pytest.mark.asyncio
async def test_existing_stale_alert_does_not_break_the_sweep(db_session):
    node = await _mk_node(db_session, node_id="node-a28-a", heartbeat_age_s=600)
    previous = await _seed_alert(
        db_session,
        dedup_key="node-stale-node-a28-a",
        alert_type="node_stale",
        status=AlertStatus.RESOLVED.value,
    )
    previous_occurrences = previous.occurrence_count

    assert await detect_stale_nodes(db_session) == 1

    await db_session.refresh(node)
    assert node.health_state == "stale"
    alerts = await _alerts(db_session, "node-stale-node-a28-a")
    assert len(alerts) == 1, "the duplicate signal must merge, not insert a second row"
    assert alerts[0].occurrence_count == previous_occurrences + 1
    assert alerts[0].status == AlertStatus.OPEN.value, "a re-raised signal must reopen the alert"


@pytest.mark.asyncio
async def test_one_duplicate_alert_cannot_take_down_the_rest_of_the_batch(db_session):
    blocked = await _mk_node(db_session, node_id="node-a28-blocked", heartbeat_age_s=600)
    other = await _mk_node(db_session, node_id="node-a28-other", heartbeat_age_s=610)
    await _seed_alert(
        db_session,
        dedup_key="node-stale-node-a28-blocked",
        alert_type="node_stale",
        status=AlertStatus.ACKNOWLEDGED.value,
    )

    assert await detect_stale_nodes(db_session) == 2

    for node in (blocked, other):
        await db_session.refresh(node)
        assert node.health_state == "stale"
    assert len(await _alerts(db_session, "node-stale-node-a28-blocked")) == 1
    assert len(await _alerts(db_session, "node-stale-node-a28-other")) == 1


@pytest.mark.asyncio
async def test_recover_sweep_survives_its_own_dedup_key(db_session):
    node = await _mk_node(db_session, node_id="node-a28-dead", heartbeat_age_s=900)
    await _seed_alert(
        db_session,
        dedup_key="node-offline-node-a28-dead",
        alert_type="node_offline",
        status=AlertStatus.RESOLVED.value,
    )

    assert await recover_stale_nodes(db_session) == 1

    await db_session.refresh(node)
    assert node.status == NodeStatus.OFFLINE.value
    alerts = await _alerts(db_session, "node-offline-node-a28-dead")
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].occurrence_count == 2


@pytest.mark.asyncio
async def test_repeated_detection_counts_only_nodes_it_actually_changed(db_session):
    await _mk_node(db_session, node_id="node-a28-repeat", heartbeat_age_s=600)

    assert await detect_stale_nodes(db_session) == 1
    # Already stale: nothing left to write, so nothing is claimed.
    assert await detect_stale_nodes(db_session) == 0
    assert len(await _alerts(db_session, "node-stale-node-a28-repeat")) == 1

    # Operator unisolates the node (health_state reset) while the old alert row
    # stays behind — the exact state that used to poison the next sweep.
    node = (await db_session.execute(
        select(SandboxNode).where(SandboxNode.node_id == "node-a28-repeat")
    )).scalar_one()
    node.health_state = "healthy"
    await db_session.flush()

    assert await detect_stale_nodes(db_session) == 1
    alerts = await _alerts(db_session, "node-stale-node-a28-repeat")
    assert len(alerts) == 1
    assert alerts[0].occurrence_count == 2
