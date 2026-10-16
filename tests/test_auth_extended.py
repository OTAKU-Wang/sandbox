"""Extended auth tests — edge cases, roles, token validation."""
import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_rejects_non_admin_roles(client: AsyncClient):
    """PZ-10: self-registration no longer accepts non-admin roles.

    Role self-selection is tightened to admin (bootstrap) or omitted; every
    other role is assigned by an admin via PATCH /auth/users/{id}/role.
    """
    for role in ["buyer", "data_provider", "operator", "regulator"]:
        unique = uuid.uuid4().hex[:8]
        resp = await client.post("/api/v1/auth/register", json={
            "username": f"{role}_{unique}",
            "email": f"{role}_{unique}@example.com",
            "password": "testpass123",
            "role": role,
        })
        assert resp.status_code == 422, f"Role {role} should be rejected: {resp.text}"


@pytest.mark.asyncio
async def test_register_admin_or_no_role(client: AsyncClient):
    """PZ-10: admin stays self-selectable; omitting role defaults to buyer.

    The suite pins CDS_ALLOW_ADMIN_SELF_REGISTER=true (conftest) because
    fixture helpers create admins after other users exist; the enforcement
    (flag off -> first-account rule) is covered by the D-e2e-01 tests below.
    """
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"adminsel_{unique}",
        "email": f"adminsel_{unique}@example.com",
        "password": "testpass123",
        "role": "admin",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["role"] == "admin"

    unique2 = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"norole_{unique2}",
        "email": f"norole_{unique2}@example.com",
        "password": "testpass123",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["role"] == "buyer"


@pytest.mark.asyncio
async def test_register_rejects_privileged_roles(client: AsyncClient):
    """Self-registration rejects non-whitelisted privileged roles.

    PZ-10: admin moved INTO the whitelist (bootstrap account); the
    remaining privileged roles stay rejected.
    """
    for role in ["operator", "regulator"]:
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
async def test_register_admin_first_account_bootstrap(client: AsyncClient, monkeypatch):
    """D-e2e-01: on an empty deployment the first account may claim admin,
    even with the escape hatch off."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "ALLOW_ADMIN_SELF_REGISTER", False)
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"boot_{unique}",
        "email": f"boot_{unique}@example.com",
        "password": "testpass123",
        "role": "admin",
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["role"] == "admin"


@pytest.mark.asyncio
async def test_register_admin_rejected_after_bootstrap(client: AsyncClient, make_user, monkeypatch):
    """D-e2e-01: once any account exists, admin self-registration is 403
    (escape hatch off) — the privilege-escalation hole is closed."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "ALLOW_ADMIN_SELF_REGISTER", False)
    await make_user()
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"lateadmin_{unique}",
        "email": f"lateadmin_{unique}@example.com",
        "password": "testpass123",
        "role": "admin",
    })
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_register_non_admin_roles_unaffected_by_flag(client: AsyncClient, monkeypatch):
    """D-e2e-01: the flag only gates the admin claim; non-admin role
    self-selection stays 422 (PZ-10 schema gate)."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "ALLOW_ADMIN_SELF_REGISTER", False)
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"prov_{unique}",
        "email": f"prov_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 422, resp.text


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
