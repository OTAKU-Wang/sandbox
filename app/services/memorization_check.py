"""Memorization Check — Carlini-style text memorization detection.

Implements the memorization detection approach from:
  Carlini et al. (2022) "Quantifying Memorization Across Neural Language Models"

Key metrics:
- Loss-based: Members have lower average loss than non-members
- n-gram overlap: Verbatim memorization detection
- p95 threshold: Calibrated from non-member distribution

This module provides a simplified API wrapping the MIA probe's
text memorization capabilities.
"""
import logging
from dataclasses import dataclass, field

from app.services.mia_probe import mia_probe, MIARiskLevel

logger = logging.getLogger(__name__)


def _distinct_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    """The n-gram口径 shared with `mia_probe.check_text_memorization` (A159).

    The probe's denominator is the size of the DEDUPLICATED n-gram set, so any
    count reported next to its ratio must use exactly this function — counting
    word positions instead made `matched_ngrams` a different quantity than the
    one the ratio was computed from.
    """
    words = text.split()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _risk_level(ratio: float, threshold: float) -> str:
    """Risk bands anchored on the CONFIGURED threshold (A159).

    Hardcoded 0.1/0.3/0.5/0.8 bands contradicted `is_memorized`: at threshold
    0.8 a ratio of 0.45 was labelled medium while `is_memorized` said False,
    and at threshold 0.05 a memorized hit was still labelled safe. Callers read
    both fields, so the bands move with the threshold: below the decision line
    only `safe`/`low` are reachable, at/above it `medium`/`high`/`critical`.
    """
    if ratio < threshold:
        return "safe" if ratio < threshold / 2.0 else "low"
    remaining = max(0.0, 1.0 - threshold)
    if remaining == 0.0 or ratio < threshold + remaining / 3.0:
        return "medium"
    if ratio < threshold + 2.0 * remaining / 3.0:
        return "high"
    return "critical"


@dataclass
class MemorizationResult:
    """Result of memorization check on generated text."""
    is_memorized: bool
    memorization_ratio: float
    matched_snippets: list[str]
    risk_level: str
    threshold: float
    total_ngrams: int = 0
    matched_ngrams: int = 0


class MemorizationChecker:
    """Text memorization checker using Carlini's approach.

    Detects whether generated text contains memorized sequences
    from reference (training) data using n-gram overlap analysis.
    """

    def __init__(
        self,
        ngram_size: int = 4,
        memorization_threshold: float = 0.3,
    ):
        self.ngram_size = ngram_size
        self.memorization_threshold = memorization_threshold

    def check(
        self,
        generated_text: str,
        reference_texts: list[str],
    ) -> MemorizationResult:
        """Check if generated text contains memorized sequences.

        Args:
            generated_text: The model's generated output.
            reference_texts: Known training/reference texts to check against.

        Returns:
            MemorizationResult with memorization assessment.
        """
        if not generated_text or not reference_texts:
            return MemorizationResult(
                is_memorized=False,
                memorization_ratio=0.0,
                matched_snippets=[],
                risk_level="safe",
                threshold=self.memorization_threshold,
            )

        ratio, snippets = mia_probe.check_text_memorization(
            generated_text, reference_texts, self.ngram_size,
        )

        # A159: same denominator as the probe (distinct n-grams), so
        # matched_ngrams is the probe's real match count and not a scaled
        # position count. The probe's own denominator is len(distinct gen
        # n-grams); recomputing it here on a different口径 broke both fields.
        total_ngrams = len(_distinct_ngrams(generated_text, self.ngram_size))
        matched_ngrams = min(total_ngrams, int(round(ratio * total_ngrams)))

        is_memorized = ratio >= self.memorization_threshold

        risk_level = _risk_level(ratio, self.memorization_threshold)

        if is_memorized:
            logger.warning(
                f"Memorization detected: ratio={ratio:.3f} "
                f"(threshold={self.memorization_threshold}), "
                f"matched={matched_ngrams}/{total_ngrams} n-grams"
            )

        return MemorizationResult(
            is_memorized=is_memorized,
            memorization_ratio=ratio,
            matched_snippets=snippets,
            risk_level=risk_level,
            threshold=self.memorization_threshold,
            total_ngrams=total_ngrams,
            matched_ngrams=matched_ngrams,
        )

    def check_batch(
        self,
        generated_texts: list[str],
        reference_texts: list[str],
    ) -> list[MemorizationResult]:
        """Check multiple generated texts for memorization.

        Args:
            generated_texts: List of model outputs to check.
            reference_texts: Known training/reference texts.

        Returns:
            List of MemorizationResult for each generated text.
        """
        return [self.check(text, reference_texts) for text in generated_texts]

    def compute_perplexity_threshold(
        self,
        validation_perplexities: list[float],
        percentile: float = 95.0,
    ) -> float:
        """Compute perplexity threshold from validation set.

        Samples with perplexity below this threshold are suspected memorizations.

        Args:
            validation_perplexities: Perplexity scores on non-member validation set.
            percentile: Percentile for threshold (default p95).

        Returns:
            Perplexity threshold.

        Raises:
            ValueError: percentile outside [0, 100]. An unclamped negative
                percentile used to reach a NEGATIVE list index, i.e. a
                threshold near the MAXIMUM perplexity — the opposite direction,
                silently (A159).
        """
        if not (0.0 <= float(percentile) <= 100.0):
            raise ValueError(
                f"percentile must be within [0, 100], got {percentile!r}"
            )
        if not validation_perplexities:
            return 10.0  # Default

        sorted_ppl = sorted(validation_perplexities)
        idx = int(len(sorted_ppl) * float(percentile) / 100)
        idx = max(0, min(idx, len(sorted_ppl) - 1))
        return sorted_ppl[idx]


# Singleton
memorization_checker = MemorizationChecker()
