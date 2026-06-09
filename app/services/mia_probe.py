"""MIA Probe — Membership Inference Attack detection for trained models.

Implements shadow-model-style MIA detection:
1. Loss-based: members tend to have lower loss than non-members
2. Confidence-based: members tend to have higher prediction confidence
3. Perplexity-based: members tend to have lower perplexity (text models)
4. Threshold calibration: dynamic p95 threshold from validation set

Reference: Shokri et al. (2017) "Membership Inference Attacks Against ML Models"
           Carlini et al. (2022) "Membership Inference Attacks From First Principles"
"""
import logging
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class MIARiskLevel(str, Enum):
    SAFE = "safe"           # risk_score < 0.3
    LOW = "low"             # 0.3 <= risk_score < 0.5
    MEDIUM = "medium"       # 0.5 <= risk_score < 0.7
    HIGH = "high"           # 0.7 <= risk_score < 0.9
    CRITICAL = "critical"   # risk_score >= 0.9


@dataclass
class MIASampleResult:
    """MIA risk assessment for a single sample."""
    sample_id: str
    loss: float
    confidence: float
    perplexity: float | None = None
    risk_score: float = 0.0
    is_member_suspected: bool = False


@dataclass
class MIAResult:
    """Aggregate MIA risk assessment for a model/dataset."""
    risk_level: MIARiskLevel
    risk_score: float
    threshold: float
    total_samples: int
    suspected_members: int
    member_ratio: float
    mean_loss_members: float = 0.0
    mean_loss_non_members: float = 0.0
    loss_separation: float = 0.0
    sample_results: list[MIASampleResult] = field(default_factory=list)


