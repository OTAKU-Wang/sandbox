import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"user_{unique}",
        "email": f"user_{unique}@example.com",
        "password": "securepass123",
        "role": "buyer",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["username"] == f"user_{unique}"
    assert data["user"]["role"] == "buyer"


@pytest.mark.asyncio
async def test_register_duplicate(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    payload = {
        "username": f"dup_{unique}",
        "email": f"dup_{unique}@example.com",
        "password": "securepass123",
    }
    resp1 = await client.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201
    resp2 = await client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code in (400, 409, 422)  # duplicate rejected


@pytest.mark.asyncio
async def test_login(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    await client.post("/api/v1/auth/register", json={
        "username": f"login_{unique}",
        "email": f"login_{unique}@example.com",
        "password": "mypass123",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "username": f"login_{unique}",
        "password": "mypass123",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    unique = uuid.uuid4().hex[:8]
    await client.post("/api/v1/auth/register", json={
        "username": f"wrong_{unique}",
        "email": f"wrong_{unique}@example.com",
        "password": "correctpass",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "username": f"wrong_{unique}",
        "password": "wrongpass",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_me(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert "username" in resp.json()


@pytest.mark.asyncio
async def test_get_me_no_token(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401 or resp.status_code == 403  # no auth header
