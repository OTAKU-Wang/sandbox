"""Tests for DP budget persistence — crash recovery and concurrent consumption."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.dp_budget import DPBudgetLedger
from app.models.dp_budget import DPBudgetStatus


@pytest.mark.asyncio
async def test_allocate_and_consume(db_session: AsyncSession):
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-1", 10.0)
    remaining = await ledger.get_remaining(db_session, "contract-1")
    assert remaining == 10.0

    ok = await ledger.consume(db_session, "contract-1", "session-1", 3.0, "query")
    assert ok is True

    remaining = await ledger.get_remaining(db_session, "contract-1")
    assert abs(remaining - 7.0) < 1e-9


@pytest.mark.asyncio
async def test_insufficient_budget(db_session: AsyncSession):
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-2", 5.0)

    ok = await ledger.consume(db_session, "contract-2", "session-1", 6.0)
    assert ok is False

    remaining = await ledger.get_remaining(db_session, "contract-2")
    assert remaining == 5.0


@pytest.mark.asyncio
async def test_consume_multiple_times(db_session: AsyncSession):
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-3", 10.0)

    await ledger.consume(db_session, "contract-3", "s1", 2.0, "query")
    await ledger.consume(db_session, "contract-3", "s1", 3.0, "train")
    await ledger.consume(db_session, "contract-3", "s2", 1.0, "query")

    remaining = await ledger.get_remaining(db_session, "contract-3")
    assert abs(remaining - 4.0) < 1e-9

    total = await ledger.get_total_consumed(db_session, "contract-3")
    assert abs(total - 6.0) < 1e-9


@pytest.mark.asyncio
async def test_ledger_entries(db_session: AsyncSession):
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-4", 10.0)
    await ledger.consume(db_session, "contract-4", "s1", 2.0, "query")
    await ledger.consume(db_session, "contract-4", "s2", 3.0, "train")

    entries = await ledger.get_ledger(db_session, "contract-4")
    assert len(entries) == 2
    ops = {e.operation for e in entries}
    assert ops == {"query", "train"}


@pytest.mark.asyncio
async def test_crash_recovery(db_session: AsyncSession):
    """Simulate crash: new ledger instance should recover state from DB."""
    ledger1 = DPBudgetLedger()
    await ledger1.allocate(db_session, "contract-5", 10.0)
    await ledger1.consume(db_session, "contract-5", "s1", 4.0, "query")
    await db_session.commit()

    # New ledger instance (empty cache) — should recover from DB
    ledger2 = DPBudgetLedger()
    remaining = await ledger2.get_remaining(db_session, "contract-5")
    assert abs(remaining - 6.0) < 1e-9


@pytest.mark.asyncio
async def test_allocate_upsert(db_session: AsyncSession):
    """Re-allocating should update, not duplicate."""
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-6", 10.0)
    await ledger.allocate(db_session, "contract-6", 20.0)

    remaining = await ledger.get_remaining(db_session, "contract-6")
    assert remaining == 20.0


@pytest.mark.asyncio
async def test_unknown_contract_returns_zero(db_session: AsyncSession):
    ledger = DPBudgetLedger()
    remaining = await ledger.get_remaining(db_session, "nonexistent")
    assert remaining == 0.0


@pytest.mark.asyncio
async def test_budget_status_materialized_on_allocate(db_session: AsyncSession):
    """Allocating a budget should create the dp_budget_status snapshot."""
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-status-1", 10.0)

    status = await ledger.get_status(db_session, "contract-status-1")
    assert status.total_epsilon == 10.0
    assert status.consumed_epsilon == 0.0
    assert status.remaining_epsilon == 10.0

    row = await db_session.get(DPBudgetStatus, "contract-status-1")
    assert row is not None
    assert row.remaining_epsilon == 10.0


@pytest.mark.asyncio
async def test_budget_status_updates_on_consume(db_session: AsyncSession):
    """Consumption should update the materialized status in the same unit of work."""
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-status-2", 10.0)

    ok = await ledger.consume(db_session, "contract-status-2", "session-1", 3.5, "query")
    assert ok is True

    status = await ledger.get_status(db_session, "contract-status-2")
    assert status.total_epsilon == 10.0
    assert abs(status.consumed_epsilon - 3.5) < 1e-9
    assert abs(status.remaining_epsilon - 6.5) < 1e-9
    assert status.last_consumed_at is not None


@pytest.mark.asyncio
async def test_budget_status_rebuilds_from_ledger_if_missing(db_session: AsyncSession):
    """A missing status snapshot should be rebuilt from allocation + ledger rows."""
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-status-3", 10.0)
    await ledger.consume(db_session, "contract-status-3", "session-1", 4.0, "query")
    await db_session.delete(await db_session.get(DPBudgetStatus, "contract-status-3"))
    await db_session.flush()

    recovered = DPBudgetLedger()
    status = await recovered.get_status(db_session, "contract-status-3")
    assert status.total_epsilon == 10.0
    assert abs(status.consumed_epsilon - 4.0) < 1e-9
    assert abs(status.remaining_epsilon - 6.0) < 1e-9


@pytest.mark.asyncio
async def test_reallocate_preserves_consumed_budget_in_status(db_session: AsyncSession):
    """Changing total budget should not erase already consumed epsilon."""
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, "contract-status-4", 10.0)
    await ledger.consume(db_session, "contract-status-4", "session-1", 4.0, "query")

    await ledger.allocate(db_session, "contract-status-4", 12.0)
    status = await ledger.get_status(db_session, "contract-status-4")

    assert status.total_epsilon == 12.0
    assert abs(status.consumed_epsilon - 4.0) < 1e-9
    assert abs(status.remaining_epsilon - 8.0) < 1e-9
