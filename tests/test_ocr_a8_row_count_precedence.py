"""A8（medium）— data_products 创建端点的运算符优先级把显式入参吃掉。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A8

`row_count=body.row_count or resource.row_count if resource else None` 解析为
`(body.row_count or resource.row_count) if resource else None` ⇒ 未关联 resource 时
调用方显式传的 row_count 全部落 NULL，且 `or` 把合法的 `0`（空表）当成「未提供」。
本文件钉住创建端点的入参优先级：显式值（含 0）优先，未提供才继承资源。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_resource import DataResource, ResourceStatus


async def _ready_resource(db_session: AsyncSession, provider_id: str, row_count: int) -> str:
    resource = DataResource(
        provider_id=uuid.UUID(provider_id),
        name=f"a8-resource-{uuid.uuid4().hex[:8]}",
        status=ResourceStatus.READY.value,
        storage_path=f"test-resources/a8-{uuid.uuid4().hex}.csv",
        row_count=row_count,
    )
    db_session.add(resource)
    await db_session.flush()
    await db_session.refresh(resource)
    return str(resource.id)


@pytest.mark.parametrize("explicit", [5, 0])
async def test_explicit_row_count_without_resource_is_kept(
    client: AsyncClient, make_user, explicit: int
):
    headers, _uid = await make_user("data_provider", "a8")
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"a8-orphan-{explicit}", "row_count": explicit},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == explicit


async def test_row_count_without_resource_and_without_body_is_null(
    client: AsyncClient, make_user
):
    headers, _uid = await make_user("data_provider", "a8")
    resp = await client.post(
        "/api/v1/data-products", json={"name": "a8-no-row-count"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] is None


@pytest.mark.parametrize("explicit,expected", [(3, 3), (0, 0)])
async def test_explicit_row_count_wins_over_resource(
    client: AsyncClient, db_session: AsyncSession, make_user, explicit: int, expected: int
):
    headers, uid = await make_user("data_provider", "a8")
    resource_id = await _ready_resource(db_session, uid, 99)
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": f"a8-linked-{explicit}", "resource_id": resource_id, "row_count": explicit},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == expected


async def test_row_count_is_inherited_from_resource_when_omitted(
    client: AsyncClient, db_session: AsyncSession, make_user
):
    headers, uid = await make_user("data_provider", "a8")
    resource_id = await _ready_resource(db_session, uid, 99)
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": "a8-inherit", "resource_id": resource_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == 99
