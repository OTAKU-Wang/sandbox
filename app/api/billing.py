"""Billing API — 产品计量对账单（2026-11-03 realignment Task 10）。

挂载：main.py 以 prefix=/api/v1/deliveries + require_group("gateway") 装载，
最终路径 /api/v1/deliveries/invoices*（计划指定的挂载家族，与 deliveries 同前缀
不同子路径，零冲突）。身份：业务主体（人或 API key）；可见性 = 合约双方
（provider/buyer）+ admin，越权一律 404（存在性不可探知，与 teams/scenarios 同口径）。

计量口径的派生说明写在 tests/test_billing_invoice_api.py 模块 docstring；
本模块只实现该口径，不另起第二套。单价为生成时刻固化的占位快照——
计费中心接入前它不是合同价，这一声明随 unit_price_config 进账单行。
"""
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_business_user
from app.models.api_endpoint import ApiEndpoint
from app.models.billing_invoice import BillingInvoice, InvoiceStatus
from app.models.compute_run import ComputeRun
from app.models.contract import Contract
from app.models.data_product import DataProduct
from app.models.data_resource import DataResource
from app.models.delivery_bundle import DeliveryBundle
from app.models.user import User
from app.services.audit_service import audit_service

router = APIRouter()

DEFAULT_UNIT_PRICES: dict[str, float] = {
    "api_call_per_1k": 0.5,
    "cpu_hour": 4.0,
    "storage_gb_day": 0.08,
    "egress_gb": 1.2,
}
_PRICE_NOTE = "占位单价（计费中心接入前的默认口径，非合同价）"
_GB = 1024**3


class InvoiceGenerateRequest(BaseModel):
    contract_id: uuid.UUID
    billing_period: str = Field(min_length=7, max_length=7, pattern=r"^\d{4}-\d{2}$")


def _period_window(billing_period: str) -> tuple[datetime, datetime]:
    year, month = int(billing_period[:4]), int(billing_period[5:7])
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


async def _visible_contract(db: AsyncSession, contract_id: uuid.UUID, user: User) -> Contract:
    res = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = res.scalar_one_or_none()
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    if getattr(user, "role", None) not in ("admin", "operator"):
        if user.id not in (contract.provider_id, contract.buyer_id):
            raise HTTPException(status_code=404, detail="contract not found")
    return contract


async def _aggregate(db: AsyncSession, contract: Contract, billing_period: str) -> dict[str, Any]:
    start, end = _period_window(billing_period)
    # 现在早于账期末时，gb·天 只计到当下——过去账期记满天数，未来天数不虚计
    days_span = (min(end, datetime.now(timezone.utc)) - start).total_seconds() / 86400
    days_span = max(0.0, round(days_span, 4))

    calls = (
        await db.execute(
            select(func.coalesce(func.sum(ApiEndpoint.calls_used), 0)).where(
                ApiEndpoint.contract_id == contract.id
            )
        )
    ).scalar_one()

    runs = list(
        (
            await db.execute(
                select(ComputeRun).where(
                    ComputeRun.contract_id == contract.id,
                    ComputeRun.created_at >= start,
                    ComputeRun.created_at < end,
                )
            )
        ).scalars().all()
    )
    cpu_hours = 0.0
    unmetered_runs = 0
    for run in runs:
        usage = run.resource_usage or {}
        cpu_seconds = usage.get("cpu_seconds")
        if isinstance(cpu_seconds, (int, float)) and not isinstance(cpu_seconds, bool):
            cpu_hours += float(cpu_seconds) / 3600
        elif run.started_at and run.finished_at:
            started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
            finished = run.finished_at if run.finished_at.tzinfo else run.finished_at.replace(tzinfo=timezone.utc)
            cpu_hours += max(0.0, (finished - started).total_seconds()) / 3600
        else:
            unmetered_runs += 1

    egress = (
        await db.execute(
            select(func.coalesce(func.sum(DeliveryBundle.size_bytes), 0)).where(
                DeliveryBundle.contract_id == contract.id,
                DeliveryBundle.created_at >= start,
                DeliveryBundle.created_at < end,
            )
        )
    ).scalar_one()

    storage_bytes = 0
    if contract.product_ids:
        product_ids = [uuid.UUID(str(p)) for p in contract.product_ids]
        storage_bytes = (
            await db.execute(
                select(func.coalesce(func.sum(DataResource.file_size_bytes), 0))
                .select_from(DataProduct)
                .join(DataResource, DataProduct.resource_id == DataResource.id)
                .where(DataProduct.id.in_(product_ids))
            )
        ).scalar_one() or 0

    storage_bytes = float(storage_bytes or 0)
    egress_bytes = float(egress or 0)

    prices = dict(DEFAULT_UNIT_PRICES)
    storage_gb_days = round((storage_bytes / _GB) * days_span, 6)
    egress_gb = egress_bytes / _GB
    api_amount = round(int(calls or 0) / 1000 * prices["api_call_per_1k"], 6)
    cpu_amount = round(cpu_hours * prices["cpu_hour"], 6)
    storage_amount = round(storage_gb_days * prices["storage_gb_day"], 6)
    egress_amount = round(egress_gb * prices["egress_gb"], 6)
    items = [
        {
            "meter": "api_calls",
            "count": int(calls or 0),
            "unit": "次",
            "unit_price": prices["api_call_per_1k"],
            "amount": api_amount,
            "note": "累计计数器按当期归属如实记录" if calls else None,
        },
        {
            "meter": "compute_cpu_hours",
            "count": round(cpu_hours, 6),
            "unit": "vCPU·h",
            "unit_price": prices["cpu_hour"],
            "amount": cpu_amount,
            "note": f"{unmetered_runs} 条 run 无 cpu_seconds 且无起止时间，按 0 计" if unmetered_runs else None,
        },
        {
            "meter": "storage_gb_days",
            "count": storage_gb_days,
            "unit": "GB·天",
            "unit_price": prices["storage_gb_day"],
            "amount": storage_amount,
            "note": None,
        },
        {
            "meter": "egress_bytes",
            "count": int(egress or 0),
            "unit": "字节",
            "unit_price": prices["egress_gb"],
            "amount": egress_amount,
            "note": None,
        },
    ]
    total = round(abs(api_amount) + abs(cpu_amount) + abs(storage_amount) + abs(egress_amount), 6)
    return {
        "api_calls_count": int(calls or 0),
        "compute_cpu_hours": round(cpu_hours, 6),
        "storage_gb_days": storage_gb_days,
        "egress_bytes": int(egress or 0),
        "unit_price_config": {**prices, "note": _PRICE_NOTE, "billing_period": billing_period},
        "total_amount": total,
        "line_items": items,
    }


