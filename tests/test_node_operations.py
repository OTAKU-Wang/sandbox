"""W14: node operations — isolate/unisolate, selection filtering, stale detection."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.core.errors import ResourceExhausted
from app.models.alert import AlertRecord
from app.models.sandbox_node import SandboxNode, NodeStatus
from app.services.node_selector import NodeSelector
from sqlalchemy import select


async def _mk_node(db_session, *, node_id: str, online=True, heartbeat_age_s=10,
                   scheduling_disabled=False) -> SandboxNode:
    node = SandboxNode(
        node_id=node_id,
        hostname=f"host-{node_id}",
        ip_address="10.0.0.1",
        status=NodeStatus.ONLINE.value if online else NodeStatus.OFFLINE.value,
        capabilities=["cpu"],
        scheduling_disabled=scheduling_disabled,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=heartbeat_age_s),
    )
    db_session.add(node)
    await db_session.flush()
    return node


@pytest_asyncio.fixture
async def admin(db_session):
    from app.models.user import User

    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"nodeadmin_{unique}",
        email=f"nodeadmin_{unique}@example.com",
        hashed_password="x",
        role="admin",
    )
    db_session.add(user)
    await db_session.flush()
    from app.services.auth_service import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.mark.asyncio
async def test_isolate_excludes_node_from_selection(client, db_session, admin):
    await _mk_node(db_session, node_id="node-a", scheduling_disabled=False)
    await _mk_node(db_session, node_id="node-b", scheduling_disabled=False)

    selector = NodeSelector()
    result = await selector.select(db_session)
    assert result.selected is not None

    resp = await client.post("/api/v1/sandbox-nodes/node-a/isolate", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["scheduling_disabled"] is True

    result2 = await selector.select(db_session)
    assert result2.selected is not None
    assert result2.selected.node_id == "node-b"


@pytest.mark.asyncio
async def test_all_isolated_raises_no_node_available(client, db_session, admin):
    await _mk_node(db_session, node_id="node-c", scheduling_disabled=False)
    resp = await client.post("/api/v1/sandbox-nodes/node-c/isolate", headers=admin)
    assert resp.status_code == 200

    selector = NodeSelector()
    with pytest.raises(ResourceExhausted) as exc_info:
        await selector.select(db_session)
    assert exc_info.value.code == "NO_NODE_AVAILABLE"
    assert exc_info.value.detail["disabled_count"] == 1


@pytest.mark.asyncio
async def test_unisolate_restores_scheduling(client, db_session, admin):
    node = await _mk_node(db_session, node_id="node-d", scheduling_disabled=True)
    node.health_state = "isolated"

    resp = await client.post("/api/v1/sandbox-nodes/node-d/unisolate", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["scheduling_disabled"] is False

    await db_session.refresh(node)
    assert node.scheduling_disabled is False

    selector = NodeSelector()
    result = await selector.select(db_session)
    assert result.selected is not None and result.selected.node_id == "node-d"


@pytest.mark.asyncio
async def test_isolate_requires_admin(client, db_session, admin):
    await _mk_node(db_session, node_id="node-e")
    from app.services.auth_service import create_access_token
    from app.models.user import User

    unique = uuid.uuid4().hex[:8]
    provider = User(
        username=f"prov_{unique}",
        email=f"prov_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(provider)
    await db_session.flush()

    resp = await client.post(
        "/api/v1/sandbox-nodes/node-e/isolate",
        headers={"Authorization": f"Bearer {create_access_token(provider.id, provider.role)}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_stale_heartbeat_marks_node_and_alerts(db_session, monkeypatch):
    node = await _mk_node(db_session, node_id="node-stale", heartbeat_age_s=600)
    settings = get_settings()
    monkeypatch.setattr(settings, "NODE_STALE_SECONDS", 300)

    from app.api.sandbox_nodes import detect_stale_nodes

    count = await detect_stale_nodes(db_session)
    assert count == 1
    await db_session.refresh(node)
    assert node.health_state == "stale"

    alert = await db_session.execute(
        select(AlertRecord).where(AlertRecord.dedup_key == "node-stale-node-stale")
    )
    assert alert.scalar_one_or_none() is not None

    # idempotent: second sweep does not re-alert
    assert await detect_stale_nodes(db_session) == 0


@pytest.mark.asyncio
async def test_node_list_requires_and_allows_admin(client, db_session, admin):
    await _mk_node(db_session, node_id="node-f")
    resp = await client.get("/api/v1/sandbox-nodes", headers=admin)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["nodes"][0]["health_state"] in ("unknown", "healthy", "stale", "isolated")


@pytest.mark.asyncio
async def test_recover_takes_hopeless_nodes_offline(db_session, monkeypatch):
    from app.api.sandbox_nodes import detect_stale_nodes, recover_stale_nodes

    hopeless = await _mk_node(db_session, node_id="node-dead", heartbeat_age_s=700)
    recovering = await _mk_node(db_session, node_id="node-slow", heartbeat_age_s=310)
    settings = get_settings()
    monkeypatch.setattr(settings, "NODE_STALE_SECONDS", 300)

    assert await detect_stale_nodes(db_session) == 2
    assert await recover_stale_nodes(db_session) == 1

    await db_session.refresh(hopeless)
    await db_session.refresh(recovering)
    assert hopeless.status == NodeStatus.OFFLINE.value
    assert hopeless.health_state == "offline"
    # within the 2x grace window: stays stale, scheduling keeps trying it
    assert recovering.status == NodeStatus.ONLINE.value
    assert recovering.health_state == "stale"

    alert = await db_session.execute(
        select(AlertRecord).where(AlertRecord.dedup_key == "node-offline-node-dead")
    )
    assert alert.scalar_one_or_none() is not None

    # idempotent sweep
    assert await recover_stale_nodes(db_session) == 0
