"""SS-18 沙箱目录契约（l2c 采集面）—— /sandbox/output 为产物口径, /scratch 为兼容期工作区.

执行面用 create_autospec 造出 CubeRuntimeAdapter（保持 isinstance 判定为真）, 并把
HARVEST_DIRS 指到 tmp 目录后**在真文件系统上执行**生成的那段容器内代码, 因此这里验证的是
真实的目录解析逻辑（不是把它抄一遍）。
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest
from fastapi import HTTPException

from app.api import sandbox_sessions as ss
from app.services import cube_runtime_adapter as cra


@pytest.fixture()
def guest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    out_dir = tmp_path / "sandbox" / "output"
    work_dir = tmp_path / "scratch"
    out_dir.mkdir(parents=True)
    work_dir.mkdir(parents=True)
    monkeypatch.setattr(cra, "HARVEST_DIRS", ((str(out_dir), "output"), (str(work_dir), "workspace")))

    adapter = mock.create_autospec(cra.CubeRuntimeAdapter, instance=True)

    async def _execute(_container_id: str, code: str, _language: str = "python", **_kw: object) -> dict:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        return {"output": proc.stdout, "exit_code": proc.returncode}

    adapter.execute = _execute
    runtime = mock.Mock()
    runtime.get_adapter.return_value = adapter
    monkeypatch.setattr(
        "app.services.sandbox_manager.get_sandbox_manager",
        mock.AsyncMock(return_value=runtime),
    )
    session = types.SimpleNamespace(container_id="l2c-test-0001")
    return {"out": out_dir, "work": work_dir, "session": session}


async def test_upload_lands_in_workspace_not_output(guest: dict) -> None:
    await ss._l2c_file_op(guest["session"], "upload", "in.csv", b"raw-input", where="workspace")
    assert (guest["work"] / "in.csv").read_bytes() == b"raw-input"
    assert not (guest["out"] / "in.csv").exists(), "上传的输入不得静默落进成果采集目录"


async def test_list_tags_source_dir_and_output_wins_on_conflict(guest: dict) -> None:
    (guest["work"] / "model.csv").write_bytes(b"stale-workspace")
    (guest["out"] / "model.csv").write_bytes(b"canonical-output")
    (guest["work"] / "note.txt").write_text("workspace only")

    listing = await ss._l2c_file_op(guest["session"], "list")
    by_name = {e["name"]: e["dir"] for e in listing["entries"]}
    assert by_name == {"model.csv": "output", "note.txt": "workspace"}
    # 向后兼容: 老调用方仍读 files（去重后的名字）
    assert listing["files"] == ["model.csv", "note.txt"]

    # 自动解析取采集优先级（/sandbox/output）
    payload = await ss._l2c_file_op(guest["session"], "download", "model.csv")
    assert payload["data"] == b"canonical-output"
    payload = await ss._l2c_file_op(guest["session"], "download", "model.csv", where="workspace")
    assert payload["data"] == b"stale-workspace"


async def test_missing_file_is_404_and_unknown_dir_is_400(guest: dict) -> None:
    with pytest.raises(HTTPException) as exc:
        await ss._l2c_file_op(guest["session"], "download", "nope.csv")
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await ss._l2c_file_op(guest["session"], "list", where="data")
    assert exc.value.status_code == 400


async def test_authorized_data_dir_is_never_harvested() -> None:
    """契约: /sandbox/data 里的授权数据永远不进成果采集面（唯一出域口是 output/兼容期工作区）。"""
    assert cra.GUEST_DATA_DIR == "/sandbox/data"
    harvest_dirs = {d for d, _tag in cra.HARVEST_DIRS}
    assert cra.GUEST_DATA_DIR not in harvest_dirs
    assert cra.GUEST_OUTPUT_DIR in harvest_dirs
    assert cra.GUEST_EXEC_DIR not in harvest_dirs
    assert cra.GUEST_EXEC_DIR.startswith("/results/"), "exec 中转目录必须留在采集面之外"
