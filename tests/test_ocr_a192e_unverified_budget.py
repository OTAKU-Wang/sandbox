"""OCR A192e: 「预算后端读不到」必须报 unverified，绝不能报 exhausted（诚实降级口径）。

owner-output-chain 已把真语义落在 `app/services/dp_budget.py`：后端读失败 ⇒
`tracked=True + source="unverified"`、`get_remaining_sync()=0.0`、`consume_sync()` 显式 False。
本文件钉住消费方没有把这个第三态压平：

1) 注入一个「连着但每次读写都抛 `BudgetBackendUnavailable`」的后端——注入的是后端，
   被测的 `budget_state_sync` 读分支与 `consume_sync` 的 spend 分支都真实走到；
2) 审查报告里出现 `unverified` 字样且不出现 `exhausted`：「无法核实」不是「已耗尽」；
3) 未核实不得走「无预算 = 免费 DP」旁路（`dp_applied` False、DP 阶段 False、不登记
   `dp_no_budget`）——owner-output-chain 已证的不变量，谁改回去这里就红；
4) 反向护栏：真·预算不足仍须报 `dp_budget_exhausted`，防止一律改名成 unverified 糊弄本文件。
"""
import uuid

import pytest

from app.services.dp_budget import BudgetBackendUnavailable, dp_budget_ledger
from app.services.output_inspection import OutputInspector, SandboxMode, dp_engine
from app.services.output_security import inspection_to_report


class _BrokenBudgetBackend:
    """「连得上、但每次读写都失败」的 ε 预算后端：Redis 挂掉/权限被撤的等价物。"""

    def ping(self):
        return True

    @staticmethod
    def _boom(op: str):
        raise BudgetBackendUnavailable(f"injected backend failure on {op}")

    def get(self, key):
        self._boom("GET")

    def set(self, key, value):
        self._boom("SET")

    def eval(self, script, numkeys, *args, **kwargs):
        self._boom("EVAL")


@pytest.fixture
def broken_backend():
    """Attach the failing backend to the ledger singleton, always detach afterwards."""
    dp_budget_ledger.attach_redis(_BrokenBudgetBackend())
    try:
        yield
    finally:
        dp_budget_ledger.attach_redis(None)


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _rows():
    return [{"name": "a", "amount": 100} for _ in range(5)]


def _disclosed_text(result) -> str:
    return " ".join(
        [finding.message for finding in result.findings] + list(result.degraded)
    ).lower()


def test_unreadable_backend_is_unverified_not_exhausted(broken_backend):
    sid = _sid("a192e")
    result = OutputInspector().inspect(
        "统计结果：共 3 条记录", "u-1", sid,
        dp_epsilon=1.0, sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        output_rows=_rows(),
    )
    text = _disclosed_text(result)

    assert "unverified" in text, f"读不到预算必须在披露里说「未核实」: {text!r}"
    assert "exhausted" not in text, f"「无法核实」不得说成「已耗尽」: {text!r}"
    types = {finding.type for finding in result.findings}
    assert "dp_budget_unverified" in types, types
    assert "dp_budget_exhausted" not in types, types

    assert result.dp_budget_state == "unverified"
    assert result.dp_budget_remaining is None, "读不到就是未知，不能报 0.0 冒充已耗尽"

    report = inspection_to_report(result)
    report_text = str(report).lower()
    assert "unverified" in report_text and "exhausted" not in report_text

    assert result.dp_applied is False, "未核实没有计账依据，噪声不能算已应用"
    assert result.noisy_output_rows is None
    assert result.stage_results["differential_privacy"] is False
    assert result.passed is False
    assert not any("dp_no_budget" in d for d in result.degraded), result.degraded


def test_unverified_still_refuses_unaccounted_spend(broken_backend):
    """不变量护栏：读不到时 tracked=True 且 consume_sync 显式 False。

    改成「读不到 = 没预算」就会重新打开不计账的免费 DP 旁路。
    """
    sid = _sid("a192e-inv")
    state = dp_budget_ledger.budget_state_sync(sid)
    assert state["tracked"] is True
    assert state["source"] == "unverified"
    assert state["remaining"] == 0.0
    assert dp_budget_ledger.consume_sync(sid, 1.0) is False


def test_genuine_exhaustion_still_reports_exhausted():
    """反向护栏：可核实的预算不足仍是 exhausted，不许改名成 unverified。"""
    sid = _sid("a192e-ex")
    dp_engine.init_budget(sid, 1.0)
    result = OutputInspector().inspect(
        "统计结果", "u-1", sid,
        dp_epsilon=2.0, sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        output_rows=_rows(),
    )
    text = _disclosed_text(result)
    assert "dp_budget_exhausted" in {finding.type for finding in result.findings}
    assert "unverified" not in text, text
    assert result.dp_budget_state == "exhausted"
    assert result.dp_budget_remaining == pytest.approx(1.0)


async def test_dp_budget_endpoint_reports_unverified_not_zero(
    broken_backend, client, auth_headers
):
    sid = f"a192e-api-{uuid.uuid4().hex[:12]}"
    resp = await client.get(
        f"/api/v1/output-control/dp/budget/{sid}", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["budget_tracked"] is True
    assert data["budget_state"] == "unverified", data
    assert data["epsilon_remaining"] is None, "读不到不能把 0.0 当剩余量吐给调用方"
    assert data["budget_authoritative"] is False
    assert data["budget_source"] == "unverified"


async def test_dp_budget_endpoint_verified_states(client, auth_headers):
    sid = f"a192e-api-{uuid.uuid4().hex[:12]}"
    init = await client.post(
        f"/api/v1/output-control/dp/budget/init?session_id={sid}&epsilon=3.0",
        headers=auth_headers,
    )
    assert init.status_code == 200

    data = (await client.get(
        f"/api/v1/output-control/dp/budget/{sid}", headers=auth_headers
    )).json()
    assert data["budget_state"] == "sufficient", data
    assert data["epsilon_remaining"] == pytest.approx(3.0)

    assert dp_engine.consume(sid, 3.0) is True
    spent = (await client.get(
        f"/api/v1/output-control/dp/budget/{sid}", headers=auth_headers
    )).json()
    assert spent["budget_state"] == "exhausted", spent
    assert spent["epsilon_remaining"] == pytest.approx(0.0)

    other = (await client.get(
        f"/api/v1/output-control/dp/budget/a192e-none-{uuid.uuid4().hex[:12]}",
        headers=auth_headers,
    )).json()
    assert other["budget_state"] == "unallocated", other
    assert other["budget_tracked"] is False
