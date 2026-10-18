"""A309/A310/A312 — 数据资源的诚实声明与分页契约（ocr 复核轮 2026-10-18）。

钉住三件事：
- A309：`POST /data-resources/{id}/desensitize` 的 `masking_applied` 必须**如实计算**——
  只有真的发生了掩码才是 true，并给出实际被掩码的列（`masked_columns`）。
  上传推断出的 schema 每列默认是 `sensitivity="unknown"`，普通 CSV 其实一个字段都没掩码，
  此时弹窗绝不能对数商宣称"PII 字段已按策略掩码"（禁伪造铁律）。
- A310：`GET /data-resources/` 加分页，但**未带分页参数的调用方拿到的仍是裸数组**
  （`sdk/cds_sdk/client.py::list_data_resources` 与既有测试都按裸数组消费）。
- A312：`DELETE /data-resources/{id}` 的响应体如实反映存储对象/DEK 是否真的删掉了。
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.data_resource import DataResource


async def _upload(client: AsyncClient, auth_headers: dict, filename: str, content: bytes,
                  content_type: str, name: str) -> dict:
    response = await client.post(
        "/api/v1/data-resources/upload",
        data={"name": name},
        files={"file": (filename, content, content_type)},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _set_schema_fields(db_session, resource_id: str, schema_fields: list[dict]) -> None:
    stored = await db_session.get(DataResource, uuid.UUID(resource_id))
    stored.schema_fields = schema_fields
    await db_session.flush()


# ---------------------------------------------------------------- A309


@pytest.mark.asyncio
async def test_desensitize_does_not_claim_masking_for_non_pii_csv(
    client: AsyncClient, auth_headers: dict, db_session,
):
    """普通 CSV（推断 schema 全是 unknown）没有任何字段被掩码 ⇒ 不得声称已脱敏。"""
    resource = await _upload(
        client, auth_headers, "orders.csv",
        b"order_id,amount\nA1,10\nA2,20\n", "text/csv", "No PII",
    )

    response = await client.post(
        f"/api/v1/data-resources/{resource['id']}/desensitize", headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["masking_applied"] is False
    assert payload["masked_columns"] == []
    # 内容原样透出（没有掩码就不能让数商以为看到的是掩码后的数据）
    assert "A1" in payload["data"]


@pytest.mark.asyncio
async def test_desensitize_reports_the_columns_it_actually_masked(
    client: AsyncClient, auth_headers: dict, db_session,
):
    """PII 列（推断为 pii）确实被掩码 ⇒ true，且逐列点名。"""
    # 手机号写成 138-0013-8000：纯数字会被上传期的类型推断先抓成 integer（既有推断口径），
    # 这里要钉的是掩码声明，不是推断口径。
    resource = await _upload(
        client, auth_headers, "people.csv",
        "name,phone,email\n张三,138-0013-8000,zhangsan@example.com\n李四,139-0013-9000,lisi@example.com\n".encode("utf-8"),
        "text/csv", "PII",
    )

    response = await client.post(
        f"/api/v1/data-resources/{resource['id']}/desensitize", headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["masking_applied"] is True
    assert payload["masked_columns"] == ["phone", "email"]
    assert "138-0013-8000" not in payload["data"]
    assert "138****8000" in payload["data"]
    assert "zhangsan@example.com" not in payload["data"]
    # 未被判为敏感的列原样保留，且不得混进 masked_columns
    assert "张三" in payload["data"]


@pytest.mark.asyncio
async def test_desensitize_ignores_sensitive_columns_absent_from_the_data(
    client: AsyncClient, auth_headers: dict, db_session,
):
    """schema 声明的敏感列不在数据里 ⇒ 什么都没掩码，声明必须回到 false。"""
    resource = await _upload(
        client, auth_headers, "orders2.csv",
        b"order_id,amount\nA1,10\n", "text/csv", "Ghost PII column",
    )
    await _set_schema_fields(
        db_session, resource["id"],
        [{"name": "order_id", "type": "string", "sensitivity": "unknown", "mask_pattern": "none"},
         {"name": "id_card", "type": "id_card", "sensitivity": "pii", "mask_pattern": "none"}],
    )

    response = await client.post(
        f"/api/v1/data-resources/{resource['id']}/desensitize", headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["masking_applied"] is False
    assert response.json()["masked_columns"] == []


@pytest.mark.asyncio
async def test_desensitize_json_resource_reports_masked_columns(
    client: AsyncClient, auth_headers: dict, db_session,
):
    """JSON 资源走另一条掩码分支，声明同样要如实。"""
    resource = await _upload(
        client, auth_headers, "people.json",
        b'[{"city": "BEIJING", "email": "someone@example.com"}]', "application/json", "JSON PII",
    )

    response = await client.post(
        f"/api/v1/data-resources/{resource['id']}/desensitize", headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["masking_applied"] is True
    assert payload["masked_columns"] == ["email"]
    assert "someone@example.com" not in payload["data"]


def test_generator_desensitize_keeps_its_str_signature():
    """`desensitize()` 的返回类型是既有契约（data_products.py 直接当字符串用）。"""
    from app.services.test_data_generator import test_data_generator

    schema = [{"name": "email", "type": "email", "sensitivity": "pii"}]
    out = test_data_generator.desensitize(b"email\nsomeone@example.com\n", "csv", schema)
    assert isinstance(out, str)
    assert "someone@example.com" not in out

    csv_text, masked = test_data_generator.desensitize_with_report(b"email\nsomeone@example.com\n", "csv", schema)
    assert masked == ["email"]
    assert csv_text == out

    # 不支持的格式：空结果 + 空列名，绝不返回"掩码过了"
    empty, none_masked = test_data_generator.desensitize_with_report(b"x", "parquet", schema)
    assert empty == ""
    assert none_masked == []


# ---------------------------------------------------------------- A310


@pytest.mark.asyncio
async def test_list_without_pagination_params_still_returns_bare_array(
    client: AsyncClient, auth_headers: dict,
):
    """向后兼容：SDK/既有调用方按裸数组消费，不传分页参数时形状不许变。"""
    await _upload(client, auth_headers, "one.csv", b"a,b\n1,2\n", "text/csv", "Compat One")

    response = await client.get("/api/v1/data-resources/", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert all(isinstance(item, dict) and "id" in item for item in body)


@pytest.mark.asyncio
async def test_list_paginated_returns_envelope_without_downloading_everything(
    client: AsyncClient, auth_headers: dict,
):
    """`paginated=true` ⇒ {items,total,page,page_size}，且只回这一页。"""
    for i in range(3):
        await _upload(client, auth_headers, f"p{i}.csv", b"a,b\n1,2\n", "text/csv", f"Page {i}")

    first = await client.get(
        "/api/v1/data-resources/", params={"paginated": "true", "page": 1, "page_size": 2},
        headers=auth_headers,
    )
    second = await client.get(
        "/api/v1/data-resources/", params={"paginated": "true", "page": 2, "page_size": 2},
        headers=auth_headers,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    page1, page2 = first.json(), second.json()

    assert page1["total"] >= 3
    assert page1["page"] == 1 and page1["page_size"] == 2
    assert len(page1["items"]) == 2
    assert len(page2["items"]) >= 1
    # 两页不重叠，且 items 就是资源对象本身（字段集与裸数组一致）
    assert not {i["id"] for i in page1["items"]} & {i["id"] for i in page2["items"]}
    assert {"schema_fields", "row_count", "status"} <= set(page1["items"][0])


@pytest.mark.asyncio
async def test_list_paginated_rejects_out_of_range_page_size(
    client: AsyncClient, auth_headers: dict,
):
    response = await client.get(
        "/api/v1/data-resources/", params={"paginated": "true", "page_size": 100000},
        headers=auth_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------- A312


@pytest.mark.asyncio
async def test_delete_reports_undestroyed_storage_and_key_honestly(
    client: AsyncClient, auth_headers: dict, db_session, monkeypatch,
):
    """记录删了但密文对象/DEK 没销毁 ⇒ 响应必须逐字段如实，UI 才有据可依。"""
    from app.services.kms_service import kms_service
    from app.services.storage_service import storage_service

    resource = await _upload(client, auth_headers, "del.csv", b"a,b\n1,2\n", "text/csv", "Delete Partial")
    stored = await db_session.get(DataResource, uuid.UUID(resource["id"]))
    stored.encryption_key_id = "key-under-test"
    await db_session.flush()

    monkeypatch.setattr(storage_service, "delete", lambda path: False, raising=True)

    def _boom(_key_id: str) -> bool:
        raise RuntimeError("kms unavailable")

    monkeypatch.setattr(kms_service, "destroy_key", _boom, raising=True)

    response = await client.delete(f"/api/v1/data-resources/{resource['id']}", headers=auth_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["deleted"] is True
    assert payload["storage_deleted"] is False
    assert payload["key_destroyed"] is False
    assert await db_session.get(DataResource, uuid.UUID(resource["id"])) is None


@pytest.mark.asyncio
async def test_delete_reports_full_cleanup(
    client: AsyncClient, auth_headers: dict, db_session,
):
    """反面：都真删掉了才是 storage_deleted/key_destroyed 双 true。"""
    resource = await _upload(client, auth_headers, "del2.csv", b"a,b\n1,2\n", "text/csv", "Delete Full")
    stored = await db_session.get(DataResource, uuid.UUID(resource["id"]))
    stored.encryption_key_id = "missing-key-is-not-destroyed"
    await db_session.flush()

    response = await client.delete(f"/api/v1/data-resources/{resource['id']}", headers=auth_headers)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["deleted"] is True
    assert payload["storage_deleted"] is True
    # destroy_key 找不到这个 key ⇒ found=False，不能报成已销毁
    assert payload["key_destroyed"] is False
