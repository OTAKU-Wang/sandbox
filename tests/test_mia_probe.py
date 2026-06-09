"""Tests for MIA Probe — Membership Inference Attack detection."""
import pytest
from app.services.mia_probe import MIAProbe, MIARiskLevel, mia_probe


class TestMIACalibration:
    """Threshold calibration tests."""

    def test_calibrate_threshold_basic(self):
        """Calibration returns a valid threshold from validation losses."""
        probe = MIAProbe()
        losses = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        threshold = probe.calibrate_threshold(losses)
        assert 0.0 < threshold <= 1.0

    def test_calibrate_threshold_p95(self):
        """Calibration uses p95 by default."""
        probe = MIAProbe(loss_threshold_percentile=95.0)
        losses = [float(i) / 100 for i in range(100)]
        threshold = probe.calibrate_threshold(losses)
        # p95 of [0.00, 0.01, ..., 0.99] should be ~0.95
        assert 0.90 <= threshold <= 0.99

    def test_calibrate_threshold_empty(self):
        """Empty validation set returns default threshold."""
        probe = MIAProbe()
        threshold = probe.calibrate_threshold([])
        assert threshold == 0.5

    def test_calibrate_threshold_single_value(self):
        """Single validation sample returns that value."""
        probe = MIAProbe()
        threshold = probe.calibrate_threshold([0.42])
        assert threshold == 0.42


class TestMIASampleAnalysis:
    """Per-sample MIA risk analysis tests."""

    def test_low_risk_sample(self):
        """High loss + low confidence = low risk (non-member)."""
        probe = MIAProbe()
        result = probe.analyze_sample(
            sample_id="test-1",
            loss=2.0,  # High loss
            confidence=0.3,  # Low confidence
            loss_threshold=0.5,
        )
        assert result.risk_score < 0.5
        assert not result.is_member_suspected

    def test_high_risk_sample(self):
        """Low loss + high confidence = high risk (suspected member)."""
        probe = MIAProbe()
        result = probe.analyze_sample(
            sample_id="test-2",
            loss=0.01,  # Very low loss
            confidence=0.99,  # Very high confidence
            loss_threshold=0.5,
        )
        assert result.risk_score >= 0.5
        assert result.is_member_suspected

    def test_medium_risk_sample(self):
        """Moderate loss + moderate confidence = medium risk."""
        probe = MIAProbe()
        result = probe.analyze_sample(
            sample_id="test-3",
            loss=0.3,
            confidence=0.7,
            loss_threshold=0.5,
        )
        assert 0.0 < result.risk_score < 1.0

    def test_perplexity_contributes_to_risk(self):
        """Low perplexity contributes to membership risk assessment."""
        probe = MIAProbe(perplexity_threshold=10.0)
        result = probe.analyze_sample(
            sample_id="test-4", loss=0.1, confidence=0.9,
            perplexity=2.0, loss_threshold=0.5,
        )
        # Low perplexity (2.0 vs threshold 10.0) should contribute positively
        assert result.perplexity == 2.0
        assert result.risk_score > 0.5  # Should still be high risk

    def test_risk_score_bounds(self):
        """Risk score is always between 0 and 1."""
        probe = MIAProbe()
        # Extreme values
        r1 = probe.analyze_sample("a", loss=0.0, confidence=1.0, loss_threshold=0.5)
        r2 = probe.analyze_sample("b", loss=100.0, confidence=0.0, loss_threshold=0.5)
        assert 0.0 <= r1.risk_score <= 1.0
        assert 0.0 <= r2.risk_score <= 1.0


