"""A35（medium-high）— `_l2c_file_op` 四个 op 对 exec 结果的处理互不一致。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A35

现状：只有 download 检查 exec 结果（:1700），upload 从不检查（容器里 python 直接
报错也回 200），list 用"没有制表符就是空目录"把失败读成"目录是空的"，delete 只认
not-found 哨兵、其它失败照样返回 deleted=name。修法要求"四个 op 共用一套判定"：
失败必须显式失败，空目录/空文件必须与失败可区分。
"""

from __future__ import annotations

import base64
import types
import uuid
from unittest import mock

import pytest
from fastapi import HTTPException

from app.api import sandbox_sessions as ss
from app.services import cube_runtime_adapter as cra

GUEST_FAIL = {
    "output": "Traceback (most recent call last):\n  File \"<string>\", line 1\nOSError: disk full",
    "exit_code": 1,
    "error": "OSError: disk full",
}


def _env(monkeypatch: pytest.MonkeyPatch, results: list[dict]) -> types.SimpleNamespace:
    adapter = mock.create_autospec(cra.CubeRuntimeAdapter, instance=True)
    calls: list[str] = []

    async def _execute(_container_id: str, code: str, _language: str = "python", **_kw: object) -> dict:
        calls.append(code)
        return results[min(len(calls) - 1, len(results) - 1)]

    adapter.execute = _execute
    runtime = mock.Mock()
    runtime.get_adapter.return_value = adapter
    monkeypatch.setattr(
        "app.services.sandbox_manager.get_sandbox_manager", mock.AsyncMock(return_value=runtime)
    )
    monkeypatch.setattr(cra, "HARVEST_DIRS", (("/sandbox/output", "output"), ("/scratch", "workspace")))
    session = types.SimpleNamespace(
        id=uuid.uuid4(), container_id="l2c-node1-" + "b" * 32, sandbox_level="l2c"
    )
    session.calls = calls  # type: ignore[attr-defined]
    return session


async def test_upload_surfaces_guest_execution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A35: 容器内写入失败不得回 200  uploaded。"""
    session = _env(monkeypatch, [GUEST_FAIL])
    with pytest.raises(HTTPException) as exc:
        await ss._l2c_file_op(session, "upload", "in.csv", b"a,b\n", where="workspace")
    assert exc.value.status_code == 502, exc.value.detail


async def test_list_distinguishes_failure_from_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A35: 采集失败必须是失败，不能是空清单。"""
    session = _env(monkeypatch, [GUEST_FAIL])
    with pytest.raises(HTTPException) as exc:
        await ss._l2c_file_op(session, "list")
    assert exc.value.status_code == 502, exc.value.detail

    session_ok = _env(monkeypatch, [{"output": "CDSLISTBEGIN\nCDSLISTEND\n", "exit_code": 0}])
    assert await ss._l2c_file_op(session_ok, "list") == {"files": [], "entries": []}


async def test_delete_only_reports_real_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    """A35: 删除失败不得返回 deleted。"""
    session = _env(monkeypatch, [GUEST_FAIL])
    with pytest.raises(HTTPException) as exc:
        await ss._l2c_file_op(session, "delete", "gone.csv")
    assert exc.value.status_code == 502, exc.value.detail


async def test_download_not_found_still_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """A35 的既有语义保持不变：not-found 哨兵仍是 404。"""
    session = _env(monkeypatch, [{"output": "CDS404\n", "exit_code": 3}])
    with pytest.raises(HTTPException) as exc:
        await ss._l2c_file_op(session, "download", "nope.csv")
    assert exc.value.status_code == 404


async def test_download_empty_file_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A35: 0 字节成果文件要能下得下来，而不是 splitlines()[-1] 的 IndexError。"""
    session = _env(monkeypatch, [{"output": "CDSDATA\n", "exit_code": 0}])
    assert await ss._l2c_file_op(session, "download", "empty.csv") == {"data": b""}


async def test_download_rejects_unparseable_output_as_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """A35: 非 base64 的杂散输出必须是 502，不得 500 抛到客户端。"""
    session = _env(monkeypatch, [{"output": "WARNING: cache cold\nnot-base64-at-all\n", "exit_code": 0}])
    with pytest.raises(HTTPException) as exc:
        await ss._l2c_file_op(session, "download", "weird.csv")
    assert exc.value.status_code == 502, exc.value.detail


async def test_download_tolerates_interleaved_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """A35: 载荷按哨兵解析，前面的诊断行不影响取数。"""
    payload = base64.b64encode(b"result").decode()
    session = _env(monkeypatch, [{"output": f"WARNING: noisy\nCDSDATA{payload}\n", "exit_code": 0}])
    assert await ss._l2c_file_op(session, "download", "ok.csv") == {"data": b"result"}
