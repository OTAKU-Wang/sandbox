"""Trust Evaluator — Four-level trust scoring for data products and users.

Implements a composite trust scoring system:
- PLATINUM: Verified, high-quality, fully compliant
- GOLD: Trusted, good track record, minor issues
- SILVER: New or limited history, basic compliance
- NEW: Unverified, no track record

Trust scores are computed from multiple signals:
- Data quality metrics (completeness, accuracy, freshness)
- Compliance history (audit logs, violations)
- Provider reputation (track record, certifications)
- Security posture (encryption, access controls)
"""
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class TrustLevel(str, Enum):
    PLATINUM = "platinum"  # 90-100
    GOLD = "gold"          # 70-89
    SILVER = "silver"      # 50-69
    NEW = "new"            # 0-49


@dataclass
class TrustScore:
    """Trust assessment result."""
    level: TrustLevel
    score: int  # 0-100
    data_quality: int  # 0-100
    compliance: int  # 0-100
    reputation: int  # 0-100
    security: int  # 0-100
    factors: list[str]  # Contributing factors


class TrustEvaluator:
    """Composite trust scoring engine.

    Computes trust scores from multiple dimensions and assigns
    a trust level (PLATINUM/GOLD/SILVER/NEW).
    """

    # Weights for each dimension
    WEIGHTS = {
        "data_quality": 0.30,
        "compliance": 0.30,
        "reputation": 0.20,
        "security": 0.20,
    }

    # Level thresholds
    THRESHOLDS = {
        TrustLevel.PLATINUM: 90,
        TrustLevel.GOLD: 70,
        TrustLevel.SILVER: 50,
        TrustLevel.NEW: 0,
    }

    def evaluate(
        self,
        data_quality_score: int = 50,
        compliance_score: int = 50,
        reputation_score: int = 50,
        security_score: int = 50,
        violations: int = 0,
        certifications: list[str] | None = None,
        data_age_days: int | None = None,
        encryption_enabled: bool = False,
    ) -> TrustScore:
        """Compute trust score from multiple signals.

        Args:
            data_quality_score: Base data quality score (0-100).
            compliance_score: Base compliance score (0-100).
            reputation_score: Base reputation score (0-100).
            security_score: Base security score (0-100).
            violations: Number of policy violations (reduces compliance).
            certifications: List of certifications (boosts reputation).
            data_age_days: Age of data in days (affects quality).
            encryption_enabled: Whether data is encrypted (boosts security).

        Returns:
            TrustScore with level and component scores.
        """
        factors = []

        # Adjust compliance based on violations
        compliance = compliance_score
        if violations > 0:
            penalty = min(violations * 10, 50)
            compliance = max(0, compliance - penalty)
            factors.append(f"violations({violations}) reduced compliance by {penalty}")

        # Adjust reputation based on certifications
        reputation = reputation_score
        if certifications:
            cert_bonus = min(len(certifications) * 5, 20)
            reputation = min(100, reputation + cert_bonus)
            factors.append(f"certifications({len(certifications)}) boosted reputation by {cert_bonus}")

        # Adjust data quality based on age
        quality = data_quality_score
        if data_age_days is not None:
            if data_age_days > 365:
                age_penalty = min((data_age_days - 365) // 30 * 2, 20)
                quality = max(0, quality - age_penalty)
                factors.append(f"data_age({data_age_days}d) reduced quality by {age_penalty}")
            elif data_age_days < 7:
                quality = min(100, quality + 5)
                factors.append("fresh data boosted quality")

        # Adjust security based on encryption
        security = security_score
        if encryption_enabled:
            security = min(100, security + 10)
            factors.append("encryption boosted security")

        # Compute weighted score
        scores = {
            "data_quality": quality,
            "compliance": compliance,
            "reputation": reputation,
            "security": security,
        }

        total = sum(scores[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
        total = int(min(100, max(0, total)))

        # Determine level
        level = TrustLevel.NEW
        for trust_level in [TrustLevel.PLATINUM, TrustLevel.GOLD, TrustLevel.SILVER]:
            if total >= self.THRESHOLDS[trust_level]:
                level = trust_level
                break

        logger.info(f"Trust evaluation: {level.value} ({total}/100) — {', '.join(factors[:3])}")

        return TrustScore(
            level=level,
            score=total,
            data_quality=quality,
            compliance=compliance,
            reputation=reputation,
            security=security,
            factors=factors,
        )

    def evaluate_provider(
        self,
        provider_id: str,
        total_products: int = 0,
        avg_quality: float = 0.0,
        violations: int = 0,
        certifications: list[str] | None = None,
    ) -> TrustScore:
        """Evaluate trust for a data provider.

        Args:
            provider_id: Provider identifier.
            total_products: Number of data products published.
            avg_quality: Average quality score across products.
            violations: Total policy violations.
            certifications: Provider certifications.

        Returns:
            TrustScore for the provider.
        """
        # Reputation based on track record
        reputation = 50
        if total_products > 0:
            reputation = min(100, 50 + total_products * 2)

        # Compliance based on violations
        compliance = max(0, 100 - violations * 15)

        return self.evaluate(
            data_quality_score=int(avg_quality),
            compliance_score=compliance,
            reputation_score=reputation,
            violations=violations,
            certifications=certifications,
        )

    def can_access(
        self,
        trust_level: TrustLevel,
        required_level: TrustLevel,
    ) -> bool:
        """Check if a trust level meets the required level.

        Args:
            trust_level: Current trust level.
            required_level: Required trust level for access.

        Returns:
            True if access is allowed.
        """
        level_order = {
            TrustLevel.NEW: 0,
            TrustLevel.SILVER: 1,
            TrustLevel.GOLD: 2,
            TrustLevel.PLATINUM: 3,
        }
        return level_order.get(trust_level, 0) >= level_order.get(required_level, 0)


# Singleton
trust_evaluator = TrustEvaluator()
