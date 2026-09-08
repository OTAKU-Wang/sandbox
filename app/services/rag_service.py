"""RAG phase-1 service (gap T11): in-domain retrieval-QA.

The corpus index is built host-side (chunk → embed → DuckDB-free flat JSON),
encrypted at rest, and executed INSIDE the sandbox by a self-contained runner
that replicates the embedding engine verbatim — the corpus never leaves the
sandbox and answers are always extractive retrieval (honest labeling:
``answer_mode="extractive_retrieval"``, ``embedding_engine`` = the engine that
actually ran).

Phase 1 deliberately pins corpus embedding to a runner-replicable engine
(tf / regex). The transformers backend is host-side only and is rejected here
(fail-closed) because the in-sandbox runner cannot yet replicate it — that is
a phase-2 item requiring the model shipped in the sandbox image.
"""
from __future__ import annotations

import json
import logging
import math
import textwrap
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.services.rag_embedding import (
    RAGEmbedder,
    RAGEmbeddingEngine,
    _RUNNER_REPLICABLE_ENGINES,
    regex_embed,
    tf_embed,
)

logger = logging.getLogger(__name__)

# Well-known filename the in-sandbox runner looks for inside workspace/input.
INDEX_FILENAME = "rag_index.json"
INDEX_VERSION = 1
ANSWER_MODE = "extractive_retrieval"
MAX_ANSWER_CHARS = 8000


@dataclass
class RagChunk:
    doc_id: str
    text: str
    vector: list[float]


@dataclass
class RagIndex:
    """Flat, self-contained corpus index (DuckDB used only at build time)."""
    corpus_id: str
    engine: str
    dim: int
    chunks: list[RagChunk] = field(default_factory=list)
    embedding_model_hash: str | None = None

    def to_dict(self) -> dict:
        return {
            "version": INDEX_VERSION,
            "corpus_id": self.corpus_id,
            "engine": self.engine,
            "dim": self.dim,
            "embedding_model_hash": self.embedding_model_hash,
            "chunks": [
                {"doc_id": c.doc_id, "text": c.text, "vector": c.vector}
                for c in self.chunks
            ],
        }

    def to_json(self) -> bytes:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> "RagIndex":
        chunks = [
            RagChunk(
                doc_id=str(c.get("doc_id", "")),
                text=str(c.get("text", "")),
                vector=[float(x) for x in (c.get("vector") or [])],
            )
            for c in (data.get("chunks") or [])
        ]
        return cls(
            corpus_id=str(data.get("corpus_id", "")),
            engine=str(data.get("engine", "tf")),
            dim=int(data.get("dim", 0)),
            chunks=chunks,
            embedding_model_hash=data.get("embedding_model_hash"),
        )

    @classmethod
    def from_json(cls, raw: bytes) -> "RagIndex":
        return cls.from_dict(json.loads(raw.decode("utf-8")))


@dataclass
class RetrievedChunk:
    doc_id: str
    text: str
    score: float


@dataclass
class RagAnswer:
    answer: str
    sources: list[str]
    embedding_engine: str
    answer_mode: str = ANSWER_MODE
    top_k: int = 0
    retrieved_chunks: int = 0
    answer_chars: int = 0
    truncated: bool = False
    backend: str = "local_sandbox"


