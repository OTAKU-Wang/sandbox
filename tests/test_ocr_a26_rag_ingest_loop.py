"""A26 — RAG ingest blocked the event loop, buffered uploads without a cap, and
let two ingests of the same data product interleave.

``POST /api/v1/rag/corpus`` ran ``build_corpus`` (chunking + embedding, CPU
bound) and two ``storage_service.upload`` calls (KMS envelope + SM4-GCM + SM3 +
object-store HTTP) *directly on the event loop* — one request stalled every
other request in the worker. ``POST /api/v1/rag/generative-model`` did
``await file.read()`` first and only checked the 256 MiB cap afterwards, inside
the service, so the whole artifact was buffered in memory before it was refused
(and ``_validate_bundle`` then decompressed ``model.onnx`` on top of it).
Finally both writes for a data product shared one deterministic object-name
pair, so concurrent ingests could land as "A's index + B's embedding model".

Fixes in the router: offload the blocking calls with ``asyncio.to_thread``,
enforce the bundle cap while streaming the upload, serialize ingestion per data
product, and publish the embedding bundle before the corpus index (the index is
the pointer the in-sandbox runner verifies).
"""
import asyncio
import threading
import uuid

import pytest

from app.api import rag as rag_api
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services import rag_generative_service
from app.services import rag_service
from app.services.storage_service import storage_service

DOCS = [
    {"doc_id": "d1", "text": "三室两厅的户型适合一家四口居住，南北通透采光好。"},
    {"doc_id": "d2", "text": "小户型一居室适合单身青年，交通便利，总价低。"},
]


async def _mk_session(db_session, user_id) -> SandboxSession:
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


class FakeIndex:
    def __init__(self, corpus_id="corpus-a26"):
        self.corpus_id = corpus_id

    def to_json(self):
        return b'{"corpus_id": "' + self.corpus_id.encode() + b'"}'


def _fake_stats(bundle: bytes | None):
    return {
        "doc_count": 2,
        "chunk_count": 3,
        "embedding_engine": "tf",
        "dim": 384,
        "embedding_model_hash": "a" * 64 if bundle else None,
        "embedding_model_bundle": bundle,
    }


@pytest.mark.asyncio
async def test_build_corpus_runs_off_the_event_loop(client, db_session, make_user, monkeypatch):
    """A CPU-bound corpus build must not occupy the event loop (A26)."""
    headers, user_id = await make_user("data_provider", "a26_prov")
    session = await _mk_session(db_session, user_id)

    real_build = rag_service.build_corpus
    build_threads = []

    def spy_build(*args, **kwargs):
        build_threads.append(threading.get_ident())
        return real_build(*args, **kwargs)

    monkeypatch.setattr(rag_service, "build_corpus", spy_build)

    resp = await client.post("/api/v1/rag/corpus",
                             json={"session_id": str(session.id), "docs": DOCS},
                             headers=headers)

    assert resp.status_code == 200, resp.text
    assert build_threads, "build_corpus was never called"
    assert build_threads[0] != threading.get_ident(), (
        "build_corpus ran on the event loop thread — it is CPU-bound and "
        "stalls every other request in the worker (A26)"
    )


@pytest.mark.asyncio
async def test_storage_uploads_run_off_the_event_loop(client, db_session, make_user, monkeypatch):
    """Envelope crypto + object-store HTTP are blocking too."""
    headers, user_id = await make_user("data_provider", "a26_prov2")
    session = await _mk_session(db_session, user_id)

    upload_threads = []
    real_upload = storage_service.upload

    def spy_upload(*args, **kwargs):
        upload_threads.append(threading.get_ident())
        return real_upload(*args, **kwargs)

    monkeypatch.setattr(storage_service, "upload", spy_upload)

    resp = await client.post("/api/v1/rag/corpus",
                             json={"session_id": str(session.id), "docs": DOCS},
                             headers=headers)

    assert resp.status_code == 200, resp.text
    assert upload_threads, "storage_service.upload was never called"
    loop_thread = threading.get_ident()
    assert all(tid != loop_thread for tid in upload_threads), (
        f"uploads ran on the event loop: {len(upload_threads)} call(s) (A26)"
    )


