"""OCR A287 leg 3（backend 半边，A287b）— POST /api/v1/kms/keys 只认 query，body 形态必然 422。

`create_dek(product_id: uuid.UUID, ...)` 是非 Body 标量参数，FastAPI 按 query 解析，
而管理台 identities.html 的原始版本把 `product_id` 放进 JSON body ⇒ 点一下必然 422。
父会话裁决的修法是 **双形态向后兼容**（A87 在 sandbox_tasks.py 落过的同款形状）：
路由同时接受 query 与 JSON body，重叠时 body 覆盖 query；422 不回显请求体内容。
本文件钉住双形态契约本身：

1. body-only 形态 201（这是 A287 的原始红）；
2. query-only 形态 201 不变（向后兼容守卫，不许把前端或路由单改成对方）；
3. 双形态同发时 body 值胜出（A87 语义）；
4. 两者皆缺 422；body 里 UUID 非法 422；
5. 422 不回显请求体内容（A87 的泄露守卫）。

不重命名任何线字段（kms.py 属跨语言契约面）；响应形状 {key_id,status,algorithm} 不动。
"""
import uuid

import pytest
from httpx import AsyncClient

PRODUCT_A = "3f2b6c1d-8a4e-4f5b-9c3d-7e2a1b0c9d8e"
PRODUCT_B = "7a1d5e2b-9c6f-4a8b-b3e1-5d0c4f8a2b67"


async def test_create_dek_accepts_json_body_product_id(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """A287 原始缺陷形态：product_id 只在 JSON body 里 ⇒ 修复前 422，修复后 201。"""
    resp = await client.post(
        "/api/v1/kms/keys", json={"product_id": PRODUCT_A}, headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text[:300]
    body = resp.json()
    # 线字段不许动（跨语言契约面）：响应仍是 {key_id, status, algorithm}
    assert body["key_id"]
    listed = await client.get(
        "/api/v1/kms/keys", params={"product_id": PRODUCT_A}, headers=admin_headers,
    )
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert [k["key_id"] for k in items] == [body["key_id"]]


async def test_create_dek_query_form_still_works(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """向后兼容守卫：既有 query 形态调用方（测试/脚本）不许被改成 body-only。"""
    resp = await client.post(
        f"/api/v1/kms/keys?product_id={PRODUCT_A}", headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text[:300]
    assert resp.json()["key_id"]


async def test_create_dek_body_wins_over_query_on_overlap(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """A87 语义：双形态同发时 body 值覆盖 query 值。"""
    resp = await client.post(
        f"/api/v1/kms/keys?product_id={PRODUCT_A}",
        json={"product_id": PRODUCT_B}, headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text[:300]
    key_id = resp.json()["key_id"]
    listed_b = await client.get(
        "/api/v1/kms/keys", params={"product_id": PRODUCT_B}, headers=admin_headers,
    )
    assert [k["key_id"] for k in listed_b.json()["items"]] == [key_id]
    listed_a = await client.get(
        "/api/v1/kms/keys", params={"product_id": PRODUCT_A}, headers=admin_headers,
    )
    assert listed_a.json()["items"] == []


async def test_create_dek_missing_product_id_still_422(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """两种形态都没有 product_id 仍然必须 422（fail-closed，不许放行空产品键）。"""
    resp = await client.post("/api/v1/kms/keys", headers=admin_headers)
    assert resp.status_code == 422
    assert "product_id" in resp.text


async def test_create_dek_body_invalid_uuid_422_without_echo(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """body 里 UUID 非法 ⇒ 422，且不回显请求体内容（A87 泄露守卫同款）。"""
    marker = "canary-must-not-be-echoed"
    resp = await client.post(
        "/api/v1/kms/keys",
        json={"product_id": f"not-a-uuid-{marker}"}, headers=admin_headers,
    )
    assert resp.status_code == 422
    assert marker not in resp.text


async def test_create_dek_malformed_body_422_without_echo(
    client: AsyncClient, admin_headers: dict[str, str]
):
    """坏 JSON / 非对象 body：422 且不回显内容；query 合法时不受 body 校验拖累的形态不在本条范围。"""
    marker = "body-canary-secret"
    resp = await client.post(
        "/api/v1/kms/keys",
        content=f"{{not json {marker}".encode(),
        headers={**admin_headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert "valid JSON" in resp.text
    assert marker not in resp.text
