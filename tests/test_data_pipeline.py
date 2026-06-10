"""Unstructured data pipeline tests."""
import json
import pytest
from pathlib import Path
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_pipeline_submit_task():
    """Unit test: submit a task."""
    from app.services.unstructured_pipeline import UnstructuredPipeline
    p = UnstructuredPipeline(workspace_root="/tmp/test-pipeline-unit")
    # Create a dummy input file
    dummy = Path("/tmp/test-pipeline-input.txt")
    dummy.write_text("hello world")
    task = p.submit_task("document", str(dummy))
    assert task.task_id
    assert task.task_type == "document"
    assert task.status == "pending"
    dummy.unlink()


@pytest.mark.asyncio
async def test_pipeline_execute_document():
    """Unit test: execute document processing in sandbox."""
    from app.services.unstructured_pipeline import UnstructuredPipeline
    p = UnstructuredPipeline(workspace_root="/tmp/test-pipeline-doc")
    dummy = Path("/tmp/test-pipeline-doc-input.txt")
    dummy.write_text("Hello World\nLine 2\nLine 3")
    task = p.submit_task("document", str(dummy))
    result = await p.execute_task(task.task_id)
    assert result.success is True
    assert result.duration_ms >= 0
    dummy.unlink()


@pytest.mark.asyncio
async def test_pipeline_execute_missing_file():
    """Unit test: missing input file returns error."""
    from app.services.unstructured_pipeline import UnstructuredPipeline
    p = UnstructuredPipeline(workspace_root="/tmp/test-pipeline-miss")
    task = p.submit_task("document", "/nonexistent/file.txt")
    result = await p.execute_task(task.task_id)
    assert result.success is False
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_pipeline_retries_and_encrypts_artifacts(tmp_path):
    """Pipeline retries transient sandbox failures and encrypts output artifacts."""
    from app.services.storage_service import storage_service
    from app.services.unstructured_pipeline import UnstructuredPipeline

    class FakeAdapter:
        def __init__(self, root: Path):
            self.root = root
            self.calls = 0

        async def execute(self, container_id: str, code: str, language: str):
            self.calls += 1
            task_id = container_id.replace("bwrap-", "")
            output_dir = self.root / task_id / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            if self.calls == 1:
                return {"exit_code": 1, "output": "transient OCR failure", "duration_ms": 1}
            (output_dir / "result.json").write_text(json.dumps({"files": [{"filename": "doc.txt", "chars": 14}]}))
            (output_dir / "doc.txt").write_text("safe aggregate", encoding="utf-8")
            return {"exit_code": 0, "output": "ok", "duration_ms": 2}

    p = UnstructuredPipeline(workspace_root=str(tmp_path / "pipeline"))
    p._adapter = FakeAdapter(tmp_path / "pipeline")
    input_file = tmp_path / "doc.txt"
    input_file.write_text("hello", encoding="utf-8")

    task = p.submit_task("document", str(input_file), options={"max_retries": 1})
    result = await p.execute_task(task.task_id)

    assert result.success is True
    assert result.output["retry_count"] == 1
    assert len(result.output["attempts"]) == 2
    assert result.output["stage_status"]["artifact_encryption"]["status"] == "completed"
    assert result.output["stage_status"]["output_review"]["status"] == "completed"
    txt_artifact = next(item for item in result.output["artifacts"] if item["path"] == "doc.txt")
    assert txt_artifact["encrypted"] is True
    assert storage_service.get_encryption_info(txt_artifact["storage_path"])["encrypted"] is True
    assert storage_service.download(txt_artifact["storage_path"]) == b"safe aggregate"