class TestMIABatchAnalysis:
    """Batch MIA analysis tests."""

    def test_batch_basic(self):
        """Batch analysis returns valid aggregate result."""
        probe = MIAProbe()
        samples = [
            {"sample_id": f"s{i}", "loss": 0.1 if i < 5 else 2.0, "confidence": 0.95 if i < 5 else 0.3}
            for i in range(10)
        ]
        result = probe.analyze_batch(samples)
        assert result.total_samples == 10
        assert 0.0 <= result.risk_score <= 1.0
        assert result.risk_level in MIARiskLevel

    def test_batch_with_validation(self):
        """Batch analysis with validation set calibration."""
        probe = MIAProbe()
        samples = [
            {"sample_id": "member", "loss": 0.01, "confidence": 0.99},
            {"sample_id": "non-member", "loss": 2.0, "confidence": 0.1},
        ]
        validation_losses = [0.5, 0.6, 0.7, 0.8, 0.9]
        result = probe.analyze_batch(samples, validation_losses=validation_losses)
        assert result.threshold > 0

    def test_batch_high_memorization(self):
        """Batch with mostly low-loss samples shows high risk."""
        probe = MIAProbe()
        # 80% suspected members (low loss, high confidence)
        samples = [
            {"sample_id": f"mem{i}", "loss": 0.01, "confidence": 0.98}
            for i in range(8)
        ] + [
            {"sample_id": f"non{i}", "loss": 2.0, "confidence": 0.1}
            for i in range(2)
        ]
        result = probe.analyze_batch(samples)
        assert result.member_ratio >= 0.5
        assert result.risk_level in (MIARiskLevel.HIGH, MIARiskLevel.CRITICAL)

    def test_batch_low_memorization(self):
        """Batch with mostly high-loss samples shows low risk."""
        probe = MIAProbe()
        samples = [
            {"sample_id": f"non{i}", "loss": 2.0, "confidence": 0.1}
            for i in range(10)
        ]
        result = probe.analyze_batch(samples)
        assert result.member_ratio < 0.3
        assert result.risk_level in (MIARiskLevel.SAFE, MIARiskLevel.LOW)

    def test_batch_empty(self):
        """Empty batch returns safe result."""
        probe = MIAProbe()
        result = probe.analyze_batch([])
        assert result.total_samples == 0
        assert result.risk_score == 0.0


class TestMIATextMemorization:
    """Text memorization detection tests."""

    def test_detects_verbatim_copy(self):
        """Detects verbatim memorization from reference text."""
        probe = MIAProbe()
        generated = "the quick brown fox jumps over the lazy dog"
        references = ["the quick brown fox jumps over the lazy dog"]
        ratio, snippets = probe.check_text_memorization(generated, references, ngram_size=4)
        assert ratio > 0.5
        assert len(snippets) > 0

    def test_no_memorization(self):
        """No overlap with reference texts."""
        probe = MIAProbe()
        generated = "completely different text with no overlap at all"
        references = ["the quick brown fox jumps over the lazy dog"]
        ratio, snippets = probe.check_text_memorization(generated, references, ngram_size=4)
        assert ratio == 0.0

    def test_partial_memorization(self):
        """Partial overlap detected."""
        probe = MIAProbe()
        generated = "the quick brown fox runs through the forest"
        references = ["the quick brown fox jumps over the lazy dog"]
        ratio, snippets = probe.check_text_memorization(generated, references, ngram_size=3)
        # Should detect "the quick brown fox" overlap
        assert ratio > 0.0

    def test_empty_generated(self):
        """Empty generated text returns 0."""
        probe = MIAProbe()
        ratio, snippets = probe.check_text_memorization("", ["some reference"], ngram_size=4)
        assert ratio == 0.0

    def test_empty_references(self):
        """Empty reference list returns 0."""
        probe = MIAProbe()
        ratio, snippets = probe.check_text_memorization("some text", [], ngram_size=4)
        assert ratio == 0.0


class TestMIARiskLevels:
    """Risk level classification tests."""

    def test_risk_level_enum_values(self):
        """All risk levels have correct string values."""
        assert MIARiskLevel.SAFE.value == "safe"
        assert MIARiskLevel.LOW.value == "low"
        assert MIARiskLevel.MEDIUM.value == "medium"
        assert MIARiskLevel.HIGH.value == "high"
        assert MIARiskLevel.CRITICAL.value == "critical"

    def test_singleton_probe_exists(self):
        """Module-level singleton probe is available."""
        assert mia_probe is not None
        assert isinstance(mia_probe, MIAProbe)
