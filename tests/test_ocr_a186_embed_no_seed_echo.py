"""A186（父会话路由 backend-14a 的 training.py 半边）— 选位密钥 key_seed 原样回给 API 调用方。

`app/services/model_watermark.py` 已把选位改成 `SM3(上下文 | 服务端 pepper | key_seed | 层名)`
并提供不可逆 `key_seed_fingerprint`，但 `WatermarkEmbedResponse.key_seed` 仍在、embed 响应
仍把种子填回去（台账 :497 / :904）。本半边：embed 响应不再回显 key_seed，只回指纹；
种子缺省时由服务端生成。明文列本身（app/models/watermark_record.py）已排队 poolA3-models，
不在本轨 files。
"""
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import training
from app.api.training import WatermarkEmbedResponse
from app.models.training_job import TrainingJob, TrainingJobStatus, TrainingJobType
from app.models.watermark_record import WatermarkRecord
from app.services.model_watermark import ModelWatermarkService, model_watermark_service

LAYER = "layer.weight"
SEED = 1234567


@pytest.fixture
async def wm_job(client, db_session: AsyncSession, make_user, monkeypatch, tmp_path):
    headers, user_id = await make_user("operator", "a186")
    root = tmp_path / "outputs"
    monkeypatch.setattr(training, "_TRAINING_OUTPUT_ROOT", root)
    path = root / "job-1" / "model.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"epoch": 1, "state_dict": {LAYER: [0.125] * 128}}), encoding="utf-8"
    )
    job_id = f"a186-{uuid.uuid4().hex[:8]}"
    db_session.add(
        TrainingJob(
            job_id=job_id,
            job_type=TrainingJobType.LLM_SFT.value,
            status=TrainingJobStatus.COMPLETED.value,
            output_path=str(path),
            user_id=uuid.UUID(user_id),
        )
    )
    await db_session.flush()
    return headers, job_id, path


@pytest.mark.asyncio
async def test_embed_response_model_has_no_seed_field():
    assert "key_seed" not in WatermarkEmbedResponse.model_fields
    assert "key_seed_fingerprint" in WatermarkEmbedResponse.model_fields


@pytest.mark.asyncio
async def test_embed_response_carries_fingerprint_not_the_seed(client: AsyncClient, wm_job):
    headers, job_id, path = wm_job

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark",
        json={
            "owner_id": "owner-a",
            "model_id": "model-a",
            "layer_names": [LAYER],
            "num_bits": 8,
            "key_seed": SEED,
        },
        headers=headers,
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key_seed_fingerprint"] == ModelWatermarkService.key_seed_fingerprint(SEED)
    assert "key_seed" not in body
    assert str(SEED) not in resp.text


@pytest.mark.asyncio
async def test_verify_response_does_not_leak_the_seed(client: AsyncClient, wm_job):
    headers, job_id, path = wm_job

    embed = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark",
        json={"owner_id": "owner-a", "model_id": "model-a", "layer_names": [LAYER], "num_bits": 8},
        headers=headers,
    )
    assert embed.status_code == 201, embed.text
    assert "key_seed" not in embed.json()

    verify = await client.get(f"/api/v1/training/jobs/{job_id}/watermark/verify", headers=headers)

    assert verify.status_code == 200, verify.text
    assert verify.json()["passed"] is True
    assert "key_seed" not in verify.json()
    assert str(SEED) not in verify.text


@pytest.mark.asyncio
async def test_seed_is_server_generated_and_persisted_when_omitted(
    client: AsyncClient, db_session: AsyncSession, wm_job
):
    headers, job_id, path = wm_job

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark",
        json={"owner_id": "owner-a", "model_id": "model-a", "layer_names": [LAYER], "num_bits": 8},
        headers=headers,
    )

    assert resp.status_code == 201, resp.text
    record = (
        await db_session.execute(select(WatermarkRecord).where(WatermarkRecord.job_id == job_id))
    ).scalar_one()
    assert 0 <= record.key_seed <= getattr(training, "_KEY_SEED_MAX", 2**31 - 1)
    assert resp.json()["key_seed_fingerprint"] == model_watermark_service.key_seed_fingerprint(
        record.key_seed
    )
