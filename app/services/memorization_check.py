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

        # Count n-grams for reporting
        words = generated_text.split()
        total_ngrams = max(0, len(words) - self.ngram_size + 1)
        matched_ngrams = int(ratio * total_ngrams)

        is_memorized = ratio >= self.memorization_threshold

        # Determine risk level
        if ratio < 0.1:
            risk_level = "safe"
        elif ratio < 0.3:
            risk_level = "low"
        elif ratio < 0.5:
            risk_level = "medium"
        elif ratio < 0.8:
            risk_level = "high"
        else:
            risk_level = "critical"

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
        """
        if not validation_perplexities:
            return 10.0  # Default

        sorted_ppl = sorted(validation_perplexities)
        idx = int(len(sorted_ppl) * percentile / 100)
        idx = min(idx, len(sorted_ppl) - 1)
        return sorted_ppl[idx]


# Singleton
memorization_checker = MemorizationChecker()
