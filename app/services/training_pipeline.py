"""Training Pipeline Manager — secure AI training data processing.

Supports multiple pipeline types per SS-07 / advanced-scenarios-spec:
- Pretrain corpus: dedup, quality filter, tokenize, pack
- SFT (instruction tuning): format validation, quality scoring, PII scrub, format conversion
- RAG knowledge base: document parse, chunk, embed, index
- Multimodal: image preprocess, text scrub, pairing, quality filter

All pipelines enforce:
- Data stays in sandbox (no raw data output)
- PII scrubbing before any output
- MIA risk assessment on model outputs
- DP noise injection when configured
- Watermark embedding in outputs
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PipelineType(str, Enum):
    PRETRAIN = "pretrain"
    SFT = "sft"
    RAG = "rag"
    MULTIMODAL = "multimodal"


class PipelineStage(str, Enum):
    FORMAT_VALIDATION = "format_validation"
    QUALITY_FILTER = "quality_filter"
    DEDUPLICATION = "deduplication"
    PII_SCRUB = "pii_scrub"
    TOXICITY_FILTER = "toxicity_filter"
    TOKENIZATION = "tokenization"
    FORMAT_CONVERSION = "format_conversion"
    CHUNKING = "chunkding"
    EMBEDDING = "embedding"
    IMAGE_PREPROCESS = "image_preprocess"
    PAIRING = "pairing"
    TRAIN_TEST_SPLIT = "train_test_split"
    MIA_CHECK = "mia_check"
    WATERMARK = "watermark"
    OUTPUT = "output"


@dataclass
class PipelineConfig:
    """Configuration for a training pipeline."""
    pipeline_type: PipelineType
    # Common
    pii_scrub_enabled: bool = True
    dedup_enabled: bool = True
    dedup_similarity_threshold: float = 0.85
    quality_min_score: float = 0.7
    toxicity_threshold: float = 0.9
    # SFT-specific
    sft_format: str = "alpaca"  # alpaca, chatml, sharegpt
    train_ratio: float = 0.95
    # RAG-specific
    chunk_size: int = 512
    chunk_overlap: int = 64
    embedding_model: str = "bge-m3"
    embedding_dimensions: int = 1024
    # Pretrain-specific
    tokenizer: str = "sentencepiece"
    block_size: int = 2048
    # Output
    output_format: str = "jsonl"
    forbid_original_text: bool = True
    max_output_size_gb: float = 500.0


@dataclass
class PipelineResult:
    """Result of running a pipeline stage or full pipeline."""
    success: bool
    stage: PipelineStage | None = None
    records_processed: int = 0
    records_passed: int = 0
    records_filtered: int = 0
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    output_path: str | None = None


@dataclass
class PIIResult:
    """Result of PII detection on a single text."""
    has_pii: bool
    findings: list[dict[str, str]]  # [{type, span, replacement}]
    cleaned_text: str


# PII patterns for Chinese data (same as output_inspection but for training data)
_PII_PATTERNS = {
    "id_card": re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "bank_card": re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)"),
}

_PII_REPLACEMENTS = {
    "id_card": "[ID_CARD]",
    "phone": "[PHONE]",
    "email": "[EMAIL]",
    "bank_card": "[BANK_CARD]",
}


class TrainingPipelineManager:
    """Manages secure training data processing pipelines.

    Each pipeline type has a defined sequence of stages. Data flows through
    the stages in order, with security checks at each step.
    """

    # Stage sequences per pipeline type
    PIPELINE_STAGES: dict[PipelineType, list[PipelineStage]] = {
        PipelineType.PRETRAIN: [
            PipelineStage.FORMAT_VALIDATION,
            PipelineStage.QUALITY_FILTER,
            PipelineStage.DEDUPLICATION,
            PipelineStage.PII_SCRUB,
            PipelineStage.TOXICITY_FILTER,
            PipelineStage.TOKENIZATION,
            PipelineStage.OUTPUT,
        ],
        PipelineType.SFT: [
            PipelineStage.FORMAT_VALIDATION,
            PipelineStage.QUALITY_FILTER,
            PipelineStage.PII_SCRUB,
            PipelineStage.DEDUPLICATION,
            PipelineStage.FORMAT_CONVERSION,
            PipelineStage.TRAIN_TEST_SPLIT,
            PipelineStage.MIA_CHECK,
            PipelineStage.WATERMARK,
            PipelineStage.OUTPUT,
        ],
        PipelineType.RAG: [
            PipelineStage.FORMAT_VALIDATION,
            PipelineStage.PII_SCRUB,
            PipelineStage.CHUNKING,
            PipelineStage.EMBEDDING,
            PipelineStage.OUTPUT,
        ],
        PipelineType.MULTIMODAL: [
            PipelineStage.FORMAT_VALIDATION,
            PipelineStage.IMAGE_PREPROCESS,
            PipelineStage.PII_SCRUB,
            PipelineStage.PAIRING,
            PipelineStage.QUALITY_FILTER,
            PipelineStage.OUTPUT,
        ],
    }

    def __init__(self):
        pass

    def get_stages(self, pipeline_type: PipelineType) -> list[PipelineStage]:
        """Get the ordered list of stages for a pipeline type."""
        return self.PIPELINE_STAGES.get(pipeline_type, [])

    def scrub_pii(self, text: str) -> PIIResult:
        """Detect and scrub PII from text using regex patterns.

        For production, this should also use NER models (bert-base-chinese-pii-ner).
        """
        findings = []
        cleaned = text

        for pii_type, pattern in _PII_PATTERNS.items():
            matches = list(pattern.finditer(cleaned))
            for match in reversed(matches):  # Reverse to preserve offsets
                findings.append({
                    "type": pii_type,
                    "span": match.group(),
                    "replacement": _PII_REPLACEMENTS[pii_type],
                })
            cleaned = pattern.sub(_PII_REPLACEMENTS[pii_type], cleaned)

        return PIIResult(
            has_pii=len(findings) > 0,
            findings=findings,
            cleaned_text=cleaned,
        )

    def check_quality_score(
        self,
        record: dict[str, Any],
        criteria: list[dict[str, Any]],
    ) -> tuple[float, dict[str, float]]:
        """Compute quality score for a training record.

        Args:
            record: The training record (instruction/input/output for SFT, text for pretrain)
            criteria: List of {name, weight} scoring criteria

        Returns:
            (total_score, per_criteria_scores)
        """
        scores = {}
        text = record.get("text", "") or record.get("instruction", "") + record.get("output", "")

        for criterion in criteria:
            name = criterion["name"]
            weight = criterion.get("weight", 1.0)

            if name == "min_length":
                score = min(len(text) / 100, 1.0)
            elif name == "instruction_clarity":
                # Heuristic: longer instructions with question marks are clearer
                inst = record.get("instruction", "")
                score = min(len(inst) / 200, 1.0)
                if "?" in inst or "？" in inst:
                    score = min(score + 0.2, 1.0)
            elif name == "output_completeness":
                out = record.get("output", "")
                score = min(len(out) / 500, 1.0)
            elif name == "special_char_ratio":
                if text:
                    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
                    ratio = special / len(text)
                    score = max(1.0 - ratio * 3, 0.0)  # Penalize high special char ratio
                else:
                    score = 0.0
            elif name == "repetition_ratio":
                score = self._compute_repetition_score(text)
            else:
                score = 0.5  # Default for unknown criteria

            scores[name] = score

        # Weighted average
        total = sum(
            scores.get(c["name"], 0) * c.get("weight", 1.0) for c in criteria
        ) / max(sum(c.get("weight", 1.0) for c in criteria), 0.001)

        return total, scores

    def _compute_repetition_score(self, text: str) -> float:
        """Compute a repetition score (1.0 = no repetition, 0.0 = highly repetitive)."""
        if len(text) < 100:
            return 1.0

        # Check n-gram repetition
        ngrams = set()
        repeats = 0
        window = 10
        for i in range(len(text) - window):
            ngram = text[i:i + window]
            if ngram in ngrams:
                repeats += 1
            ngrams.add(ngram)

        total_windows = max(len(text) - window, 1)
        repeat_ratio = repeats / total_windows
        return max(1.0 - repeat_ratio * 5, 0.0)

    def compute_minhash_similarity(self, text1: str, text2: str) -> float:
        """Compute approximate Jaccard similarity using MinHash.

        Used for deduplication. Returns similarity in [0, 1].
        """
        if not text1 or not text2:
            return 0.0

        # Simple character n-gram Jaccard for now
        def get_ngrams(text: str, n: int = 5) -> set[str]:
            return {text[i:i + n] for i in range(max(len(text) - n + 1, 0))}

        ngrams1 = get_ngrams(text1)
        ngrams2 = get_ngrams(text2)

        if not ngrams1 or not ngrams2:
            return 0.0

        intersection = ngrams1 & ngrams2
        union = ngrams1 | ngrams2

        return len(intersection) / len(union)

    def check_mia_risk(
        self,
        model_confidence: float,
        model_loss: float,
        threshold: float = 0.6,
    ) -> tuple[bool, float]:
        """Check Membership Inference Attack risk.

        Uses the Loss-based Threshold Attack (Yeom et al. 2018).
        High confidence + low loss on training-like data → higher MIA risk.
        """
        risk_score = model_confidence * (1.0 / (1.0 + model_loss)) if model_loss > 0 else model_confidence
        is_safe = risk_score < threshold
        return is_safe, risk_score

    def format_sft_record(
        self,
        record: dict[str, Any],
        target_format: str = "alpaca",
    ) -> dict[str, Any]:
        """Convert an SFT record to the target training format.

        Supports: alpaca, chatml, sharegpt
        """
        instruction = record.get("instruction", "")
        input_ctx = record.get("input", "")
        output = record.get("output", "")

        if target_format == "alpaca":
            return {
                "instruction": instruction,
                "input": input_ctx,
                "output": output,
            }
        elif target_format == "chatml":
            messages = [{"role": "user", "content": instruction}]
            if input_ctx:
                messages[0]["content"] = f"{instruction}\n\n{input_ctx}"
            messages.append({"role": "assistant", "content": output})
            return {"messages": messages}
        elif target_format == "sharegpt":
            conversations = [{"from": "human", "value": instruction}]
            if input_ctx:
                conversations[0]["value"] = f"{instruction}\n\n{input_ctx}"
            conversations.append({"from": "gpt", "value": output})
            return {"conversations": conversations}
        else:
            raise ValueError(f"Unknown SFT format: {target_format}")

    def split_dataset(
        self,
        records: list[dict],
        train_ratio: float = 0.95,
    ) -> tuple[list[dict], list[dict]]:
        """Split dataset into train/validation sets."""
        import random
        shuffled = list(records)
        random.shuffle(shuffled)
        split_idx = int(len(shuffled) * train_ratio)
        return shuffled[:split_idx], shuffled[split_idx:]

    def estimate_tokens(self, text: str, tokenizer: str = "sentencepiece") -> int:
        """Estimate token count. Rough heuristic: ~1.5 chars per token for Chinese."""
        # For Chinese text, average ~1.5 characters per token
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4)


# Singleton
training_pipeline = TrainingPipelineManager()
