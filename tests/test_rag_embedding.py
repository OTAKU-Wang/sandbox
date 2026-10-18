"""RAG embedding engine tests (gap T11).

Honest engine labeling, determinism, dimensionality and cosine behaviour of
the dependency-light tf/regex backends.
"""
import math

import pytest

from app.services.rag_embedding import (
    EmbeddingResult,
    EngineUnavailable,
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


# ---------------------------------------------------------------------------
# A129 — 维度不得拿空文本去探，维度不一致不得静默归零
# ---------------------------------------------------------------------------

def _patch_transformers_model(monkeypatch, hidden_size=32):
    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.hidden_size = hidden_size
    model = _Cfg()
    model.config = cfg
    monkeypatch.setattr(RAGEmbedder, "_load_transformers", lambda self: (object(), model))
    return RAGEmbedder(backend="transformers", model_name="fake/model")


def test_transformers_engine_reports_the_model_dimension_for_empty_text(monkeypatch):
    """空文本不得回一个写死 512 的零向量：引擎真实维度是 384/768 时索引 dim 会错。"""
    embedder = _patch_transformers_model(monkeypatch, hidden_size=32)

    result = embedder.embed("")

    assert result.engine == RAGEmbeddingEngine.TRANSFORMERS
    assert result.dim == 32
    assert len(result.vector) == 32
    assert set(result.vector) == {0.0}


def test_transformers_engine_fails_closed_when_the_model_cannot_be_resolved(monkeypatch):
    """模型加载不了 ⇒ 如实不可用，不能交一个可能错误的 dim 让索引落盘。"""
    def _no_model(self):
        raise EngineUnavailable("embedding model unavailable (test)")

    monkeypatch.setattr(RAGEmbedder, "_load_transformers", _no_model)
    embedder = RAGEmbedder(backend="transformers", model_name="fake/model")

    with pytest.raises(EngineUnavailable):
        embedder.embed("")

    with pytest.raises(EngineUnavailable):
        embedder.engine_dimension()


def test_engine_dimension_agrees_with_the_vectors_the_engine_produces():
    for backend in (RAGEmbeddingEngine.TF, RAGEmbeddingEngine.REGEX):
        embedder = RAGEmbedder(backend=backend)
        assert embedder.engine_dimension() == 512
        assert embedder.embed("合同 结算 周期").dim == embedder.engine_dimension()
        assert embedder.embed("").dim == embedder.engine_dimension()


def test_cosine_rejects_dimension_mismatch_instead_of_scoring_zero():
    """返回 0 把「索引与模型维度不一致」伪装成「这条不相关」。"""
    embedder = RAGEmbedder(backend="tf")
    with pytest.raises(ValueError, match="dimension"):
        embedder.cosine([0.1] * 512, [0.1] * 384)


def test_cosine_rejects_empty_vectors():
    embedder = RAGEmbedder(backend="tf")
    with pytest.raises(ValueError):
        embedder.cosine([], [])


def test_embedding_result_dimension_must_match_its_vector():
    with pytest.raises(ValueError):
        EmbeddingResult(vector=[0.0] * 4, engine=RAGEmbeddingEngine.TRANSFORMERS, dim=512)
