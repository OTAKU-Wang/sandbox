"""A167 — `key_seed` 无边界：越界值穿过「先改权重」直到落库才 int4 溢出 500（high，活路径）。

`WatermarkEmbedRequest.key_seed` 原先没有任何 `ge/le`，而 `watermark_records.key_seed`
是 `Integer` ⇒ PG INTEGER。致命的是顺序：`_save_model_checkpoint()` 先把嵌了水印的权重
回写磁盘，之后 `db.flush()` 才因 `value out of range for type integer` 抛错，事务回滚掉
「模型已被水印」的唯一审计记录 —— 磁盘上的模型已被改写而无人知道。
要求：① 边界取自列宽（列类型变则边界跟着变，不是字面量）；② 校验必须在改写
checkpoint 之前；③ 越界请求不得动到 checkpoint 文件，且返回 4xx 而不是 500。

A186b（``### A186`` 的最后一条腿）把列的**逻辑**宽度从 ``Integer`` 加宽到
``BigInteger``（密文列 ``EncryptedKeySeed`` 的整型基类；物理落存仍是 0030 的
``cds-ks1:`` 封套 ``TEXT``），于是这里的合法域从 0…2**31-1 变成 0…2**63-1。
本文件与 ``app/api/training.py::_key_seed_column_max`` 是同一轮改动：边界仍然只能
由列宽推导，任何一侧单独改都会红 —— 加宽的是水印定位密钥的**熵**（台账：「水印的全部
安全性 = 这颗 31-bit 种子」），所以下面必须真的受理 >2**31-1 的种子。
"""
import json
import os
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import BigInteger, Integer, SmallInteger, String, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import training
from app.api.training import WatermarkEmbedResponse

_key_seed_column_max = getattr(training, "_key_seed_column_max", lambda: 2**31 - 1)
from app.models.training_job import TrainingJob, TrainingJobStatus, TrainingJobType
from app.models.watermark_record import EncryptedKeySeed, WatermarkRecord

LAYER = "layer.weight"
_UNSET = object()


def _snapshot(path):
    return path.read_bytes(), os.stat(path).st_mtime_ns


def _body(seed):
    body = {
        "owner_id": "owner-a",
        "model_id": "model-a",
        "layer_names": [LAYER],
        "num_bits": 8,
    }
    if seed is not _UNSET:
        body["key_seed"] = seed
    return body


@pytest.fixture
async def wm_job(client, db_session: AsyncSession, make_user, monkeypatch, tmp_path):
    headers, user_id = await make_user("operator", "a167")
    root = tmp_path / "outputs"
    monkeypatch.setattr(training, "_TRAINING_OUTPUT_ROOT", root)
    path = root / "job-1" / "model.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"epoch": 1, "state_dict": {LAYER: [0.125] * 128}}), encoding="utf-8"
    )
    job_id = f"a167-{uuid.uuid4().hex[:8]}"
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


async def _record_count(db_session: AsyncSession, job_id: str) -> int:
    rows = (
        await db_session.execute(select(WatermarkRecord).where(WatermarkRecord.job_id == job_id))
    ).scalars().all()
    return len(rows)


@pytest.mark.asyncio
async def test_out_of_range_seed_is_rejected_without_touching_checkpoint(
    client: AsyncClient, db_session: AsyncSession, wm_job
):
    headers, job_id, path = wm_job
    before = _snapshot(path)

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark", json=_body(2**63), headers=headers
    )

    assert resp.status_code == 422, resp.text
    assert _snapshot(path) == before, "checkpoint was rewritten before the seed was rejected"
    assert await _record_count(db_session, job_id) == 0


@pytest.mark.asyncio
async def test_negative_seed_is_rejected_without_touching_checkpoint(
    client: AsyncClient, db_session: AsyncSession, wm_job
):
    headers, job_id, path = wm_job
    before = _snapshot(path)

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark", json=_body(-1), headers=headers
    )

    assert resp.status_code == 422, resp.text
    assert _snapshot(path) == before
    assert await _record_count(db_session, job_id) == 0


@pytest.mark.asyncio
async def test_handler_check_rejects_narrowed_column_before_disk_write(
    client: AsyncClient, db_session: AsyncSession, wm_job, monkeypatch
):
    """schema 边界取自 import 时的列宽；列被改窄时 handler 仍必须在动磁盘前拒掉。"""
    headers, job_id, path = wm_job
    monkeypatch.setattr(
        WatermarkRecord.__table__.c.key_seed, "type", SmallInteger(), raising=False
    )
    before = _snapshot(path)
    assert _key_seed_column_max() == 2**15 - 1

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark", json=_body(1_000_000), headers=headers
    )

    assert resp.status_code == 400, resp.text
    assert "key_seed" in resp.json()["detail"]
    assert _snapshot(path) == before, "handler rewrote the checkpoint and only then failed"
    assert await _record_count(db_session, job_id) == 0


@pytest.mark.asyncio
async def test_seed_at_column_limit_is_accepted(client: AsyncClient, wm_job):
    headers, job_id, path = wm_job

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark", json=_body(2**63 - 1), headers=headers
    )

    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_seed_above_the_historical_31_bit_cap_is_accepted_and_roundtrips(
    client: AsyncClient, db_session: AsyncSession, wm_job
):
    """A186b：加宽的是种子熵。>2**31-1 的种子必须受理、落存并原样读回。"""
    headers, job_id, path = wm_job
    seed = 2**62 + 12345  # 在 31 位域下会被 422 拒掉

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark", json=_body(seed), headers=headers
    )

    assert resp.status_code == 201, resp.text
    record = (
        await db_session.execute(select(WatermarkRecord).where(WatermarkRecord.job_id == job_id))
    ).scalar_one()
    assert record.key_seed == seed, "the widened seed was truncated on the way to the DB"


@pytest.mark.asyncio
async def test_seed_bound_is_derived_from_the_column_type(monkeypatch):
    # A186b：密文列的逻辑宽度是 BIGINT → 0…2**63-1；列被改窄时边界必须跟着收窄。
    assert _key_seed_column_max() == 2**63 - 1
    monkeypatch.setattr(
        WatermarkRecord.__table__.c.key_seed, "type", Integer(), raising=False
    )
    assert _key_seed_column_max() == 2**31 - 1
    monkeypatch.setattr(
        WatermarkRecord.__table__.c.key_seed, "type", BigInteger(), raising=False
    )
    assert _key_seed_column_max() == 2**63 - 1


@pytest.mark.asyncio
async def test_embed_without_seed_still_works(client: AsyncClient, wm_job):
    headers, job_id, path = wm_job

    resp = await client.post(
        f"/api/v1/training/jobs/{job_id}/watermark", json=_body(_UNSET), headers=headers
    )

    assert resp.status_code == 201, resp.text
    assert "key_seed" not in WatermarkEmbedResponse.model_fields


def test_sealed_column_with_bounded_storage_is_refused(monkeypatch):
    """A186b fail-closed：封套是变长 base64，落存列带长度上限时宁可不受理种子。"""

    class _BoundedSeal(EncryptedKeySeed):
        impl = String(32)

    monkeypatch.setattr(
        WatermarkRecord.__table__.c.key_seed, "type", _BoundedSeal(), raising=False
    )
    with pytest.raises(RuntimeError, match="bounded"):
        _key_seed_column_max()
