"""ocr A3 + A165: 数据管道上传越界写与 process-path 零约束/明文残留/DICOM 假脱敏。

钉死：
- 上传文件名过 path_security.sanitize_filename，落点必须在上传根内；
  同名文件不得静默互相覆盖；
- POST /process-path 的 input_path 必须解析在上传根内（safe_resolve_within），
  越界/逃逸一律 400 —— ADMIN/OPERATOR 也不能让流水线读任意宿主路径；
- 任务结束（成功/失败/输入缺失）无条件 rmtree 工作目录：明文输入、OCR 全文、
  抽帧图不得永久残留在宿主机；
- DICOM 脱敏失败（引擎缺失或单文件失败）必须让任务失败（非零退出码），
  不再把 partial_success=True 写进一个没人读的字段后照常报成功。
"""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.api import data_pipeline as dp

pytestmark = pytest.mark.asyncio


def _uproot() -> Path:
    return Path(dp.UPLOAD_ROOT).resolve()


class TestUploadSanitization:
    async def test_traversal_filename_cannot_escape_upload_root(
        self, client: AsyncClient, auth_headers: dict
    ):
        marker = f"a3-escape-{uuid.uuid4().hex[:8]}.txt"
        resp = await client.post(
            "/api/v1/data-pipeline/upload",
            files={"file": (f"../../{marker}", b"payload", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        stored = Path(resp.json()["file_path"]).resolve()
        assert stored.is_relative_to(_uproot()), (
            f"文件名未净化，落点逃逸出上传根: {stored}"
        )
        stray = _uproot().parent / marker
        assert not stray.exists(), f"越界写留下了 {stray}"
        stray.unlink(missing_ok=True)

    async def test_nested_filename_flattens_instead_of_500(
        self, client: AsyncClient, auth_headers: dict
    ):
        resp = await client.post(
            "/api/v1/data-pipeline/upload",
            files={"file": (f"sub/dir/a3-nested-{uuid.uuid4().hex[:6]}.txt", b"x", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"带路径分隔的文件名把 open() 打进 500: {resp.status_code}"
        assert Path(resp.json()["file_path"]).resolve().is_relative_to(_uproot())

    async def test_same_name_uploads_do_not_silently_overwrite(
        self, client: AsyncClient, auth_headers: dict
    ):
        name = f"a3-dup-{uuid.uuid4().hex[:8]}.txt"
        first = await client.post(
            "/api/v1/data-pipeline/upload",
            files={"file": (name, b"first", "text/plain")},
            headers=auth_headers,
        )
        second = await client.post(
            "/api/v1/data-pipeline/upload",
            files={"file": (name, b"second", "text/plain")},
            headers=auth_headers,
        )
        assert first.status_code == 200 and second.status_code == 200
        p1 = Path(first.json()["file_path"])
        p2 = Path(second.json()["file_path"])
        assert p1 != p2, "同名上传静默覆盖（同名即同文件 ⇒ 先上传方的数据蒸发）"
        assert p1.read_bytes() == b"first"


class TestProcessPathContainment:
    async def test_absolute_path_outside_upload_root_rejected(
        self, client: AsyncClient, operator_headers: dict, tmp_path: Path
    ):
        outside = tmp_path / "outside.txt"
        outside.write_text("host data the pipeline must not read", encoding="utf-8")
        resp = await client.post(
            "/api/v1/data-pipeline/process-path",
            params={"task_type": "document", "input_path": str(outside)},
            headers=operator_headers,
        )
        assert resp.status_code == 400, (
            f"上传根外的绝对路径必须被拒（A3②/A165①），实得 {resp.status_code}: {resp.text}"
        )

    async def test_traversal_input_path_rejected(
        self, client: AsyncClient, operator_headers: dict
    ):
        sneaky = str(Path(dp.UPLOAD_ROOT) / ".." / "a165-escape.txt")
        resp = await client.post(
            "/api/v1/data-pipeline/process-path",
            params={"task_type": "document", "input_path": sneaky},
            headers=operator_headers,
        )
        assert resp.status_code == 400, resp.text

    async def test_relative_path_inside_upload_root_accepted(
        self, client: AsyncClient, operator_headers: dict
    ):
        uid = uuid.uuid4().hex
        target_dir = _uproot() / uid
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "in.txt").write_text("inside the root", encoding="utf-8")
        resp = await client.post(
            "/api/v1/data-pipeline/process-path",
            params={"task_type": "document", "input_path": f"{uid}/in.txt"},
            headers=operator_headers,
        )
        assert resp.status_code == 200, (
            f"上传根内的相对路径是合法输入，实得 {resp.status_code}: {resp.text}"
        )
        assert resp.json()["task_id"]


class _OkAdapter:
    def __init__(self, root: Path):
        self.root = root

    async def execute(self, container_id: str, code: str, language: str):
        out = self.root / container_id.replace("bwrap-", "") / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps({"result": "ok"}), encoding="utf-8")
        return {"exit_code": 0, "output": json.dumps({"result": "ok"}), "duration_ms": 5}


class _FailAdapter:
    async def execute(self, container_id: str, code: str, language: str):
        return {"exit_code": 1, "output": "sandbox blew up", "duration_ms": 1}


class TestWorkspaceErase:
    async def test_workspace_removed_on_success(self, tmp_path: Path):
        from app.services.unstructured_pipeline import UnstructuredPipeline

        p = UnstructuredPipeline(workspace_root=str(tmp_path / "ws"))
        p._adapter = _OkAdapter(tmp_path / "ws")
        secret = tmp_path / "secret.txt"
        secret.write_text("PLAINTEXT INPUT", encoding="utf-8")
        task = p.submit_task("document", str(secret))
        result = await p.execute_task(task.task_id)
        assert result.success is True
        ws = tmp_path / "ws" / task.task_id
        assert not ws.exists(), "明文输入/中间产物永久残留在工作目录（A165②）"

    async def test_workspace_removed_on_failure(self, tmp_path: Path):
        from app.services.unstructured_pipeline import UnstructuredPipeline

        p = UnstructuredPipeline(workspace_root=str(tmp_path / "ws"))
        p._adapter = _FailAdapter()
        inp = tmp_path / "in.txt"
        inp.write_text("x", encoding="utf-8")
        task = p.submit_task("document", str(inp), options={"max_retries": 0})
        result = await p.execute_task(task.task_id)
        assert result.success is False
        assert not (tmp_path / "ws" / task.task_id).exists()

    async def test_workspace_removed_when_input_missing(self, tmp_path: Path):
        from app.services.unstructured_pipeline import UnstructuredPipeline

        p = UnstructuredPipeline(workspace_root=str(tmp_path / "ws"))
        task = p.submit_task("document", str(tmp_path / "nope.txt"))
        result = await p.execute_task(task.task_id)
        assert result.success is False
        assert not (tmp_path / "ws" / task.task_id).exists()


class TestDicomHardFail:
    def test_dicom_strip_failure_exits_nonzero(self, tmp_path: Path):
        """门禁 venv 无 pydicom ⇒ 命中「脱敏引擎不可用」分支：必须非零退出，
        绝不允许 partial_success=True 之后任务照常报成功。"""
        from app.services.unstructured_pipeline import UnstructuredPipeline

        p = UnstructuredPipeline(workspace_root=str(tmp_path / "ws"))
        script = p._dicom_script("case.dcm")
        assert "partial_success" not in script, "没人读的 partial_success 字段必须删除"

        inp = tmp_path / "input"
        out = tmp_path / "output"
        inp.mkdir()
        out.mkdir()
        (inp / "case.dcm").write_bytes(b"DICM dummy")
        run_src = script.replace('Path("/workspace/input")', f'Path({str(inp)!r})') \
                        .replace('Path("/workspace/output")', f'Path({str(out)!r})')
        runner = tmp_path / "dicom_run.py"
        runner.write_text(run_src, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(runner)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        assert proc.returncode != 0, (
            f"脱敏失败却退出 0（任务将谎报成功）: stdout={proc.stdout!r}"
        )
        assert "case.dcm" in (proc.stderr + proc.stdout)
