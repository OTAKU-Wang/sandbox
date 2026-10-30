"""RAG API (gap T11, phase 1) — in-domain retrieval-QA corpus ingestion.

The query itself reuses the existing task lifecycle (``POST /sandbox-tasks``
with ``task_type="rag_query"``); this router only provides corpus ingestion.
Ingested documents are chunked + embedded, stored encrypted at rest via
``storage_service`` (SM4-GCM envelope), and materialized into the sandbox
workspace at query time — the corpus never leaves the domain.

A26 (event-loop / serialisation / publish-order / bundle-cap):
* ``build_corpus`` (chunking + embedding) and the two ``storage_service.upload``
  calls (KMS envelope + SM4-GCM + SM3 + object-store HTTP) are blocking, so they
  run in a worker thread via ``asyncio.to_thread`` — never on the event loop,
  where one ingest would stall every other request in the worker.
* both writes of one data product share one deterministic object-name pair, so
  ingestion is serialized behind one lock per data product (``_INGEST_LOCKS``).
* the embedding bundle is published BEFORE the corpus index: the index is the
  pointer the in-sandbox runner verifies, so a visible index must never point at
  a bundle that has not landed yet.
* the generative bundle cap is enforced while streaming the upload (announced
  size first, then a bounded read) instead of after ``file.read()`` buffered —
  and then decompressed — a 256 MiB artifact in memory.
"""
import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.user import User, UserRole
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services.audit_service import audit_service

router = APIRouter()

# --- A26: per-data-product ingest serialisation --------------------------------
# The corpus index and the embedding bundle of one data product are written to
# one deterministic object-name pair, so two concurrent ingests of the SAME
# product could land as "A's index + B's embedding model". Writes therefore run
# under one asyncio.Lock per data-product id.
#
# Key: ``str(data_product_id)``. Retention is bounded by the data-product table
# — one entry per distinct product, never one per request. Once the registry
# reaches ``_MAX_INGEST_LOCKS`` the oldest entries nobody currently holds are
# dropped (insertion order ≈ least-recently-used); a lock held by an in-flight
# ingest is never evicted underneath its owner.
#
# Scope is honest: an asyncio.Lock guards one event loop / one worker process.
# Multi-worker deployments still need object-store-level compare-and-swap for
# cross-worker serialisation of the same product's object-name pair.
_INGEST_LOCKS: dict[str, asyncio.Lock] = {}
_MAX_INGEST_LOCKS = 1024

# Streaming read size for the generative bundle upload.
_BUNDLE_READ_CHUNK = 64 * 1024


def _ingest_lock(data_product_id: str) -> asyncio.Lock:
    lock = _INGEST_LOCKS.get(data_product_id)
    if lock is not None:
        return lock
    if len(_INGEST_LOCKS) >= _MAX_INGEST_LOCKS:
        stale = [key for key, held in _INGEST_LOCKS.items() if not held.locked()]
        for key in stale[: max(1, len(_INGEST_LOCKS) // 2)]:
            del _INGEST_LOCKS[key]
    # No await between the lookup above and this insert: on a single event loop
    # two concurrent requests of one product can never end up with two locks.
    lock = asyncio.Lock()
    _INGEST_LOCKS[data_product_id] = lock
    return lock


def _upload_off_loop(data, object_name: str, content_type: str) -> dict:
    """Serialize (if needed) + encrypt + upload — blocking, so the caller wraps
    this in ``asyncio.to_thread`` (A26)."""
    from app.services.storage_service import storage_service

    payload = data() if callable(data) else data
    return storage_service.upload(payload, object_name, content_type=content_type)


def _declared_upload_size(file: UploadFile) -> int | None:
    """Size of the uploaded part from a value we already hold: the multipart
    parser's running byte count (``UploadFile.size``) or, failing that, the
    part's own ``Content-Length``. None when the client announced nothing."""
    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > 0:
        return declared
    header = file.headers.get("content-length") if file.headers else None
    if header:
        try:
            return int(header)
        except ValueError:
            return None
    return None


async def _read_upload_capped(file: UploadFile, cap_bytes: int) -> bytes:
    """Read an upload that must stay under ``cap_bytes``, refusing it BEFORE it is
    buffered (A26).

    The announced size is checked first, so an oversize upload is refused without
    reading a single byte. Without an announced size the read is bounded anyway:
    at most ``cap_bytes + 1`` bytes are ever taken out of the stream, so an
    attacker cannot make this endpoint buffer an unbounded artifact.
    """
    declared = _declared_upload_size(file)
    if declared is not None and declared > cap_bytes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Bundle exceeds size limit ({cap_bytes} bytes): "
                f"upload announced {declared} bytes — refused before buffering"
            ),
        )

    chunks: list[bytes] = []
    total = 0
    while total < cap_bytes + 1:
        chunk = await file.read(min(_BUNDLE_READ_CHUNK, cap_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > cap_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"Bundle exceeds size limit ({cap_bytes} bytes)",
            )
        chunks.append(chunk)
    return b"".join(chunks)


class RagDoc(BaseModel):
    doc_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1)