class MIAProbe:
    """Membership Inference Attack probe.

    Detects whether a model has memorized training data by analyzing
    loss distributions, confidence patterns, and perplexity scores.
    """

    def __init__(
        self,
        loss_threshold_percentile: float = 95.0,
        confidence_threshold: float = 0.95,
        perplexity_threshold: float = 10.0,
    ):
        self.loss_threshold_percentile = loss_threshold_percentile
        self.confidence_threshold = confidence_threshold
        self.perplexity_threshold = perplexity_threshold

    def calibrate_threshold(
        self,
        validation_losses: list[float],
    ) -> float:
        """Calibrate MIA threshold from validation set losses.

        Uses p95 of validation losses as the decision boundary.
        Samples with loss below this threshold are suspected members.

        Args:
            validation_losses: Loss values on a known non-member validation set.

        Returns:
            Calibrated loss threshold.
        """
        if not validation_losses:
            return 0.5  # Default threshold

        sorted_losses = sorted(validation_losses)
        idx = int(len(sorted_losses) * self.loss_threshold_percentile / 100)
        idx = min(idx, len(sorted_losses) - 1)
        threshold = sorted_losses[idx]

        logger.info(f"MIA threshold calibrated: p{self.loss_threshold_percentile} = {threshold:.4f} "
                     f"(from {len(sorted_losses)} validation samples)")
        return threshold

    def analyze_sample(
        self,
        sample_id: str,
        loss: float,
        confidence: float,
        perplexity: float | None = None,
        loss_threshold: float | None = None,
    ) -> MIASampleResult:
        """Analyze a single sample for MIA risk.

        Args:
            sample_id: Unique identifier for the sample.
            loss: Model loss on this sample (lower = more memorized).
            confidence: Model prediction confidence (0-1, higher = more memorized).
            perplexity: Perplexity score for text models (lower = more memorized).
            loss_threshold: Calibrated loss threshold. If None, uses heuristic.

        Returns:
            MIASampleResult with risk assessment.
        """
        if loss_threshold is None:
            loss_threshold = 0.5

        # Component scores (each 0-1, higher = more likely member)
        # Loss score: sigmoid-like mapping where loss < threshold → high score
        if loss_threshold > 0:
            ratio = loss / loss_threshold
            if ratio <= 1.0:
                loss_score = 1.0 - ratio * 0.5  # loss <= threshold → score 0.5-1.0
            else:
                loss_score = max(0, 1.0 / (1.0 + (ratio - 1.0) * 3))  # loss > threshold → decaying
        else:
            loss_score = 0.0
        confidence_score = max(0, (confidence - 0.5) * 2)  # Map 0.5-1.0 to 0-1

        perplexity_score = 0.0
        if perplexity is not None and perplexity > 0:
            # Lower perplexity = higher membership probability
            perplexity_score = max(0, 1.0 - (perplexity / self.perplexity_threshold))

        # Weighted combination
        weights = [0.5, 0.3, 0.2]  # loss, confidence, perplexity
        scores = [loss_score, confidence_score, perplexity_score]

        if perplexity is None:
            # No perplexity available, redistribute weight
            weights = [0.6, 0.4]
            scores = [loss_score, confidence_score]

        risk_score = sum(w * s for w, s in zip(weights, scores))
        risk_score = min(1.0, max(0.0, risk_score))

        # Member suspected if risk exceeds 0.5
        is_member = risk_score >= 0.5

        return MIASampleResult(
            sample_id=sample_id,
            loss=loss,
            confidence=confidence,
            perplexity=perplexity,
            risk_score=risk_score,
            is_member_suspected=is_member,
        )

    def analyze_batch(
        self,
        samples: list[dict],
        validation_losses: list[float] | None = None,
    ) -> MIAResult:
        """Analyze a batch of samples for MIA risk.

        Args:
            samples: List of dicts with keys: sample_id, loss, confidence, [perplexity].
            validation_losses: Optional validation set for threshold calibration.

        Returns:
            MIAResult with aggregate risk assessment.
        """
        # Calibrate threshold
        if validation_losses:
            threshold = self.calibrate_threshold(validation_losses)
        else:
            # Heuristic: use p75 of sample losses (higher than median to catch low-loss members)
            losses = sorted([s["loss"] for s in samples])
            if losses:
                idx = int(len(losses) * 0.75)
                idx = min(idx, len(losses) - 1)
                threshold = losses[idx]
            else:
                threshold = 0.5

        # Analyze each sample
        results = []
        for sample in samples:
            result = self.analyze_sample(
                sample_id=sample.get("sample_id", "unknown"),
                loss=sample["loss"],
                confidence=sample["confidence"],
                perplexity=sample.get("perplexity"),
                loss_threshold=threshold,
            )
            results.append(result)

        if not results:
            return MIAResult(
                risk_level=MIARiskLevel.SAFE,
                risk_score=0.0,
                threshold=threshold,
                total_samples=0,
                suspected_members=0,
                member_ratio=0.0,
            )

        # Aggregate statistics
        suspected = [r for r in results if r.is_member_suspected]
        member_losses = [r.loss for r in results if r.is_member_suspected]
        non_member_losses = [r.loss for r in results if not r.is_member_suspected]

        mean_loss_members = statistics.mean(member_losses) if member_losses else 0.0
        mean_loss_non_members = statistics.mean(non_member_losses) if non_member_losses else 0.0
        loss_separation = mean_loss_non_members - mean_loss_members

        # Overall risk score: combination of member ratio and loss separation
        member_ratio = len(suspected) / len(results) if results else 0.0
        overall_risk = min(1.0, member_ratio * 1.5 + (1.0 / (1.0 + loss_separation)) * 0.5)

        # Determine risk level
        if overall_risk < 0.3:
            risk_level = MIARiskLevel.SAFE
        elif overall_risk < 0.5:
            risk_level = MIARiskLevel.LOW
        elif overall_risk < 0.7:
            risk_level = MIARiskLevel.MEDIUM
        elif overall_risk < 0.9:
            risk_level = MIARiskLevel.HIGH
        else:
            risk_level = MIARiskLevel.CRITICAL

        logger.warning(
            f"MIA probe: risk={risk_level.value} ({overall_risk:.3f}), "
            f"suspected_members={len(suspected)}/{len(results)}, "
            f"loss_separation={loss_separation:.4f}"
        )

        return MIAResult(
            risk_level=risk_level,
            risk_score=overall_risk,
            threshold=threshold,
            total_samples=len(results),
            suspected_members=len(suspected),
            member_ratio=member_ratio,
            mean_loss_members=mean_loss_members,
            mean_loss_non_members=mean_loss_non_members,
            loss_separation=loss_separation,
            sample_results=results,
        )

    def check_text_memorization(
        self,
        generated_text: str,
        reference_texts: list[str],
        ngram_size: int = 4,
    ) -> tuple[float, list[str]]:
        """Check if generated text contains memorized sequences from reference.

        Uses n-gram overlap detection to find verbatim or near-verbatim
        memorization from training data.

        Args:
            generated_text: The model's generated output.
            reference_texts: Known training/reference texts to check against.
            ngram_size: Size of n-grams for overlap detection.

        Returns:
            (memorization_ratio, matched_snippets) — ratio of matched n-grams.
        """
        def get_ngrams(text: str, n: int) -> set[tuple[str, ...]]:
            words = text.split()
            return {tuple(words[i:i+n]) for i in range(len(words) - n + 1)}

        gen_ngrams = get_ngrams(generated_text, ngram_size)
        if not gen_ngrams:
            return 0.0, []

        matched_snippets = []
        total_matched = 0

        for ref in reference_texts:
            ref_ngrams = get_ngrams(ref, ngram_size)
            overlap = gen_ngrams & ref_ngrams
            if overlap:
                total_matched += len(overlap)
                # Extract the matched region
                for ngram in list(overlap)[:3]:  # Top 3 matches
                    snippet = " ".join(ngram)
                    matched_snippets.append(snippet)

        memorization_ratio = total_matched / len(gen_ngrams) if gen_ngrams else 0.0
        return memorization_ratio, matched_snippets


# Singleton
mia_probe = MIAProbe()
