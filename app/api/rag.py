"""RAG API (gap T11, phase 1) — in-domain retrieval-QA corpus ingestion.

The query itself reuses the existing task lifecycle (``POST /sandbox-tasks``
with ``task_type="rag_query"``); this router only provides corpus ingestion.
Ingested documents are chunked + embedded, stored encrypted at rest via
``storage_service`` (SM4-GCM envelope), and materialized into the sandbox
workspace at query time — the corpus never leaves the domain.
"""
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

    # Build the corpus (runner-replicable embedding engine enforced inside).
    from app.services.rag_service import (
        build_corpus,
        corpus_object_name,
        embedding_model_object_name,
    )
    from app.services.storage_service import storage_service

    try:
        index, stats = build_corpus(
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

    # Durable encrypted copy at the deterministic object name (keyed by the
    # session's data product so the runner's materialization can find it).
    object_name = corpus_object_name(str(session.data_product_id))
    stored = storage_service.upload(index.to_json(), object_name, content_type="application/json")

    # Phase 2: when a transformers corpus is built, ship the embedding model
    # bundle alongside so the in-sandbox runner can reproduce query vectors.
    embedding_model_ref = None
    embedding_model_hash = stats.get("embedding_model_hash")
    if stats.get("embedding_model_bundle"):
        emb_name = embedding_model_object_name(str(session.data_product_id))
        emb_stored = storage_service.upload(
            stats["embedding_model_bundle"], emb_name, content_type="application/octet-stream"
        )
        embedding_model_ref = emb_name

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
    from app.services.rag_generative_service import register_generative_model as _register

    artifact = await file.read()
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
