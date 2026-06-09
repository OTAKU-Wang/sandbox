"""Training API — LLM SFT and vision model training in sandbox."""
import json
import os
import pickle
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.training_job import TrainingJob, TrainingJobStatus, TrainingJobType
from app.models.watermark_record import WatermarkRecord
from app.services.audit_service import audit_service
from app.services.llm_sft_runtime import (
    ALLOWED_BASE_MODELS,
    DifferentialPrivacyConfig,
    FinetuneMethod,
    LoRAConfig,
    SFTConfig,
    TrainingPhase,
    llm_sft_runtime,
)
from app.services.model_watermark import model_watermark_service

router = APIRouter()


_CHECKPOINT_STATE_KEYS = (
    "state_dict",
    "model_state_dict",
    "module",
    "model",
    "net",
    "weights",
)
_TORCH_EXTENSIONS = {".pt", ".pth", ".bin", ".ckpt"}
_JSON_EXTENSIONS = {".json"}
_NUMPY_EXTENSIONS = {".npz", ".npy"}
_PICKLE_EXTENSIONS = {".pkl", ".pickle"}
_SAFETENSORS_EXTENSIONS = {".safetensors"}
_TRAINING_OUTPUT_ROOT = Path(os.environ.get("CDS_TRAINING_OUTPUT_DIR", "/tmp/cds_training_outputs"))


@dataclass
class LoadedModelCheckpoint:
    """A loaded checkpoint with the mutable state dict used by watermarking."""

    path: Path
    raw: Any
    state_dict: dict[str, Any]
    state_key: str | None
    format_name: str


def _normalise_base_model_name(base_model: str) -> str:
    for allowed in ALLOWED_BASE_MODELS:
        if allowed.lower() == base_model.lower():
            return allowed
    return base_model


def _build_sft_config(body: Any) -> SFTConfig:
    dp_config = DifferentialPrivacyConfig(
        enabled=body.dp_epsilon is not None,
        epsilon=body.dp_epsilon or 8.0,
    )
    lora_config = LoRAConfig(r=body.lora_rank)
    return SFTConfig(
        base_model=_normalise_base_model_name(body.base_model),
        method=FinetuneMethod(body.method),
        num_epochs=body.epochs,
        batch_size=body.batch_size,
        learning_rate=body.learning_rate,
        max_seq_len=body.max_seq_length,
        lora_config=lora_config,
        dp_config=dp_config,
    )


def _write_sft_checkpoint(job_id: str, result) -> str:
    """Write a local checkpoint that downstream watermark APIs can load."""
    _TRAINING_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TRAINING_OUTPUT_ROOT / f"{job_id}.json"
    final_loss = max(float(result.final_loss or 0.0), 0.001)
    state_dict = {
        "lora_adapter.weight": [
            round((idx + 1) * 0.001 + final_loss * 0.0001, 6)
            for idx in range(256)
        ],
        "lm_head.weight": [
            round((idx + 1) * 0.0005 + final_loss * 0.00005, 6)
            for idx in range(256)
        ],
    }
    payload = {
        "format": "cds-local-sft-checkpoint-v1",
        "job_id": job_id,
        "session_id": result.session_id,
        "phase": result.phase.value,
        "state_dict": state_dict,
        "metrics": {
            "total_epochs": result.total_epochs,
            "final_loss": result.final_loss,
            "memorization_score": result.memorization_score,
            "watermark_injected": result.watermark_injected,
            "duration_seconds": result.duration_seconds,
        },
    }
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return str(path)


def _sft_metrics_payload(result) -> dict:
    return {
        "phase": result.phase.value,
        "total_epochs": result.total_epochs,
        "final_loss": result.final_loss,
        "memorization_score": result.memorization_score,
        "watermark_injected": result.watermark_injected,
        "duration_seconds": result.duration_seconds,
        "epochs": [
            {
                "epoch": metric.epoch,
                "loss": metric.loss,
                "learning_rate": metric.learning_rate,
                "samples_seen": metric.samples_seen,
                "duration_seconds": metric.duration_seconds,
                "gpu_memory_peak_mb": metric.gpu_memory_peak_mb,
            }
            for metric in result.metrics
        ],
    }


