"""A32（high）— l2c 文件名未经转义拼进容器内 Python 源码。

台账: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A32

`_l2c_file_op` 用 f-string 把文件名直拼进容器内那段 python（`open(dir+'/'+{name},...)`），
唯一的清洗是 `validate_filename`（禁 `/`、`\\`、`..`、控制字符），单引号、分号、括号、
`#` 全部放行。于是文件名本身就是一段在沙箱用户身份下执行的 Python，而且它**绕过了
code_scanner 这道关**（扫描器只扫 execute 的代码体，永远看不到这段拼接源码）。

执行面用 create_autospec 造出 CubeRuntimeAdapter（保持 isinstance 判定为真），并把
HARVEST_DIRS 指到 tmp 目录后**在真文件系统上执行**生成的那段容器内代码，所以这里
验证的是真实注入行为，不是把拼接逻辑抄一遍。
"""

from __future__ import annotations

import subprocess
import sys
import types
import uuid
from pathlib import Path
from unittest import mock

import pytest
from fastapi import HTTPException

from app.api import sandbox_sessions as ss
from app.services import cube_runtime_adapter as cra

INJECTION = "x','wb');import pathlib;pathlib.Path('A32_PWNED').write_text('1');open('y','wb');#"


@pytest.fixture()
def guest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    out_dir = tmp_path / "sandbox" / "output"
    work_dir = tmp_path / "scratch"
    out_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    monkeypatch.setattr(cra, "HARVEST_DIRS", ((str(out_dir), "output"), (str(work_dir), "workspace")))

    adapter = mock.create_autospec(cra.CubeRuntimeAdapter, instance=True)
    seen: list[str] = []

    async def _execute(_container_id: str, code: str, _language: str = "python", **_kw: object) -> dict:
        seen.append(code)
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=30, cwd=str(tmp_path)
        )
        result = {"output": proc.stdout, "exit_code": proc.returncode}
        if proc.returncode:
            result["error"] = proc.stderr
        return result

    adapter.execute = _execute
    runtime = mock.Mock()
    runtime.get_adapter.return_value = adapter
    monkeypatch.setattr(
        "app.services.sandbox_manager.get_sandbox_manager",
        mock.AsyncMock(return_value=runtime),
    )
    session = types.SimpleNamespace(
        id=uuid.uuid4(), container_id="l2c-node1-" + "a" * 32, sandbox_level="l2c"
    )
    return {"out": out_dir, "work": work_dir, "session": session, "tmp": tmp_path, "seen": seen}


async def test_upload_filename_cannot_execute_python_in_the_guest(guest: dict) -> None:
    """A32: 带引号/分号的文件名只能是一个文件名，不得变成容器内可执行代码。"""
    await ss._l2c_file_op(guest["session"], "upload", INJECTION, b"raw-input", where="workspace")

    marker = guest["tmp"] / "A32_PWNED"
    assert not marker.exists(), "A32: the filename was executed as Python inside the guest"

    landed = guest["work"] / INJECTION
    assert landed.read_bytes() == b"raw-input", "A32 的修复不得改变合法上传的落盘语义"


async def test_generated_guest_source_embeds_no_raw_filename(guest: dict) -> None:
    """A32: 四个 op 生成的那段源码里都不得出现未转义的原始文件名。"""
    marker = "a32marker;import os;.txt"
    ops = [
        ("upload", b"payload", "workspace"),
        ("download", b"", ""),
        ("delete", b"", ""),
        ("list", b"", ""),
    ]
    for op, data, where in ops:
        guest["seen"].clear()
        try:
            await ss._l2c_file_op(guest["session"], op, marker, data, where=where)
        except HTTPException:
            pass
        assert guest["seen"], f"{op}: no guest source was produced"
        code = guest["seen"][-1]
        assert marker not in code, f"A32: {op} interpolated the raw filename into guest source"
        assert "base64" in code, f"A32: {op} must pass the name as an encoded argument, not as source"
