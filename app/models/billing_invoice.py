"""BillingInvoice Model — 产品计量对账单（Task 3）.

按账期（billing_period，如 "2026-11"）汇总合约维度的四类计量：API 调用数、
计算 CPU 小时、存储 GB·天、出域字节。单价快照存 unit_price_config，金额
明细存 line_items——生成时刻的单价必须固化在账单行里，之后的调价不改历史
账单。blockchain_tx_hash 关联存证锚定（可空：未锚定如实为 NULL）。

账单行不可变：状态机 generated → confirmed / disputed → settled 只前进，
作废走 DISPUTED，不物理删除。
"""
from enum import Enum
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base

# 生产列类型是 PG 原生 JSONB；SQLite 道 with_variant 如实降级为 JSON（见 0035 迁移）。
_JSONB = JSONB().with_variant(JSON(), "sqlite")


class InvoiceStatus(str, Enum):
    GENERATED = "generated"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    SETTLED = "settled"


class BillingInvoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (
        Index("ix_billing_invoices_contract_id", "contract_id"),
        Index("ix_billing_invoices_billing_period", "billing_period"),
        Index("ix_billing_invoices_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_no = Column(String(64), nullable=False, unique=True)
    contract_id = Column(UUID(as_uuid=True), nullable=False)
    product_id = Column(UUID(as_uuid=True), nullable=True)
    buyer_id = Column(UUID(as_uuid=True), nullable=True)
    billing_period = Column(String(32), nullable=False)
    api_calls_count = Column(Integer, nullable=False, default=0)
    compute_cpu_hours = Column(Float, nullable=False, default=0.0)
    storage_gb_days = Column(Float, nullable=False, default=0.0)
    egress_bytes = Column(Integer, nullable=False, default=0)
    unit_price_config = Column(_JSONB, nullable=False, default=dict)
    total_amount = Column(Float, nullable=False, default=0.0)
    line_items = Column(_JSONB, nullable=False, default=list)
    blockchain_tx_hash = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default=InvoiceStatus.GENERATED.value)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
