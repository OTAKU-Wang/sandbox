"""A112: 驱动已执行但信封解析不到时，同一单元格被再执行一遍（high，非幂等副作用放大）。

`run_cell` 的 prefer_kernel 分支里，「信封解不出来」和「驱动根本没跑」原来是同一条
出路——落到函数末尾的 `run_stateless`，于是已经在沙箱里跑完的单元格（INSERT、写文件、
调外部接口）被跑第二遍，且第二遍的结果当正常结果落库。本文件钉住区分后的口径：
有执行痕迹（驱动写过输出）⇒ 只如实降级、绝不重跑；没跑过的三种形态照旧回落。
"""
import base64
import json

import pytest

from app.services import notebook_service as nb


def _envelope(payload: dict) -> str:
    return nb.ENVELOPE_MARKER + base64.b64encode(json.dumps(payload).encode()).decode()


def _executor_factory(first: dict, rest: dict):
    calls: list[str] = []

    async def _executor(script: str) -> dict:
        calls.append(script)
        return dict(first if len(calls) == 1 else rest)

    return _executor, calls


@pytest.mark.asyncio
async def test_truncated_envelope_does_not_rerun_cell():
    executor, calls = _executor_factory(
        {"output": "row inserted\nCDS_CELL_B64::###base64 line truncated by timeout###\n",
         "exit_code": 0, "stderr": ""},
        {"output": "SECOND RUN", "exit_code": 0, "stderr": ""},
    )

    result = await nb.run_cell(executor, "insert into t values (1)", prefer_kernel=True)

    assert len(calls) == 1, f"单元格被执行了 {len(calls)} 遍：非幂等副作用被放大"
    assert result["ok"] is False
    assert result.get("output_unparseable") is True
    assert "SECOND RUN" not in json.dumps(result, ensure_ascii=False, default=str)
    assert result["degraded_reason"] == nb.OUTPUT_UNPARSEABLE_REASON


@pytest.mark.asyncio
async def test_dlp_rewritten_output_does_not_rerun_cell():
    executor, calls = _executor_factory(
        {"output": "row inserted\n（base64 信封行被输出策略改写后已不成形）\n",
         "exit_code": 0, "stderr": ""},
        {"output": "SECOND RUN", "exit_code": 0, "stderr": ""},
    )

    result = await nb.run_cell(executor, "call_external_api()", prefer_kernel=True)

    assert len(calls) == 1, "驱动已经跑过单元格，输出被改写不是重跑的理由"
    assert result["ok"] is False
    assert result.get("output_unparseable") is True


@pytest.mark.asyncio
async def test_transport_error_still_falls_back_once():
    executor, calls = _executor_factory(
        {"output": "", "error": "sandbox transport unavailable", "exit_code": 0},
        {"output": "stateless run", "exit_code": 0, "stderr": ""},
    )

    result = await nb.run_cell(executor, "print(1)", prefer_kernel=True)

    assert result["backend"] == "cds_stateless"
    assert result["kernel_error"] == "transport error"
    assert result.get("output_unparseable") is None


@pytest.mark.asyncio
async def test_envelope_without_cell_result_still_runs_stateless():
    executor, calls = _executor_factory(
        {"output": _envelope({"kernel_error": "kernel connect failed"}) + "\n",
         "exit_code": 0, "stderr": ""},
        {"output": "stateless run", "exit_code": 0, "stderr": ""},
    )

    result = await nb.run_cell(executor, "print(1)", prefer_kernel=True)

    assert len(calls) == 2, "kernel 没跑过单元格，独立执行一次是正当回落"
    assert result["backend"] == "cds_stateless"
    assert result["stdout"] == "stateless run"


@pytest.mark.asyncio
async def test_no_trace_of_driver_run_falls_back():
    executor, calls = _executor_factory(
        {"output": "", "exit_code": 0, "stderr": ""},
        {"output": "stateless run", "exit_code": 0, "stderr": ""},
    )

    result = await nb.run_cell(executor, "print(1)", prefer_kernel=True)

    assert len(calls) == 2
    assert result["backend"] == "cds_stateless"