@pytest.mark.asyncio
async def test_embedding_bundle_is_published_before_the_corpus_index(
    client, db_session, make_user, monkeypatch
):
    """The index is the pointer the runner verifies — it must land last."""
    headers, user_id = await make_user("data_provider", "a26_prov3")
    session = await _mk_session(db_session, user_id)

    def fake_build(docs, **kwargs):
        return FakeIndex(), _fake_stats(b"FAKE-BUNDLE-BYTES")

    uploaded: list[str] = []

    def spy_upload(data, object_name, content_type=None):
        uploaded.append(object_name)
        return {"checksum": "x" * 64, "encrypted": True, "size": len(data)}

    monkeypatch.setattr(rag_service, "build_corpus", fake_build)
    monkeypatch.setattr(storage_service, "upload", spy_upload)

    resp = await client.post("/api/v1/rag/corpus",
                             json={"session_id": str(session.id), "docs": DOCS},
                             headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["embedding_model_ref"] and uploaded == [
        body["embedding_model_ref"], body["storage_object_ref"],
    ], f"corpus index must be the last write, got {uploaded}"


@pytest.mark.asyncio
async def test_ingest_of_one_data_product_is_serialised(client, db_session, make_user, monkeypatch):
    """Two ingests of the same product must not interleave into A's index + B's model."""
    headers, user_id = await make_user("data_provider", "a26_prov4")
    session = await _mk_session(db_session, user_id)
    product_key = str(session.data_product_id)

    lock_state: list[bool] = []

    def spy_upload(data, object_name, content_type=None):
        lock = getattr(rag_api, "_INGEST_LOCKS", {}).get(product_key)
        lock_state.append(bool(lock is not None and lock.locked()))
        return {"checksum": "x" * 64, "encrypted": True, "size": len(data)}

    monkeypatch.setattr(rag_service, "build_corpus",
                        lambda docs, **kw: (FakeIndex(), _fake_stats(b"B")))
    monkeypatch.setattr(storage_service, "upload", spy_upload)

    resp = await client.post("/api/v1/rag/corpus",
                             json={"session_id": str(session.id), "docs": DOCS},
                             headers=headers)

    assert resp.status_code == 200, resp.text
    assert lock_state, "no upload was recorded"
    assert all(lock_state), (
        "the storage writes ran outside the per-data-product ingest lock — "
        "concurrent ingests of one product overwrite each other's index and "
        "embedding bundle (A26)"
    )


@pytest.mark.asyncio
async def test_ingest_lock_is_not_shared_across_products(client, db_session, make_user, monkeypatch):
    """The serialisation key is the data product, not a global mutex."""
    headers, user_id = await make_user("data_provider", "a26_prov5")
    s1 = await _mk_session(db_session, user_id)
    s2 = await _mk_session(db_session, user_id)

    monkeypatch.setattr(rag_service, "build_corpus",
                        lambda docs, **kw: (FakeIndex(), _fake_stats(None)))
    monkeypatch.setattr(storage_service, "upload",
                        lambda data, name, content_type=None: {"checksum": "c", "encrypted": True})

    r1 = await client.post("/api/v1/rag/corpus",
                           json={"session_id": str(s1.id), "docs": DOCS}, headers=headers)
    r2 = await client.post("/api/v1/rag/corpus",
                           json={"session_id": str(s2.id), "docs": DOCS}, headers=headers)

    assert r1.status_code == 200 and r2.status_code == 200
    keys = {str(s1.data_product_id), str(s2.data_product_id)}
    held = {k for k in keys if k in rag_api._INGEST_LOCKS}
    assert held == keys, "each data product must get its own ingest lock"


@pytest.mark.asyncio
async def test_oversize_bundle_is_refused_before_being_buffered(
    client, make_user, monkeypatch
):
    """The cap has to be enforced while streaming, not after the whole upload
    has been read into memory."""
    headers, _ = await make_user("data_provider", "a26_prov6")
    monkeypatch.setattr(rag_generative_service, "_MAX_BUNDLE_BYTES", 4096)

    buffered: list[int] = []

    async def must_not_be_called(db, *, owner_id, name, artifact_bytes):
        buffered.append(len(artifact_bytes))
        raise AssertionError(
            f"register() received {len(artifact_bytes)} bytes — the whole "
            "oversize bundle was buffered before the cap was checked (A26)"
        )

    monkeypatch.setattr(rag_generative_service, "register_generative_model", must_not_be_called)

    resp = await client.post(
        "/api/v1/rag/generative-model",
        files={"file": ("bundle.zip", b"z" * 20_000, "application/zip")},
        data={"name": "a26-bundle"},
        headers=headers,
    )

    assert resp.status_code == 400, resp.text
    assert not buffered, "the service must never see an already-buffered oversize bundle"
    assert "4096" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bundle_within_the_cap_still_reaches_the_service(client, make_user, monkeypatch):
    """Reverse case: a small upload is streamed and handed over unchanged."""
    headers, _ = await make_user("data_provider", "a26_prov7")
    monkeypatch.setattr(rag_generative_service, "_MAX_BUNDLE_BYTES", 4096)
    seen: list[int] = []

    class FakeModel:
        model_id = uuid.uuid4()
        name = "a26"
        vocab_size = 3
        size_bytes = 100

    async def fake_register(db, *, owner_id, name, artifact_bytes):
        seen.append(len(artifact_bytes))
        return FakeModel()

    monkeypatch.setattr(rag_generative_service, "register_generative_model", fake_register)

    resp = await client.post(
        "/api/v1/rag/generative-model",
        files={"file": ("bundle.zip", b"z" * 200, "application/zip")},
        data={"name": "a26-bundle"},
        headers=headers,
    )

    assert resp.status_code == 201, resp.text
    assert seen == [200]


@pytest.mark.asyncio
async def test_ingest_locks_do_not_leak_across_products(client, db_session, make_user, monkeypatch):
    """The lock registry is keyed by data product, so its growth is bounded by
    the product table — assert no per-request accumulation."""
    headers, user_id = await make_user("data_provider", "a26_prov8")
    session = await _mk_session(db_session, user_id)
    monkeypatch.setattr(rag_service, "build_corpus",
                        lambda docs, **kw: (FakeIndex(), _fake_stats(None)))
    monkeypatch.setattr(storage_service, "upload",
                        lambda data, name, content_type=None: {"checksum": "c", "encrypted": True})

    before = len(rag_api._INGEST_LOCKS)
    for _ in range(5):
        resp = await client.post("/api/v1/rag/corpus",
                                 json={"session_id": str(session.id), "docs": DOCS},
                                 headers=headers)
        assert resp.status_code == 200, resp.text
    assert len(rag_api._INGEST_LOCKS) == before + 0 or len(rag_api._INGEST_LOCKS) == before + 1
    await asyncio.sleep(0)
