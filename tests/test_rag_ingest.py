"""RAG corpus ingestion API tests (gap T11).

POST /rag/corpus: authorization, size/count caps, runner-replicable engine
enforcement, and encrypted at-rest storage via storage_service.
"""
import uuid

import pytest

from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus

DOCS = [
    {"doc_id": "d1", "text": "三室两厅的户型适合一家四口居住，南北通透采光好。"},
    {"doc_id": "d2", "text": "小户型一居室适合单身青年，交通便利，总价低。"},
]


async def _mk_session(db_session, user_id):
    product = DataProduct(provider_id=uuid.UUID(user_id), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    session = SandboxSession(
        user_id=uuid.UUID(user_id),
        data_product_id=product.id,
        status=SessionStatus.RUNNING.value,
        container_id="test-container",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


@pytest.mark.asyncio
async def test_ingest_corpus_ok(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/rag/corpus",
        json={"session_id": str(session.id), "docs": DOCS},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_count"] == 2
    assert body["chunk_count"] >= 2
    assert body["embedding_engine"] == "tf"
    assert body["encrypted"] is True
    assert body["storage_object_ref"].startswith("rag/corpus/")

    # durable encrypted copy exists and decrypts back
    from app.services.storage_service import storage_service
    from app.services.rag_service import RagIndex

    blob = storage_service.download(body["storage_object_ref"])
    index = RagIndex.from_json(blob)
    assert index.corpus_id == body["corpus_id"]
    assert len(index.chunks) == body["chunk_count"]


@pytest.mark.asyncio
async def test_ingest_corpus_requires_owner(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    other_headers, _other = await make_user("data_provider", "other")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/rag/corpus",
        json={"session_id": str(session.id), "docs": DOCS},
        headers=other_headers,
    )
    assert resp.status_code == 403

    resp2 = await client.post(
        "/api/v1/rag/corpus",
        json={"session_id": str(session.id), "docs": DOCS},
    )
    assert resp2.status_code in (401, 403)


@pytest.mark.asyncio
async def test_ingest_corpus_doc_count_cap(client, db_session, make_user):
    from app.core.config import get_settings

    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)
    many = [{"doc_id": f"d{i}", "text": "内容"} for i in range(get_settings().RAG_MAX_DOCS + 5)]

    resp = await client.post(
        "/api/v1/rag/corpus",
        json={"session_id": str(session.id), "docs": many},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_corpus_empty_docs(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/rag/corpus",
        json={"session_id": str(session.id), "docs": []},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_corpus_rejects_transformers_without_model(client, db_session, make_user, monkeypatch):
    # N6: transformers corpus build is allowed (model shipped alongside), but
    # still fails closed when the host-side model cannot be resolved — never a
    # silent tf fallback, never a network hang in tests.
    from app.services.rag_embedding import EngineUnavailable, RAGEmbedder

    def _no_model(self):
        raise EngineUnavailable("embedding model unavailable (test)")

    monkeypatch.setattr(RAGEmbedder, "_load_transformers", _no_model)

    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/rag/corpus",
        json={
            "session_id": str(session.id),
            "docs": DOCS,
            "embedding_backend": "transformers",
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert "embedding model unavailable" in resp.json()["detail"]
