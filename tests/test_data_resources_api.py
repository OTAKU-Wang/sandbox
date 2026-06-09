import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.data_resource import DataResource


async def _upload_csv_resource(client: AsyncClient, auth_headers: dict, name: str = "People") -> dict:
    response = await client.post(
        "/api/v1/data-resources/upload",
        data={"name": name},
        files={"file": ("people.csv", b"id,name\n1,Alice\n2,Bob\n", "text/csv")},
        headers=auth_headers,
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_delete_data_resource_removes_unreferenced_storage(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
):
    resource = await _upload_csv_resource(client, auth_headers, "Delete Resource")
    resource_id = uuid.UUID(resource["id"])

    result = await db_session.execute(select(DataResource).where(DataResource.id == resource_id))
    stored = result.scalar_one()
    storage_path = stored.storage_path
    assert storage_path
    assert Path(storage_path).exists()

    response = await client.delete(f"/api/v1/data-resources/{resource_id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert response.json()["storage_deleted"] is True
    result = await db_session.execute(select(DataResource).where(DataResource.id == resource_id))
    assert result.scalar_one_or_none() is None
    assert not Path(storage_path).exists()


@pytest.mark.asyncio
async def test_delete_data_resource_rejects_product_reference(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
):
    resource = await _upload_csv_resource(client, auth_headers, "Referenced Resource")
    resource_id = resource["id"]
    product_response = await client.post(
        "/api/v1/data-products",
        json={"name": "Product With Resource", "resource_id": resource_id},
        headers=auth_headers,
    )
    assert product_response.status_code == 201

    response = await client.delete(f"/api/v1/data-resources/{resource_id}", headers=auth_headers)

    assert response.status_code == 409
    assert "referenced by data products" in response.json()["detail"]
    assert await db_session.get(DataResource, uuid.UUID(resource_id)) is not None