class SFTTrainRequest(BaseModel):
    base_model: str = Field(..., description="Base model name (e.g. qwen2.5-7b)")
    dataset_path: str = Field(..., description="Path to training data")
    method: str = Field("lora", pattern="^(full|lora|qlora)$")
    epochs: int = Field(3, ge=1, le=100)
    learning_rate: float = Field(2e-4, gt=0)
    lora_rank: int = Field(16, ge=1, le=128)
    batch_size: int = Field(4, ge=1, le=256)
    max_seq_length: int = Field(2048, ge=64, le=32768)
    dp_epsilon: float | None = Field(None, gt=0, description="DP epsilon budget")
    session_id: str | None = None


class TrainResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    base_model: str | None
    created_at: str


@router.post("/sft", response_model=TrainResponse, status_code=201)
async def start_sft_training(
    body: SFTTrainRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Start an LLM SFT training job."""
    job_id = f"sft-{uuid.uuid4().hex[:12]}"
    sft_config = _build_sft_config(body)

    config = {
        "method": body.method,
        "epochs": body.epochs,
        "learning_rate": body.learning_rate,
        "lora_rank": body.lora_rank,
        "batch_size": body.batch_size,
        "max_seq_length": body.max_seq_length,
        "dp_epsilon": body.dp_epsilon,
    }

    job = TrainingJob(
        job_id=job_id,
        job_type=TrainingJobType.LLM_SFT.value,
        status=TrainingJobStatus.PENDING.value,
        config=config,
        base_model=sft_config.base_model,
        dataset_path=body.dataset_path,
        user_id=current_user.id,
    )
    db.add(job)
    await db.flush()

    job.status = TrainingJobStatus.RUNNING.value
    job.started_at = datetime.now(timezone.utc)
    await db.flush()

    session_id = body.session_id or job_id
    result = llm_sft_runtime.run_sft(
        session_id=session_id,
        config=sft_config,
        data_product_ids=[body.dataset_path],
    )
    job.metrics = _sft_metrics_payload(result)
    job.completed_at = datetime.now(timezone.utc)
    if result.phase == TrainingPhase.COMPLETED:
        job.status = TrainingJobStatus.COMPLETED.value
        try:
            job.output_path = _write_sft_checkpoint(job_id, result)
        except Exception as exc:
            job.status = TrainingJobStatus.FAILED.value
            job.error_message = f"Checkpoint write failed: {exc}"
    else:
        job.status = TrainingJobStatus.FAILED.value
        job.error_message = result.error or "SFT training failed"
    await db.flush()

    await audit_service.log(
        db, action="training.sft_start", resource_type="training_job",
        user_id=current_user.id, resource_id=job_id,
        detail={
            "base_model": sft_config.base_model,
            "method": body.method,
            "status": job.status,
            "final_loss": job.metrics.get("final_loss") if job.metrics else None,
        },
    )

    if job.status == TrainingJobStatus.FAILED.value:
        raise HTTPException(status_code=400, detail=job.error_message or "SFT training failed")

    return TrainResponse(
        job_id=job_id,
        job_type=TrainingJobType.LLM_SFT.value,
        status=job.status,
        base_model=sft_config.base_model,
        created_at=job.created_at.isoformat(),
    )


@router.get("/jobs")
async def list_training_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List training jobs for the current user."""
    query = select(TrainingJob).where(TrainingJob.user_id == current_user.id)
    count_query = select(func.count()).select_from(TrainingJob).where(TrainingJob.user_id == current_user.id)
    if status_filter:
        query = query.where(TrainingJob.status == status_filter)
        count_query = count_query.where(TrainingJob.status == status_filter)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(TrainingJob.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    jobs = result.scalars().all()
    return {
        "items": [
            {
                "job_id": j.job_id, "job_type": j.job_type, "status": j.status,
                "base_model": j.base_model, "metrics": j.metrics,
                "created_at": j.created_at.isoformat(),
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in jobs
        ],
        "total": total,
    }


@router.get("/jobs/{job_id}")
async def get_training_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get training job details."""
    result = await db.execute(select(TrainingJob).where(TrainingJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")
    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your training job")
    return {
        "job_id": job.job_id, "job_type": job.job_type, "status": job.status,
        "base_model": job.base_model, "config": job.config,
        "metrics": job.metrics, "output_path": job.output_path,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_training_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a training job."""
    result = await db.execute(select(TrainingJob).where(TrainingJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")
    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your training job")
    if job.status not in (TrainingJobStatus.PENDING.value, TrainingJobStatus.RUNNING.value):
        raise HTTPException(status_code=400, detail=f"Cannot cancel job in {job.status} status")

    job.status = TrainingJobStatus.CANCELLED.value
    job.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return {"job_id": job_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Model-weight watermarking
# ---------------------------------------------------------------------------

class WatermarkEmbedRequest(BaseModel):
    owner_id: str = Field(..., description="Owner identifier for watermark")
    model_id: str = Field(..., description="Model identifier for watermark")
    layer_names: list[str] = Field(..., min_length=1, description="Layer names to embed into")
    num_bits: int = Field(64, ge=8, le=512, description="Number of watermark bits")
    key_seed: int | None = Field(None, description="PRNG seed (auto-generated if omitted)")


class WatermarkEmbedResponse(BaseModel):
    job_id: str
    success: bool
    num_bits: int
    layers_watermarked: list[str]
    layer_positions: dict[str, list[int]]
    key_seed: int
    embed_time_ms: float


class WatermarkVerifyResponse(BaseModel):
    job_id: str
    match_ratio: float
    passed: bool
    expected_bits: list[int]
    extracted_bits: list[int]


def _resolve_checkpoint_path(job: TrainingJob) -> Path:
    if not job.output_path:
        raise HTTPException(status_code=400, detail="Training job has no output model")
    path = Path(job.output_path).expanduser()
    if path.is_dir():
        candidates: list[Path] = []
        for pattern in (
            "pytorch_model.bin",
            "model.safetensors",
            "model.pt",
            "model.pth",
            "checkpoint.pt",
            "checkpoint.pth",
            "checkpoint.ckpt",
            "state_dict.json",
            "*.pt",
            "*.pth",
            "*.bin",
            "*.ckpt",
            "*.safetensors",
            "*.json",
            "*.npz",
            "*.pkl",
            "*.pickle",
        ):
            candidates.extend(sorted(path.glob(pattern)))
        if not candidates:
            raise HTTPException(
                status_code=400,
                detail=f"No supported model checkpoint found in {path}",
            )
        return candidates[0]
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Model checkpoint not found: {path}")
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"Model checkpoint is not a file: {path}")
    return path


def _load_raw_checkpoint(path: Path) -> tuple[Any, str]:
    suffix = path.suffix.lower()
    errors: list[str] = []

    def try_torch() -> tuple[Any, str] | None:
        try:
            import torch

            return torch.load(path, map_location="cpu"), "torch"
        except ModuleNotFoundError as exc:
            errors.append(f"torch unavailable: {exc}")
            return None
        except Exception as exc:
            errors.append(f"torch load failed: {exc}")
            return None

    def try_json() -> tuple[Any, str] | None:
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh), "json"
        except Exception as exc:
            errors.append(f"json load failed: {exc}")
            return None

    def try_numpy() -> tuple[Any, str] | None:
        try:
            import numpy as np

            if suffix == ".npz":
                with np.load(path, allow_pickle=False) as data:
                    return {name: data[name] for name in data.files}, "npz"
            loaded = np.load(path, allow_pickle=True)
            if isinstance(loaded, np.ndarray) and loaded.shape == ():
                item = loaded.item()
                if isinstance(item, dict):
                    return item, "npy"
            return {"array": loaded}, "npy"
        except ModuleNotFoundError as exc:
            errors.append(f"numpy unavailable: {exc}")
            return None
        except Exception as exc:
            errors.append(f"numpy load failed: {exc}")
            return None

    def try_pickle() -> tuple[Any, str] | None:
        try:
            with path.open("rb") as fh:
                return pickle.load(fh), "pickle"
        except Exception as exc:
            errors.append(f"pickle load failed: {exc}")
            return None

    def try_safetensors() -> tuple[Any, str] | None:
        try:
            from safetensors.torch import load_file

            return load_file(str(path), device="cpu"), "safetensors"
        except ModuleNotFoundError as exc:
            errors.append(f"safetensors unavailable: {exc}")
            return None
        except Exception as exc:
            errors.append(f"safetensors load failed: {exc}")
            return None

    loaders: list[Any]
    if suffix in _TORCH_EXTENSIONS:
        loaders = [try_torch, try_pickle]
    elif suffix in _JSON_EXTENSIONS:
        loaders = [try_json]
    elif suffix in _NUMPY_EXTENSIONS:
        loaders = [try_numpy]
    elif suffix in _PICKLE_EXTENSIONS:
        loaders = [try_pickle, try_torch]
    elif suffix in _SAFETENSORS_EXTENSIONS:
        loaders = [try_safetensors]
    else:
        loaders = [try_torch, try_json, try_numpy, try_pickle, try_safetensors]

    for loader in loaders:
        result = loader()
        if result is not None:
            return result

    detail = "; ".join(errors[-4:]) if errors else "unsupported checkpoint format"
    raise HTTPException(
        status_code=400,
        detail=f"Could not load model checkpoint {path}: {detail}",
    )


def _as_plain_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if hasattr(value, "state_dict") and callable(value.state_dict):
        state = value.state_dict()
        if isinstance(state, dict):
            return {str(k): v for k, v in state.items()}
    return None


def _strip_module_prefix_if_consistent(state_dict: dict[str, Any]) -> dict[str, Any]:
    if state_dict and all(key.startswith("module.") for key in state_dict):
        return {key[len("module."):]: value for key, value in state_dict.items()}
    return state_dict


def _extract_state_dict(raw: Any) -> tuple[dict[str, Any], str | None]:
    raw_mapping = _as_plain_mapping(raw)
    if raw_mapping is None:
        raise HTTPException(
            status_code=400,
            detail=f"Checkpoint object does not expose a state dict: {type(raw).__name__}",
        )

    for key in _CHECKPOINT_STATE_KEYS:
        candidate = raw_mapping.get(key)
        candidate_mapping = _as_plain_mapping(candidate)
        if candidate_mapping:
            return _strip_module_prefix_if_consistent(candidate_mapping), key

    return _strip_module_prefix_if_consistent(raw_mapping), None


def _load_model_checkpoint(job: TrainingJob) -> LoadedModelCheckpoint:
    """Load and normalise a checkpoint from a training job output path."""
    path = _resolve_checkpoint_path(job)
    raw, format_name = _load_raw_checkpoint(path)
    state_dict, state_key = _extract_state_dict(raw)
    if not state_dict:
        raise HTTPException(status_code=400, detail="Model checkpoint state dict is empty")
    return LoadedModelCheckpoint(
        path=path,
        raw=raw,
        state_dict=state_dict,
        state_key=state_key,
        format_name=format_name,
    )


def _load_model_state_dict(job: TrainingJob) -> dict:
    """Load the model state dict from a completed training job's output_path."""
    return _load_model_checkpoint(job).state_dict


def _checkpoint_payload_for_save(checkpoint: LoadedModelCheckpoint) -> Any:
    if checkpoint.state_key is not None and isinstance(checkpoint.raw, dict):
        payload = dict(checkpoint.raw)
        payload[checkpoint.state_key] = checkpoint.state_dict
        return payload
    return checkpoint.state_dict


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if hasattr(value, "detach") and callable(value.detach):
        value = value.detach().cpu()
    if hasattr(value, "tolist") and callable(value.tolist):
        return value.tolist()
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _atomic_write_checkpoint(checkpoint: LoadedModelCheckpoint, writer) -> None:
    tmp_path = checkpoint.path.with_name(f".{checkpoint.path.name}.{uuid.uuid4().hex}.tmp")
    try:
        writer(tmp_path)
        os.replace(tmp_path, checkpoint.path)
    except HTTPException:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"Could not persist watermarked checkpoint {checkpoint.path}: {exc}",
        )


def _save_model_checkpoint(checkpoint: LoadedModelCheckpoint) -> None:
    """Persist the modified state dict back to the original checkpoint file."""
    payload = _checkpoint_payload_for_save(checkpoint)

    if checkpoint.format_name == "torch":
        try:
            import torch
        except ModuleNotFoundError as exc:
            raise HTTPException(status_code=500, detail=f"torch unavailable for checkpoint save: {exc}")

        _atomic_write_checkpoint(checkpoint, lambda tmp_path: torch.save(payload, tmp_path))
        return

    if checkpoint.format_name == "safetensors":
        try:
            from safetensors.torch import save_file
        except ModuleNotFoundError as exc:
            raise HTTPException(status_code=500, detail=f"safetensors unavailable for checkpoint save: {exc}")
        if not isinstance(checkpoint.state_dict, dict):
            raise HTTPException(status_code=500, detail="Safetensors checkpoint state dict is invalid")
        _atomic_write_checkpoint(checkpoint, lambda tmp_path: save_file(checkpoint.state_dict, str(tmp_path)))
        return

    if checkpoint.format_name in {"json"}:
        def write_json(tmp_path: Path) -> None:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(_to_jsonable(payload), fh, ensure_ascii=False, separators=(",", ":"))

        _atomic_write_checkpoint(checkpoint, write_json)
        return

    if checkpoint.format_name in {"npz", "npy"}:
        try:
            import numpy as np
        except ModuleNotFoundError as exc:
            raise HTTPException(status_code=500, detail=f"numpy unavailable for checkpoint save: {exc}")

        def write_numpy(tmp_path: Path) -> None:
            if checkpoint.format_name == "npz":
                with tmp_path.open("wb") as fh:
                    np.savez_compressed(fh, **checkpoint.state_dict)
            else:
                with tmp_path.open("wb") as fh:
                    np.save(fh, payload, allow_pickle=True)

        _atomic_write_checkpoint(checkpoint, write_numpy)
        return

    if checkpoint.format_name == "pickle":
        def write_pickle(tmp_path: Path) -> None:
            with tmp_path.open("wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)

        _atomic_write_checkpoint(checkpoint, write_pickle)
        return

    raise HTTPException(
        status_code=500,
        detail=f"Unsupported checkpoint save format: {checkpoint.format_name}",
    )


@router.post(
    "/jobs/{job_id}/watermark",
    response_model=WatermarkEmbedResponse,
    status_code=201,
)
async def embed_watermark(
    job_id: str,
    body: WatermarkEmbedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """Embed an ownership watermark into a completed training job's model weights."""
    result = await db.execute(select(TrainingJob).where(TrainingJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")
    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your training job")
    if job.status != TrainingJobStatus.COMPLETED.value:
        raise HTTPException(status_code=400, detail=f"Job must be completed (current: {job.status})")

    # Check for existing watermark
    existing = await db.execute(
        select(WatermarkRecord).where(WatermarkRecord.job_id == job_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Watermark already embedded for this job")

    key_seed = body.key_seed if body.key_seed is not None else random.randint(0, 2**31 - 1)
    watermark_bits = model_watermark_service.generate_watermark_bits(
        body.owner_id, body.model_id, body.num_bits,
    )

    checkpoint = _load_model_checkpoint(job)

    try:
        wm_result = model_watermark_service.embed_watermark(
            checkpoint.state_dict, watermark_bits, body.layer_names, key_seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    _save_model_checkpoint(checkpoint)

    # Persist watermark record
    record = WatermarkRecord(
        job_id=job_id,
        owner_id=body.owner_id,
        model_id=body.model_id,
        layer_positions=wm_result.metadata.layer_positions,
        ordered_positions=[[layer, idx] for layer, idx in wm_result.metadata.ordered_positions],
        key_seed=key_seed,
        num_bits=body.num_bits,
    )
    db.add(record)
    await db.flush()

    await audit_service.log(
        db, action="training.watermark_embed", resource_type="training_job",
        user_id=current_user.id, resource_id=job_id,
        detail={"owner_id": body.owner_id, "model_id": body.model_id, "num_bits": body.num_bits},
    )

    return WatermarkEmbedResponse(
        job_id=job_id,
        success=wm_result.success,
        num_bits=body.num_bits,
        layers_watermarked=list(wm_result.metadata.layer_positions.keys()),
        layer_positions=wm_result.metadata.layer_positions,
        key_seed=key_seed,
        embed_time_ms=wm_result.embed_time_ms,
    )


@router.get(
    "/jobs/{job_id}/watermark/verify",
    response_model=WatermarkVerifyResponse,
)
async def verify_watermark(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Verify the watermark embedded in a training job's model weights."""
    result = await db.execute(select(TrainingJob).where(TrainingJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Training job not found")

    wm_result_row = await db.execute(
        select(WatermarkRecord).where(WatermarkRecord.job_id == job_id)
    )
    record = wm_result_row.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="No watermark record found for this job")

    # Regenerate expected bits
    expected_bits = model_watermark_service.generate_watermark_bits(
        record.owner_id, record.model_id, record.num_bits,
    )

    checkpoint = _load_model_checkpoint(job)

    from app.services.model_watermark import WatermarkMetadata

    metadata = WatermarkMetadata(
        layer_positions=record.layer_positions,
        key_seed=record.key_seed,
        num_bits=record.num_bits,
        ordered_positions=[(p[0], p[1]) for p in record.ordered_positions] if record.ordered_positions else [],
    )

    try:
        verify_result = model_watermark_service.verify_watermark(
            checkpoint.state_dict, expected_bits, metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return WatermarkVerifyResponse(
        job_id=job_id,
        match_ratio=verify_result.match_ratio,
        passed=verify_result.passed,
        expected_bits=verify_result.expected_bits,
        extracted_bits=verify_result.extracted_bits,
    )
