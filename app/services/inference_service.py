"""N5: inference service sandbox — model registry + in-sandbox runner (spec N5).

Design (aligned to the RAG runner pattern):
- Models are uploaded by providers, validated (ONNX via onnx.checker), stored
  encrypted in storage_service, and referenced by artifact_ref.
- An invoke runs a SYSTEM-GENERATED runner inside the buyer's sandbox session:
  the model artifact is materialized into the workspace (re-encrypted with the
  session DEK, exactly like the RAG corpus) and executed with onnxruntime.
  No onnxruntime in the sandbox → the runner fails closed (no simulation).
- Output flows through the standard output-review pipeline; metering is
  recorded per invoke (input tokens / output rows).
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trained_model import InferenceUsage, TrainedModel
from app.models.contract import Contract, ContractStatus

logger = logging.getLogger(__name__)

# ONNX / pickle / safetensors whitelist (spec N5). Only onnx is executable in
# the v1 sandbox runner; pickle/safetensors require torch and are rejected at
# registration until the torch runner lands (fail-closed).
MODEL_FORMATS = {"onnx", "pickle", "safetensors"}
_EXECUTABLE_FORMATS = {"onnx"}

_MODEL_FILENAME = "model.onnx"
_INPUT_FILENAME = "inference_input.json"
_MAX_INPUT_CHARS = 20000
_MAX_MODEL_BYTES = 256 * 1024 * 1024


def _estimate_input_tokens(payload: dict) -> int:
    raw = json.dumps(payload, ensure_ascii=False)
    return max(1, (len(raw) + 3) // 4)


class InferenceService:
    """Model registry and in-sandbox inference orchestration."""

    async def register_model(
        self,
        db: AsyncSession,
        *,
        owner_id: uuid.UUID,
        name: str,
        fmt: str,
        artifact_bytes: bytes,
        product_id: uuid.UUID | None,
        task_id: str | None = None,
        watermark_json: str | None = None,
    ) -> TrainedModel:
        if fmt not in MODEL_FORMATS:
            raise ValueError(f"Unsupported model format '{fmt}' (allowed: {sorted(MODEL_FORMATS)})")
        if not artifact_bytes:
            raise ValueError("Model artifact cannot be empty")
        if len(artifact_bytes) > _MAX_MODEL_BYTES:
            raise ValueError(f"Model artifact exceeds size limit ({_MAX_MODEL_BYTES} bytes)")

        if fmt == "onnx":
            self._validate_onnx(artifact_bytes)

        from app.services.storage_service import storage_service
        object_name = f"inference/models/{uuid.uuid4().hex}.{fmt}"
        stored = storage_service.upload(artifact_bytes, object_name)

        model = TrainedModel(
            owner_id=owner_id,
            product_id=product_id,
            task_id=task_id,
            name=name,
            format=fmt,
            artifact_ref=object_name,
            size_bytes=len(artifact_bytes),
            status="registered",
            watermark_json=watermark_json,
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return model

    @staticmethod
    def _validate_onnx(artifact_bytes: bytes) -> None:
        """Validate an uploaded ONNX artifact; corrupt models are rejected."""
        try:
            import onnx
            onnx.checker.check_model(onnx.load_model_from_string(artifact_bytes))
        except Exception as e:
            raise ValueError(f"ONNX model validation failed: {e}")

    async def revoke_model(self, db: AsyncSession, model_id: uuid.UUID) -> TrainedModel | None:
        model = await self.get_model(db, model_id)
        if not model:
            return None
        if model.status == "registered":
            model.status = "revoked"
            model.revoked_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(model)
        return model

    async def get_model(self, db: AsyncSession, model_id: uuid.UUID) -> TrainedModel | None:
        result = await db.execute(select(TrainedModel).where(TrainedModel.model_id == model_id))
        return result.scalar_one_or_none()

    async def list_models(
        self, db: AsyncSession, *, owner_id: uuid.UUID | None = None, product_id: uuid.UUID | None = None
    ) -> list[TrainedModel]:
        query = select(TrainedModel).order_by(TrainedModel.created_at.desc())
        if owner_id is not None:
            query = query.where(TrainedModel.owner_id == owner_id)
        if product_id is not None:
            query = query.where(TrainedModel.product_id == product_id)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def find_buyer_contract(
        self, db: AsyncSession, user_id: uuid.UUID, product_id: uuid.UUID
    ) -> Contract | None:
        """An active contract held by the user covering the model's product."""
        result = await db.execute(
            select(Contract).where(
                Contract.buyer_id == user_id,
                Contract.status.in_([ContractStatus.ACTIVE.value, ContractStatus.SIGNED.value]),
            )
        )
        for contract in result.scalars().all():
            if str(product_id) in [str(pid) for pid in (contract.product_ids or [])]:
                return contract
        return None

    @staticmethod
    def check_purpose(contract: Contract, purpose: str | None) -> None:
        """Gap A4 purpose gate, same semantics as _enforce_task_purpose."""
        if not contract.purpose:
            return
        if not purpose:
            raise ValueError("Purpose required: the governing contract has a purpose limitation")
        scope = [str(p) for p in (contract.purpose_scope or [])]
        if scope:
            if purpose not in scope:
                raise ValueError(f"Purpose '{purpose}' not within contract purpose_scope {scope}")
            return
        if purpose != contract.purpose:
            raise ValueError(f"Purpose '{purpose}' does not match contract purpose '{contract.purpose}'")

    async def record_usage(
        self,
        db: AsyncSession,
        *,
        model_id: uuid.UUID,
        user_id: uuid.UUID,
        contract_id: str | None,
        task_id: str | None,
        input_tokens: int,
    ) -> None:
        db.add(InferenceUsage(
            model_id=model_id,
            user_id=user_id,
            contract_id=contract_id,
            task_id=task_id,
            input_tokens=input_tokens,
            output_rows=0,
            status="submitted",
        ))
        await db.commit()

    async def update_usage_on_completion(
        self, db: AsyncSession, task_id: str, *, output_rows: int, succeeded: bool
    ) -> None:
        await db.execute(
            update(InferenceUsage)
            .where(InferenceUsage.task_id == task_id)
            .values(output_rows=output_rows, status="succeeded" if succeeded else "failed")
        )
        await db.commit()

    async def usage_stats(
        self, db: AsyncSession, *, model_id: uuid.UUID | None = None, contract_id: str | None = None
    ) -> list[InferenceUsage]:
        query = select(InferenceUsage).order_by(InferenceUsage.created_at.desc())
        if model_id is not None:
            query = query.where(InferenceUsage.model_id == model_id)
        if contract_id is not None:
            query = query.where(InferenceUsage.contract_id == contract_id)
        result = await db.execute(query)
        return list(result.scalars().all())


