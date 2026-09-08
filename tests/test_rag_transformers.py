"""N6: RAG phase-2 — transformers embedding parity host vs in-sandbox runner.

Builds a TINY real BERT model + character-level WordPiece tokenizer entirely
in-test (no network), embeds a corpus host-side, ships the packaged model
bundle to a sandbox workspace, and asserts the runner retrieves the SAME
top-k sources as the host reference — proving query vectors are reproducible
in-sandbox when the model travels with the corpus (hash-verified).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

DOCS = [
    {"doc_id": "d1", "text": "三室两厅的户型适合一家四口居住，南北通透采光好。"},
    {"doc_id": "d2", "text": "小户型一居室适合单身青年，交通便利，总价低。"},
    {"doc_id": "d3", "text": "别墅拥有独立花园和车位，适合大家庭与宠物。"},
]
QUERY = "适合一家四口的房子"

_SPECIALS = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"]


def _build_tiny_bert(model_dir: Path):
    import torch
    from transformers import BertConfig, BertModel, BertTokenizer

    chars = sorted({ch for d in DOCS for ch in d["text"]} | set(QUERY))
    vocab = {t: i for i, t in enumerate(_SPECIALS + chars)}
    config = BertConfig(
        vocab_size=len(vocab), hidden_size=32, num_hidden_layers=2,
        num_attention_heads=4, intermediate_size=64,
    )
    model = BertModel(config)
    model.save_pretrained(str(model_dir))
    BertTokenizer(vocab=vocab, do_lower_case=True).save_pretrained(str(model_dir))
    return vocab


def _run_runner(runner, workspace):
    return subprocess.run(
        [sys.executable, "-c", runner], cwd=workspace, capture_output=True, text=True,
        env={**os.environ},
    )


def test_transformers_corpus_host_runner_parity(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services.rag_embedding import RAGEmbedder
    from app.services.rag_service import (
        build_corpus,
        build_rag_runner,
        retrieve,
    )

    _build_tiny_bert(tmp_path / "tiny-bert")
    # Point the embedder at the in-test model (no network download).
    monkeypatch.setattr(get_settings(), "RAG_EMBEDDING_MODEL", str(tmp_path / "tiny-bert"))

    # Host corpus built with transformers, model bundle packaged + hashed.
    index, stats = build_corpus(
        DOCS, chunk_size=64, overlap=8,
        embedding_backend="transformers", ship_transformers=True,
    )
    assert index.engine == "transformers"
    assert stats["embedding_model_hash"]
    assert stats["embedding_model_bundle"]
    assert index.embedding_model_hash == stats["embedding_model_hash"]

    # Host reference retrieval.
    host_sources = [c.doc_id for c in retrieve(QUERY, index, top_k=2)[0]]

    # In-sandbox runner with the shipped (hash-verified) model bundle.
    runner = build_rag_runner(
        QUERY, top_k=2, embedding_model_hash=stats["embedding_model_hash"],
    )
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "rag_index.json").write_bytes(index.to_json())
        (ws / "input" / "embedding_model.bundle").write_bytes(stats["embedding_model_bundle"])
        proc = _run_runner(runner, ws)
        assert proc.returncode == 0, proc.stderr
        result = json.loads([ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1])

    assert result["sources"] == host_sources
    assert result["rag_meta"]["embedding_engine"] == "transformers"


def test_transformers_runner_fails_closed_without_bundle(tmp_path, monkeypatch):
    """A transformers corpus without a shipped model bundle fails closed in
    the sandbox (never silently falls back to tf)."""
    from app.core.config import get_settings
    from app.services.rag_service import build_corpus, build_rag_runner

    _build_tiny_bert(tmp_path / "tiny-bert")
    monkeypatch.setattr(get_settings(), "RAG_EMBEDDING_MODEL", str(tmp_path / "tiny-bert"))

    index, _stats = build_corpus(
        DOCS, chunk_size=64, overlap=8,
        embedding_backend="transformers", ship_transformers=True,
    )
    runner = build_rag_runner(QUERY, top_k=2)
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "rag_index.json").write_bytes(index.to_json())
        proc = _run_runner(runner, ws)  # no embedding_model.bundle
    assert proc.returncode != 0
    assert "not shipped into sandbox" in proc.stderr


def test_tf_to_transformers_rebuild_migration(tmp_path, monkeypatch):
    """Re-ingesting a corpus with a different engine replaces the engine and
    records the new model hash — the tf→transformers migration path."""
    from app.core.config import get_settings
    from app.services.rag_service import build_corpus

    _build_tiny_bert(tmp_path / "tiny-bert")
    monkeypatch.setattr(get_settings(), "RAG_EMBEDDING_MODEL", str(tmp_path / "tiny-bert"))

    index_tf, stats_tf = build_corpus(DOCS, chunk_size=64, overlap=8, embedding_backend="tf")
    assert index_tf.engine == "tf" and not stats_tf.get("embedding_model_hash")

    index_t, stats_t = build_corpus(
        DOCS, chunk_size=64, overlap=8,
        embedding_backend="transformers", ship_transformers=True,
    )
    assert index_t.engine == "transformers"
    assert stats_t["embedding_model_hash"] and stats_t["embedding_model_bundle"]
