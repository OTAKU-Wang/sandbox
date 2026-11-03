"""SS-realignment Task 7 — 数据资源自动分类分级探查。

钉住 POST /api/v1/data-resources/{resource_id}/auto-profile 的三件事：

1. 字段级识别结果与整体推荐敏感等级。识别**必须**复用仓内既有的
   ``app/services/pii_ner.py``（本仓唯一的 PII 表所在），严禁端点自带第 4 份中文正则表；
2. 探查结果真的写回 ``DataResource`` 实体元数据（``field_classifications`` /
   ``schema_fields``）。这不是展示用的副产品：``output_policy._field_rules_for_contract``
   与 ``sandbox_db._field_classifications_for_contract`` 读的就是这两列 ——
   level≥3 强制掩码、level≥4 禁止出域。写错形态（非 int、层级越界）会在查询期 fail-closed；
3. 规则 NER 层的命中只进报告、不进管控。A194 把该层的误报钉在账上（P2：任意日期串
   命中"出生日期"；P3：任意地址串命中"地址"），所以只有确定性的 regex 层证据
   才允许推导 level≥3。

测试里的每一个 pii_type / severity 期望值都来自对 ``pii_ner`` 的实测，不是推测。
"""
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.data_resource import DataResource

# 每列的期望识别结果均实测确认：id_card→critical/regex，手机号→phone/high/regex，
# 邮箱→email/medium/regex，统一社会信用代码→social_credit_code/high/regex。
CSV_WITH_PII = (
    "name,id_card,mobile,email,credit_code,revenue\n"
    "张三,110101199003072341,13800138000,zhangsan@example.com,91110108MA01C8K47T,50000\n"
    "李四,310101198512150011,13900139000,lisi@example.com,91310000MA1FL0GH2X,70000\n"
)

CSV_CLEAN = (
    "order_id,region,amount\n"
    "A001,East,120\n"
    "A002,West,80\n"
)


_CONTENT_TYPES = {"csv": "text/csv", "json": "application/json", "image": "image/png"}


