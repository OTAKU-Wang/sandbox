"""A318 — 心跳端点不得把常量当实测延迟返回（禁伪造铁律）。

`GET /api/v1/connectors/{id}/heartbeat` 观测面只有 `is_healthy` / `last_heartbeat`，
本服务从不测 RTT。原来硬编码 `"latency_ms": 0`，前端一度把它当"延迟 0ms"的测量值渲染；
前端半边已由 backend-15b 撤掉展示，本文件钉后端半边：**未测量必须说 null**，
不得用 0 冒充一个真的很快/真的量过的值。
"""
import uuid

import pytest
from httpx import AsyncClient

from app.models.connector import Connector


@pytest.mark.asyncio
async def test_heartbeat_reports_unmeasured_latency_as_null(
    client: AsyncClient, admin_headers: dict[str, str], db_session
):
    connector = Connector(
        space_id=f"a318-{uuid.uuid4().hex[:8]}",
        space_name="a318",
        space_url="https://peer.example.internal",
        api_key_hash="0" * 64,
        api_key_prefix="a318prefix",
        is_healthy=True,
        last_heartbeat=None,
        registered_by=uuid.uuid4(),
    )
    db_session.add(connector)
    await db_session.flush()
    await db_session.refresh(connector)

    resp = await client.get(
        f"/api/v1/connectors/{connector.id}/heartbeat", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["latency_ms"] is None, (
        f"未测量的延迟不得写成 {body['latency_ms']!r}（0 = 谎称量过且为 0ms）"
    )
    assert body["status"] == "healthy"
    assert body["last_seen"] is None


@pytest.mark.asyncio
async def test_heartbeat_latency_key_present_so_clients_can_discriminate(
    client: AsyncClient, admin_headers: dict[str, str], db_session
):
    """键必须在（缺键会让前端 `?? 0` 一类兜底把"未知"再变回 0）。"""
    connector = Connector(
        space_id=f"a318b-{uuid.uuid4().hex[:8]}",
        space_name="a318b",
        space_url="https://peer.example.internal",
        api_key_hash="1" * 64,
        api_key_prefix="a318bprefix",
        is_healthy=False,
        registered_by=uuid.uuid4(),
    )
    db_session.add(connector)
    await db_session.flush()
    await db_session.refresh(connector)

    resp = await client.get(
        f"/api/v1/connectors/{connector.id}/heartbeat", headers=admin_headers
    )
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert "latency_ms" in body, "缺键会让消费方用 0 兜底，等于把未知当已知"
    assert body["latency_ms"] is None
    assert body["status"] == "unknown"