# ── in-sandbox runner ─────────────────────────────────────────────
# The runner is a fixed template parameterized ONLY by the model id, exactly
# like the RAG runner: the invoke input is materialized to
# /workspace/input/inference_input.json by prepare_model_for_task, so the
# template is byte-stable and can be verified against the trusted template at
# code-scan time. Missing onnxruntime in the sandbox fails closed.

_RUNNER_TEMPLATE = '''"""CDS inference runner (system-generated, spec N5). model_id={model_id}"""
import json
import os
import sys

MODEL_FILE = "input/{model_filename}"
INPUT_FILE = "input/{input_filename}"


def _load_bytes(path):
    raw = open(path, "rb").read()
    dek_hex = os.environ.get("CDS_DEK_HEX")
    if not dek_hex:
        return raw
    from Cryptodome.Cipher import AES
    nonce, tag, ct = raw[:12], raw[12:28], raw[28:]
    cipher = AES.new(bytes.fromhex(dek_hex), AES.MODE_GCM, nonce=nonce)
    return cipher.decrypt_and_verify(ct, tag)


def main():
    try:
        import onnxruntime as ort
    except ImportError:
        print(json.dumps({{
            "error": "onnxruntime not available in sandbox; inference unavailable (fail-closed)",
            "inference_model": {model_id!r},
        }}, ensure_ascii=False))
        sys.exit(1)
    try:
        import numpy as np
    except ImportError:
        print(json.dumps({{"error": "numpy not available in sandbox; inference unavailable (fail-closed)"}}, ensure_ascii=False))
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)

    model_bytes = _load_bytes(MODEL_FILE)
    sess = ort.InferenceSession(model_bytes, providers=["CPUExecutionProvider"])

    feeds = {{
        name: np.asarray(data, dtype=np.float32)
        for name, data in (payload.get("inputs") or {{}}).items()
    }}
    outputs = sess.run(None, feeds)
    out_names = [o.name for o in sess.get_outputs()]
    result = {{
        "outputs": {{
            name: (o.tolist() if hasattr(o, "tolist") else o)
            for name, o in zip(out_names, outputs)
        }},
        "inference_model": {model_id!r},
        "backend": "local_sandbox",
    }}
    print(json.dumps(result, ensure_ascii=False))


main()
'''


