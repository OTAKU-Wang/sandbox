"""Task 10（2026-11-03 realignment 计划）：产品计量对账单生成 + CSV 导出.

计量口径按代码实测派生（计划草案只点名了字段，未定义聚合规则）：
- api_calls_count：合约下 ``api_endpoints.calls_used`` 求和——该列是累计计数器，
  无按账期切片的原始表；账期归属如实记录在 ``unit_price_config.metering_notes``。
- compute_cpu_hours：账期内 ``compute_runs``；优先 ``resource_usage["cpu_seconds"]``
  （调度器实际记账位），缺失才回落 finished-started 挂钟差；两者皆无 → 0（不猜）。
- egress_bytes：账期内 ``delivery_bundles.size_bytes`` 求和。
- storage_gb_days：合约产品挂接的 ``data_resources.file_size_bytes`` × 账期天数；
  产品未挂资源如实为 0。
- 单价是生成时刻固化进 ``unit_price_config`` 的快照（模块默认价，计费中心接入前
  的占位口径，不是合同价——line item 里带 note 如实声明）。
账单不可变：同 (contract, period) 重复生成 409，不覆写历史行。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.api_endpoint import ApiEndpoint
from app.models.compute_run import ComputeRun
from app.models.contract import Contract
from app.models.delivery_bundle import DeliveryBundle


def _period_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def _mk_contract(db, provider_id, buyer_id, **kw):
    if isinstance(provider_id, str):
        provider_id = uuid.UUID(provider_id)
    if isinstance(buyer_id, str):
        buyer_id = uuid.UUID(buyer_id)
    contract = Contract(
        contract_no=f"CT-{uuid.uuid4().hex[:12]}",
        contract_type="data_usage",
        status="active",
        provider_id=provider_id,
        buyer_id=buyer_id,
        title="计量对账测试合约",
        product_ids=[str(uuid.uuid4())],
        **kw,
    )
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def _meter(db, contract, provider_id):
    if isinstance(provider_id, str):
        provider_id = uuid.UUID(provider_id)
    now = datetime.now(timezone.utc)
    db.add(
        ApiEndpoint(
            owner_id=provider_id, contract_id=contract.id,
            name="call-counter", slug=f"inv-ep-{uuid.uuid4().hex[:8]}",
            calls_used=150,
        )
    )
    db.add(
        ComputeRun(
            contract_id=contract.id, status="success",
            started_at=now - timedelta(hours=2), finished_at=now,
            resource_usage={"cpu_seconds": 7200},
            created_at=now,
        )
    )
    db.add(
        DeliveryBundle(
            contract_id=contract.id, object_name=f"b-{uuid.uuid4().hex[:8]}.tar",
            size_bytes=3 * 1024**3, created_at=now,
        )
    )
    # 上一账期的 run：必须不落入本期（created_at 账期过滤的可证伪断言）
    db.add(
        ComputeRun(
            contract_id=contract.id, status="success",
            started_at=now - timedelta(days=40), finished_at=now - timedelta(days=40) + timedelta(hours=5),
            resource_usage={"cpu_seconds": 18000},
            created_at=now - timedelta(days=40),
        )
    )
    await db.flush()


class TestInvoiceGenerate:
    async def test_generate_aggregates_metering_and_freezes_prices(self, client, db_session, make_user):
        headers, provider_id = await make_user(role="data_provider", prefix="inv_prov")
        _, buyer_id = await make_user(role="buyer", prefix="inv_buyer")
        contract = await _mk_contract(db_session, provider_id, buyer_id)
        await _meter(db_session, contract, provider_id)

        period = _period_now()
        resp = await client.post(
            "/api/v1/deliveries/invoices/generate",
            json={"contract_id": str(contract.id), "billing_period": period},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["api_calls_count"] == 150
        assert body["compute_cpu_hours"] == pytest.approx(2.0)  # 只有本期 run；40 天前的不并入
        assert body["egress_bytes"] == 3 * 1024**3
        assert body["status"] == "generated"
        names = {li["meter"] for li in body["line_items"]}
        assert {"api_calls", "compute_cpu_hours", "egress_bytes"} <= names
        assert body["total_amount"] > 0
        assert body["unit_price_config"], "单价快照必须固化"
        assert "占位" in body["unit_price_config"]["note"]

    async def test_duplicate_period_conflicts_409(self, client, db_session, make_user):
        headers, uid = await make_user(role="data_provider", prefix="inv_d_prov")
        _, buyer_id = await make_user(role="buyer", prefix="inv_d_buyer")
        contract = await _mk_contract(db_session, uid, buyer_id)
        period = _period_now()
        r1 = await client.post("/api/v1/deliveries/invoices/generate", json={"contract_id": str(contract.id), "billing_period": period}, headers=headers)
        assert r1.status_code == 201
        r2 = await client.post("/api/v1/deliveries/invoices/generate", json={"contract_id": str(contract.id), "billing_period": period}, headers=headers)
        assert r2.status_code == 409

    async def test_outsider_cannot_generate(self, client, db_session, make_user):
        headers_p, uid_p = await make_user(role="data_provider", prefix="inv_o_prov")
        _, buyer_id = await make_user(role="buyer", prefix="inv_o_buyer")
        headers_x, _ = await make_user(role="data_provider", prefix="inv_o_out")
        contract = await _mk_contract(db_session, uid_p, buyer_id)
        resp = await client.post(
            "/api/v1/deliveries/invoices/generate",
            json={"contract_id": str(contract.id), "billing_period": _period_now()},
            headers=headers_x,
        )
        assert resp.status_code in (403, 404)


class TestInvoiceQueryAndExport:
    async def _gen(self, client, db_session, make_user):
        headers, uid = await make_user(role="data_provider", prefix="inv_x_prov")
        _, buyer_id = await make_user(role="buyer", prefix="inv_x_buyer")
        contract = await _mk_contract(db_session, uid, buyer_id)
        await _meter(db_session, contract, uid)
        period = _period_now()
        r = await client.post("/api/v1/deliveries/invoices/generate", json={"contract_id": str(contract.id), "billing_period": period}, headers=headers)
        assert r.status_code == 201, r.text
        return headers, contract, period, r.json()

    async def test_list_envelope(self, client, db_session, make_user):
        headers, contract, period, inv = await self._gen(client, db_session, make_user)
        resp = await client.get(
            f"/api/v1/deliveries/invoices?contract_id={contract.id}", headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert {"items", "total", "page", "page_size"} <= set(body)
        assert body["total"] == 1
        assert body["items"][0]["invoice_no"] == inv["invoice_no"]

    async def test_export_csv_content_type_and_row(self, client, db_session, make_user):
        headers, contract, period, inv = await self._gen(client, db_session, make_user)
        resp = await client.get(
            f"/api/v1/deliveries/invoices/{inv['id']}/export",
            headers=headers,
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert inv["invoice_no"] in resp.text
        assert "150" in resp.text

    async def test_export_without_invoice_404(self, client, db_session, make_user):
        headers, uid = await make_user(role="data_provider", prefix="inv_n_prov")
        resp = await client.get(
            f"/api/v1/deliveries/invoices/{uuid.uuid4()}/export",
            headers=headers,
        )
        assert resp.status_code == 404