class CorpusIngestRequest(BaseModel):
    session_id: uuid.UUID
    docs: list[RagDoc]
    chunk_size: int | None = Field(None, ge=8, le=2048)
    overlap: int | None = Field(None, ge=0, le=512)
    embedding_backend: str | None = None


@router.post("/corpus", status_code=status.HTTP_200_OK)
async def ingest_corpus(
    body: CorpusIngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Chunk + embed + encrypt a session corpus for in-sandbox retrieval."""
    from app.core.config import get_settings
    settings = get_settings()

    # Authorize the session (same ownership/status rules as task creation).
    result = await db.execute(select(SandboxSession).where(SandboxSession.id == body.session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Sandbox session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not the owner of this session")
    if session.status not in (SessionStatus.READY.value, SessionStatus.RUNNING.value):
        raise HTTPException(status_code=400, detail=f"Session not ready (status: {session.status})")
    if not session.container_id:
        raise HTTPException(status_code=400, detail="Session has no container")
    if not session.data_product_id:
        raise HTTPException(status_code=400, detail="Session has no data product to attach a corpus to")

    # Size / count caps.
    if not body.docs:
        raise HTTPException(status_code=422, detail="docs cannot be empty")
    if len(body.docs) > settings.RAG_MAX_DOCS:
        raise HTTPException(status_code=422, detail=f"docs exceeds max ({settings.RAG_MAX_DOCS})")
    total_bytes = sum(len(d.text.encode("utf-8")) for d in body.docs)
    if total_bytes > settings.RAG_MAX_CORPUS_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"corpus exceeds max size ({settings.RAG_MAX_CORPUS_BYTES} bytes)",
        )

    # A26: the build is CPU bound and the uploads are blocking crypto + object
    # store HTTP, so every one of them runs in a worker thread; and because both
    # objects of one data product have a deterministic name, the writes of one
    # product are serialized. The lock is released before the audit write below.
    from app.services import rag_service

    product_key = str(session.data_product_id)
    async with _ingest_lock(product_key):
        try:
            index, stats = await asyncio.to_thread(
                rag_service.build_corpus,
                [{"doc_id": d.doc_id, "text": d.text} for d in body.docs],
                chunk_size=body.chunk_size,
                overlap=body.overlap,
                embedding_backend=body.embedding_backend,
                ship_transformers=(body.embedding_backend == "transformers"),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            # EngineUnavailable and model-load failures are honest 422s — the
            # caller asked for an engine the host cannot run (never silent fallback).
            from app.services.rag_embedding import EngineUnavailable
            if isinstance(e, EngineUnavailable):
                raise HTTPException(status_code=422, detail=str(e))
            raise

        embedding_model_hash = stats.get("embedding_model_hash")
        bundle = stats.get("embedding_model_bundle")

        # Phase 2: when a transformers corpus is built, ship the embedding model
        # bundle alongside so the in-sandbox runner can reproduce query vectors.
        #
        # A26 publish order — bundle FIRST, index LAST. The corpus index is the
        # commit point: it carries the embedding_model_hash the runner verifies,
        # so it must be the last write. Publishing it first would let a reader (or
        # the runner) see an index whose bundle is still absent.
        embedding_model_ref = None
        if bundle:
            embedding_model_ref = rag_service.embedding_model_object_name(product_key)
            await asyncio.to_thread(
                _upload_off_loop, bundle, embedding_model_ref, "application/octet-stream",
            )

        # Durable encrypted copy at the deterministic object name (keyed by the
        # session's data product so the runner's materialization can find it).
        object_name = rag_service.corpus_object_name(product_key)
        stored = await asyncio.to_thread(
            _upload_off_loop,
            index.to_json,
            object_name,
            "application/json",
        )

    await audit_service.log(
        db, action="rag.corpus_ingest", resource_type="rag_corpus",
        user_id=current_user.id, resource_id=index.corpus_id,
        detail={
            "session_id": str(body.session_id),
            "data_product_id": str(session.data_product_id),
            "doc_count": stats["doc_count"],
            "chunk_count": stats["chunk_count"],
            "embedding_engine": stats["embedding_engine"],
            "embedding_model_hash": embedding_model_hash,
            "storage_object_ref": object_name,
            "checksum": stored.get("checksum"),
        },
    )

    return {
        "corpus_id": index.corpus_id,
        "doc_count": stats["doc_count"],
        "chunk_count": stats["chunk_count"],
        "embedding_engine": stats["embedding_engine"],
        "dim": stats["dim"],
        "embedding_model_hash": embedding_model_hash,
        "embedding_model_ref": embedding_model_ref,
        "storage_object_ref": object_name,
        "checksum": stored.get("checksum"),
        "encrypted": stored.get("encrypted", True),
    }


@router.post("/generative-model", status_code=status.HTTP_201_CREATED)
async def register_generative_model(
    file: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OPERATOR, UserRole.DATA_PROVIDER)),
):
    """Register a RAG generative answer bundle (zip: model.onnx + vocab.txt).

    The inner ONNX model is validated; the bundle is stored encrypted and
    materialized into the buyer's workspace when a rag_query uses
    ``answer_mode="generative"`` with this model_id. Fail-closed without it.
    """
    from app.services import rag_generative_service
    from app.services.rag_generative_service import register_generative_model as _register

    # A26: the cap is enforced while streaming the upload, not afterwards. The
    # single source of truth is the service's own ``_MAX_BUNDLE_BYTES`` (read
    # here at request time), so the route can never accept what the service
    # would later reject — and the artifact never has to be buffered whole (the
    # service then decompresses ``model.onnx`` on top of that buffer).
    cap_bytes = rag_generative_service._MAX_BUNDLE_BYTES
    try:
        artifact = await _read_upload_capped(file, cap_bytes)
    except HTTPException:
        # An oversize bundle is a refusal with a trail, not a silently dropped
        # upload (the request session is rolled back by get_db on the raise).
        await audit_service.log_standalone(
            action="rag.generative_model.register", resource_type="rag_generative_model",
            user_id=current_user.id,
            detail={
                "name": name, "filename": file.filename,
                "refused": "bundle exceeds size limit", "limit_bytes": cap_bytes,
            },
        )
        raise

    try:
        model = await _register(db, owner_id=current_user.id, name=name, artifact_bytes=artifact)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await audit_service.log(
        db, action="rag.generative_model.register", resource_type="rag_generative_model",
        user_id=current_user.id, resource_id=str(model.model_id),
        detail={"name": model.name, "vocab_size": model.vocab_size, "size_bytes": model.size_bytes},
    )
    return {
        "model_id": str(model.model_id),
        "name": model.name,
        "vocab_size": model.vocab_size,
        "size_bytes": model.size_bytes,
    }
