"""DP budget guardrail tests (gap E3)."""
import pytest

from app.core.config import get_settings
from app.services.dp_budget import dp_budget_ledger


@pytest.mark.asyncio
async def test_consume_rejects_non_positive_epsilon(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "DP_MIN_EPSILON_CONSUMPTION", 0.0)
    await dp_budget_ledger.allocate(db_session, "c-guard-1", 10.0)
    ok = await dp_budget_ledger.consume(db_session, "c-guard-1", "s", 0.0)
    assert ok is False
    ok = await dp_budget_ledger.consume(db_session, "c-guard-1", "s", -1.0)
    assert ok is False


@pytest.mark.asyncio
async def test_consume_rejects_above_per_request_ceiling(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "DP_MAX_EPSILON_PER_CONSUMPTION", 1.0)
    await dp_budget_ledger.allocate(db_session, "c-guard-2", 10.0)
    ok = await dp_budget_ledger.consume(db_session, "c-guard-2", "s", 1.5)
    assert ok is False
    # within ceiling → allowed
    ok = await dp_budget_ledger.consume(db_session, "c-guard-2", "s", 0.5)
    assert ok is True


@pytest.mark.asyncio
async def test_allocate_rejects_above_global_cap(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "DP_MAX_EPSILON_ALLOCATION", 50.0)
    with pytest.raises(ValueError):
        await dp_budget_ledger.allocate(db_session, "c-guard-3", 100.0)
    # within cap → allowed
    await dp_budget_ledger.allocate(db_session, "c-guard-3", 20.0)


@pytest.mark.asyncio
async def test_guardrails_off_by_default(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "DP_MAX_EPSILON_PER_CONSUMPTION", None)
    monkeypatch.setattr(get_settings(), "DP_MAX_EPSILON_ALLOCATION", None)
    await dp_budget_ledger.allocate(db_session, "c-guard-4", 10.0)
    ok = await dp_budget_ledger.consume(db_session, "c-guard-4", "s", 10.0)
    assert ok is True
