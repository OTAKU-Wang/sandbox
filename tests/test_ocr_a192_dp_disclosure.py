"""OCR A192: DP 阶段启用却没跑时必须进 not_evaluated；无预算旁路必须披露。

已定口径（2026-10-16 拍板，specs/ss-18-prototype-alignment.md §8）：成果闸对阶段级真空
**只披露不拦**，所以这里的判据是「披露补齐」而不是「翻 passed」：
1) `dp_epsilon` 缺省/<=0 ⇒ DP 没跑 ⇒ `differential_privacy` 进 `not_evaluated` 并随报告带出，
   但 `stage_results` 与 `passed` 语义不变（不得顺手变成拦）；
2) 会话没有分配预算却照样加噪 ⇒ 走的是不计账的进程内旁路 ⇒ 必须进 `degraded`；
3) 反向护栏：真花了预算、真加了噪时，两处披露都不得出现。
"""
import uuid

from app.services.output_inspection import OutputInspector, SandboxMode, dp_engine
from app.services.output_security import inspection_to_report


def _sid():
    return f"a192-{uuid.uuid4().hex[:12]}"


def _rows():
    return [{"name": "a", "amount": 100} for _ in range(5)]


def test_dp_stage_enabled_but_not_applied_is_listed_as_not_evaluated():
    result = OutputInspector().inspect(
        "统计结果", "u-1", _sid(),
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=_rows(),
    )
    assert result.dp_applied is False
    assert "differential_privacy" in result.not_evaluated, (
        "DP 启用却没跑，必须登记 not_evaluated，否则披露渠道里根本看不见 DP"
    )
    report = inspection_to_report(result)
    assert "differential_privacy" in report["not_evaluated"]
    assert result.stage_results["differential_privacy"] is True
    assert result.passed is True, "真空只披露不拦（2026-10-16 口径），不得改成拦"
    assert report["blocked"] is False


def test_dp_epsilon_zero_is_listed_as_not_evaluated():
    result = OutputInspector().inspect(
        "统计结果", "u-1", _sid(), dp_epsilon=0.0,
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=_rows(),
    )
    assert "differential_privacy" in result.not_evaluated


def test_dp_scene_without_dp_stage_is_not_flagged():
    result = OutputInspector().inspect(
        "hello", "u-1", _sid(), sandbox_mode=SandboxMode.DEVELOP.value,
    )
    assert "differential_privacy" not in result.not_evaluated
    assert result.degraded == []


def test_ad_hoc_dp_without_tracked_budget_is_disclosed():
    result = OutputInspector().inspect(
        "统计结果", "u-1", _sid(), dp_epsilon=1.0,
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=_rows(),
    )
    assert result.dp_applied is True, "噪声是真的加了——这条披露不是否认噪声"
    assert result.dp_budget_remaining is None
    assert any("dp_no_budget" in d for d in result.degraded), (
        f"未分配预算即可无限次拿到「已应用 DP」，必须在 degraded 里可见: {result.degraded}"
    )


def test_tracked_budget_consumption_is_not_flagged():
    sid = _sid()
    dp_engine.init_budget(sid, 10.0)
    result = OutputInspector().inspect(
        "统计结果", "u-1", sid, dp_epsilon=2.0,
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=_rows(),
    )
    assert result.dp_applied is True
    assert result.dp_budget_remaining is not None
    assert "differential_privacy" not in result.not_evaluated
    assert not any("dp_no_budget" in d for d in result.degraded), result.degraded
