"""Tests for AI Training API: SFT training, job management."""
import json
from pathlib import Path

from fastapi import HTTPException
import pytest
from httpx import AsyncClient

from app.api.training import (
    _load_model_checkpoint,
    _load_model_state_dict,
    _save_model_checkpoint,
)
from app.models.training_job import TrainingJob, TrainingJobStatus, TrainingJobType
from app.services.model_watermark import model_watermark_service


def _checkpoint_job(path) -> TrainingJob:
    return TrainingJob(
        job_id="sft-checkpoint-test",
        job_type=TrainingJobType.LLM_SFT.value,
        status=TrainingJobStatus.COMPLETED.value,
        output_path=str(path),
    )


def test_load_model_state_dict_from_json_wrapper(tmp_path):
    """Watermark APIs load real checkpoint files instead of returning an empty dict."""
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps({
            "epoch": 3,
            "state_dict": {
                "module.layer.weight": [0.1, 0.2, 0.3, 0.4],
            },
        }),
        encoding="utf-8",
    )

    state_dict = _load_model_state_dict(_checkpoint_job(path))

    assert state_dict == {"layer.weight": [0.1, 0.2, 0.3, 0.4]}


def test_model_weight_watermark_is_persisted_to_json_checkpoint(tmp_path):
    """Embedded watermarks survive a fresh checkpoint reload."""
    path = tmp_path / "checkpoint.json"
    path.write_text(
        json.dumps({
            "epoch": 1,
            "state_dict": {
                "layer.weight": [0.125] * 16,
            },
        }),
        encoding="utf-8",
    )
    job = _checkpoint_job(path)
    bits = model_watermark_service.generate_watermark_bits("owner-a", "model-a", num_bits=8)

    checkpoint = _load_model_checkpoint(job)
    embed_result = model_watermark_service.embed_watermark(
        checkpoint.state_dict,
        bits,
        ["layer.weight"],
        key_seed=1234,
    )
    _save_model_checkpoint(checkpoint)

    reloaded = _load_model_checkpoint(job)
    verify_result = model_watermark_service.verify_watermark(
        reloaded.state_dict,
        bits,
        embed_result.metadata,
    )

    assert verify_result.passed is True
    assert verify_result.match_ratio == 1.0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "state_dict" in saved
    assert saved["state_dict"]["layer.weight"] != [0.125] * 16


def test_load_model_state_dict_missing_checkpoint_fails_closed(tmp_path):
    missing = tmp_path / "missing.pt"

    with pytest.raises(HTTPException) as exc_info:
        _load_model_state_dict(_checkpoint_job(missing))

    assert exc_info.value.status_code == 400
    assert "not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_start_sft_training(client: AsyncClient, operator_headers: dict):
    """Start an SFT training job."""
    resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
        "method": "lora",
        "epochs": 3,
        "learning_rate": 2e-4,
        "lora_rank": 16,
        "batch_size": 4,
        "max_seq_length": 2048,
    }, headers=operator_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["job_type"] == "llm_sft"
    assert data["status"] == "completed"
    assert data["base_model"] == "Qwen2.5-7B"
    assert data["job_id"].startswith("sft-")

    get_resp = await client.get(f"/api/v1/training/jobs/{data['job_id']}", headers=operator_headers)
    job = get_resp.json()
    assert job["metrics"]["final_loss"] > 0
    assert job["metrics"]["watermark_injected"] is True
    assert Path(job["output_path"]).exists()


@pytest.mark.asyncio
async def test_start_sft_training_qlora(client: AsyncClient, operator_headers: dict):
    """Start a QLoRA training job."""
    resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-14b",
        "dataset_path": "/data/train.jsonl",
        "method": "qlora",
    }, headers=operator_headers)
    assert resp.status_code == 201
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_start_sft_training_full(client: AsyncClient, operator_headers: dict):
    """Start a full fine-tuning job."""
    resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
        "method": "full",
    }, headers=operator_headers)
    assert resp.status_code == 201
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_start_sft_invalid_method(client: AsyncClient, operator_headers: dict):
    """Invalid method is rejected."""
    resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
        "method": "invalid",
    }, headers=operator_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_start_sft_missing_model(client: AsyncClient, operator_headers: dict):
    """Missing required field is rejected."""
    resp = await client.post("/api/v1/training/sft", json={
        "dataset_path": "/data/train.jsonl",
    }, headers=operator_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_training_jobs(client: AsyncClient, operator_headers: dict):
    """List training jobs returns paginated results."""
    # Create a job first
    await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
    }, headers=operator_headers)

    resp = await client.get("/api/v1/training/jobs", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1
    assert len(data["items"]) >= 1
    job = data["items"][0]
    assert "job_id" in job
    assert "status" in job
    assert "created_at" in job


@pytest.mark.asyncio
async def test_list_training_jobs_with_status_filter(client: AsyncClient, operator_headers: dict):
    """List training jobs with status filter."""
    await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
    }, headers=operator_headers)

    resp = await client.get("/api/v1/training/jobs?status=completed", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["status"] == "completed"


@pytest.mark.asyncio
async def test_get_training_job(client: AsyncClient, operator_headers: dict):
    """Get a specific training job by ID."""
    create_resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
        "method": "lora",
        "epochs": 5,
    }, headers=operator_headers)
    job_id = create_resp.json()["job_id"]

    resp = await client.get(f"/api/v1/training/jobs/{job_id}", headers=operator_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == job_id
    assert data["base_model"] == "Qwen2.5-7B"
    assert data["config"]["epochs"] == 5
    assert data["config"]["method"] == "lora"
    assert data["status"] == "completed"
    assert data["output_path"] is not None
    assert data["metrics"]["total_epochs"] == 5


@pytest.mark.asyncio
async def test_get_training_job_not_found(client: AsyncClient, operator_headers: dict):
    """Get non-existent job returns 404."""
    resp = await client.get("/api/v1/training/jobs/nonexistent", headers=operator_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_completed_training_job_rejected(client: AsyncClient, operator_headers: dict):
    """Completed local training jobs cannot be cancelled retroactively."""
    create_resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
    }, headers=operator_headers)
    job_id = create_resp.json()["job_id"]

    resp = await client.post(f"/api/v1/training/jobs/{job_id}/cancel", headers=operator_headers)
    assert resp.status_code == 400
    assert "Cannot cancel job in completed status" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_training_job_not_found(client: AsyncClient, operator_headers: dict):
    """Cancel non-existent job returns 404."""
    resp = await client.post("/api/v1/training/jobs/nonexistent/cancel", headers=operator_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_training_unauthorized(client: AsyncClient):
    """Training endpoints require auth."""
    resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_training_dp_epsilon(client: AsyncClient, operator_headers: dict):
    """DP epsilon parameter is stored in config."""
    resp = await client.post("/api/v1/training/sft", json={
        "base_model": "qwen2.5-7b",
        "dataset_path": "/data/train.jsonl",
        "dp_epsilon": 8.0,
    }, headers=operator_headers)
    assert resp.status_code == 201
    job_id = resp.json()["job_id"]

    get_resp = await client.get(f"/api/v1/training/jobs/{job_id}", headers=operator_headers)
    assert get_resp.json()["config"]["dp_epsilon"] == 8.0
