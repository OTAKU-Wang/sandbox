"""RAG service tests (gap T11) — chunking, corpus build, retrieval,
extractive answer and the system-generated runner template validation.
"""
import pytest

from app.services.rag_service import (
    INDEX_VERSION,
    RagIndex,
    build_answer,
    build_corpus,
    build_rag_runner,
    chunk_text,
    retrieve,
    validate_rag_runner,
)

DOCS = [
    {"doc_id": "d1", "text": "三室两厅的户型适合一家四口居住，南北通透采光好。"},
    {"doc_id": "d2", "text": "小户型一居室适合单身青年，交通便利，总价低。"},
    {"doc_id": "d3", "text": "别墅拥有独立花园和车位，适合大家庭与宠物。"},
]


def test_chunk_text_short_text_single_chunk():
    assert chunk_text("短文本", chunk_size=256, overlap=32) == ["短文本"]


def test_chunk_text_sliding_window():
    text = "中" * 100
    chunks = chunk_text(text, chunk_size=30, overlap=5)
    assert len(chunks) >= 4
    assert all(len(c) <= 30 for c in chunks)
    assert chunks[0] == "中" * 30


def test_build_corpus_stats_and_engine():
    index, stats = build_corpus(DOCS, chunk_size=64, overlap=8)
    assert stats["doc_count"] == 3
    assert stats["chunk_count"] >= 3
    assert stats["embedding_engine"] == "tf"
    assert index.engine == "tf"
    assert index.dim == 512
    assert index.chunks


def test_build_corpus_transformers_fail_closed_without_shipping():
    # Phase-1 call sites (no ship_transformers) still reject transformers.
    with pytest.raises(ValueError, match="requires the model bundle"):
        build_corpus(DOCS, embedding_backend="transformers")


def test_build_corpus_transformers_shipping_requires_model(monkeypatch):
    # ship_transformers=True allows the engine but still fails closed when the
    # host-side transformers model cannot be resolved (honest — no model, no
    # corpus; never a silent tf fallback).
    from app.services.rag_embedding import EngineUnavailable, RAGEmbedder

    def _no_model(self):
        raise EngineUnavailable("embedding model unavailable (test)")

    monkeypatch.setattr(RAGEmbedder, "_load_transformers", _no_model)
    with pytest.raises(EngineUnavailable):
        build_corpus(DOCS, embedding_backend="transformers", ship_transformers=True)


def test_rag_index_roundtrip():
    index, _ = build_corpus(DOCS, chunk_size=64, overlap=8)
    restored = RagIndex.from_json(index.to_json())
    assert restored.corpus_id == index.corpus_id
    assert restored.engine == index.engine
    assert restored.dim == index.dim
    assert len(restored.chunks) == len(index.chunks)
    assert restored.chunks[0].vector == index.chunks[0].vector


def test_retrieve_top_k_ordering():
    index, _ = build_corpus(DOCS, chunk_size=64, overlap=8)
    results, engine = retrieve("适合一家四口的房子", index, top_k=2)
    assert engine == "tf"
    assert len(results) == 2
    assert results[0].doc_id == "d1"
    assert results[0].score >= results[1].score


def test_retrieve_empty_corpus():
    empty = RagIndex(corpus_id="c", engine="tf", dim=512, chunks=[])
    results, engine = retrieve("anything", empty, top_k=5)
    assert results == []
    assert engine == "tf"


def test_build_answer_extractive_and_honest():
    index, _ = build_corpus(DOCS, chunk_size=64, overlap=8)
    results, engine = retrieve("适合一家四口的房子", index, top_k=2)
    ans = build_answer(results, 2, engine)
    assert ans.answer_mode == "extractive_retrieval"
    assert ans.embedding_engine == "tf"
    assert ans.top_k == 2
    assert "三室两厅" in ans.answer
    assert "d1" in ans.sources


def test_build_rag_runner_compiles_and_carries_query():
    runner = build_rag_runner("什么样的房子适合家庭？", top_k=3)
    compile(runner, "<runner>", "exec")
    assert "_RAG_QUERY = '什么样的房子适合家庭？'" in runner
    assert "_RAG_TOP_K = 3" in runner


def test_validate_rag_runner_accepts_generated():
    runner = build_rag_runner("适合一家四口的房子", top_k=2)
    ok, reason, query = validate_rag_runner(runner)
    assert ok is True
    assert query == "适合一家四口的房子"


def test_validate_rag_runner_rejects_tamper():
    runner = build_rag_runner("适合一家四口的房子", top_k=2)
    tampered = runner.replace(
        "import sys\n", "import sys\nimport socket\n", 1,
    )
    assert "import socket" in tampered
    ok, reason, _ = validate_rag_runner(tampered)
    assert ok is False
    assert "template" in reason


def test_validate_rag_runner_rejects_plain_code():
    ok, reason, _ = validate_rag_runner("print(1)")
    assert ok is False


def test_validate_rag_runner_rejects_code_in_query_position():
    # A "query" that is not a plain string literal must be rejected.
    bad = build_rag_runner("q").replace("_RAG_QUERY = 'q'", "_RAG_QUERY = __import__('os').system('x')")
    ok, reason, _ = validate_rag_runner(bad)
    assert ok is False


def test_index_version():
    assert INDEX_VERSION == 1