async def _upload(client: AsyncClient, headers: dict[str, str], filename: str, body: bytes,
                  fmt: str = "csv", name: str = "客户敏感信息表") -> str:
    # upload 不读 format 表单字段，格式由 content_type + 后缀嗅探（_detect_format），
    # 所以必须给真实 content type，否则 scan.png 会被当成 csv。
    resp = await client.post(
        "/api/v1/data-resources/upload",
        files={"file": (filename, body, _CONTENT_TYPES[fmt])},
        data={"name": name, "description": "自动分类分级探查"},
        headers=headers,
    )
    # 200 而非 201：upload 路由没有声明 status_code（derive-from-source，不是笔误）。
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _by_field(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {col["field"]: col for col in body["columns"]}


def _detail(resp: Response) -> str:
    payload = resp.json()
    assert isinstance(payload, dict)
    return str(payload["detail"])


@pytest.mark.asyncio
async def test_auto_profile_detects_pii_and_recommends_high(client, auth_headers):
    """核心链路：上传含身份证/手机号/邮箱/统一信用代码的资源 → 推荐 high。"""
    res_id = await _upload(client, auth_headers, "customers.csv", CSV_WITH_PII.encode("utf-8"))

    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["sensitivity_level"] in ("high", "L1", "sensitive")
    assert body["sensitivity_level"] == "high"

    cols = _by_field(body)
    assert set(cols) == {"name", "id_card", "mobile", "email", "credit_code", "revenue"}
    # 字段名只是线索，识别证据来自取样值本身（实测：张三 不命中，身份证串命中）。
    assert cols["id_card"]["pii_type"] == "id_card"
    assert cols["mobile"]["pii_type"] == "phone"
    assert cols["email"]["pii_type"] == "email"
    assert cols["credit_code"]["pii_type"] == "social_credit_code"
    assert cols["name"]["pii_type"] is None
    assert cols["revenue"]["pii_type"] is None
    assert cols["id_card"]["samples_checked"] == 2
    assert cols["id_card"]["evidence_rows"] == 2

    # 识别结果必须能落成引擎与出域网关认识的形态：critical→4（禁出域）、high→3（强制掩码）、
    # medium→2（不管控）。level 语义见 DataResource.field_classifications 的注释。
    assert body["field_classifications"]["id_card"] == 4
    assert body["field_classifications"]["mobile"] == 3
    assert body["field_classifications"]["credit_code"] == 3
    assert body["field_classifications"]["email"] == 2
    assert "name" not in body["field_classifications"]
    assert body["classification_level"] == 4

    # 管控预览直接复用 policy_compiler.field_rules_from_classifications，不另算一套。
    assert body["enforcement_preview"] == {
        "mask_fields": ["mobile", "credit_code"],
        "deny_out_fields": ["id_card"],
    }

    # 引擎如实披露（T9 口径）：auto 在缺 LAC 的环境回落 rule，响应里说的就是实际跑的。
    # 取值集直接来自 pii_ner.PIINERService._resolve_engine 的四个可达分支。
    assert body["engine"] in ("rule", "lac", "transformers", "regex_only")
    assert body["scanned_rows"] == 2
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_auto_profile_persists_entity_metadata(client, auth_headers, db_session):
    """探查结论必须真的写回实体：下游出域管控读的就是这两列，没落库＝功能形同虚设。"""
    res_id = await _upload(client, auth_headers, "customers.csv", CSV_WITH_PII.encode("utf-8"))

    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    resource = await db_session.get(DataResource, uuid.UUID(res_id))
    await db_session.refresh(resource)
    assert resource.field_classifications == {
        "id_card": 4, "mobile": 3, "credit_code": 3, "email": 2,
    }
    # schema_fields 的每一条都换成新对象（普通 JSON 列原地改同一对象不会被标脏）。
    schema = {f["name"]: f for f in resource.schema_fields}
    assert schema["id_card"]["pii_type"] == "id_card"
    assert schema["id_card"]["sensitivity"] == "pii"
    assert schema["id_card"]["classification_level"] == 4
    assert schema["revenue"]["pii_type"] is None
    assert schema["revenue"]["sensitivity"] == "unknown"


@pytest.mark.asyncio
async def test_auto_profile_writes_audit_trail(client, auth_headers, db_session):
    res_id = await _upload(client, auth_headers, "customers.csv", CSV_WITH_PII.encode("utf-8"))
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 200, resp.text

    rows = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "data_resource.auto_profile")
    )).scalars().all()
    mine = [r for r in rows if r.resource_id == res_id]
    assert len(mine) == 1, f"期望恰好一条 auto_profile 审计，实际 {len(mine)}"
    assert mine[0].resource_type == "data_resource"
    assert mine[0].detail["sensitivity_level"] == "high"
    assert mine[0].detail["pii_columns"] == 4


@pytest.mark.asyncio
async def test_auto_profile_ner_layer_findings_are_not_enforced(
    client, auth_headers, db_session, monkeypatch
):
    """A194 P2/P3：规则 NER 层误报（任意地址串→地址、任意日期→出生日期）不得进管控。

    地址列 HIGH severity 若按 regex 层同等对待，就会被写成 level 3 ⇒ 该列被强制掩码，
    而这条命中本身是钉在账上的高误报来源。这里钉住"报告里说、管控里不做"。
    """
    from app.services import pii_ner

    monkeypatch.setattr(
        pii_ner, "pii_ner_service", pii_ner.PIINERService(ner_engine="rule"), raising=True
    )
    csv_body = (
        "addr,born\n"
        "北京市海淀区中关村大街1号,1990年03月07日\n"
    ).encode("utf-8")
    res_id = await _upload(client, auth_headers, "addr.csv", csv_body, name="地址与日期表")

    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cols = _by_field(body)

    assert cols["addr"]["pii_type"] == "address"
    assert cols["addr"]["layer"] == "ner"
    assert cols["addr"]["enforced"] is False
    assert cols["born"]["pii_type"] == "date_of_birth"
    assert cols["born"]["enforced"] is False
    # NER 层证据最高只到 internal（2），因此不产生任何 mask / deny。
    assert body["enforcement_preview"] == {"mask_fields": [], "deny_out_fields": []}
    assert all(level <= 2 for level in body["field_classifications"].values())

    resource = await db_session.get(DataResource, uuid.UUID(res_id))
    await db_session.refresh(resource)
    assert all(level <= 2 for level in (resource.field_classifications or {}).values())


