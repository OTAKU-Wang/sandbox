"""SS-realignment Task 8 — 数据产品版本 Diff 与版本回退。

钉住 /api/v1/data-products/{product_id}/versions 系列的三件事：

1. ``POST /versions`` 创建下一版本。版本号是 **整数**：``app/models/data_product.py``
   的 ``version`` 列是 ``Integer``，行级版本模型（parent_id + is_latest）是本仓既有
   设计，计划草案里的 "1.0"/"2.0.0" 字符串版本与该列实际形态不符，按"遵循现有
   数据模型"铁律取整数版本语义（derive-from-source，不是对草案的擅自改写）。
2. ``GET /versions/diff`` 按 added_fields / removed_fields / modified_fields 三张
   清单比对两个版本的 ``data_schema["fields"]``（本仓 schema 约定形态，与
   output_control / test_data_generator 消费的是同一形状）。
3. ``POST /versions/rollback`` 把 is_latest 翻回目标历史版本 —— 当前产品
   （is_latest 行）的当前版本号与 Schema 随之恢复，历史行不被改写、不新增行。

以及关键操作的两条审计：``data_product.create_version`` / ``data_product.rollback``。
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from typing import Mapping

from app.models.audit_log import AuditLog

V1_SCHEMA = {
    "fields": [
        {"name": "tax_id", "type": "string"},
        {"name": "taxpayer_name", "type": "string", "desc": "企业名称"},
        {"name": "region_code", "type": "string"},
    ],
}
# v2 相对 v1：新增 credit_score（added）、删除 region_code（removed）、
# taxpayer_name 类型 string→text（modified）。三张清单各钉一个真实差异。
V2_SCHEMA = {
    "fields": [
        {"name": "tax_id", "type": "string"},
        {"name": "taxpayer_name", "type": "text", "desc": "企业名称"},
        {"name": "credit_score", "type": "number"},
    ],
}


async def _create_product(client: AsyncClient, headers: dict[str, str], name: str, schema: Mapping[str, object]) -> str:
    resp = await client.post(
        "/api/v1/data-products",
        json={"name": name, "product_type": "structured", "data_schema": schema},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _versions(client: AsyncClient, headers: dict[str, str], product_id: str) -> list[dict[str, object]]:
    resp = await client.get(f"/api/v1/data-products/{product_id}/versions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["versions"]


@pytest.mark.asyncio
async def test_version_create_diff_and_rollback_round_trip(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session,
):
    product_id = await _create_product(client, auth_headers, "企业税务画像数据集", V1_SCHEMA)
    assert [(v["version"], v["is_latest"]) for v in await _versions(client, auth_headers, product_id)] == [(1, True)]

    # 1) 创建第二个版本：整数自增到 2，parent 指向当前行，新版本接管 is_latest
    r2 = await client.post(
        f"/api/v1/data-products/{product_id}/versions",
        json={"schema_definition": V2_SCHEMA, "changelog": "新增信用评分列"},
        headers=auth_headers,
    )
    assert r2.status_code == 201, r2.text
    v2 = r2.json()
    assert v2["version"] == 2
    assert v2["parent_id"] == product_id
    assert v2["status"] == "draft"
    assert v2["change_summary"] == "新增信用评分列"
    v2_id = v2["id"]

    versions = await _versions(client, auth_headers, product_id)
    assert [(v["version"], v["is_latest"]) for v in versions] == [(1, False), (2, True)]

    # 2) diff 1→2：三张清单各自命中
    r12 = await client.get(
        f"/api/v1/data-products/{product_id}/versions/diff",
        params={"from_version": 1, "to_version": 2},
        headers=auth_headers,
    )
    assert r12.status_code == 200, r12.text
    d12 = r12.json()
    assert d12["added_fields"] == ["credit_score"]
    assert d12["removed_fields"] == ["region_code"]
    assert [(m["name"], m["from_type"], m["to_type"]) for m in d12["modified_fields"]] == [
        ("taxpayer_name", "string", "text")
    ]
    assert d12["unchanged_fields"] == ["tax_id"]
    # 删列 + 类型变更 ⇒ 破坏性变更
    assert d12["breaking_change"] is True

    # 自比对：全等，无破坏
    r11 = await client.get(
        f"/api/v1/data-products/{product_id}/versions/diff",
        params={"from_version": 1, "to_version": 1},
        headers=auth_headers,
    )
    assert r11.status_code == 200, r11.text
    d11 = r11.json()
    assert d11["added_fields"] == [] and d11["removed_fields"] == [] and d11["modified_fields"] == []
    assert d11["unchanged_fields"] == ["region_code", "tax_id", "taxpayer_name"]
    assert d11["breaking_change"] is False

    # 3) 回退到 1：is_latest 翻回 v1 行，当前版本与 Schema 恢复；v2 行仍是历史
    rb = await client.post(
        f"/api/v1/data-products/{product_id}/versions/rollback",
        json={"target_version": 1},
        headers=auth_headers,
    )
    assert rb.status_code == 200, rb.text
    body = rb.json()
    assert body["current_version"] == 1
    assert body["restored_product_id"] == product_id
    assert body["rolled_back_from"] == 2

    versions = await _versions(client, auth_headers, product_id)
    assert [(v["version"], v["is_latest"]) for v in versions] == [(1, True), (2, False)]

    restored = await client.get(f"/api/v1/data-products/{product_id}", headers=auth_headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["data_schema"] == V1_SCHEMA
    survivor = await client.get(f"/api/v1/data-products/{v2_id}", headers=auth_headers)
    assert survivor.json()["data_schema"] == V2_SCHEMA

    # 4) 审计台账：两条关键操作各落一行，detail 携带版本事实
    create_rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "data_product.create_version")
    )).scalars().all()
    assert [r.resource_id for r in create_rows] == [v2_id]
    assert create_rows[0].detail["version"] == 2

    rollback_rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "data_product.rollback")
    )).scalars().all()
    assert [r.resource_id for r in rollback_rows] == [product_id]
    assert rollback_rows[0].detail["from_version"] == 2
    assert rollback_rows[0].detail["to_version"] == 1


@pytest.mark.asyncio
async def test_version_endpoints_reject_bad_requests(
    client: AsyncClient,
    auth_headers: dict[str, str],
    make_user,
):
    product_id = await _create_product(client, auth_headers, "版本守卫数据集", V1_SCHEMA)
    r2 = await client.post(
        f"/api/v1/data-products/{product_id}/versions",
        json={"schema_definition": V2_SCHEMA},
        headers=auth_headers,
    )
    assert r2.status_code == 201, r2.text

    # 显式版本号不大于现有最大版本 → 400
    bad = await client.post(
        f"/api/v1/data-products/{product_id}/versions",
        json={"version": 2},
        headers=auth_headers,
    )
    assert bad.status_code == 400

    # schema_definition.fields 形态错误 → 422（pydantic 校验，不落库）
    bad_schema = await client.post(
        f"/api/v1/data-products/{product_id}/versions",
        json={"schema_definition": {"fields": "not-a-list"}},
        headers=auth_headers,
    )
    assert bad_schema.status_code == 422

    # diff / rollback 引用不存在的版本 → 404
    missing_diff = await client.get(
        f"/api/v1/data-products/{product_id}/versions/diff",
        params={"from_version": 1, "to_version": 9},
        headers=auth_headers,
    )
    assert missing_diff.status_code == 404
    missing_rb = await client.post(
        f"/api/v1/data-products/{product_id}/versions/rollback",
        json={"target_version": 9},
        headers=auth_headers,
    )
    assert missing_rb.status_code == 404

    # 回退到当前最新版本：幂等 200，最新版本不动
    idem = await client.post(
        f"/api/v1/data-products/{product_id}/versions/rollback",
        json={"target_version": 2},
        headers=auth_headers,
    )
    assert idem.status_code == 200, idem.text
    assert idem.json()["current_version"] == 2
    assert idem.json()["already_latest"] is True
    versions = await _versions(client, auth_headers, product_id)
    assert [(v["version"], v["is_latest"]) for v in versions] == [(1, False), (2, True)]

    # 非属主不能建新版本 / 回退（可见性检查先于属主检查会 404，这里是 owner-only 的 403）
    other_headers, _ = await make_user("data_provider", "ver_other")
    forbidden = await client.post(
        f"/api/v1/data-products/{product_id}/versions/rollback",
        json={"target_version": 1},
        headers=other_headers,
    )
    assert forbidden.status_code == 403
    forbidden_create = await client.post(
        f"/api/v1/data-products/{product_id}/versions",
        json={"changelog": "intruder"},
        headers=other_headers,
    )
    assert forbidden_create.status_code == 403
