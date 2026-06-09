import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.data_product import DataProduct
from app.models.field_exposure import (
    ExposureRequestStatus,
    FieldExposureRequest,
    FieldVisibilityConfig,
)
from app.services.gateway_service import GatewayService
from app.services.storage_service import storage_service


async def _create_published_product(client: AsyncClient, auth_headers: dict, publish_product) -> str:
    resp = await client.post(
        "/api/v1/data-products",
        json={
            "name": "Field Exposure Product",
            "product_type": "structured",
            "data_schema": {
                "fields": [
                    {"name": "city", "type": "string"},
                    {"name": "salary", "type": "number"},
                    {"name": "identity_no", "type": "string"},
                ],
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    product_id = resp.json()["id"]
    await publish_product(product_id)
    return product_id


@pytest.mark.asyncio
async def test_restricted_field_requires_admin_review(
    client: AsyncClient,
    auth_headers: dict,
    admin_headers: dict,
    make_user,
    publish_product,
):
    buyer_headers, _ = await make_user("buyer", "buyer")
    product_id = await _create_published_product(client, auth_headers, publish_product)

    config_resp = await client.put(
        f"/api/v1/field-exposure/products/{product_id}/field-visibility",
        json={
            "default_sensitivity": "internal",
            "field_rules": {
                "city": {"sensitivity": "public"},
                "identity_no": {
                    "sensitivity": "restricted",
                    "auto_approve": True,
                    "description": "Government ID number",
                },
            },
        },
        headers=auth_headers,
    )
    assert config_resp.status_code == 200

    request_resp = await client.post(
        "/api/v1/field-exposure/exposure-requests",
        json={
            "product_id": product_id,
            "requested_fields": ["city", "identity_no"],
            "justification": "Regulated KYC risk analysis",
        },
        headers=buyer_headers,
    )
    assert request_resp.status_code == 201
    request_data = request_resp.json()
    assert request_data["status"] == "pending"
    assert request_data["approved_fields"] is None

    provider_review = await client.post(
        f"/api/v1/field-exposure/exposure-requests/{request_data['id']}/review",
        json={"approved_fields": ["city", "identity_no"]},
        headers=auth_headers,
    )
    assert provider_review.status_code == 403
    assert "Restricted fields require" in provider_review.json()["detail"]

    admin_review = await client.post(
        f"/api/v1/field-exposure/exposure-requests/{request_data['id']}/review",
        json={"approved_fields": ["city", "identity_no"]},
        headers=admin_headers,
    )
    assert admin_review.status_code == 200
    reviewed = admin_review.json()
    assert reviewed["status"] == "approved"
    assert reviewed["approved_fields"] == ["city", "identity_no"]


@pytest.mark.asyncio
async def test_admin_can_list_pending_provider_scope_requests(
    client: AsyncClient,
    auth_headers: dict,
    admin_headers: dict,
    make_user,
    publish_product,
):
    buyer_headers, _ = await make_user("buyer", "buyer")
    product_id = await _create_published_product(client, auth_headers, publish_product)

    await client.put(
        f"/api/v1/field-exposure/products/{product_id}/field-visibility",
        json={
            "default_sensitivity": "internal",
            "field_rules": {
                "identity_no": {"sensitivity": "restricted"},
            },
        },
        headers=auth_headers,
    )
    request_resp = await client.post(
        "/api/v1/field-exposure/exposure-requests",
        json={
            "product_id": product_id,
            "requested_fields": ["identity_no"],
            "justification": "Compliance review",
        },
        headers=buyer_headers,
    )
    assert request_resp.status_code == 201

    list_resp = await client.get(
        "/api/v1/field-exposure/exposure-requests?as_provider=true&status=pending",
        headers=admin_headers,
    )
    assert list_resp.status_code == 200
    assert any(item["id"] == request_resp.json()["id"] for item in list_resp.json())


@pytest.mark.asyncio
async def test_admin_can_set_field_visibility(
    client: AsyncClient,
    auth_headers: dict,
    admin_headers: dict,
):
    create_resp = await client.post(
        "/api/v1/data-products",
        json={"name": "Admin Field Policy", "data_schema": {"fields": [{"name": "city", "type": "string"}]}},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    config_resp = await client.put(
        f"/api/v1/field-exposure/products/{product_id}/field-visibility",
        json={
            "default_sensitivity": "internal",
            "field_rules": {"city": {"sensitivity": "public"}},
        },
        headers=admin_headers,
    )

    assert config_resp.status_code == 200
    assert config_resp.json()["field_rules"]["city"]["sensitivity"] == "public"


@pytest.mark.asyncio
async def test_unpublished_field_visibility_hidden_from_buyer(
    client: AsyncClient,
    auth_headers: dict,
    make_user,
):
    buyer_headers, _ = await make_user("buyer", "hidden_field_buyer")
    create_resp = await client.post(
        "/api/v1/data-products",
        json={"name": "Draft Field Policy", "data_schema": {"fields": [{"name": "city", "type": "string"}]}},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]
    config_resp = await client.put(
        f"/api/v1/field-exposure/products/{product_id}/field-visibility",
        json={
            "default_sensitivity": "internal",
            "field_rules": {"city": {"sensitivity": "public"}},
        },
        headers=auth_headers,
    )
    assert config_resp.status_code == 200

    visibility_resp = await client.get(
        f"/api/v1/field-exposure/products/{product_id}/field-visibility",
        headers=buyer_headers,
    )
    approved_resp = await client.get(
        f"/api/v1/field-exposure/products/{product_id}/approved-fields",
        headers=buyer_headers,
    )

    assert visibility_resp.status_code == 404
    assert approved_resp.status_code == 404


@pytest.mark.asyncio
async def test_published_field_visibility_visible_to_buyer(
    client: AsyncClient,
    auth_headers: dict,
    make_user,
    publish_product,
):
    buyer_headers, _ = await make_user("buyer", "visible_field_buyer")
    product_id = await _create_published_product(client, auth_headers, publish_product)
    config_resp = await client.put(
        f"/api/v1/field-exposure/products/{product_id}/field-visibility",
        json={
            "default_sensitivity": "internal",
            "field_rules": {"city": {"sensitivity": "public"}},
        },
        headers=auth_headers,
    )
    assert config_resp.status_code == 200

    visibility_resp = await client.get(
        f"/api/v1/field-exposure/products/{product_id}/field-visibility",
        headers=buyer_headers,
    )

    assert visibility_resp.status_code == 200
    assert visibility_resp.json()["field_rules"]["city"]["sensitivity"] == "public"


@pytest.mark.asyncio
async def test_non_buyer_cannot_create_field_exposure_request(
    client: AsyncClient,
    auth_headers: dict,
    operator_headers: dict,
    publish_product,
):
    product_id = await _create_published_product(client, auth_headers, publish_product)

    request_resp = await client.post(
        "/api/v1/field-exposure/exposure-requests",
        json={"product_id": product_id, "requested_fields": ["city"]},
        headers=operator_headers,
    )

    assert request_resp.status_code == 403
    assert "Only buyers" in request_resp.json()["detail"]


@pytest.mark.asyncio
async def test_gateway_query_enforces_approved_field_exposure(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
    make_user,
    publish_product,
):
    _, buyer_id = await make_user("buyer", "gateway_buyer")
    product_id = await _create_published_product(client, auth_headers, publish_product)
    product_uuid = uuid.UUID(product_id)

    product = await db_session.get(DataProduct, product_uuid)
    upload = storage_service.upload(
        b"city,salary,identity_no\nShanghai,100,110101199001011234\n",
        f"field-exposure/{product_id}/payroll.csv",
    )
    product.encrypted_storage_path = upload["path"]

    db_session.add(
        FieldVisibilityConfig(
            product_id=product_uuid,
            default_sensitivity="sensitive",
            field_rules={
                "city": {"sensitivity": "public"},
                "salary": {"sensitivity": "sensitive"},
                "identity_no": {"sensitivity": "restricted"},
            },
        )
    )
    contract = Contract(
        contract_no=f"FIELD-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=product.provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Field exposure gateway contract",
        terms={},
        product_ids=[product_id],
        allowed_operations="query",
    )
    db_session.add(contract)
    await db_session.flush()

    gateway = GatewayService()
    denied = await gateway.execute_query(
        db_session,
        contract,
        product_id,
        "SELECT city, salary FROM data",
        "json",
    )
    assert denied.success is False
    assert denied.status_code == 403
    assert "salary" in denied.error

    db_session.add(
        FieldExposureRequest(
            product_id=product_uuid,
            buyer_id=uuid.UUID(buyer_id),
            requested_fields=["salary"],
            approved_fields=["salary"],
            status=ExposureRequestStatus.APPROVED.value,
            reviewed_by=product.provider_id,
            reviewed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    await db_session.flush()

    allowed = await gateway.execute_query(
        db_session,
        contract,
        product_id,
        "SELECT city, salary FROM data",
        "json",
    )
    assert allowed.success is True
    assert allowed.data["columns"] == ["city", "salary"]
    assert allowed.data["rows"] == [["Shanghai", "100"]]


@pytest.mark.asyncio
async def test_gateway_query_rejects_select_star_with_field_visibility(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
    make_user,
    publish_product,
):
    _, buyer_id = await make_user("buyer", "gateway_star")
    product_id = await _create_published_product(client, auth_headers, publish_product)
    product_uuid = uuid.UUID(product_id)
    product = await db_session.get(DataProduct, product_uuid)
    upload = storage_service.upload(
        b"city,salary\nShanghai,100\n",
        f"field-exposure/{product_id}/wildcard.csv",
    )
    product.encrypted_storage_path = upload["path"]
    db_session.add(
        FieldVisibilityConfig(
            product_id=product_uuid,
            default_sensitivity="internal",
            field_rules={"city": {"sensitivity": "public"}, "salary": {"sensitivity": "internal"}},
        )
    )
    contract = Contract(
        contract_no=f"FIELD-{uuid.uuid4().hex[:12]}",
        contract_type=ContractType.DATA_QUERY.value,
        status=ContractStatus.ACTIVE.value,
        provider_id=product.provider_id,
        buyer_id=uuid.UUID(buyer_id),
        title="Field wildcard gateway contract",
        terms={},
        product_ids=[product_id],
        allowed_operations="query",
    )
    db_session.add(contract)
    await db_session.flush()

    response = await GatewayService().execute_query(db_session, contract, product_id, "SELECT * FROM data", "json")

    assert response.success is False
    assert response.status_code == 403
    assert "SELECT * is not allowed" in response.error