@pytest.mark.asyncio
async def test_auto_profile_clean_file_reports_low_and_persists_empty(
    client, auth_headers, db_session
):
    """无敏感字段：如实报 low + 空管控，而不是把"没查到"写成某个等级。"""
    res_id = await _upload(client, auth_headers, "orders.csv", CSV_CLEAN.encode("utf-8"),
                           name="订单表")
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["sensitivity_level"] == "low"
    assert body["field_classifications"] == {}
    assert body["enforcement_preview"] == {"mask_fields": [], "deny_out_fields": []}
    assert body["classification_level"] is None
    assert {c["field"] for c in body["columns"]} == {"order_id", "region", "amount"}
    assert all(c["pii_type"] is None for c in body["columns"])

    resource = await db_session.get(DataResource, uuid.UUID(res_id))
    await db_session.refresh(resource)
    assert resource.field_classifications == {}


@pytest.mark.asyncio
async def test_auto_profile_scans_only_first_500_rows(client, auth_headers):
    """成本上限是契约的一部分：只读前 500 行，并且如实声明截断。"""
    lines = ["id,note"] + [f"{i},plain-value-{i}" for i in range(600)]
    res_id = await _upload(client, auth_headers, "big.csv", "\n".join(lines).encode("utf-8"),
                           name="大文件")
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["scanned_rows"] == 500
    assert body["truncated"] is True
    # 截断之外还要有取样上限：每列送进 pii_ner 的值数远小于行数。
    assert body["columns_sampled_per_field"] < 500
    assert all(c["samples_checked"] <= body["columns_sampled_per_field"] for c in body["columns"])


@pytest.mark.asyncio
async def test_auto_profile_supports_json_records(client, auth_headers):
    body_json = (
        '[{"customer": "张三", "phone": "13800138000"},'
        ' {"customer": "李四", "phone": "13900139000"}]'
    ).encode("utf-8")
    res_id = await _upload(client, auth_headers, "customers.json", body_json, fmt="json",
                           name="客户 JSON")
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format"] == "json"
    assert _by_field(body)["phone"]["pii_type"] == "phone"
    assert body["sensitivity_level"] == "high"


@pytest.mark.asyncio
async def test_auto_profile_rejects_non_tabular_format(client, auth_headers):
    """非表格资源：拒掉并说明原因，绝不回一句"低敏感"冒充测过。"""
    res_id = await _upload(client, auth_headers, "scan.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64,
                           fmt="image", name="影像片")
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 400
    assert _detail(resp)


@pytest.mark.asyncio
async def test_auto_profile_refuses_without_data_rows(client, auth_headers):
    """0 个数据行（无表头或空文件）不给等级，只给拒接理由。"""
    res_id = await _upload(client, auth_headers, "headless.csv", b"13800138000,50000\n",
                           name="无表头")
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 400
    assert "数据行" in _detail(resp)

    empty_id = await _upload(client, auth_headers, "blank.csv", b",\n",
                             name="空表头")
    resp = await client.post(f"/api/v1/data-resources/{empty_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 400
    assert "表头" in _detail(resp)


@pytest.mark.asyncio
async def test_auto_profile_rejects_malformed_json(client, auth_headers):
    res_id = await _upload(client, auth_headers, "broken.json", b"{not json", fmt="json",
                           name="坏 JSON")
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=auth_headers)
    assert resp.status_code == 400
    assert "解析失败" in _detail(resp)


@pytest.mark.asyncio
async def test_auto_profile_404_and_403(client, auth_headers, make_user):
    resp = await client.post(
        f"/api/v1/data-resources/{uuid.uuid4()}/auto-profile", headers=auth_headers
    )
    assert resp.status_code == 404

    other_headers, _ = await make_user("data_provider", "other")
    res_id = await _upload(client, auth_headers, "customers.csv", CSV_WITH_PII.encode("utf-8"))
    resp = await client.post(f"/api/v1/data-resources/{res_id}/auto-profile", headers=other_headers)
    assert resp.status_code == 403
