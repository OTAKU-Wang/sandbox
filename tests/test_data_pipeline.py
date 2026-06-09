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
        assert data["success"] is True
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
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_api_buyer_cannot_process_path(client: AsyncClient, auth_headers: dict):
    """API test: buyer role cannot use process-path."""
    resp = await client.post(
        "/api/v1/data-pipeline/process-path?task_type=document&input_path=/tmp/test",
        headers=auth_headers,
    )
    assert resp.status_code == 403
