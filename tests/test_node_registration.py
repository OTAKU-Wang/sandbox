"""D-e2e-14: admin node registration endpoint (POST /api/v1/sandbox-nodes).

The registry previously had no runtime population path (NodeSelector.register_node
was test-only), so no node could ever enter scheduling. The endpoint must:
- require admin (non-admin 403)
- require capabilities.cubelet_endpoint (422 without it)
- create the node row and audit it
- NOT fake health: probe-gated (probe ok -> healthy/online, else offline)
"""
import uuid

import pytest
import pytest_asyncio

from app.models.sandbox_node import SandboxNode, NodeStatus
from sqlalchemy import select


@pytest_asyncio.fixture
async def admin(db_session):
    from app.models.user import User
    from app.services.auth_service import create_access_token

    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"regadmin_{unique}",
        email=f"regadmin_{unique}@example.com",
        hashed_password="x",
        role="admin",
    )
    db_session.add(user)
    await db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest_asyncio.fixture
async def operator(db_session):
    from app.models.user import User
    from app.services.auth_service import create_access_token

    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"regop_{unique}",
        email=f"regop_{unique}@example.com",
        hashed_password="x",
        role="operator",
    )
    db_session.add(user)
    await db_session.flush()
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.mark.asyncio
async def test_register_requires_admin(client, db_session, operator):
    r = await client.post(
        "/api/v1/sandbox-nodes",
        headers=operator,
        json={
            "node_id": "node-reg-1",
            "hostname": "h1",
            "ip_address": "10.0.0.9",
            "capabilities": {"cubelet_endpoint": "10.0.0.9:9443"},
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_register_requires_cubelet_endpoint(client, db_session, admin):
    r = await client.post(
        "/api/v1/sandbox-nodes",
        headers=admin,
        json={"node_id": "node-reg-2", "hostname": "h2", "ip_address": "10.0.0.9",
              "capabilities": {"cpu": True}},
    )
    assert r.status_code == 422
    assert "cubelet_endpoint" in r.text


@pytest.mark.asyncio
async def test_register_creates_node_and_audits(client, db_session, admin, monkeypatch):
    from app.services import cube_node_client

    # RT-07: 内联探针（List + GetStorageMetrics）由 conftest 的 TestCubeNodeClient
    # 桩满足 —— 无需再 monkeypatch 旧的 cube_node_health_probe 包装。
    r = await client.post(
        "/api/v1/sandbox-nodes",
        headers=admin,
        json={
            "node_id": "node-reg-3",
            "hostname": "h3",
            "ip_address": "10.0.0.9",
            "capabilities": {"cubelet_endpoint": "10.0.0.9:9443", "tee": "none"},
            "cpu_cores": 8,
            "memory_mb": 16384,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["probe_ok"] is True
    assert body["health_state"] == "healthy"
    assert body["status"] == NodeStatus.ONLINE.value

    result = await db_session.execute(select(SandboxNode).where(SandboxNode.node_id == "node-reg-3"))
    node = result.scalar_one()
    assert node.capabilities["cubelet_endpoint"] == "10.0.0.9:9443"

    from app.models.audit_log import AuditLog

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "node.register")
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_register_probe_down_keeps_offline(client, db_session, admin, monkeypatch):
    from app.services import cube_node_client

    # D-e2e-14/RT-07: 注册初始探针是内联的 get_cube_node_client List+
    # GetStorageMetrics（不再走 cube_node_health_probe 包装）—— 按新契约
    # monkeypatch 客户端工厂让探针失败。
    class _DownClient:
        def list(self, request):
            raise AssertionError("probe must be down in this test")

        def get_storage_metrics(self, request):
            raise AssertionError("probe must be down in this test")

    monkeypatch.setattr(
        cube_node_client, "get_cube_node_client",
        lambda node, settings=None: _DownClient(),
    )

    r = await client.post(
        "/api/v1/sandbox-nodes",
        headers=admin,
        json={
            "node_id": "node-reg-4",
            "hostname": "h4",
            "ip_address": "10.0.0.10",
            "capabilities": {"cubelet_endpoint": "10.0.0.10:9443"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["probe_ok"] is False
    assert body["status"] == NodeStatus.OFFLINE.value
    assert body["health_state"] == "unknown"


@pytest.mark.asyncio
async def test_register_bad_node_id_rejected(client, db_session, admin):
    r = await client.post(
        "/api/v1/sandbox-nodes",
        headers=admin,
        json={
            "node_id": "Bad ID!",
            "hostname": "h5",
            "ip_address": "10.0.0.11",
            "capabilities": {"cubelet_endpoint": "10.0.0.11:9443"},
        },
    )
    assert r.status_code == 422
