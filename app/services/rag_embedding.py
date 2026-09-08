"""RAG embedding engines (gap T11, phase 1).

Honest-labeling embedding layer modelled on the PII NER engine pattern
(G-137): the result always reports the engine that ACTUALLY ran.

Phase 1 ships two self-contained, deterministic engines the in-sandbox
runner can replicate verbatim (tf, regex) and an optional transformers
backend for host-side use / future phases. ``RAG_EMBEDDING_BACKEND=auto``
resolves to ``tf`` for phase 1 so corpus and query embeddings stay
consistent inside the sandbox (transformers-based retrieval requires the
model to be shipped in the sandbox image — phase 2, fail-closed here).
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Fixed feature dimension for the hashed TF / regex backends. Keeps vectors
# dense, bounded and comparable regardless of vocabulary size.
TF_DIM = 512

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+")

# Engines the in-sandbox runner can reproduce. transformers requires the
# model bundle to be shipped with the corpus (phase 2) — gated in build_corpus.
_RUNNER_REPLICABLE_ENGINES = {"tf", "regex", "transformers"}


class RAGEmbeddingEngine:
    """Supported embedding engines."""
    AUTO = "auto"
    TF = "tf"
    TRANSFORMERS = "transformers"
    REGEX = "regex"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in {cls.AUTO, cls.TF, cls.TRANSFORMERS, cls.REGEX}


class EngineUnavailable(RuntimeError):
    """Raised when the requested embedding backend cannot run here."""


@dataclass
class EmbeddingResult:
    """A single embedded vector with honest engine labeling."""
    vector: list[float]
    engine: str  # the engine that ACTUALLY produced the vector
    dim: int

    def to_dict(self) -> dict:
        return {"vector": self.vector, "engine": self.engine, "dim": self.dim}


def _hash_token(token: str) -> int:
    """Deterministic 32-bit hash of a token (stdlib-only, runner-replicable)."""
    h = 2166136261  # FNV-1a base
    for ch in token.encode("utf-8"):
        h = (h ^ ch) * 16777619 & 0xFFFFFFFF
    return h


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0.0:
        return vec
    return [x / norm for x in vec]


def tf_embed(text: str, dim: int = TF_DIM) -> list[float]:
    """Char n-gram (n=2,3) + CJK unigram hashed TF vector, L2-normalized.

    Deterministic and self-contained so the sandbox runner can reproduce
    exactly the same vector for the same text.
    """
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


def regex_embed(text: str, dim: int = TF_DIM) -> list[float]:
    """Word / CJK-char token hashed TF vector, L2-normalized."""
    vec = [0.0] * dim
    t = (text or "").lower().strip()
    if not t:
        return vec
    for tok in _WORD_RE.findall(t):
        _add_hashed(vec, tok, dim)
    return _l2_normalize(vec)


def _add_hashed(vec: list[float], token: str, dim: int) -> None:
    h = _hash_token(token)
    idx = h % dim
    sign = 1.0 if (h // dim) % 2 == 0 else -1.0
    vec[idx] += sign


class RAGEmbedder:
    """Pluggable embedding front-end with honest engine labeling.

    ``backend`` accepts: auto (default → tf for phase 1), tf, regex,
    transformers (host-side only; not runner-replicable yet).
    """

    def __init__(self, backend: str | None = None, model_name: str | None = None):
        from app.core.config import get_settings
        settings = get_settings()
        self._configured = (backend or settings.RAG_EMBEDDING_BACKEND or RAGEmbeddingEngine.AUTO).lower()
        if not RAGEmbeddingEngine.is_valid(self._configured):
            raise ValueError(
                f"Unknown RAG_EMBEDDING_BACKEND={self._configured!r}; "
                f"expected one of auto/tf/transformers/regex"
            )
        self._model_name = model_name or settings.RAG_EMBEDDING_MODEL
        self._transformers = None  # lazy-loaded (tokenizer, model)

    # ── public API ─────────────────────────────────────────────

    def resolve_engine(self) -> str:
        """The concrete engine that will run for this backend.

        ``auto`` resolves to ``tf`` for phase 1 so corpus and query vectors
        are reproducible inside the sandbox runner.
        """
        if self._configured == RAGEmbeddingEngine.AUTO:
            return RAGEmbeddingEngine.TF
        return self._configured

    def embed(self, text: str) -> EmbeddingResult:
        engine = self.resolve_engine()
        text = (text or "").strip()
        if not text:
            # Degenerate input → honest zero vector from the resolved engine.
            return EmbeddingResult(vector=[0.0] * TF_DIM, engine=engine, dim=TF_DIM)

        if engine == RAGEmbeddingEngine.TF:
            return EmbeddingResult(vector=tf_embed(text), engine=engine, dim=TF_DIM)
        if engine == RAGEmbeddingEngine.REGEX:
            return EmbeddingResult(vector=regex_embed(text), engine=engine, dim=TF_DIM)
        if engine == RAGEmbeddingEngine.TRANSFORMERS:
            vec = self._transformers_embed(text)
            return EmbeddingResult(vector=vec, engine=engine, dim=len(vec))
        raise EngineUnavailable(f"Embedding engine {engine!r} cannot run here")

    def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        return [self.embed(t) for t in texts]

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        return float(dot)  # vectors are L2-normalized

    # ── transformers backend (host-side; phase 2 path) ─────────

    def _load_transformers(self):
        if self._transformers is not None:
            return self._transformers
        try:
            from transformers import AutoModel, AutoTokenizer
        except Exception as e:  # pragma: no cover - env dependent
            raise EngineUnavailable(
                "transformers backend requested but transformers is not importable"
            ) from e
        try:
            tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            model = AutoModel.from_pretrained(self._model_name)
        except Exception as e:  # pragma: no cover - env dependent
            raise EngineUnavailable(
                f"Failed to load embedding model {self._model_name!r}: {e}"
            ) from e
        self._transformers = (tokenizer, model)
        return self._transformers

    def _transformers_embed(self, text: str) -> list[float]:  # pragma: no cover - env dependent
        import torch

        tokenizer, model = self._load_transformers()
        model.eval()
        inputs = tokenizer(
            text, return_tensors="pt", truncation=True, max_length=256, padding=True,
        )
        with torch.no_grad():
            outputs = model(**inputs)
        hidden = outputs.last_hidden_state  # (1, seq, hidden)
        mask = inputs["attention_mask"].unsqueeze(-1).float()
        summed = (hidden * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1.0)
        mean = (summed / counts)[0]
        norm = torch.linalg.vector_norm(mean)
        if norm > 0:
            mean = mean / norm
        return [float(x) for x in mean.tolist()]


# Singleton
rag_embedder = RAGEmbedder()