@pytest.mark.asyncio
async def test_pipeline_output_review_blocks_critical_text_artifact(tmp_path):
    """Critical DLP findings block release while artifacts remain encrypted."""
    from app.services.unstructured_pipeline import UnstructuredPipeline

    class PiiAdapter:
        async def execute(self, container_id: str, code: str, language: str):
            task_id = container_id.replace("bwrap-", "")
            output_dir = tmp_path / "pipeline" / task_id / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "result.json").write_text(json.dumps({"files": [{"filename": "pii.txt", "chars": 25}]}))
            (output_dir / "pii.txt").write_text("身份证号 110101199001011234", encoding="utf-8")
            return {"exit_code": 0, "output": "ok", "duration_ms": 1}

    p = UnstructuredPipeline(workspace_root=str(tmp_path / "pipeline"))
    p._adapter = PiiAdapter()
    input_file = tmp_path / "pii.txt"
    input_file.write_text("input", encoding="utf-8")

    task = p.submit_task("document", str(input_file))
    result = await p.execute_task(task.task_id)

    assert result.success is False
    assert "blocked" in result.error.lower()
    assert result.output["output_review"]["blocked"] is True
    assert result.output["stage_status"]["artifact_encryption"]["status"] == "completed"
    assert all(item["encrypted"] for item in result.output["artifacts"])
    assert task.status == "failed"


@pytest.mark.asyncio
async def test_pipeline_nonexistent_task():
    """Unit test: get nonexistent task returns None."""
    from app.services.unstructured_pipeline import UnstructuredPipeline
    p = UnstructuredPipeline(workspace_root="/tmp/test-pipeline-none")
    assert p.get_task("no-such-id") is None
    result = await p.execute_task("no-such-id")
    assert result.success is False


@pytest.mark.asyncio
async def test_api_process_path_no_auth(client: AsyncClient):
    """API test: unauthenticated request returns 401."""
    resp = await client.post("/api/v1/data-pipeline/process-path?task_type=document&input_path=/tmp/test")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_api_process_path_invalid_type(client: AsyncClient, operator_headers: dict):
    """API test: invalid task_type returns 400."""
    resp = await client.post(
        "/api/v1/data-pipeline/process-path?task_type=invalid&input_path=/tmp/test",
        headers=operator_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_process_path_not_found(client: AsyncClient, operator_headers: dict):
    """API test: nonexistent path returns 404."""
    resp = await client.post(
        "/api/v1/data-pipeline/process-path?task_type=document&input_path=/nonexistent/file.txt",
        headers=operator_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_list_tasks(client: AsyncClient, operator_headers: dict):
    """API test: list tasks returns list."""
    resp = await client.get("/api/v1/data-pipeline/tasks", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_api_process_path_document(client: AsyncClient, operator_headers: dict):
    """API test: process a local document file."""
    dummy = Path("/tmp/test-api-doc.txt")
    dummy.write_text("Test content for API pipeline")
    try:
        resp = await client.post(
            f"/api/v1/data-pipeline/process-path?task_type=document&input_path={dummy}",
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_type"] == "document"
        assert data["success"] is True, data
        assert data["duration_ms"] >= 0
    finally:
        dummy.unlink()


@pytest.mark.asyncio
async def test_api_get_task(client: AsyncClient, operator_headers: dict):
    """API test: get task by ID after processing."""
    dummy = Path("/tmp/test-api-get.txt")
    dummy.write_text("content")
    try:
        # Process first
        resp = await client.post(
            f"/api/v1/data-pipeline/process-path?task_type=document&input_path={dummy}",
            headers=operator_headers,
        )
        task_id = resp.json()["task_id"]

        # Get by ID
        resp2 = await client.get(f"/api/v1/data-pipeline/tasks/{task_id}", headers=operator_headers)
        assert resp2.status_code == 200
        assert resp2.json()["task_id"] == task_id
    finally:
        dummy.unlink()


@pytest.mark.asyncio
async def test_api_get_nonexistent_task(client: AsyncClient, operator_headers: dict):
    """API test: get nonexistent task returns 404."""
    resp = await client.get("/api/v1/data-pipeline/tasks/nonexistent", headers=operator_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_upload_and_process(client: AsyncClient, operator_headers: dict):
    """API test: upload file and process."""
    import io
    content = b"Test upload content for pipeline"
    files = {"file": ("test.txt", io.BytesIO(content), "text/plain")}
    data = {"task_type": "document"}
    resp = await client.post(
        "/api/v1/data-pipeline/upload-and-process",
        files=files,
        data=data,
        headers=operator_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True, data


@pytest.mark.asyncio
async def test_api_buyer_cannot_process_path(client: AsyncClient, auth_headers: dict):
    """API test: buyer role cannot use process-path."""
    resp = await client.post(
        "/api/v1/data-pipeline/process-path?task_type=document&input_path=/tmp/test",
        headers=auth_headers,
    )
    assert resp.status_code == 403
