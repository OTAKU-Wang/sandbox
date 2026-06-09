import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_data_product(client: AsyncClient, auth_headers: dict):
    resp = await client.post("/api/v1/data-products", json={
        "name": "Test Dataset",
        "description": "A test dataset",
        "product_type": "structured",
        "industry": "finance",
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Dataset"
    assert data["status"] == "draft"


@pytest.mark.asyncio
async def test_list_data_products(client: AsyncClient, auth_headers: dict):
    await client.post("/api/v1/data-products", json={"name": "P1"}, headers=auth_headers)
    await client.post("/api/v1/data-products", json={"name": "P2"}, headers=auth_headers)
    resp = await client.get("/api/v1/data-products", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_get_data_product(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post("/api/v1/data-products", json={"name": "Get Me"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/data-products/{product_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Get Me"


@pytest.mark.asyncio
async def test_update_data_product(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post("/api/v1/data-products", json={"name": "Old Name"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    resp = await client.patch(f"/api/v1/data-products/{product_id}", json={"name": "New Name"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


@pytest.mark.asyncio
async def test_delete_data_product(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post("/api/v1/data-products", json={"name": "Delete Me"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    resp = await client.delete(f"/api/v1/data-products/{product_id}", headers=auth_headers)
    assert resp.status_code == 204
