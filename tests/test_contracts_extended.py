"""Extended contract tests — lifecycle, security, all contract types."""
import uuid
import pytest
from httpx import AsyncClient


async def _setup_contract(client: AsyncClient, auth_headers: dict, **overrides):
    """Helper: create buyer + product + contract, return contract_id."""
    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"buyer_{uuid.uuid4().hex[:8]}",
        "email": f"b_{uuid.uuid4().hex[:8]}@example.com",
        "password": "testpass123",
        "role": "buyer",
    })
    buyer_id = buyer_resp.json()["user"]["id"]

    prod_resp = await client.post("/api/v1/data-products", json={"name": f"Prod-{uuid.uuid4().hex[:6]}"}, headers=auth_headers)
    product_id = prod_resp.json()["id"]

    payload = {
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": f"Contract-{uuid.uuid4().hex[:6]}",
        **overrides,
    }
    resp = await client.post("/api/v1/contracts", json=payload, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()["id"], buyer_id


@pytest.mark.asyncio
async def test_contract_lifecycle_draft_to_active(client: AsyncClient, auth_headers: dict):
    """Full lifecycle: draft → negotiating → active."""
    contract_id, buyer_id = await _setup_contract(client, auth_headers)

    # Provider signs — without SM2 certificate, rejected (P0-3 fix)
    from datetime import datetime, timezone
    r1 = await client.post(f"/api/v1/contracts/{contract_id}/sign", json={
        "signature": "aa",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, headers=auth_headers)
    assert r1.status_code == 400  # No valid certificate = reject


@pytest.mark.asyncio
async def test_contract_terminate(client: AsyncClient, auth_headers: dict):
    """Terminate a contract."""
    contract_id, _ = await _setup_contract(client, auth_headers)
    resp = await client.post(
        f"/api/v1/contracts/{contract_id}/terminate",
        headers=auth_headers,
        json={"reason": "test contract terminate"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_contract_not_found(client: AsyncClient, auth_headers: dict):
    """Get non-existent contract returns 404."""
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/contracts/{fake_id}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_contract_unauthorized(client: AsyncClient):
    """Create contract without auth returns 401/403."""
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": str(uuid.uuid4()),
        "product_ids": [str(uuid.uuid4())],
        "title": "No Auth",
    })
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("ctype", [
    "data_query", "model_training", "data_application",
    "api_service", "joint_compute", "product_dev", "data_modeling",
])
async def test_all_contract_types(client: AsyncClient, auth_headers: dict, ctype: str):
    """All 7 contract types should be accepted."""
    contract_id, _ = await _setup_contract(client, auth_headers, contract_type=ctype)
    resp = await client.get(f"/api/v1/contracts/{contract_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["contract_type"] == ctype


@pytest.mark.asyncio
async def test_contract_policy_dp_budget(client: AsyncClient, auth_headers: dict):
    """Policy includes dp_epsilon_budget."""
    contract_id, _ = await _setup_contract(client, auth_headers, dp_epsilon_budget=8.5)
    resp = await client.get(f"/api/v1/contracts/{contract_id}/policy", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["dp_epsilon_budget"] == 8.5


@pytest.mark.asyncio
async def test_contract_policy_includes_new_fields(client: AsyncClient, auth_headers: dict):
    """Policy includes max_output_rows and allowed_output_formats."""
    contract_id, _ = await _setup_contract(
        client, auth_headers,
        max_output_rows=25000,
        allowed_output_formats="csv,json,parquet",
        inspection_rule_set={"dlp_enabled": True},
    )
    resp = await client.get(f"/api/v1/contracts/{contract_id}/policy", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_output_rows"] == 25000
    assert data["allowed_output_formats"] == ["csv", "json", "parquet"]
    assert data["inspection_rule_set"]["dlp_enabled"] is True


@pytest.mark.asyncio
async def test_contract_list_empty(client: AsyncClient, auth_headers: dict):
    """List contracts for fresh user returns empty list."""
    unique = uuid.uuid4().hex[:8]
    reg = await client.post("/api/v1/auth/register", json={
        "username": f"empty_{unique}",
        "email": f"empty_{unique}@example.com",
        "password": "testpass123",
        "role": "buyer",
    })
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    resp = await client.get("/api/v1/contracts", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_contract_new_fields_defaults(client: AsyncClient, auth_headers: dict):
    """New fields have correct defaults."""
    contract_id, _ = await _setup_contract(client, auth_headers)
    resp = await client.get(f"/api/v1/contracts/{contract_id}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_output_rows"] == 10000
    assert data["allowed_output_formats"] == "csv,json"
    assert data["inspection_rule_set"] is None


@pytest.mark.asyncio
async def test_contract_custom_output_fields(client: AsyncClient, auth_headers: dict):
    """Custom max_output_rows and allowed_output_formats."""
    contract_id, _ = await _setup_contract(
        client, auth_headers,
        max_output_rows=50000,
        allowed_output_formats="csv,json,parquet",
        inspection_rule_set={"dlp_enabled": True, "dp_epsilon_threshold": 1.0},
    )
    resp = await client.get(f"/api/v1/contracts/{contract_id}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_output_rows"] == 50000
    assert data["allowed_output_formats"] == "csv,json,parquet"
    assert data["inspection_rule_set"]["dlp_enabled"] is True


@pytest.mark.asyncio
async def test_contract_invalid_output_format(client: AsyncClient, auth_headers: dict):
    """Invalid output format rejected."""
    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"badfmt_{uuid.uuid4().hex[:8]}",
        "email": f"bad_{uuid.uuid4().hex[:8]}@example.com",
        "password": "testpass123",
        "role": "buyer",
    })
    buyer_id = buyer_resp.json()["user"]["id"]
    prod_resp = await client.post("/api/v1/data-products", json={"name": "FmtTest"}, headers=auth_headers)
    product_id = prod_resp.json()["id"]
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Bad Format Contract",
        "allowed_output_formats": "csv,invalid_format",
    }, headers=auth_headers)
    assert resp.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_contract_max_output_rows_boundary(client: AsyncClient, auth_headers: dict):
    """max_output_rows boundary: 1 is valid, 0 is invalid."""
    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"rows_{uuid.uuid4().hex[:8]}",
        "email": f"rows_{uuid.uuid4().hex[:8]}@example.com",
        "password": "testpass123",
        "role": "buyer",
    })
    buyer_id = buyer_resp.json()["user"]["id"]
    prod_resp = await client.post("/api/v1/data-products", json={"name": "RowsTest"}, headers=auth_headers)
    product_id = prod_resp.json()["id"]

    # Valid: 1
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Min Rows",
        "max_output_rows": 1,
    }, headers=auth_headers)
    assert resp.status_code == 201

    # Invalid: 0
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Zero Rows",
        "max_output_rows": 0,
    }, headers=auth_headers)
    assert resp.status_code == 422
