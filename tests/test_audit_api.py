"""API-level tests for audit endpoints.

Covers:
- GET /api/v1/audit/records — list with pagination and filtering
- GET /api/v1/audit/records/{id} — single record
- POST /api/v1/audit/anchor — Merkle tree anchoring
- GET /api/v1/audit/verify/{id} — blockchain verification
- GET /api/v1/audit/merkle-proof/{id} — Merkle proof
- RBAC: operator/regulator/admin allowed, data_provider/buyer denied
"""
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.auth_service import hash_password, create_access_token
from app.models.user import User


# ─── Helpers ──────────────────────────────

async def _register_with_role(client: AsyncClient, db_session: AsyncSession, role: str) -> dict:
    """Create a user with specific role directly in DB and return auth headers."""
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"audit_{role}_{unique}",
        email=f"audit_{role}_{unique}@example.com",
        hashed_password=hash_password("testpass123"),
        role=role,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}


async def _create_audit_record(client: AsyncClient, headers: dict, action: str = "test_action") -> None:
    """Create an audit record via output inspection (triggers audit log)."""
    await client.post("/api/v1/output-control/inspect", json={
        "output": f"Audit test for {action}",
        "session_id": f"audit-test-{uuid.uuid4().hex[:8]}",
    }, headers=headers)


# ─── List Records ──────────────────────────────

@pytest.mark.asyncio
async def test_audit_list_records(client: AsyncClient, db_session: AsyncSession):
    """Operator can list audit records."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers)

    resp = await client.get("/api/v1/audit/records", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_audit_list_pagination(client: AsyncClient, db_session: AsyncSession):
    """Pagination parameters should work."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers)
    await _create_audit_record(client, headers)

    resp = await client.get("/api/v1/audit/records?skip=0&limit=1", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) <= 1
    assert data["page_size"] == 1


