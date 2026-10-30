"""OCR A190: 重构检测按位置拼元组比较，改列序/改数值字面量/投影即零成本绕过。

HEAD 用 `tuple(str(v) for v in row.values())` —— 按 dict 插入顺序取值、不按列名对齐，
所以「调换列序」「`2` vs `2.0`」「少几列的投影」都能零成本绕过，而 docstring 自称
field level；真正的逐字段函数 `check_field_reconstruction` 又没被 `inspect` 主阶段调用。

判据：参照比对按列名对齐（数值 float() 归一化、字符串 ID 不去前导零），
投影也算搬到参照行，聚合格式输出仍须放行；主阶段必须真的用上逐字段口径。
"""
import pytest

from app.services.output_inspection import InspectionStage, OutputInspector, SandboxMode


@pytest.fixture
def inspector():
    return OutputInspector()


SOURCE = [{"a": 1, "b": 2.0, "c": "007"}] * 10


def test_reordered_columns_are_still_detected(inspector):
    output = [{"c": "007", "a": 1, "b": 2.0} for _ in range(10)]
    passed, rate = inspector.check_data_reconstruction(output, [dict(r) for r in SOURCE])
    assert passed is False, "调换列序不该等于换了一份数据"
    assert rate == pytest.approx(1.0)


def test_numeric_literal_reformatting_is_detected(inspector):
    output = [{"a": 1.0, "b": 2, "c": "007"} for _ in range(10)]
    passed, rate = inspector.check_data_reconstruction(output, [dict(r) for r in SOURCE])
    assert passed is False, "2 与 2.0 是同一个数值，改字面量写法不该绕开检测"
    assert rate == pytest.approx(1.0)


def test_projection_is_detected(inspector):
    output = [{"a": 1, "c": "007"} for _ in range(10)]
    passed, rate = inspector.check_data_reconstruction(output, [dict(r) for r in SOURCE])
    assert passed is False, "投影（少几列）搬走的仍是参照行的原始单元格，必须计入匹配率"
    assert rate == pytest.approx(1.0)


def test_aggregate_output_still_passes(inspector):
    """反向护栏：聚合结果没有参照行的列名，不该被误判成重构。"""
    output = [{"count": 100, "avg_age": 35}]
    passed, rate = inspector.check_data_reconstruction(output, [dict(r) for r in SOURCE])
    assert passed is True
    assert rate == 0.0


def test_string_id_leading_zero_is_not_merged(inspector):
    """台账口径「字符串 ID 不去前导零」："007" 与 7 是两个不同编号，不得归一成一个值。"""
    output = [{"a": 1, "b": 2.0, "c": 7} for _ in range(10)]
    passed, rate = inspector.check_data_reconstruction(output, [dict(r) for r in SOURCE])
    assert passed is True, "把 007 与 7 归一化会把两个真实存在的不同编号并成一个（误报源）"


def _ssn_source():
    return [{"sn": f"sn-{i:06d}", "bucket": i % 3} for i in range(200)]


def _ssn_dump_output():
    """SELECT DISTINCT sn：整行都不等于参照行（bucket 被改掉），只有逐字段口径看得见。"""
    return [{"sn": f"sn-{i:06d}", "bucket": -1} for i in range(200)]


def test_main_stage_uses_field_level_detection():
    inspector = OutputInspector()
    result = inspector.inspect(
        "明细导出",
        "u-1",
        "s-a190",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        output_rows=_ssn_dump_output(),
        detail={"source_rows": _ssn_source()},
    )
    hits = [f for f in result.findings if f.type == "field_reconstruction"]
    assert hits, (
        "整行比对看不见 DISTINCT 拖列，主阶段必须真的调用逐字段口径，"
        f"否则成果闸只剩一半。stage_results={result.stage_results}"
    )
    assert result.stage_results[InspectionStage.DATA_RECONSTRUCTION.value] is False
    assert result.passed is False


def test_field_level_finding_does_not_leak_raw_values():
    inspector = OutputInspector()
    result = inspector.inspect(
        "明细导出",
        "u-1",
        "s-a190b",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        output_rows=_ssn_dump_output(),
        detail={"source_rows": _ssn_source()},
    )
    for finding in result.findings:
        for sample in finding.samples:
            assert "sn-000001" not in sample, "A189 口径：findings.samples 不得带参照数据原值"


def test_low_cardinality_column_does_not_trip_field_check(inspector):
    """低基数列（distinct < 1/阈值）碰一下就越过集交阈值 —— 那是 k-匿名阶段的活，
    逐字段口径在这种列上没有判别力，不该在这里制造误报。"""
    source = [{"gender": ["M", "F", "X"][i % 3], "n": i} for i in range(30)]
    output = [{"gender": "M", "cnt": 10}, {"gender": "F", "cnt": 12}]
    result = inspector.inspect(
        "按性别汇总",
        "u-1",
        "s-a190c",
        sandbox_mode=SandboxMode.STRUCTURED_QUERY.value,
        output_rows=output,
        detail={"source_rows": source},
    )
    assert not any(f.type == "field_reconstruction" for f in result.findings)
