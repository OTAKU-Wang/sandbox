"""Tests for security middleware and input validation."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_security_headers(client: AsyncClient):
    """All responses should include security headers."""
    resp = await client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_security_headers_jupyter_embed_exempt(client: AsyncClient):
    """D-e2e-48: /api/jupyter/* 是唯一允许同源 iframe 的前缀（开发环嵌入），
    其余路径维持 DENY——两个方向都要钉住，漏任一边都会静默回归。"""
    resp = await client.get("/api/jupyter/login")  # 无票据：状态码不论，头必到位
    assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
    csp = resp.headers.get("content-security-policy", "")
    assert "frame-ancestors 'self'" in csp
    # 同源豁免不得泄漏到相邻前缀（tickets 写接口的 405/422 响应同样要 DENY）
    other = await client.get("/api/jupyter/tickets")
    assert other.headers.get("x-frame-options") == "SAMEORIGIN"  # 同前缀，豁免按前缀不按方法
    deny = await client.get("/api/v1/notebooks")
    assert deny.headers.get("x-frame-options") == "DENY"


@pytest.mark.asyncio
async def test_invalid_product_type(client: AsyncClient, auth_headers: dict):
    """Invalid product_type should be rejected."""
    resp = await client.post("/api/v1/data-products", json={
        "name": "Test",
        "product_type": "invalid_type",
    }, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_product_name(client: AsyncClient, auth_headers: dict):
    """Empty product name should be rejected."""
    resp = await client.post("/api/v1/data-products", json={
        "name": "",
    }, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_contract_type(client: AsyncClient, auth_headers: dict):
    """Invalid contract_type should be rejected."""
    import uuid
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "invalid_type",
        "buyer_id": str(uuid.uuid4()),
        "data_product_id": str(uuid.uuid4()),
        "title": "Test",
    }, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_sandbox_level(client: AsyncClient, auth_headers: dict):
    """Invalid sandbox_level should be rejected."""
    import uuid
    resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": str(uuid.uuid4()),
        "sandbox_level": "L4",
    }, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_negative_dp_budget(client: AsyncClient, auth_headers: dict):
    """Negative DP budget should be rejected."""
    import uuid
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": str(uuid.uuid4()),
        "data_product_id": str(uuid.uuid4()),
        "title": "Test",
        "dp_epsilon_budget": -5.0,
    }, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_extreme_duration(client: AsyncClient, auth_headers: dict):
    """Extreme max_duration_hours should be rejected."""
    import uuid
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": str(uuid.uuid4()),
        "data_product_id": str(uuid.uuid4()),
        "title": "Test",
        "max_duration_hours": 99999,
    }, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_signature(client: AsyncClient, auth_headers: dict):
    """Empty signature should be rejected."""
    import uuid
    # First create a contract
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": str(uuid.uuid4()),
        "data_product_id": str(uuid.uuid4()),
        "title": "Test Contract",
    }, headers=auth_headers)
    if resp.status_code == 201:
        contract_id = resp.json()["id"]
        resp = await client.post(f"/api/v1/contracts/{contract_id}/sign", json={
            "signature": "",
        }, headers=auth_headers)
        assert resp.status_code == 422
