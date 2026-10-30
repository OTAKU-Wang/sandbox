"""A145 — the DP ε ledger cached `remaining` once and never read the DB again.

`_ensure_loaded()` returned early for any contract already in `_loaded`, `consume()` decided on the
in-process `_cache` value and only then wrote, and the status-sync failure was swallowed by
`except Exception: pass`. With the helm/k3s default of several API replicas each replica spent its
own copy of the same ε budget, and a failed DB write left the cache permanently ahead of the ledger.
ε is a compliance control, so the deduction itself has to be decided by the database.
"""
import pytest

from app.services.dp_budget import DPBudgetLedger

CID = "a145-contract"


class _FlakySession:
    """Delegates to the real session but fails the Nth flush."""

    def __init__(self, inner, fail_at: int):
        self._inner = inner
        self._fail_at = fail_at
        self._flushes = 0

    def add(self, obj):
        self._inner.add(obj)

    async def flush(self, *args, **kwargs):
        self._flushes += 1
        if self._flushes >= self._fail_at:
            raise RuntimeError("simulated ledger write failure")
        return await self._inner.flush(*args, **kwargs)

    async def execute(self, *args, **kwargs):
        return await self._inner.execute(*args, **kwargs)

    def begin_nested(self):
        return self._inner.begin_nested()


@pytest.mark.asyncio
async def test_replica_b_cannot_spend_the_epsilon_replica_a_already_spent(db_session):
    replica_a = DPBudgetLedger()
    replica_b = DPBudgetLedger()
    await replica_a.allocate(db_session, CID, 10.0)

    assert await replica_a.get_remaining(db_session, CID) == 10.0
    assert await replica_b.get_remaining(db_session, CID) == 10.0

    assert await replica_a.consume(db_session, CID, "session-a", 6.0) is True
    assert await replica_b.consume(db_session, CID, "session-b", 6.0) is False, (
        "two replicas must not each spend the same 10.0 ε budget"
    )

    status = await replica_b.get_status(db_session, CID)
    assert abs(status.consumed_epsilon - 6.0) < 1e-9
    assert abs(status.remaining_epsilon - 4.0) < 1e-9

    entries = await replica_a.get_ledger(db_session, CID)
    assert len(entries) == 1, "a rejected consumption must not leave an ε entry behind"


@pytest.mark.asyncio
async def test_loaded_budget_is_refreshed_from_the_db_instead_of_frozen(db_session, monkeypatch):
    replica_a = DPBudgetLedger()
    replica_b = DPBudgetLedger()
    await replica_a.allocate(db_session, CID, 10.0)

    assert await replica_b.get_remaining(db_session, CID) == 10.0
    monkeypatch.setattr(DPBudgetLedger, "_CACHE_TTL", 0.0)

    assert await replica_a.consume(db_session, CID, "session-a", 4.0) is True

    assert abs(await replica_b.get_remaining(db_session, CID) - 6.0) < 1e-9, (
        "a replica's cached remaining must expire back to the DB value"
    )


@pytest.mark.asyncio
async def test_failed_ledger_write_does_not_leave_the_cache_ahead_of_the_db(db_session):
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, CID, 10.0)

    allowed = await ledger.consume(_FlakySession(db_session, fail_at=1), CID, "session-a", 4.0)

    assert allowed is False, "a consumption the database rejected must not report success"
    assert abs(await ledger.get_remaining(db_session, CID) - 10.0) < 1e-9, (
        "the cache must not keep an ε deduction the ledger never recorded"
    )


@pytest.mark.asyncio
async def test_consuming_exactly_the_remaining_still_succeeds(db_session):
    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, CID, 10.0)

    assert await ledger.consume(db_session, CID, "session-a", 10.0) is True
    assert abs(await ledger.get_remaining(db_session, CID)) < 1e-9
    assert await ledger.consume(db_session, CID, "session-a", 0.5) is False


@pytest.mark.asyncio
async def test_consume_materializes_a_missing_status_row(db_session):
    from app.models.dp_budget import DPBudgetStatus

    ledger = DPBudgetLedger()
    await ledger.allocate(db_session, CID, 10.0)
    row = await db_session.get(DPBudgetStatus, CID)
    await db_session.delete(row)
    await db_session.flush()
    ledger._loaded.discard(CID)
    ledger._cache.pop(CID, None)

    assert await ledger.consume(db_session, CID, "session-a", 3.0) is True

    status = await ledger.get_status(db_session, CID)
    assert abs(status.consumed_epsilon - 3.0) < 1e-9
    assert abs(status.remaining_epsilon - 7.0) < 1e-9
