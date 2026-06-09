"""Tests for Federation API endpoints."""
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient

from app.services.federation_connector import GatewayResponse


@pytest.mark.asyncio
async def test_establish_trust(client: AsyncClient, operator_headers: dict):
    """Establish a trust relationship."""
    resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-remote-1",
        "space_name": "Remote CDS",
        "endpoint": "https://remote.cds.example.com",
        "trust_level": "basic",
    }, headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["trust_id"].startswith("trust-")
    assert data["remote_space"] == "cds-remote-1"
    assert data["trust_level"] == "basic"
    assert data["status"] == "active"
    assert "read_catalog" in data["allowed_operations"]


@pytest.mark.asyncio
async def test_establish_trust_verified(client: AsyncClient, operator_headers: dict):
    """Establish trust with verified level."""
    resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-verified",
        "space_name": "Verified CDS",
        "endpoint": "https://verified.cds.example.com",
        "trust_level": "verified",
        "allowed_operations": ["search", "read_catalog", "write_catalog"],
        "policy_sync": True,
    }, headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["trust_level"] == "verified"
    assert data["policy_sync_enabled"] is True
    assert "write_catalog" in data["allowed_operations"]


@pytest.mark.asyncio
async def test_establish_trust_invalid_level(client: AsyncClient, operator_headers: dict):
    """Invalid trust level is rejected."""
    resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-x",
        "space_name": "X",
        "endpoint": "https://x.example.com",
        "trust_level": "invalid",
    }, headers=operator_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_trusts(client: AsyncClient, operator_headers: dict):
    """List trust relationships."""
    # Create a trust first
    await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-list-test",
        "space_name": "List Test",
        "endpoint": "https://list.example.com",
    }, headers=operator_headers)

    resp = await client.get("/api/v1/federation/trusts", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    trust = data[0]
    assert "trust_id" in trust
    assert "remote_space" in trust
    assert "status" in trust


@pytest.mark.asyncio
async def test_list_trusts_filter_status(client: AsyncClient, operator_headers: dict):
    """List trusts filtered by status."""
    await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-filter-test",
        "space_name": "Filter Test",
        "endpoint": "https://filter.example.com",
    }, headers=operator_headers)

    resp = await client.get("/api/v1/federation/trusts?status=active", headers=operator_headers)
    assert resp.status_code == 200
    for trust in resp.json():
        assert trust["status"] == "active"


@pytest.mark.asyncio
async def test_revoke_trust(client: AsyncClient, operator_headers: dict):
    """Revoke a trust relationship."""
    create_resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-revoke",
        "space_name": "Revoke Test",
        "endpoint": "https://revoke.example.com",
    }, headers=operator_headers)
    trust_id = create_resp.json()["trust_id"]

    resp = await client.post(f"/api/v1/federation/trusts/{trust_id}/revoke", headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


@pytest.mark.asyncio
async def test_revoke_trust_not_found(client: AsyncClient, operator_headers: dict):
    """Revoke non-existent trust returns 404."""
    resp = await client.post("/api/v1/federation/trusts/nonexistent/revoke", headers=operator_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_suspend_trust(client: AsyncClient, operator_headers: dict):
    """Suspend a trust relationship."""
    create_resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-suspend",
        "space_name": "Suspend Test",
        "endpoint": "https://suspend.example.com",
    }, headers=operator_headers)
    trust_id = create_resp.json()["trust_id"]

    resp = await client.post(f"/api/v1/federation/trusts/{trust_id}/suspend", headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"


@pytest.mark.asyncio
async def test_send_request(client: AsyncClient, operator_headers: dict):
    """Send a cross-space request."""
    create_resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-req",
        "space_name": "Request Test",
        "endpoint": "https://req.example.com",
        "allowed_operations": ["search"],
    }, headers=operator_headers)
    trust_id = create_resp.json()["trust_id"]

    # Mock the HTTP call to the remote space (no real server in tests)
    mock_response = GatewayResponse(
        request_id="req-mock",
        status_code=200,
        data={"items": [{"id": "remote-p1", "name": "Remote Product"}], "total": 1},
        source_space="cds-req",
    )
    with patch(
        "app.services.federation_connector.FederationConnector._execute_remote_request",
        return_value=mock_response,
    ):
        resp = await client.post("/api/v1/federation/request", json={
            "trust_id": trust_id,
            "operation": "search",
            "resource": "/data/products",
            "payload": {"query": "test"},
        }, headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status_code"] == 200
    assert data["request_id"].startswith("req-")
    assert data["source_space"] == "cds-req"


@pytest.mark.asyncio
async def test_send_request_trust_not_found(client: AsyncClient, operator_headers: dict):
    """Send request to non-existent trust returns 404."""
    resp = await client.post("/api/v1/federation/request", json={
        "trust_id": "nonexistent",
        "operation": "search",
        "resource": "/data",
    }, headers=operator_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_request_disallowed_operation(client: AsyncClient, operator_headers: dict):
    """Send request with disallowed operation returns 403."""
    create_resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-disallow",
        "space_name": "Disallow Test",
        "endpoint": "https://disallow.example.com",
        "allowed_operations": ["read_catalog"],
    }, headers=operator_headers)
    trust_id = create_resp.json()["trust_id"]

    resp = await client.post("/api/v1/federation/request", json={
        "trust_id": trust_id,
        "operation": "write_catalog",
        "resource": "/data",
    }, headers=operator_headers)
    assert resp.status_code == 200
    assert resp.json()["status_code"] == 403


@pytest.mark.asyncio
async def test_federation_audit(client: AsyncClient, operator_headers: dict):
    """Get federation audit log."""
    # Create trust and send a request to generate audit
    create_resp = await client.post("/api/v1/federation/trust", json={
        "space_id": "cds-audit",
        "space_name": "Audit Test",
        "endpoint": "https://audit.example.com",
        "allowed_operations": ["search"],
    }, headers=operator_headers)
    trust_id = create_resp.json()["trust_id"]

    mock_response = GatewayResponse(
        request_id="req-audit", status_code=200,
        data={"items": [], "total": 0}, source_space="cds-audit",
    )
    with patch(
        "app.services.federation_connector.FederationConnector._execute_remote_request",
        return_value=mock_response,
    ):
        await client.post("/api/v1/federation/request", json={
            "trust_id": trust_id,
            "operation": "search",
            "resource": "/data",
        }, headers=operator_headers)

    resp = await client.get("/api/v1/federation/audit", headers=operator_headers)
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) >= 1
    assert entries[0]["operation"] == "search"


@pytest.mark.asyncio
async def test_federation_stats(client: AsyncClient, operator_headers: dict):
    """Get federation statistics."""
    resp = await client.get("/api/v1/federation/stats", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_trusts" in data
    assert "active_trusts" in data
    assert "total_requests" in data
    assert "audit_entries" in data


@pytest.mark.asyncio
async def test_federation_unauthorized(client: AsyncClient):
    """Federation endpoints require auth."""
    resp = await client.get("/api/v1/federation/trusts")
    assert resp.status_code == 401
