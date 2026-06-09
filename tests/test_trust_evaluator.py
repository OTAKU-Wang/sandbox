"""Tests for Trust Evaluator — Four-level trust scoring."""
import pytest
from app.services.trust_evaluator import TrustEvaluator, TrustLevel, trust_evaluator


class TestTrustLevelEnum:
    """TrustLevel enum tests."""

    def test_trust_level_values(self):
        assert TrustLevel.PLATINUM.value == "platinum"
        assert TrustLevel.GOLD.value == "gold"
        assert TrustLevel.SILVER.value == "silver"
        assert TrustLevel.NEW.value == "new"


class TestTrustEvaluation:
    """Trust score computation tests."""

    def test_high_scores_yield_platinum(self):
        """All high scores should yield PLATINUM."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate(
            data_quality_score=95,
            compliance_score=95,
            reputation_score=95,
            security_score=95,
        )
        assert result.level == TrustLevel.PLATINUM
        assert result.score >= 90

    def test_medium_scores_yield_gold(self):
        """Medium scores should yield GOLD."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate(
            data_quality_score=75,
            compliance_score=75,
            reputation_score=75,
            security_score=75,
        )
        assert result.level == TrustLevel.GOLD
        assert 70 <= result.score < 90

    def test_low_scores_yield_silver(self):
        """Low scores should yield SILVER."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate(
            data_quality_score=55,
            compliance_score=55,
            reputation_score=55,
            security_score=55,
        )
        assert result.level == TrustLevel.SILVER
        assert 50 <= result.score < 70

    def test_very_low_scores_yield_new(self):
        """Very low scores should yield NEW."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate(
            data_quality_score=20,
            compliance_score=20,
            reputation_score=20,
            security_score=20,
        )
        assert result.level == TrustLevel.NEW
        assert result.score < 50

    def test_default_scores(self):
        """Default scores should yield SILVER (50 across all dimensions)."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate()
        assert result.level == TrustLevel.SILVER
        assert result.score == 50

    def test_score_bounds(self):
        """Score is always between 0 and 100."""
        evaluator = TrustEvaluator()
        r1 = evaluator.evaluate(data_quality_score=0, compliance_score=0,
                                reputation_score=0, security_score=0)
        r2 = evaluator.evaluate(data_quality_score=100, compliance_score=100,
                                reputation_score=100, security_score=100)
        assert 0 <= r1.score <= 100
        assert 0 <= r2.score <= 100


class TestTrustAdjustments:
    """Trust score adjustment tests."""

    def test_violations_reduce_compliance(self):
        """Policy violations reduce compliance score."""
        evaluator = TrustEvaluator()
        clean = evaluator.evaluate(compliance_score=80, violations=0)
        dirty = evaluator.evaluate(compliance_score=80, violations=3)
        assert dirty.compliance < clean.compliance
        assert dirty.score < clean.score

    def test_certifications_boost_reputation(self):
        """Certifications boost reputation score."""
        evaluator = TrustEvaluator()
        no_cert = evaluator.evaluate(reputation_score=60, certifications=None)
        with_cert = evaluator.evaluate(reputation_score=60, certifications=["ISO27001", "SOC2"])
        assert with_cert.reputation > no_cert.reputation

    def test_old_data_reduces_quality(self):
        """Old data reduces quality score."""
        evaluator = TrustEvaluator()
        fresh = evaluator.evaluate(data_quality_score=80, data_age_days=30)
        old = evaluator.evaluate(data_quality_score=80, data_age_days=730)
        assert old.data_quality < fresh.data_quality

    def test_fresh_data_boosts_quality(self):
        """Very fresh data slightly boosts quality."""
        evaluator = TrustEvaluator()
        normal = evaluator.evaluate(data_quality_score=80, data_age_days=30)
        fresh = evaluator.evaluate(data_quality_score=80, data_age_days=3)
        assert fresh.data_quality >= normal.data_quality

    def test_encryption_boosts_security(self):
        """Encryption enabled boosts security score."""
        evaluator = TrustEvaluator()
        no_enc = evaluator.evaluate(security_score=70, encryption_enabled=False)
        with_enc = evaluator.evaluate(security_score=70, encryption_enabled=True)
        assert with_enc.security > no_enc.security

    def test_factors_recorded(self):
        """Adjustments are recorded in factors list."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate(
            violations=2,
            certifications=["ISO27001"],
            data_age_days=400,
            encryption_enabled=True,
        )
        assert len(result.factors) > 0


class TestProviderEvaluation:
    """Provider-specific trust evaluation tests."""

    def test_new_provider(self):
        """New provider with no track record gets NEW level."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate_provider("new-provider", total_products=0)
        assert result.level in (TrustLevel.NEW, TrustLevel.SILVER)

    def test_established_provider(self):
        """Provider with many products and good quality gets higher trust."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate_provider(
            "good-provider",
            total_products=20,
            avg_quality=85.0,
            violations=0,
            certifications=["ISO27001"],
        )
        assert result.level in (TrustLevel.GOLD, TrustLevel.PLATINUM)

    def test_provider_with_violations(self):
        """Provider with violations gets reduced trust."""
        evaluator = TrustEvaluator()
        result = evaluator.evaluate_provider(
            "bad-provider",
            total_products=5,
            avg_quality=70.0,
            violations=5,
        )
        assert result.compliance < 50


class TestAccessControl:
    """Trust-based access control tests."""

    def test_platinum_accesses_all(self):
        """PLATINUM can access all levels."""
        evaluator = TrustEvaluator()
        assert evaluator.can_access(TrustLevel.PLATINUM, TrustLevel.PLATINUM)
        assert evaluator.can_access(TrustLevel.PLATINUM, TrustLevel.GOLD)
        assert evaluator.can_access(TrustLevel.PLATINUM, TrustLevel.SILVER)
        assert evaluator.can_access(TrustLevel.PLATINUM, TrustLevel.NEW)

    def test_new_cannot_access_higher(self):
        """NEW cannot access higher levels."""
        evaluator = TrustEvaluator()
        assert evaluator.can_access(TrustLevel.NEW, TrustLevel.NEW)
        assert not evaluator.can_access(TrustLevel.NEW, TrustLevel.SILVER)
        assert not evaluator.can_access(TrustLevel.NEW, TrustLevel.GOLD)
        assert not evaluator.can_access(TrustLevel.NEW, TrustLevel.PLATINUM)

    def test_singleton_exists(self):
        """Module-level singleton is available."""
        assert trust_evaluator is not None
        assert isinstance(trust_evaluator, TrustEvaluator)
