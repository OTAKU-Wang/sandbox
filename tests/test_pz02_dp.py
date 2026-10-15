"""PZ-02 (MR-A3): real DP noise in output inspection + budget unification.

Covers the spec §3.1 PZ-02 contract:
- epsilon > 0 -> real Laplace noise on the numeric output rows, budget
  consumed exactly once, second call sees the reduced budget;
- epsilon=0 / None -> DP NOT applied (no infinite-budget semantics);
- tracked-but-exhausted budget -> output blocked (fail-closed);
- the engine's former ``_budgets`` dict is gone: all budget reads/writes
  go through the persistent dp_budget_ledger (single source of truth).
"""
import random
import uuid

import pytest

from app.services.dp_budget import dp_budget_ledger
from app.services.output_inspection import OutputInspector, dp_engine


def _sid(prefix="pz02"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _rows():
    # 5 identical rows -> one k=5 anonymity group (k_anonymity stage needs k>=5).
    return [{"name": "a", "amount": 100 + i, "count": 5} for i in range(5)]


def test_dp_noise_applied_and_budget_consumed_once():
    sid = _sid()
    dp_engine.init_budget(sid, 10.0)
    inspector = OutputInspector()

    r1 = inspector.inspect(
        "统计结果：共 3 条记录", "u1", sid,
        dp_epsilon=2.0, output_rows=_rows(),
    )
    assert r1.dp_applied is True
    assert r1.dp_budget_remaining == pytest.approx(8.0)
    assert r1.noisy_output_rows is not None
    assert [row["name"] for row in r1.noisy_output_rows] == ["a"] * 5
    # Real Laplace noise: numeric fields are perturbed, strings untouched.
    assert r1.noisy_output_rows[0]["amount"] != 100.0
    assert r1.noisy_output_rows[0]["count"] != 5.0
    assert r1.stage_results["differential_privacy"] is True

    # Second call: same query, budget consumed again -> reduced budget.
    r2 = inspector.inspect(
        "统计结果：共 3 条记录", "u1", sid,
        dp_epsilon=2.0, output_rows=_rows(),
    )
    assert r2.dp_applied is True
    assert dp_engine.get_remaining(sid) == pytest.approx(6.0)


def test_fixed_seed_deterministic_noise():
    inspector = OutputInspector()
    random.seed(42)
    r_a = inspector.inspect(
        "统计", "u1", _sid(), dp_epsilon=0.5, output_rows=_rows()
    )
    random.seed(42)
    r_b = inspector.inspect(
        "统计", "u1", _sid(), dp_epsilon=0.5, output_rows=_rows()
    )
    a = [row["amount"] for row in r_a.noisy_output_rows]
    b = [row["amount"] for row in r_b.noisy_output_rows]
    assert a == b, "fixed seed must reproduce the exact noise draw"
    assert a != [100.0, 101.0, 102.0, 103.0, 104.0], "noise must actually perturb"


def test_epsilon_zero_and_none_not_applied():
    inspector = OutputInspector()
    sid = _sid()
    r0 = inspector.inspect(
        "数值 42", "u1", sid, dp_epsilon=0.0, output_rows=[{"amount": 42}]
    )
    assert r0.dp_applied is False
    assert r0.noisy_output_rows is None
    rn = inspector.inspect("数值 42", "u1", sid)
    assert rn.dp_applied is False
    # epsilon=0 is never a budget consumption.
    dp_engine.init_budget(sid, 5.0)
    assert dp_engine.consume(sid, 0.0) is False
    assert dp_engine.get_remaining(sid) == 5.0


def test_budget_exhausted_blocks_release():
    sid = _sid()
    dp_engine.init_budget(sid, 1.0)
    r = OutputInspector().inspect(
        "统计", "u1", sid, dp_epsilon=2.0, output_rows=[{"amount": 1}]
    )
    assert r.dp_applied is False
    assert r.stage_results["differential_privacy"] is False
    assert r.passed is False
    assert any(
        f.type == "dp_budget_exhausted" and f.severity == "high"
        for f in r.findings
    )
    assert r.dp_budget_remaining == pytest.approx(1.0), "failed consume must not touch the budget"


def test_ad_hoc_epsilon_without_tracked_budget():
    sid = _sid()
    r = OutputInspector().inspect(
        "统计", "u1", sid, dp_epsilon=1.0, output_rows=[{"amount": 10}]
    )
    # Noise still applied for real; nothing consumed (no budget exists).
    assert r.dp_applied is True
    assert r.dp_budget_remaining is None
    assert dp_budget_ledger.has_budget_sync(sid) is False


def test_engine_budgets_delegate_to_ledger_single_source():
    # The duplicate in-process budget store is deleted.
    assert not hasattr(dp_engine, "_budgets")

    sid = _sid()
    assert dp_budget_ledger.has_budget_sync(sid) is False

    dp_engine.init_budget(sid, 5.0)
    assert dp_budget_ledger.has_budget_sync(sid) is True
    assert dp_budget_ledger.get_remaining_sync(sid) == 5.0
    assert dp_engine.get_remaining(sid) == 5.0

    assert dp_engine.consume(sid, 1.5) is True
    assert dp_engine.get_remaining(sid) == pytest.approx(3.5)
    assert dp_budget_ledger.get_remaining_sync(sid) == pytest.approx(3.5)

    assert dp_engine.consume(sid, 100.0) is False
    assert dp_engine.get_remaining(sid) == pytest.approx(3.5)
