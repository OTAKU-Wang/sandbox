import uuid
import pytest
from httpx import AsyncClient

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct


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
async def test_update_data_product_rejects_lifecycle_status_bypass(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post("/api/v1/data-products", json={"name": "Lifecycle Bypass"}, headers=auth_headers)
    product_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/data-products/{product_id}",
        json={"status": "published"},
        headers=auth_headers,
    )

    assert resp.status_code == 400
    assert "lifecycle endpoints" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_delete_data_product(client: AsyncClient, auth_headers: dict):
    create_resp = await client.post("/api/v1/data-products", json={"name": "Delete Me"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    resp = await client.delete(f"/api/v1/data-products/{product_id}", headers=auth_headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_data_product_rejects_published_product(client: AsyncClient, auth_headers: dict, publish_product):
    create_resp = await client.post("/api/v1/data-products", json={"name": "Published Delete"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    await publish_product(product_id)

    resp = await client.delete(f"/api/v1/data-products/{product_id}", headers=auth_headers)

    assert resp.status_code == 409
    assert "archive non-draft" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_archive_published_data_product(client: AsyncClient, auth_headers: dict, publish_product):
    create_resp = await client.post("/api/v1/data-products", json={"name": "Archive Me"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    await publish_product(product_id)

    resp = await client.post(f"/api/v1/data-products/{product_id}/archive", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_delete_data_product_rejects_contract_reference(client: AsyncClient, auth_headers: dict, make_user):
    _, buyer_id = await make_user("buyer", "delete_contract_ref")
    create_resp = await client.post("/api/v1/data-products", json={"name": "Contract Referenced"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    contract_resp = await client.post("/api/v1/contracts", json={
        "contract_type": "data_query",
        "buyer_id": buyer_id,
        "product_ids": [product_id],
        "title": "Contract Reference",
    }, headers=auth_headers)
    assert contract_resp.status_code == 201

    resp = await client.delete(f"/api/v1/data-products/{product_id}", headers=auth_headers)

    assert resp.status_code == 409
    assert "referenced by contracts" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_archive_data_product_rejects_active_contract(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
    make_user,
    publish_product,
):
    _, buyer_id = await make_user("buyer", "archive_contract_ref")
    create_resp = await client.post("/api/v1/data-products", json={"name": "Active Contract Product"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    await publish_product(product_id)
    product = await db_session.get(DataProduct, uuid.UUID(product_id))
    contract = Contract(
        contract_no=f"ARCH-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=product.provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Active Product Contract",
        terms={},
        product_ids=[product_id],
    )
    db_session.add(contract)
    await db_session.flush()

    resp = await client.post(f"/api/v1/data-products/{product_id}/archive", headers=auth_headers)

    assert resp.status_code == 409
    assert "active contracts" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_product_versions_hide_unpublished_product_from_buyer(
    client: AsyncClient,
    auth_headers: dict,
    make_user,
):
    buyer_headers, _ = await make_user("buyer", "versions_buyer")
    create_resp = await client.post("/api/v1/data-products", json={"name": "Draft Version Root"}, headers=auth_headers)
    product_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/data-products/{product_id}/versions", headers=buyer_headers)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_product_versions_hide_draft_successor_from_buyer(
    client: AsyncClient,
    auth_headers: dict,
    make_user,
    publish_product,
):
    buyer_headers, _ = await make_user("buyer", "versions_visible_buyer")
    create_resp = await client.post("/api/v1/data-products", json={"name": "Published Version Root"}, headers=auth_headers)
    product_id = create_resp.json()["id"]
    await publish_product(product_id)
    new_version_resp = await client.post(
        f"/api/v1/data-products/{product_id}/new-version?change_summary=metadata",
        headers=auth_headers,
    )
    assert new_version_resp.status_code == 201
    draft_version_id = new_version_resp.json()["id"]

    resp = await client.get(f"/api/v1/data-products/{product_id}/versions", headers=buyer_headers)

    assert resp.status_code == 200
    version_ids = {item["id"] for item in resp.json()["versions"]}
    assert product_id in version_ids
    assert draft_version_id not in version_ids