@pytest.mark.asyncio
async def test_audit_list_filter_by_action(client: AsyncClient, db_session: AsyncSession):
    """Filtering by action should work."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers, "unique_action_xyz")

    resp = await client.get("/api/v1/audit/records?action=unique_action_xyz", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert all(item["action"] == "unique_action_xyz" for item in data["items"])


@pytest.mark.asyncio
async def test_audit_list_filter_by_resource_type(client: AsyncClient, db_session: AsyncSession):
    """Filtering by resource_type should work."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers)

    resp = await client.get("/api/v1/audit/records?resource_type=sandbox_session", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert all(item["resource_type"] == "sandbox_session" for item in data["items"])


# ─── Get Single Record ──────────────────────────────

@pytest.mark.asyncio
async def test_audit_get_record(client: AsyncClient, db_session: AsyncSession):
    """Getting a specific record by ID should work."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers)

    # First list to get an ID
    list_resp = await client.get("/api/v1/audit/records", headers=headers)
    record_id = list_resp.json()["items"][0]["id"]

    resp = await client.get(f"/api/v1/audit/records/{record_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == record_id


@pytest.mark.asyncio
async def test_audit_get_record_invalid_id(client: AsyncClient, db_session: AsyncSession):
    """Invalid UUID should return 400."""
    headers = await _register_with_role(client, db_session, "operator")
    resp = await client.get("/api/v1/audit/records/not-a-uuid", headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_audit_get_record_not_found(client: AsyncClient, db_session: AsyncSession):
    """Non-existent record should return 404."""
    headers = await _register_with_role(client, db_session, "operator")
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/audit/records/{fake_id}", headers=headers)
    assert resp.status_code == 404


# ─── Anchor Endpoint ──────────────────────────────

@pytest.mark.asyncio
async def test_audit_anchor_records(client: AsyncClient, db_session: AsyncSession):
    """Anchoring records should compute Merkle root."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers)

    # Get record IDs
    list_resp = await client.get("/api/v1/audit/records", headers=headers)
    items = list_resp.json()["items"]
    record_ids = [items[0]["id"]]

    resp = await client.post("/api/v1/audit/anchor", json={
        "record_ids": record_ids,
    }, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "merkle_root" in data
    assert data["anchored"] == 1
    assert isinstance(data["merkle_root"], str)
    assert len(data["merkle_root"]) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_audit_anchor_invalid_id(client: AsyncClient, db_session: AsyncSession):
    """Invalid record ID in anchor request should return 400."""
    headers = await _register_with_role(client, db_session, "operator")
    resp = await client.post("/api/v1/audit/anchor", json={
        "record_ids": ["not-a-uuid"],
    }, headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_audit_anchor_nonexistent_records(client: AsyncClient, db_session: AsyncSession):
    """Non-existent records should return 404."""
    headers = await _register_with_role(client, db_session, "operator")
    resp = await client.post("/api/v1/audit/anchor", json={
        "record_ids": [str(uuid.uuid4())],
    }, headers=headers)
    assert resp.status_code == 404


# ─── Verify Endpoint ──────────────────────────────

@pytest.mark.asyncio
async def test_audit_verify_not_anchored(client: AsyncClient, db_session: AsyncSession):
    """Record not anchored should return verified=False."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers)

    list_resp = await client.get("/api/v1/audit/records", headers=headers)
    record_id = list_resp.json()["items"][0]["id"]

    resp = await client.get(f"/api/v1/audit/verify/{record_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["verified"] is False
    assert "not anchored" in data["reason"].lower()


@pytest.mark.asyncio
async def test_audit_verify_invalid_id(client: AsyncClient, db_session: AsyncSession):
    """Invalid UUID should return 400."""
    headers = await _register_with_role(client, db_session, "operator")
    resp = await client.get("/api/v1/audit/verify/not-a-uuid", headers=headers)
    assert resp.status_code == 400


# ─── Merkle Proof Endpoint ──────────────────────────────

@pytest.mark.asyncio
async def test_audit_merkle_proof_not_anchored(client: AsyncClient, db_session: AsyncSession):
    """Record not anchored should return 400."""
    headers = await _register_with_role(client, db_session, "operator")
    await _create_audit_record(client, headers)

    list_resp = await client.get("/api/v1/audit/records", headers=headers)
    record_id = list_resp.json()["items"][0]["id"]

    resp = await client.get(f"/api/v1/audit/merkle-proof/{record_id}", headers=headers)
    assert resp.status_code == 400
    assert "not anchored" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_audit_merkle_proof_invalid_id(client: AsyncClient, db_session: AsyncSession):
    """Invalid UUID should return 400."""
    headers = await _register_with_role(client, db_session, "operator")
    resp = await client.get("/api/v1/audit/merkle-proof/not-a-uuid", headers=headers)
    assert resp.status_code == 400


# ─── RBAC Tests ──────────────────────────────

@pytest.mark.asyncio
async def test_audit_rbac_operator_allowed(client: AsyncClient, db_session: AsyncSession):
    """Operator should have access to audit endpoints."""
    headers = await _register_with_role(client, db_session, "operator")
    resp = await client.get("/api/v1/audit/records", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_audit_rbac_regulator_allowed(client: AsyncClient, db_session: AsyncSession):
    """Regulator should have access to audit endpoints."""
    headers = await _register_with_role(client, db_session, "regulator")
    resp = await client.get("/api/v1/audit/records", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_audit_rbac_admin_allowed(client: AsyncClient, db_session: AsyncSession):
    """Admin should have access to audit endpoints."""
    headers = await _register_with_role(client, db_session, "admin")
    resp = await client.get("/api/v1/audit/records", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_audit_rbac_data_provider_denied(client: AsyncClient, db_session: AsyncSession):
    """Data provider should be denied (403)."""
    headers = await _register_with_role(client, db_session, "data_provider")
    resp = await client.get("/api/v1/audit/records", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_rbac_buyer_denied(client: AsyncClient, db_session: AsyncSession):
    """Buyer should be denied (403)."""
    headers = await _register_with_role(client, db_session, "buyer")
    resp = await client.get("/api/v1/audit/records", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_rbac_unauthenticated(client: AsyncClient, db_session: AsyncSession):
    """Unauthenticated requests should return 401."""
    resp = await client.get("/api/v1/audit/records")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_audit_rbac_anchor_denied_for_buyer(client: AsyncClient, db_session: AsyncSession):
    """Buyer should not be able to anchor records."""
    headers = await _register_with_role(client, db_session, "buyer")
    resp = await client.post("/api/v1/audit/anchor", json={"record_ids": [str(uuid.uuid4())]}, headers=headers)
    assert resp.status_code == 403
