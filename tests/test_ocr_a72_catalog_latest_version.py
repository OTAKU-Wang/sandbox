"""OCR A72 — 数据目录同时列出一产品的多个已发布版本（业务正确性）。

台账（docs/ocr-review-2026-10-18/ocr-verified-findings.md ### A72）：`catalog.py` 只过
`status == published`，而发布新版本既不降级同系列旧行、也不清 status ⇒ 一个产品发布过
N 次就有 N 行 published 同时出现在买家目录里。修法口径：目录只保留版本链上没有被
「已发布的后继行」顶替过的那些行（`is_latest` 为真 ⟺ 链上没有更晚创建的版本，因此该
判据在链上唯一保留一行）。

第二个用例钉住修法的边界：新版本还在 draft（尚未发布）时，旧版本仍是当前在售行，
不得因为 `is_latest=false` 就从目录里消失——按 is_latest 一刀切过滤会引入这个可用性
回归。写侧「发布时降级同系列旧行」属 app/api/data_products.py（见 NEEDS-OWNER）。
"""
import uuid

import pytest
from httpx import AsyncClient


@pytest.fixture
async def provider_headers(client: AsyncClient) -> dict[str, str]:
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"a72_provider_{unique}",
        "email": f"a72_provider_{unique}@example.com",
        "password": "testpass123",
    })
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def buyer_headers(client: AsyncClient) -> dict[str, str]:
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"a72_buyer_{unique}",
        "email": f"a72_buyer_{unique}@example.com",
        "password": "testpass123",
    })
    assert resp.status_code == 201
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _create(client: AsyncClient, headers: dict[str, str], **overrides) -> str:
    payload = {"name": f"A72 Product {uuid.uuid4().hex[:6]}", **overrides}
    resp = await client.post("/api/v1/data-products", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _new_version(client: AsyncClient, headers: dict[str, str], product_id: str) -> str:
    resp = await client.post(
        f"/api/v1/data-products/{product_id}/new-version", headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_a72_second_publish_does_not_double_list(
    client: AsyncClient, provider_headers: dict[str, str], buyer_headers: dict[str, str], publish_product
):
    """同一产品发布两个版本后，目录里只出现最新版本一行，total 同步收窄。"""
    v1 = await _create(client, provider_headers, name="A72 Series")
    await publish_product(v1)
    v2 = await _new_version(client, provider_headers, v1)
    await publish_product(v2)

    resp = await client.get("/api/v1/catalog?limit=100", headers=buyer_headers)
    assert resp.status_code == 200
    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert v2 in ids, f"latest published version missing from catalog: {ids}"
    assert v1 not in ids, f"superseded published version still listed: {ids}"
    assert data["total"] == 1, (
        "count query must use the same superseded-version filter as the item query "
        f"(total={data['total']}, items={len(ids)})"
    )
    assert len(ids) == len(set(ids)), "catalog must not list one product twice"


@pytest.mark.asyncio
async def test_a72_superseded_version_still_resolvable_by_id(
    client: AsyncClient, provider_headers: dict[str, str], buyer_headers: dict[str, str], publish_product
):
    """被顶替的旧版本仍可按 id 取详情：存量合约里存的是旧版本 id，目录去重不得把它打成 404。"""
    v1 = await _create(client, provider_headers, name="A72 Legacy")
    await publish_product(v1)
    v2 = await _new_version(client, provider_headers, v1)
    await publish_product(v2)

    resp = await client.get(f"/api/v1/catalog/{v1}", headers=buyer_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == v1


@pytest.mark.asyncio
async def test_a72_unpublished_new_version_does_not_delist_current(
    client: AsyncClient, provider_headers: dict[str, str], buyer_headers: dict[str, str], publish_product
):
    """新版本还在 draft 时，已发布的旧版本必须留在目录里（不得按 is_latest 一刀切）。"""
    v1 = await _create(client, provider_headers, name="A72 In Flight")
    await publish_product(v1)
    await _new_version(client, provider_headers, v1)

    resp = await client.get("/api/v1/catalog?limit=100", headers=buyer_headers)
    data = resp.json()
    assert v1 in [item["id"] for item in data["items"]], (
        "publishing v2 is still a draft — the currently published version must stay "
        f"discoverable, got {data['items']}"
    )
    assert data["total"] == 1
