import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_contract(client: AsyncClient, auth_headers: dict):
    # Create buyer
    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"buyer_{uuid.uuid4().hex[:8]}",
        "email": f"buyer_{uuid.uuid4().hex[:8]}@example.com",
        "password": "buyerpass",
        "role": "buyer",
    })
    buyer_id = buyer_resp.json()["user"]["id"]

    # Create data product
    product_resp = await client.post("/api/v1/data-products", json={"name": "Contract Test Product"}, headers=auth_headers)
    product_id = product_resp.json()["id"]

    # Create contract
    resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Test Contract",
        "allowed_sandbox_levels": "L2,L3",
        "allowed_operations": "read,analyze",
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "draft"
    assert data["contract_no"].startswith("CDS-")


@pytest.mark.asyncio
async def test_list_contracts(client: AsyncClient, auth_headers: dict):
    resp = await client.get("/api/v1/contracts", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_regulator_can_read_all_contracts(client: AsyncClient, auth_headers: dict, make_user):
    regulator_headers, _ = await make_user("regulator", "regulator")

    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"reg_buyer_{uuid.uuid4().hex[:8]}",
        "email": f"reg_buyer_{uuid.uuid4().hex[:8]}@example.com",
        "password": "buyerpass",
        "role": "buyer",
    })
    buyer_id = buyer_resp.json()["user"]["id"]

    product_resp = await client.post("/api/v1/data-products", json={"name": "Regulator Contract Product"}, headers=auth_headers)
    product_id = product_resp.json()["id"]

    contract_resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Regulator Visible Contract",
    }, headers=auth_headers)
    assert contract_resp.status_code == 201
    contract_id = contract_resp.json()["id"]

    list_resp = await client.get("/api/v1/contracts", headers=regulator_headers)
    assert list_resp.status_code == 200
    assert any(contract["id"] == contract_id for contract in list_resp.json()["items"])

    detail_resp = await client.get(f"/api/v1/contracts/{contract_id}", headers=regulator_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == contract_id


@pytest.mark.asyncio
async def test_sign_contract(client: AsyncClient, auth_headers: dict):
    # Setup
    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"signer_{uuid.uuid4().hex[:8]}",
        "email": f"signer_{uuid.uuid4().hex[:8]}@example.com",
        "password": "signerpass",
        "role": "buyer",
    })
    buyer_id = buyer_resp.json()["user"]["id"]

    product_resp = await client.post("/api/v1/data-products", json={"name": "Sign Test"}, headers=auth_headers)
    product_id = product_resp.json()["id"]

    contract_resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Sign Test Contract",
    }, headers=auth_headers)
    contract_id = contract_resp.json()["id"]

    # Provider signs — without SM2 certificate, should be rejected (P0-3 fix)
    resp = await client.post(f"/api/v1/contracts/{contract_id}/sign", json={
        "signature": "04aabbccdd",
    }, headers=auth_headers)
    # After P0-3: signing without valid certificate returns 400
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_contract_policy(client: AsyncClient, auth_headers: dict):
    buyer_resp = await client.post("/api/v1/auth/register", json={
        "username": f"policy_{uuid.uuid4().hex[:8]}",
        "email": f"policy_{uuid.uuid4().hex[:8]}@example.com",
        "password": "policypass",
        "role": "buyer",
    })
    buyer_id = buyer_resp.json()["user"]["id"]

    product_resp = await client.post("/api/v1/data-products", json={"name": "Policy Test"}, headers=auth_headers)
    product_id = product_resp.json()["id"]

    contract_resp = await client.post("/api/v1/contracts", json={
        "contract_type": "model_training",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Policy Test Contract",
        "dp_epsilon_budget": 5.0,
    }, headers=auth_headers)
    contract_id = contract_resp.json()["id"]

    resp = await client.get(f"/api/v1/contracts/{contract_id}/policy", headers=auth_headers)
    assert resp.status_code == 200
    policy = resp.json()
    assert "allowed_sandbox_levels" in policy
    assert policy["dp_epsilon_budget"] == 5.0
