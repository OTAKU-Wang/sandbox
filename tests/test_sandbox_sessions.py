import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_sandbox_session(client: AsyncClient, auth_headers: dict):
    # Create a data product first
    product_resp = await client.post("/api/v1/data-products", json={
        "name": "Session Test Product",
        "product_type": "structured",
    }, headers=auth_headers)
    product_id = product_resp.json()["id"]

    # Update status to published
    await client.patch(f"/api/v1/data-products/{product_id}", json={"status": "published"}, headers=auth_headers)

    # Create sandbox session
    resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
        "sandbox_level": "L3",
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] in ("running", "failed")
    assert data["sandbox_level"] == "L3"


@pytest.mark.asyncio
async def test_list_sandbox_sessions(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/sandbox-sessions", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_session_unpublished_product(client: AsyncClient, auth_headers: dict):
    product_resp = await client.post("/api/v1/data-products", json={"name": "Draft"}, headers=auth_headers)
    product_id = product_resp.json()["id"]
    resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
    }, headers=auth_headers)
    assert resp.status_code == 400  # not published


@pytest.mark.asyncio
async def test_terminate_sandbox_session(client: AsyncClient, auth_headers: dict):
    product_resp = await client.post("/api/v1/data-products", json={"name": "Term"}, headers=auth_headers)
    product_id = product_resp.json()["id"]
    await client.patch(f"/api/v1/data-products/{product_id}", json={"status": "published"}, headers=auth_headers)

    session_resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
    }, headers=auth_headers)
    session_id = session_resp.json()["id"]

    resp = await client.post(f"/api/v1/sandbox-sessions/{session_id}/terminate", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "terminated"


@pytest.mark.asyncio
async def test_regulator_can_read_but_not_terminate_sessions(client: AsyncClient, auth_headers: dict, make_user):
    regulator_headers, _ = await make_user("regulator", "regulator")

    product_resp = await client.post("/api/v1/data-products", json={"name": "Regulator Session"}, headers=auth_headers)
    product_id = product_resp.json()["id"]
    await client.patch(f"/api/v1/data-products/{product_id}", json={"status": "published"}, headers=auth_headers)

    session_resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": product_id,
    }, headers=auth_headers)
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    list_resp = await client.get("/api/v1/sandbox-sessions", headers=regulator_headers)
    assert list_resp.status_code == 200
    assert any(session["id"] == session_id for session in list_resp.json()["items"])

    detail_resp = await client.get(f"/api/v1/sandbox-sessions/{session_id}", headers=regulator_headers)
    assert detail_resp.status_code == 200

    terminate_resp = await client.post(f"/api/v1/sandbox-sessions/{session_id}/terminate", headers=regulator_headers)
    assert terminate_resp.status_code == 403
