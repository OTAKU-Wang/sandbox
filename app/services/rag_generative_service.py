"""N6: RAG generative answer models — ONNX bundle registry + materialization.

A generative bundle is a zip of ``model.onnx`` (outputs ``answer_logits``
float32 [1, V] given input ``context`` float32 [1, 512]) plus ``vocab.txt``
(one token per line, V lines). The runner loads it with onnxruntime (same
loader as N5) and decodes the argmax token — honest conditional answer
synthesis over a provider-registered model, fail-closed when the bundle or
onnxruntime is absent.
"""
import io
import logging
import uuid
import zipfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_generative_model import RAGGenerativeModel

logger = logging.getLogger(__name__)

_MAX_BUNDLE_BYTES = 256 * 1024 * 1024
_BUNDLE_FILENAME = "generative_model.bundle"


class RAGGenerativeService:
    async def register(
        self,
        db: AsyncSession,
        *,
        owner_id: uuid.UUID,
        name: str,
        artifact_bytes: bytes,
    ) -> RAGGenerativeModel:
        if not artifact_bytes:
            raise ValueError("Generative bundle cannot be empty")
        if len(artifact_bytes) > _MAX_BUNDLE_BYTES:
            raise ValueError(f"Bundle exceeds size limit ({_MAX_BUNDLE_BYTES} bytes)")

        vocab_size, model_bytes = self._validate_bundle(artifact_bytes)

        from app.services.storage_service import storage_service
        object_name = f"rag/generative/{uuid.uuid4().hex}.bundle"
        storage_service.upload(artifact_bytes, object_name, content_type="application/octet-stream")

        model = RAGGenerativeModel(
            owner_id=owner_id,
            name=name,
            artifact_ref=object_name,
            vocab_size=vocab_size,
            size_bytes=len(artifact_bytes),
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return model

    @staticmethod
    def _validate_bundle(bundle: bytes) -> tuple[int, bytes]:
        """Validate a zip bundle: must contain model.onnx (valid ONNX) and
        vocab.txt (non-empty, one token per line). Returns (vocab_size, model_bytes)."""
        try:
            zf = zipfile.ZipFile(io.BytesIO(bundle))
        except zipfile.BadZipFile as e:
            raise ValueError(f"Generative bundle is not a valid zip: {e}")
        names = set(zf.namelist())
        if "model.onnx" not in names or "vocab.txt" not in names:
            raise ValueError("Bundle must contain model.onnx and vocab.txt")
        model_bytes = zf.read("model.onnx")
        vocab_raw = zf.read("vocab.txt").decode("utf-8")
        vocab = [ln for ln in vocab_raw.splitlines() if ln.strip()]
        if not vocab:
            raise ValueError("vocab.txt must contain at least one token")
        try:
            import onnx
            onnx.checker.check_model(onnx.load_model_from_string(model_bytes))
        except Exception as e:
            raise ValueError(f"ONNX model validation failed: {e}")
        return len(vocab), model_bytes

    async def get(self, db: AsyncSession, model_id: uuid.UUID) -> RAGGenerativeModel | None:
        result = await db.execute(select(RAGGenerativeModel).where(RAGGenerativeModel.model_id == model_id))
        return result.scalar_one_or_none()


async def prepare_rag_generative_model(db: AsyncSession, session, workspace, model_id: uuid.UUID) -> dict | None:
    """Materialize a generative bundle into workspace/input (host side).

    Downloads the encrypted bundle, writes it as ``input/generative_model.bundle``
    and re-encrypts with the session DEK so the runner decrypts it with
    ``CDS_DEK_HEX``. Returns ``{model_id, vocab_size}`` or None when missing.
    """
    from app.services.storage_service import storage_service
    from app.services.sandbox_security import encrypt_workspace_file
    from app.services.rag_service import _session_workspace_dek

    model = await rag_generative_service.get(db, model_id)
    if not model:
        logger.warning("[RAG] generative model %s not found", model_id)
        return None
    try:
        bundle = storage_service.download(model.artifact_ref)
    except Exception as e:
        logger.warning("[RAG] generative bundle download failed (%s): %s", model.artifact_ref, e)
        return None
    if not bundle:
        logger.warning("[RAG] generative bundle %s empty", model.artifact_ref)
        return None

    input_dir = workspace / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    target = input_dir / _BUNDLE_FILENAME
    target.write_bytes(bundle)

    dek = await _session_workspace_dek(workspace)
    if dek:
        try:
            encrypt_workspace_file(target, dek)
        except Exception as e:
            logger.warning("[RAG] generative bundle re-encryption failed: %s", e)
            return None

    logger.info("[RAG] generative bundle ready session=%s model=%s", getattr(session, "id", "?"), model_id)
    return {"model_id": str(model.model_id), "vocab_size": model.vocab_size}


rag_generative_service = RAGGenerativeService()
