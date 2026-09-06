"""RAG embedding engine tests (gap T11).

Honest engine labeling, determinism, dimensionality and cosine behaviour of
the dependency-light tf/regex backends.
"""
import math

import pytest

from app.services.rag_embedding import (
    RAGEmbedder,
    RAGEmbeddingEngine,
    regex_embed,
    tf_embed,
)


def test_tf_embed_is_deterministic_and_normalized():
    v1 = tf_embed("三室两厅适合一家四口")
    v2 = tf_embed("三室两厅适合一家四口")
    assert v1 == v2
    assert len(v1) == 512
    norm = math.sqrt(sum(x * x for x in v1))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_tf_embed_differs_for_different_text():
    a = tf_embed("三室两厅适合一家四口")
    b = tf_embed("别墅拥有独立花园")
    assert a != b


def test_tf_embed_empty_returns_zero_vector():
    v = tf_embed("   ")
    assert v == [0.0] * 512


def test_regex_embed_case_insensitive_tokens():
    a = regex_embed("Hello WORLD 你好")
    b = regex_embed("hello world 你好")
    assert a == b


def test_embedder_auto_resolves_to_tf_phase1():
    embedder = RAGEmbedder(backend=RAGEmbeddingEngine.AUTO)
    assert embedder.resolve_engine() == RAGEmbeddingEngine.TF


def test_embedder_honest_engine_label():
    embedder = RAGEmbedder(backend=RAGEmbeddingEngine.TF)
    res = embedder.embed("一些中文文本内容用于检索")
    assert res.engine == RAGEmbeddingEngine.TF
    assert res.dim == 512


def test_embedder_regex_label():
    embedder = RAGEmbedder(backend=RAGEmbeddingEngine.REGEX)
    res = embedder.embed("some words here")
    assert res.engine == RAGEmbeddingEngine.REGEX


def test_embedder_empty_text_zero_vector_honest():
    embedder = RAGEmbedder(backend=RAGEmbeddingEngine.TF)
    res = embedder.embed("")
    assert res.vector == [0.0] * 512
    assert res.engine == RAGEmbeddingEngine.TF


def test_embedder_unknown_backend_raises():
    with pytest.raises(ValueError):
        RAGEmbedder(backend="bogus")


def test_cosine_between_normalized_vectors():
    embedder = RAGEmbedder(backend=RAGEmbeddingEngine.TF)
    a = embedder.embed("适合一家四口的房子").vector
    b = embedder.embed("适合一家四口的房子").vector
    c = embedder.embed("今天天气不错").vector
    assert embedder.cosine(a, b) == pytest.approx(1.0, abs=1e-6)
    assert embedder.cosine(a, c) < embedder.cosine(a, b)
