"""DP Budget Ledger — persistent differential privacy budget tracking."""
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Float, func, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class DPBudgetEntry(Base):
    """Individual DP budget consumption record."""
    __tablename__ = "dp_budget_entries"
    __table_args__ = (
        Index("ix_dp_budget_contract", "contract_id"),
        Index("ix_dp_budget_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    epsilon_consumed: Mapped[float] = mapped_column(Float, nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False, default="query")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DPBudgetAllocation(Base):
    """Total DP budget allocated per contract."""
    __tablename__ = "dp_budget_allocations"

    contract_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    total_epsilon: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DPBudgetStatus(Base):
    """Materialized DP budget status per contract.

    Specs call for a PostgreSQL materialized view named ``dp_budget_status``.
    The application also runs unit tests on SQLite, so this model is a portable
    materialized snapshot kept in sync by ``DPBudgetLedger`` writes.
    """
    __tablename__ = "dp_budget_status"

    contract_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    total_epsilon: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    consumed_epsilon: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    remaining_epsilon: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
