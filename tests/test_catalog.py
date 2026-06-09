"""Tests for the data catalog API."""
import uuid
import pytest
from httpx import AsyncClient


@pytest.fixture
async def provider_headers(client: AsyncClient) -> dict:
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"provider_{unique}",
        "email": f"prov_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def buyer_headers(client: AsyncClient) -> dict:
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"buyer_{unique}",
        "email": f"buyer_{unique}@example.com",
        "password": "testpass123",
        "role": "buyer",
    })
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create_product(client: AsyncClient, headers: dict, **overrides) -> str:
    payload = {"name": f"Product-{uuid.uuid4().hex[:6]}", **overrides}
    resp = await client.post("/api/v1/data-products", json=payload, headers=headers)
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_catalog_empty(client: AsyncClient, buyer_headers: dict):
    """Empty catalog returns empty list."""
    resp = await client.get("/api/v1/catalog", headers=buyer_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_catalog_only_published(client: AsyncClient, provider_headers: dict, buyer_headers: dict, publish_product):
    """Only published products appear in catalog."""
    draft_id = await _create_product(client, provider_headers, name="Draft Product")
    pub_id = await _create_product(client, provider_headers, name="Published Product")
    await publish_product(pub_id)

    resp = await client.get("/api/v1/catalog", headers=buyer_headers)
    data = resp.json()
    names = [item["name"] for item in data["items"]]
    assert "Published Product" in names
    assert "Draft Product" not in names


@pytest.mark.asyncio
async def test_catalog_search(client: AsyncClient, provider_headers: dict, buyer_headers: dict, publish_product):
    """Search by name/description."""
    pid1 = await _create_product(client, provider_headers, name="Weather Data", description="Global weather")
    pid2 = await _create_product(client, provider_headers, name="Finance Data", description="Stock prices")
    await publish_product(pid1)
    await publish_product(pid2)

    resp = await client.get("/api/v1/catalog?q=weather", headers=buyer_headers)
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Weather Data"


@pytest.mark.asyncio
async def test_catalog_filter_product_type(client: AsyncClient, provider_headers: dict, buyer_headers: dict, publish_product):
    """Filter by product_type."""
    pid1 = await _create_product(client, provider_headers, name="Structured", product_type="structured")
    pid2 = await _create_product(client, provider_headers, name="Unstructured", product_type="unstructured")
    await publish_product(pid1)
    await publish_product(pid2)

    resp = await client.get("/api/v1/catalog?product_type=structured", headers=buyer_headers)
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["product_type"] == "structured"


@pytest.mark.asyncio
async def test_catalog_filter_industry(client: AsyncClient, provider_headers: dict, buyer_headers: dict, publish_product):
    """Filter by industry."""
    pid1 = await _create_product(client, provider_headers, name="Health", industry="healthcare")
    pid2 = await _create_product(client, provider_headers, name="Finance", industry="finance")
    await publish_product(pid1)
    await publish_product(pid2)

    resp = await client.get("/api/v1/catalog?industry=finance", headers=buyer_headers)
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["industry"] == "finance"


@pytest.mark.asyncio
async def test_catalog_filter_security_level(client: AsyncClient, provider_headers: dict, buyer_headers: dict, publish_product):
    """Filter by security_level."""
    pid1 = await _create_product(client, provider_headers, name="Public Data", security_level="public")
    pid2 = await _create_product(client, provider_headers, name="Secret Data", security_level="secret")
    await publish_product(pid1)
    await publish_product(pid2)

    resp = await client.get("/api/v1/catalog?security_level=public", headers=buyer_headers)
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["security_level"] == "public"


@pytest.mark.asyncio
async def test_catalog_pagination(client: AsyncClient, provider_headers: dict, buyer_headers: dict, publish_product):
    """Pagination works correctly."""
    for i in range(5):
        pid = await _create_product(client, provider_headers, name=f"Product-{i}")
        await publish_product(pid)

    resp = await client.get("/api/v1/catalog?skip=0&limit=2", headers=buyer_headers)
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5

    resp2 = await client.get("/api/v1/catalog?skip=4&limit=2", headers=buyer_headers)
    data2 = resp2.json()
    assert len(data2["items"]) == 1


@pytest.mark.asyncio
async def test_catalog_product_detail(client: AsyncClient, provider_headers: dict, buyer_headers: dict, publish_product):
    """Get detail of a published product."""
    pid = await _create_product(client, provider_headers, name="Detail Test", description="A test product",
                                 security_level="confidential", allowed_operations=["read", "query"])
    await publish_product(pid)

    resp = await client.get(f"/api/v1/catalog/{pid}", headers=buyer_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Detail Test"
    assert data["security_level"] == "confidential"
    assert data["allowed_operations"] == ["read", "query"]


@pytest.mark.asyncio
async def test_catalog_draft_not_visible(client: AsyncClient, provider_headers: dict, buyer_headers: dict):
    """Draft products return 404 from catalog detail."""
    pid = await _create_product(client, provider_headers, name="Draft")
    resp = await client.get(f"/api/v1/catalog/{pid}", headers=buyer_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_catalog_requires_auth(client: AsyncClient):
    """Unauthenticated request returns 401."""
    resp = await client.get("/api/v1/catalog")
    assert resp.status_code == 401
