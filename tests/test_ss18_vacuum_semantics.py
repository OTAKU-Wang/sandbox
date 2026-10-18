"""SS-18 §8 P0′ 真空判据 —— 空输入不得被读成「检查通过并放行」。

钉住三层语义：
1) 成果闸 ``OutputGateway.process`` 提交 0 行 → 诚实失败并置 ``empty_output``，不产出可交付载荷；
2) 独立统计量 ``KAnonymityChecker`` 在无行时保留数学上的真空为真，但必须报 ``evaluated=False``；
3) 检查流水 ``OutputInspector`` 把「阶段启用却无从检查」列进 ``not_evaluated``，并随报告带出。

判据原文见 specs/ss-18-prototype-alignment.md §8 P0′：空/全空白输入必须诚实失败或显式
no_op。翻 ``passed`` 会连带改 DP/合规闸语义，所以这里翻的是**成果闸**、披露的是**统计量**。
"""
import pytest
from httpx import AsyncClient

from app.services.k_anonymity import KAnonymityChecker
from app.services.output_gateway import OutputPolicy, output_gateway
from app.services.output_inspection import OutputInspector, SandboxMode
from app.services.output_security import inspection_to_report


def _policy(fmt: str = "json") -> OutputPolicy:
    return OutputPolicy(allowed_output_formats=[fmt])


@pytest.mark.parametrize("output_format", ["json", "csv"])
def test_gateway_fails_closed_on_zero_rows(output_format: str) -> None:
    result = output_gateway.process([], output_format, _policy(output_format))
    assert result.success is False
    assert result.empty_output is True
    assert result.row_count == 0
    assert result.output_data is None
    assert "Empty output" in (result.error or "")


def test_gateway_zero_rows_never_reaches_inspection() -> None:
    """0 行时不该编出一份「已通过检查」的安全报告——报告为空即证明没走过流水线。"""
    result = output_gateway.process([], "json", _policy("json"))
    assert result.security_report == {}
    assert result.findings == []


def test_gateway_still_releases_nonempty_rows() -> None:
    """反向护栏：有行时照常放行，空成果守卫不得顺手把正常输出也拦掉。"""
    result = output_gateway.process([{"x": 1}, {"x": 2}], "json", _policy("json"),
                                    user_id="u-1", session_id="s-1")
    assert result.success is True
    assert result.empty_output is False
    assert result.row_count == 2
    assert "not_evaluated" in result.security_report


def test_k_anonymity_vacuum_is_flagged_as_not_evaluated() -> None:
    result = KAnonymityChecker(min_k=5).check([], ["age"])
    assert result.passed is True  # 数学上的真空为真，保留
    assert result.evaluated is False  # 但不可当放行凭据
    assert result.reason
    assert result.total_rows == 0


def test_k_anonymity_csv_vacuum_is_flagged_as_not_evaluated() -> None:
    checker = KAnonymityChecker(min_k=5)
    assert checker.check_csv("", ["age"]).evaluated is False
    assert checker.check_csv("age,zip\n", ["age"]).evaluated is False


def test_k_anonymity_with_rows_is_evaluated() -> None:
    rows = [{"age": 30 + i, "zip": "100"} for i in range(6)]
    result = KAnonymityChecker(min_k=5).check(rows, ["age"])
    assert result.evaluated is True
    assert result.reason == ""
    assert result.passed is False  # 每组只有 1 人，低于 k=5


def test_inspection_lists_structured_stages_it_could_not_evaluate() -> None:
    result = OutputInspector().inspect(
        "[]", user_id="u-1", session_id="s-1",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value, output_rows=None,
    )
    report = inspection_to_report(result)
    for stage in ("data_reconstruction", "k_anonymity"):
        assert stage in result.not_evaluated
        assert stage in report["not_evaluated"]
        assert result.stage_results[stage] is True  # 旧字段语义不变，判定方改读 not_evaluated


def test_inspection_lists_model_safety_stage_without_model_output() -> None:
    result = OutputInspector().inspect(
        "epoch 3 done", user_id="u-1", session_id="s-1",
        sandbox_mode=SandboxMode.STRUCTURED_MODELING.value,
    )
    assert "model_safety" in result.not_evaluated
    assert "k_anonymity" not in result.not_evaluated
    assert isinstance(result.degraded, list)


def test_report_defaults_are_empty_lists_for_plain_text_scene() -> None:
    """DEVELOP 场景不启用结构化阶段：既不谎报未评估，也不能缺字段。"""
    result = OutputInspector().inspect(
        "hello", user_id="u-1", session_id="s-1", sandbox_mode=SandboxMode.DEVELOP.value,
    )
    report = inspection_to_report(result)
    assert report["not_evaluated"] == []
    assert report["degraded"] == []


@pytest.mark.asyncio
async def test_gateway_api_reports_empty_output(client: AsyncClient, auth_headers: dict) -> None:
    resp = await client.post("/api/v1/output-control/gateway", json={
        "data": [],
        "output_format": "json",
        "allowed_output_formats": ["json"],
    }, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["empty_output"] is True
    assert body["row_count"] == 0
    assert body["output_data"] is None
