"""RAG API (gap T11, phase 1) — in-domain retrieval-QA corpus ingestion.

The query itself reuses the existing task lifecycle (``POST /sandbox-tasks``
with ``task_type="rag_query"``); this router only provides corpus ingestion.
Ingested documents are chunked + embedded, stored encrypted at rest via
``storage_service`` (SM4-GCM envelope), and materialized into the sandbox
workspace at query time — the corpus never leaves the domain.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
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
    from app.services.rag_service import build_corpus, corpus_object_name
    from app.services.storage_service import storage_service

    try:
        index, stats = build_corpus(
            [{"doc_id": d.doc_id, "text": d.text} for d in body.docs],
            chunk_size=body.chunk_size,
            overlap=body.overlap,
            embedding_backend=body.embedding_backend,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Durable encrypted copy at the deterministic object name (keyed by the
    # session's data product so the runner's materialization can find it).
    object_name = corpus_object_name(str(session.data_product_id))
    stored = storage_service.upload(index.to_json(), object_name, content_type="application/json")

    await audit_service.log(
        db, action="rag.corpus_ingest", resource_type="rag_corpus",
        user_id=current_user.id, resource_id=index.corpus_id,
        detail={
            "session_id": str(body.session_id),
            "data_product_id": str(session.data_product_id),
            "doc_count": stats["doc_count"],
            "chunk_count": stats["chunk_count"],
            "embedding_engine": stats["embedding_engine"],
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
        "storage_object_ref": object_name,
        "checksum": stored.get("checksum"),
        "encrypted": stored.get("encrypted", True),
    }