def _is_cjk_char(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def chunk_text(text: str, chunk_size: int = 256, overlap: int = 32) -> list[str]:
    """Sliding-window chunking; CJK characters count as one unit each."""
    chunk_size = max(8, int(chunk_size))
    overlap = max(0, min(int(overlap), chunk_size - 1))
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= chunk_size:
        return [t]
    chunks: list[str] = []
    step = chunk_size - overlap
    start = 0
    while start < len(t):
        chunks.append(t[start:start + chunk_size])
        if start + chunk_size >= len(t):
            break
        start += step
    return chunks


def _ensure_runner_replicable_engine(engine: str, *, what: str, ship_transformers: bool = False) -> str:
    if engine not in _RUNNER_REPLICABLE_ENGINES:
        raise ValueError(
            f"{what}: embedding engine {engine!r} is not supported for in-sandbox "
            f"retrieval (runner-replicable engines: {sorted(_RUNNER_REPLICABLE_ENGINES)})."
        )
    if engine == "transformers" and not ship_transformers:
        raise ValueError(
            f"{what}: embedding engine 'transformers' requires the model bundle "
            f"to be shipped with the corpus (phase 2) — ingest via the API so the "
            f"model files travel alongside the corpus."
        )
    return engine


def build_corpus(
    docs: list[dict],
    chunk_size: int | None = None,
    overlap: int | None = None,
    embedding_backend: str | None = None,
    corpus_id: str | None = None,
    ship_transformers: bool = False,
) -> tuple[RagIndex, dict]:
    """Build a corpus index from ``docs`` = [{doc_id, text}, ...].

    Returns (RagIndex, stats). tf/regex are always runner-replicable;
    ``transformers`` requires ``ship_transformers=True`` — the host embeds with
    the transformers model and returns the packaged model bundle in stats so
    the API can ship it to the sandbox (phase 2).
    """
    from app.core.config import get_settings
    settings = get_settings()

    embedder = RAGEmbedder(backend=embedding_backend)
    engine = _ensure_runner_replicable_engine(
        embedder.resolve_engine(), what="corpus build", ship_transformers=ship_transformers
    )

    chunk_size = chunk_size or settings.RAG_CHUNK_SIZE
    overlap = overlap if overlap is not None else settings.RAG_CHUNK_OVERLAP

    chunks: list[RagChunk] = []
    doc_stats: dict[str, int] = {}
    for doc in docs:
        doc_id = str(doc.get("doc_id", "")) or str(uuid.uuid4())[:8]
        text = str(doc.get("text", ""))
        pieces = chunk_text(text, chunk_size, overlap)
        doc_stats[doc_id] = len(pieces)
        for piece in pieces:
            vec = embedder.embed(piece)
            chunks.append(RagChunk(doc_id=doc_id, text=piece, vector=vec.vector))

    embedding_model_hash = None
    embedding_model_bundle = None
    if engine == "transformers":
        # Package the host-side transformers model so the API can ship it
        # alongside the corpus; the runner verifies the same hash.
        embedding_model_bundle, embedding_model_hash = _package_transformers_model(embedder)

    index = RagIndex(
        corpus_id=corpus_id or f"corpus-{uuid.uuid4().hex[:12]}",
        engine=engine,
        dim=embedder.embed("").dim,
        chunks=chunks,
        embedding_model_hash=embedding_model_hash,
    )
    stats = {
        "doc_count": len(docs),
        "chunk_count": len(chunks),
        "embedding_engine": engine,
        "dim": index.dim,
        "docs": doc_stats,
        "embedding_model_hash": embedding_model_hash,
    }
    if embedding_model_bundle is not None:
        stats["embedding_model_bundle"] = embedding_model_bundle
    return index, stats


def _package_transformers_model(embedder) -> tuple[bytes, str]:
    """Save the loaded transformers model/tokenizer into a deterministic zip.

    Returns (bundle_zip_bytes, sha256_of_plaintext_files). The runner decrypts
    the shipped bundle, hashes the extracted files in the same sorted order,
    and rejects on mismatch.
    """
    import hashlib
    import io
    import os as _os
    import tempfile
    import zipfile

    tokenizer, model = embedder._load_transformers()
    tmp = tempfile.mkdtemp(prefix="cds-emb-pkg-")
    model.save_pretrained(tmp)
    tokenizer.save_pretrained(tmp)
    names = sorted(_os.listdir(tmp))

    digest = hashlib.sha256()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name in names:
            with open(_os.path.join(tmp, name), "rb") as fh:
                data = fh.read()
            digest.update(data)
            zf.writestr(name, data)
    return buf.getvalue(), digest.hexdigest()


def retrieve(
    query: str,
    index: RagIndex,
    top_k: int = 5,
) -> tuple[list[RetrievedChunk], str]:
    """Embed the query with the index's engine and return top-k chunks."""
    if not index.chunks:
        return [], index.engine
    engine = _ensure_runner_replicable_engine(
        index.engine, what="retrieval",
        ship_transformers=(index.engine == "transformers"),
    )
    embedder = RAGEmbedder(backend=engine)
    qvec = embedder.embed(query).vector
    scored: list[tuple[float, RagChunk]] = []
    for chunk in index.chunks:
        score = embedder.cosine(qvec, chunk.vector)
        scored.append((score, chunk))
    scored.sort(key=lambda p: p[0], reverse=True)
    top_k = max(1, int(top_k))
    results = [
        RetrievedChunk(doc_id=c.doc_id, text=c.text, score=float(score))
        for score, c in scored[:top_k]
    ]
    return results, engine


def build_answer(
    retrieved: list[RetrievedChunk],
    top_k: int,
    engine: str,
    max_chars: int = MAX_ANSWER_CHARS,
) -> RagAnswer:
    """Extractive answer from top-k chunks (phase 1 — no generative LLM)."""
    sources: list[str] = []
    parts: list[str] = []
    total = 0
    truncated = False
    for rc in retrieved:
        if rc.doc_id not in sources:
            sources.append(rc.doc_id)
        piece = rc.text.strip()
        if not piece:
            continue
        if total + len(piece) + 2 > max_chars:
            remaining = max_chars - total
            if remaining > 40:
                parts.append(piece[:remaining] + "…")
                total += remaining + 1
            truncated = True
            break
        parts.append(piece)
        total += len(piece) + 2  # separator
    answer = "\n\n".join(parts).strip()
    return RagAnswer(
        answer=answer,
        sources=sources,
        embedding_engine=engine,
        top_k=top_k,
        retrieved_chunks=len(retrieved),
        answer_chars=len(answer),
        truncated=truncated,
    )


# ── in-sandbox runner ─────────────────────────────────────────

_RUNNER_HEADER = '''\
# CDS RAG runner (system-generated; phase-1 extractive + phase-2 generative).
# Retrieves from the session corpus INSIDE the sandbox and answers
# extractively (default) or generatively via a provider-registered ONNX
# bundle. Self-contained: stdlib + AEAD decrypt helper.
import io
import json
import math
import os
import re
import sys
import zipfile

INDEX_FILENAME = "input/rag_index.json"
INDEX_VERSION = 1
GENERATIVE_BUNDLE_FILE = "input/generative_model.bundle"
EMBEDDING_MODEL_BUNDLE = "input/embedding_model.bundle"

_CJK_RE = re.compile(r"[\\u4e00-\\u9fff]")
_WORD_RE = re.compile(r"[\\u4e00-\\u9fff]|[a-zA-Z0-9_]+")


def _hash_token(token):
    h = 2166136261
    for ch in token.encode("utf-8"):
        h = (h ^ ch) * 16777619 & 0xFFFFFFFF
    return h


def _l2_normalize(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0.0:
        return vec
    return [x / norm for x in vec]


def _add_hashed(vec, token, dim):
    h = _hash_token(token)
    idx = h % dim
    sign = 1.0 if (h // dim) % 2 == 0 else -1.0
    vec[idx] += sign


def tf_embed(text, dim=512):
    vec = [0.0] * dim
    t = (text or "").strip()
    if not t:
        return vec
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            _add_hashed(vec, t[i:i + n], dim)
    for ch in t:
        if _CJK_RE.match(ch):
            _add_hashed(vec, ch, dim)
    return _l2_normalize(vec)


def regex_embed(text, dim=512):
    vec = [0.0] * dim
    t = (text or "").lower().strip()
    if not t:
        return vec
    for tok in _WORD_RE.findall(t):
        _add_hashed(vec, tok, dim)
    return _l2_normalize(vec)


def _decrypt_workspace_file(raw, dek_hex):
    dek = bytes.fromhex(dek_hex)
    nonce = raw[:12]
    tag = raw[12:28]
    ct = raw[28:]
    try:
        from Cryptodome.Cipher import AES
        return AES.new(dek, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag)
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(dek).decrypt(nonce, ct + tag, None)


def _load_bytes(path):
    raw = open(path, "rb").read()
    dek_hex = os.environ.get("CDS_DEK_HEX", "")
    if dek_hex:
        raw = _decrypt_workspace_file(raw, dek_hex)
    return raw


def _load_index():
    raw = _load_bytes(INDEX_FILENAME)
    data = json.loads(raw.decode("utf-8"))
    if int(data.get("version", 0)) != INDEX_VERSION:
        raise RuntimeError("rag index version mismatch: %r" % data.get("version"))
    return data


def _sha256_of_dir(path):
    import hashlib
    h = hashlib.sha256()
    for p in sorted(os.listdir(path)):
        with open(os.path.join(path, p), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def _load_embedding_model_dir():
    bundle_bytes = _load_bytes(EMBEDDING_MODEL_BUNDLE)
    zf = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    import tempfile
    tmp = tempfile.mkdtemp(prefix="cds-emb-")
    for name in zf.namelist():
        if "/" in name or name.startswith("."):
            continue
        with open(os.path.join(tmp, name), "wb") as fh:
            fh.write(zf.read(name))
    return tmp


def _transformers_embed(text):
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError:
        raise RuntimeError(
            "transformers unavailable in sandbox (embedding engine=transformers); "
            "install torch+transformers in the sandbox image or rebuild the corpus with tf"
        )
    if _EMBEDDING_MODEL_HASH:
        actual = _sha256_of_dir(_load_embedding_model_dir())
        if actual != _EMBEDDING_MODEL_HASH:
            raise RuntimeError("embedding model hash mismatch in sandbox")
    model_dir = _load_embedding_model_dir()
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModel.from_pretrained(model_dir)
    model.eval()
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=256, padding=True)
    with torch.no_grad():
        outputs = model(**inputs)
    hidden = outputs.last_hidden_state
    mask = inputs["attention_mask"].unsqueeze(-1).float()
    summed = (hidden * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1.0)
    mean = (summed / counts)[0]
    norm = torch.linalg.vector_norm(mean)
    if norm > 0:
        mean = mean / norm
    return [float(x) for x in mean.tolist()]


def _embed_query(query, engine):
    if engine == "tf":
        return tf_embed(query)
    if engine == "regex":
        return regex_embed(query)
    if engine == "transformers":
        if not os.path.exists(EMBEDDING_MODEL_BUNDLE):
            raise RuntimeError(
                "embedding model not shipped into sandbox (engine=transformers); "
                "rebuild the corpus with tf or re-ingest with the model present"
            )
        return _transformers_embed(query)
    raise RuntimeError(
        "unsupported in-sandbox embedding engine %r (phase 2 supports tf/regex/transformers)" % engine
    )


def _generative_answer(context_text):
    if not _RAG_GENERATIVE_MODEL_ID:
        raise RuntimeError("generative answer requested but no generative model provisioned")
    bundle_bytes = _load_bytes(GENERATIVE_BUNDLE_FILE)
    zf = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    model_bytes = zf.read("model.onnx")
    vocab = zf.read("vocab.txt").decode("utf-8").splitlines()
    try:
        import numpy as np
        import onnxruntime as ort
    except ImportError:
        raise RuntimeError(
            "onnxruntime/numpy unavailable in sandbox (generative mode); inference unavailable (fail-closed)"
        )
    sess = ort.InferenceSession(model_bytes, providers=["CPUExecutionProvider"])
    ctx_vec = tf_embed(context_text)
    logits = sess.run(None, {"context": np.asarray([ctx_vec], dtype=np.float32)})[0]
    idx = int(np.argmax(logits[0]))
    return vocab[idx] if 0 <= idx < len(vocab) else "<unk>"


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def main():
    query = _RAG_QUERY
    top_k = _RAG_TOP_K
    index = _load_index()
    engine = index.get("engine", "tf")
    qvec = _embed_query(query, engine)
    chunks = index.get("chunks", [])
    scored = []
    for c in chunks:
        v = c.get("vector") or []
        scored.append((_cosine(qvec, v), c))
    scored.sort(key=lambda p: p[0], reverse=True)
    top = scored[:max(1, top_k)]
    sources = []
    parts = []
    for score, c in top:
        doc_id = c.get("doc_id", "")
        if doc_id not in sources:
            sources.append(doc_id)
        parts.append((c.get("text") or "").strip())

    answer_mode = _RAG_ANSWER_MODE
    if answer_mode == "generative":
        import hashlib
        answer = _generative_answer("\\n\\n".join(parts))
        wm = hashlib.sha256((_RAG_GENERATIVE_MODEL_ID or "").encode()).hexdigest()[:8]
        answer = "%s [CDS-WM:%s]" % (answer, wm)
        watermark = True
    else:
        answer = "\\n\\n".join(p for p in parts if p).strip()
        watermark = False

    result = {
        "answer": answer,
        "sources": sources,
        "rag_meta": {
            "embedding_engine": engine,
            "answer_mode": answer_mode,
            "generative_model": _RAG_GENERATIVE_MODEL_ID,
            "watermark": watermark,
            "top_k": len(top),
            "retrieved_chunks": len(top),
            "source_doc_ids": sources,
            "answer_chars": len(answer),
            "truncated": False,
            "backend": "local_sandbox",
        },
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0
'''

# NOTE: the `if __name__ == "__main__"` entry guard is appended by
# build_rag_runner AFTER the per-task `_RAG_QUERY` / `_RAG_TOP_K` literals are
# defined (module statements run top-to-bottom).


def build_rag_runner(
    query: str,
    top_k: int | None = None,
    answer_mode: str = "extractive_retrieval",
    generative_model_id: str | None = None,
    embedding_model_hash: str | None = None,
) -> str:
    """Generate the self-contained runner source for a RAG_QUERY task.

    The query (and answer mode / model ids) travel inside the generated code
    as module-level literals (the code is stored encrypted as ``code_content``
    and code-scanned), so they never land in a plaintext column. The runner
    embeds the same embedding functions host-side uses, guaranteeing identical
    vectors inside the sandbox.
    """
    from app.core.config import get_settings
    settings = get_settings()
    effective_top_k = int(top_k or settings.RAG_DEFAULT_TOP_K)
    if effective_top_k < 1:
        effective_top_k = 1
    mode = answer_mode or "extractive_retrieval"
    if mode not in ("extractive_retrieval", "generative"):
        raise ValueError(f"Unknown answer_mode {mode!r}")
    body = textwrap.dedent(_RUNNER_HEADER).rstrip()
    tail = (
        "\n\n# --- generated per-task params ---\n"
        f"_RAG_QUERY = {query!r}\n"
        f"_RAG_TOP_K = {effective_top_k}\n"
        f"_RAG_ANSWER_MODE = {mode!r}\n"
        f"_RAG_GENERATIVE_MODEL_ID = {generative_model_id!r}\n"
        f"_EMBEDDING_MODEL_HASH = {embedding_model_hash!r}\n"
        "\nif __name__ == '__main__':\n    sys.exit(main())\n"
    )
    return body + tail


def validate_rag_runner(code: str) -> tuple[bool, str, str | None]:
    """Verify a RAG runner is EXACTLY the system-generated template.

    RAG runners are system code (a fixed template + the user's query and
    answer-mode/model literals), not user-submitted code, so the general code
    scanner's import whitelist does not apply. Safety is guaranteed by
    byte-exactness: extract the embedded literals, regenerate the runner, and
    require an exact match. Any tampering — extra imports, calls, network,
    file writes — makes the byte comparison fail and the runner is rejected.

    Returns (passed, reason, extracted_query).
    """
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"runner is not valid python: {e}", None

    query: str | None = None
    top_k: int | None = None
    answer_mode: str | None = None
    generative_model_id: str | None = None
    embedding_model_hash: str | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "_RAG_QUERY":
                if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                    return False, "_RAG_QUERY must be a string literal (no code injection)", None
                query = node.value.value
            elif isinstance(target, ast.Name) and target.id == "_RAG_TOP_K":
                if not isinstance(node.value, ast.Constant):
                    return False, "_RAG_TOP_K must be a literal", None
                top_k = node.value.value
            elif isinstance(target, ast.Name) and target.id == "_RAG_ANSWER_MODE":
                if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                    return False, "_RAG_ANSWER_MODE must be a string literal", None
                answer_mode = node.value.value
            elif isinstance(target, ast.Name) and target.id == "_RAG_GENERATIVE_MODEL_ID":
                if node.value is None or not isinstance(node.value, ast.Constant):
                    return False, "_RAG_GENERATIVE_MODEL_ID must be a literal", None
                generative_model_id = node.value.value
            elif isinstance(target, ast.Name) and target.id == "_EMBEDDING_MODEL_HASH":
                if node.value is None or not isinstance(node.value, ast.Constant):
                    return False, "_EMBEDDING_MODEL_HASH must be a literal", None
                embedding_model_hash = node.value.value

    if query is None:
        return False, "runner missing _RAG_QUERY literal", None

    expected = build_rag_runner(
        query,
        top_k=top_k,
        answer_mode=answer_mode or "extractive_retrieval",
        generative_model_id=generative_model_id,
        embedding_model_hash=embedding_model_hash,
    )
    if code != expected:
        return False, "runner differs from the system-generated template (tampered or stale)", query
    return True, "runner matches the system-generated template", query


# ── persistence helpers ────────────────────────────────────────

def materialize_index_to_resource(
    index: RagIndex,
    resource_dir: str | Path,
    filename: str = INDEX_FILENAME,
) -> Path:
    """Write the plaintext index into a resource directory (host side).

    The durable encrypted copy lives in ``storage_service`` (envelope
    encryption); this local file is what session provision copies into the
    sandbox workspace and re-encrypts with the session DEK.
    """
    resource_dir = Path(resource_dir)
    resource_dir.mkdir(parents=True, exist_ok=True)
    target = resource_dir / filename
    target.write_bytes(index.to_json())
    return target


def encrypt_index_blob(index: RagIndex) -> dict:
    """Store the index at rest via storage_service envelope encryption.

    Returns ``{object_ref, checksum, size, encrypted}`` (see storage_service).
    """
    from app.services.storage_service import storage_service
    blob = index.to_json()
    object_name = f"rag/{index.corpus_id}/{INDEX_FILENAME}"
    return storage_service.upload(blob, object_name, content_type="application/json")


def corpus_object_name(data_product_id: str | object) -> str:
    """Deterministic storage location of a session's corpus index.

    Phase 1 derives the object name from the session's data product so no
    extra DB record is needed; re-ingesting a product overwrites atomically.
    """
    return f"rag/corpus/{data_product_id}/{INDEX_FILENAME}"


def embedding_model_object_name(data_product_id: str | object) -> str:
    """Deterministic storage location of a session's transformers embedding
    model bundle (phase 2), sibling to the corpus index."""
    return f"rag/corpus/{data_product_id}/embedding_model.bundle"


def _extract_rag_metrics(output: str) -> dict:
    """Parse retrieval metrics from a RAG runner's JSON output for metering."""
    if not output:
        return {}
    try:
        line = [ln for ln in output.strip().splitlines() if ln.strip()][-1]
        data = json.loads(line)
    except (ValueError, IndexError):
        return {}
    meta = data.get("rag_meta") or {}
    return {
        "retrieved_chunks": meta.get("retrieved_chunks"),
        "top_k": meta.get("top_k"),
        "answer_mode": meta.get("answer_mode"),
        "embedding_engine": meta.get("embedding_engine"),
        "generative_model": meta.get("generative_model"),
        "watermark": meta.get("watermark"),
    }


async def prepare_corpus_for_task(db, session, workspace: Path) -> dict | None:
    """Materialize a session's corpus index into workspace/input (host side).

    Downloads the encrypted index (storage_service envelope), writes the
    plaintext to ``input/rag_index.json`` and re-encrypts it with the session
    DEK exactly like provision does — so the in-sandbox runner decrypts it
    with ``CDS_DEK_HEX`` and the file is never plaintext at rest. For
    transformers-engine corpora the embedding model bundle is shipped
    alongside (``input/embedding_model.bundle``) so the runner can reproduce
    query vectors; the index records the model hash for runner verification.

    Returns ``{corpus_id, engine, chunk_count}`` or None when no corpus exists
    (the runner then fails closed with a clear missing-index error).
    """
    from app.services.storage_service import storage_service

    data_product_id = getattr(session, "data_product_id", None)
    if not data_product_id:
        return None
    object_name = corpus_object_name(str(data_product_id))
    try:
        blob = storage_service.download(object_name)
        if not blob:
            return None
    except Exception as e:
        logger.warning("[RAG] Corpus download failed (%s): %s", object_name, e)
        return None

    try:
        index = RagIndex.from_json(blob)
    except Exception as e:
        logger.warning("[RAG] Corpus parse failed (%s): %s", object_name, e)
        return None

    input_dir = workspace / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    target = input_dir / INDEX_FILENAME
    target.write_bytes(index.to_json())

    if index.engine == "transformers":
        try:
            emb_blob = storage_service.download(embedding_model_object_name(str(data_product_id)))
            if emb_blob:
                (input_dir / "embedding_model.bundle").write_bytes(emb_blob)
        except Exception as e:
            logger.warning("[RAG] Embedding model bundle download failed: %s", e)

    dek = await _session_workspace_dek(workspace)
    if dek:
        try:
            from app.services.sandbox_security import encrypt_workspace_file
            encrypt_workspace_file(target, dek)
            if (input_dir / "embedding_model.bundle").exists():
                encrypt_workspace_file(input_dir / "embedding_model.bundle", dek)
        except Exception as e:
            logger.warning("[RAG] Corpus workspace re-encryption failed: %s", e)
            return None

    logger.info(
        "[RAG] Corpus ready for session=%s engine=%s chunks=%d",
        getattr(session, "id", "?"), index.engine, len(index.chunks),
    )
    return {
        "corpus_id": index.corpus_id,
        "engine": index.engine,
        "chunk_count": len(index.chunks),
    }


async def _session_workspace_dek(workspace: Path) -> bytes | None:
    """Resolve the session workspace DEK from .security_config.json."""
    try:
        config_path = workspace / ".security_config.json"
        if not config_path.exists():
            return None
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        key_id = cfg.get("workspace_key_id")
        if not key_id:
            return None
        from app.services.kms_service import kms_service
        return kms_service.get_key(key_id)
    except Exception as e:
        logger.warning("[RAG] Workspace DEK resolution failed: %s", e)
        return None
