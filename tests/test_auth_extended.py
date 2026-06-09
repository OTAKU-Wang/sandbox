"""Extended auth tests — edge cases, roles, token validation."""
import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_allowed_roles(client: AsyncClient):
    """Self-registration allows buyer and data_provider roles."""
    for role in ["data_provider", "buyer"]:
        unique = uuid.uuid4().hex[:8]
        resp = await client.post("/api/v1/auth/register", json={
            "username": f"{role}_{unique}",
            "email": f"{role}_{unique}@example.com",
            "password": "testpass123",
            "role": role,
        })
        assert resp.status_code == 201, f"Role {role} failed: {resp.text}"
        assert resp.json()["user"]["role"] == role


@pytest.mark.asyncio
async def test_register_rejects_privileged_roles(client: AsyncClient):
    """Self-registration rejects privileged roles (operator, admin, regulator)."""
    for role in ["operator", "admin", "regulator"]:
        unique = uuid.uuid4().hex[:8]
        resp = await client.post("/api/v1/auth/register", json={
            "username": f"{role}_{unique}",
            "email": f"{role}_{unique}@example.com",
            "password": "testpass123",
            "role": role,
        })
        assert resp.status_code == 422, f"Role {role} should be rejected: {resp.text}"


@pytest.mark.asyncio
async def test_register_short_password(client: AsyncClient):
    """Short password is rejected with 422."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"short_{unique}",
        "email": f"short_{unique}@example.com",
        "password": "ab",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_email(client: AsyncClient):
    """Invalid email is rejected with 422."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"badmail_{unique}",
        "email": "not-an-email",
        "password": "testpass123",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    """Login with non-existent user returns 401."""
    resp = await client.post("/api/v1/auth/login", json={
        "username": f"ghost_{uuid.uuid4().hex[:8]}",
        "password": "testpass123",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_refresh_flow(client: AsyncClient):
    """Register → use token → still valid after multiple calls."""
    unique = uuid.uuid4().hex[:8]
    reg = await client.post("/api/v1/auth/register", json={
        "username": f"tok_{unique}",
        "email": f"tok_{unique}@example.com",
        "password": "testpass123",
    })
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    for _ in range(3):
        resp = await client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_malformed_token(client: AsyncClient):
    """Malformed token returns 401."""
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_with_organization(client: AsyncClient):
    """Register with organization field."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"org_{unique}",
        "email": f"org_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
        "organization": "Test Corp",
    })
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_user_options_lists_minimal_buyer_fields(client: AsyncClient, auth_headers: dict):
    unique = uuid.uuid4().hex[:8]
    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"option_buyer_{unique}",
        "email": f"option_buyer_{unique}@example.com",
        "password": "testpass123",
        "role": "buyer",
        "organization": "Buyer Org",
    })
    assert buyer_resp.status_code == 201

    resp = await client.get("/api/v1/users/options?role=buyer", headers=auth_headers)
    assert resp.status_code == 200
    users = resp.json()
    buyer = next((user for user in users if user["username"] == f"option_buyer_{unique}"), None)
    assert buyer is not None
    assert buyer["role"] == "buyer"
    assert buyer["organization"] == "Buyer Org"
    assert "email" not in buyer


@pytest.mark.asyncio
async def test_user_options_rejects_invalid_role(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/users/options?role=superuser", headers=auth_headers)
    assert resp.status_code == 400