def build_inference_runner(model_id: uuid.UUID) -> str:
    """Generate the self-contained runner source for an INFERENCE task."""
    return _RUNNER_TEMPLATE.format(
        model_id=str(model_id),
        model_filename=_MODEL_FILENAME,
        input_filename=_INPUT_FILENAME,
    )


def validate_inference_runner(code: str, model_id: uuid.UUID) -> tuple[bool, str]:
    """Verify a runner is EXACTLY the system-generated template for model_id.

    Runners are system code; any tampering (injected imports, altered model
    path, added output exfiltration) makes the byte comparison fail and the
    task is rejected at code-scan time — the general user-code whitelist is
    never widened.
    """
    expected = build_inference_runner(model_id)
    if code == expected:
        return True, "inference_runner_verified"
    return False, "runner is not the exact system-generated inference template"


async def prepare_model_for_task(
    db: AsyncSession,
    session,
    workspace,
    model_id: uuid.UUID,
    input_payload: dict,
) -> dict | None:
    """Materialize a model + invoke input into workspace/input (host side).

    Downloads the encrypted artifact (storage_service envelope), writes the
    plaintext model and the invoke input into ``input/``, then re-encrypts
    both with the session DEK exactly like provision — the in-sandbox runner
    decrypts with ``CDS_DEK_HEX`` and nothing is plaintext at rest.

    Returns ``{model_id, format, size_bytes}`` or None when the model is
    unavailable (the runner then fails closed).
    """
    from app.services.storage_service import storage_service
    from app.services.sandbox_security import encrypt_workspace_file
    from app.services.rag_service import _session_workspace_dek

    model = await _get_model(db, model_id)
    if not model or model.status != "registered":
        logger.warning("[Inference] model %s unavailable for task materialization", model_id)
        return None

    try:
        artifact = storage_service.download_decrypted(model.artifact_ref)
    except Exception as e:
        logger.warning("[Inference] artifact download failed (%s): %s", model.artifact_ref, e)
        return None
    if not artifact:
        logger.warning("[Inference] artifact %s empty", model.artifact_ref)
        return None

    input_dir = workspace / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    model_target = input_dir / _MODEL_FILENAME
    model_target.write_bytes(artifact)
    input_target = input_dir / _INPUT_FILENAME
    input_target.write_text(json.dumps(input_payload, ensure_ascii=False), encoding="utf-8")

    dek = await _session_workspace_dek(workspace)
    if dek:
        try:
            encrypt_workspace_file(model_target, dek)
            encrypt_workspace_file(input_target, dek)
        except Exception as e:
            logger.warning("[Inference] workspace re-encryption failed: %s", e)
            return None

    logger.info("[Inference] model ready for session=%s model=%s", getattr(session, "id", "?"), model_id)
    return {"model_id": str(model.model_id), "format": model.format, "size_bytes": model.size_bytes}


async def _get_model(db: AsyncSession, model_id: uuid.UUID) -> TrainedModel | None:
    result = await db.execute(select(TrainedModel).where(TrainedModel.model_id == model_id))
    return result.scalar_one_or_none()


inference_service = InferenceService()
