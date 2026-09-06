"""RAG runner end-to-end tests (gap T11).

Executes the generated runner as a real subprocess — plaintext and
session-DEK-encrypted corpus — and asserts host/runner consistency so the
in-sandbox retrieval matches the host-side reference exactly.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.services.rag_service import build_answer, build_corpus, build_rag_runner, retrieve

DOCS = [
    {"doc_id": "d1", "text": "三室两厅的户型适合一家四口居住，南北通透采光好。"},
    {"doc_id": "d2", "text": "小户型一居室适合单身青年，交通便利，总价低。"},
    {"doc_id": "d3", "text": "别墅拥有独立花园和车位，适合大家庭与宠物。"},
]
QUERY = "适合一家四口的房子"


def _run_runner(runner, workspace, env_extra):
    proc = subprocess.run(
        [sys.executable, "-c", runner],
        cwd=workspace,
        capture_output=True,
        text=True,
        env={**os.environ, **env_extra},
    )
    return proc


def _parse_output(proc):
    assert proc.returncode == 0, f"runner failed: {proc.stderr}"
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


def _make_corpus_and_runner():
    index, _stats = build_corpus(DOCS, chunk_size=64, overlap=8)
    runner = build_rag_runner(QUERY, top_k=2)
    return index, runner


def test_runner_plaintext_retrieval():
    index, runner = _make_corpus_and_runner()
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "rag_index.json").write_bytes(index.to_json())
        proc = _run_runner(runner, ws, {})
        result = _parse_output(proc)

    assert result["sources"] == ["d1", "d2"]
    assert "三室两厅" in result["answer"]
    meta = result["rag_meta"]
    assert meta["embedding_engine"] == "tf"
    assert meta["answer_mode"] == "extractive_retrieval"
    assert meta["backend"] == "local_sandbox"
    assert meta["top_k"] == 2


def test_runner_encrypted_corpus_with_session_dek():
    from Cryptodome.Cipher import AES

    index, runner = _make_corpus_and_runner()
    dek = os.urandom(16)
    raw = index.to_json()
    cipher = AES.new(dek, AES.MODE_GCM, nonce=os.urandom(12))
    ct, tag = cipher.encrypt_and_digest(raw)
    encrypted = cipher.nonce + tag + ct

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "rag_index.json").write_bytes(encrypted)
        proc = _run_runner(runner, ws, {"CDS_DEK_HEX": dek.hex()})
        result = _parse_output(proc)

    assert result["sources"] == ["d1", "d2"]
    assert "三室两厅" in result["answer"]


def test_runner_encrypted_without_dek_fails_closed():
    from Cryptodome.Cipher import AES

    index, runner = _make_corpus_and_runner()
    dek = os.urandom(16)
    raw = index.to_json()
    cipher = AES.new(dek, AES.MODE_GCM, nonce=os.urandom(12))
    ct, tag = cipher.encrypt_and_digest(raw)

    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "rag_index.json").write_bytes(cipher.nonce + tag + ct)
        proc = _run_runner(runner, ws, {})  # no CDS_DEK_HEX
    assert proc.returncode != 0


def test_runner_matches_host_retrieval():
    index, runner = _make_corpus_and_runner()
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)
        (ws / "input" / "rag_index.json").write_bytes(index.to_json())
        proc = _run_runner(runner, ws, {})
        result = _parse_output(proc)

    results, engine = retrieve(QUERY, index, top_k=2)
    host = build_answer(results, 2, engine)
    assert result["answer"] == host.answer
    assert result["sources"] == host.sources
    assert result["rag_meta"]["answer_mode"] == host.answer_mode


def test_runner_missing_index_fails_closed():
    _index, runner = _make_corpus_and_runner()
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "input").mkdir(parents=True)  # no rag_index.json
        proc = _run_runner(runner, ws, {})
    assert proc.returncode != 0
    assert "rag_index.json" in (proc.stderr or "")