@router.post("/invoices/generate", status_code=201)
async def generate_invoice(
    body: InvoiceGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    contract = await _visible_contract(db, body.contract_id, current_user)
    exists = (
        await db.execute(
            select(BillingInvoice).where(
                BillingInvoice.contract_id == contract.id,
                BillingInvoice.billing_period == body.billing_period,
            )
        )
    ).scalars().first()
    if exists is not None:
        raise HTTPException(status_code=409, detail="该合约此账期已有账单（账单行不可变，如需修正走争议流程）")

    agg = await _aggregate(db, contract, body.billing_period)
    invoice = BillingInvoice(
        invoice_no=f"INV-{body.billing_period}-{uuid.uuid4().hex[:10].upper()}",
        contract_id=contract.id,
        product_id=uuid.UUID(str(contract.product_ids[0])) if contract.product_ids else None,
        buyer_id=contract.buyer_id,
        billing_period=body.billing_period,
        status=InvoiceStatus.GENERATED.value,
        created_at=datetime.now(timezone.utc),
        **agg,
    )
    db.add(invoice)
    await db.flush()
    await audit_service.log(
        db, action="billing.invoice.generate", resource_type="billing_invoice",
        user_id=current_user.id, resource_id=str(invoice.id),
        detail={"contract_id": str(contract.id), "billing_period": body.billing_period, "total_amount": invoice.total_amount},
    )
    await db.refresh(invoice)
    return _invoice_out(invoice)


def _invoice_out(invoice: BillingInvoice) -> dict[str, Any]:
    return {
        "id": str(invoice.id),
        "invoice_no": invoice.invoice_no,
        "contract_id": str(invoice.contract_id),
        "product_id": str(invoice.product_id) if invoice.product_id is not None else None,
        "buyer_id": str(invoice.buyer_id) if invoice.buyer_id is not None else None,
        "billing_period": invoice.billing_period,
        "api_calls_count": invoice.api_calls_count,
        "compute_cpu_hours": invoice.compute_cpu_hours,
        "storage_gb_days": invoice.storage_gb_days,
        "egress_bytes": invoice.egress_bytes,
        "unit_price_config": invoice.unit_price_config,
        "total_amount": invoice.total_amount,
        "line_items": invoice.line_items,
        "blockchain_tx_hash": invoice.blockchain_tx_hash,
        "status": invoice.status,
        "created_at": invoice.created_at,
    }


@router.get("/invoices")
async def list_invoices(
    contract_id: uuid.UUID | None = None,
    billing_period: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    stmt = select(BillingInvoice)
    my_contracts = None
    if getattr(current_user, "role", None) not in ("admin", "operator"):
        res = await db.execute(
            select(Contract.id).where(
                (Contract.provider_id == current_user.id) | (Contract.buyer_id == current_user.id)
            )
        )
        my_contracts = [row for row in res.scalars().all()]
        stmt = stmt.where(BillingInvoice.contract_id.in_(my_contracts))
    if contract_id is not None:
        stmt = stmt.where(BillingInvoice.contract_id == contract_id)
    if billing_period:
        stmt = stmt.where(BillingInvoice.billing_period == billing_period)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    res = await db.execute(stmt.order_by(BillingInvoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    items = [_invoice_out(inv) for inv in res.scalars().all()]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/invoices/{invoice_id}/export")
async def export_invoice_csv(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_business_user),
):
    res = await db.execute(select(BillingInvoice).where(BillingInvoice.id == invoice_id))
    invoice = res.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    await _visible_contract(db, uuid.UUID(str(invoice.contract_id)), current_user)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["账单编号", "合约ID", "账期", "API调用数", "CPU小时", "存储GB天", "出域字节", "金额(元)", "状态", "生成时间"])
    for inv in [invoice]:
        created = inv.created_at.isoformat() if inv.created_at is not None else ""
        writer.writerow(
            [
                inv.invoice_no, str(inv.contract_id), inv.billing_period,
                inv.api_calls_count, inv.compute_cpu_hours, inv.storage_gb_days,
                inv.egress_bytes, inv.total_amount, inv.status, created,
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{invoice.invoice_no}.csv"'},
    )
