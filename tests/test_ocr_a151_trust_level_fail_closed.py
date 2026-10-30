"""A151 — `TrustEvaluator.can_access()` 对不认识的信任等级必须 fail-closed。

OCR ledger: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A151.

旧写法 `level_order.get(trust_level, 0) >= level_order.get(required_level, 0)`
把「不认识」当成 0 级：缺失或拼错的 `required_level` 等于**不要求任何信任**，
与同文件 `evaluate()` 的保守默认相反。修法是先归一（`TrustLevel(x)`）再比较，
归一失败一律拒绝。注意：`TrustLevel` 是 `str, Enum` 且 `str.__hash__` 在 MRO 里
排在 `Enum.__hash__` 前面，所以**合法**的字符串入参（`"gold"`）本来就命中，
本条只针对**不合法**入参。
"""
import pytest

from app.services.trust_evaluator import TrustEvaluator, TrustLevel


@pytest.fixture()
def evaluator() -> TrustEvaluator:
    return TrustEvaluator()


class TestUnknownLevelDenied:
    """不认识的等级（缺失/拼错/大小写变体）不得放行。"""

    def test_misspelled_required_level_denied(self, evaluator):
        """拼错的 required_level 不得被当成 0 级（= 不要求信任）。"""
        assert evaluator.can_access(TrustLevel.PLATINUM, "gold_platinum") is False

    def test_missing_required_level_denied(self, evaluator):
        """required_level 为 None（配置缺失）必须拒绝，而不是放行。"""
        assert evaluator.can_access(TrustLevel.GOLD, None) is False

    def test_unknown_subject_level_denied(self, evaluator):
        """不认识的调用方等级不得落到 NEW 拿到 NEW 的权限。"""
        assert evaluator.can_access("platinumx", TrustLevel.NEW) is False

    def test_uppercase_variant_not_treated_as_no_requirement(self, evaluator):
        """`"PLATINUM"` 不是合法等级值：旧写法 dict-miss → 0 → 等于不要求。"""
        assert evaluator.can_access("platinum", "PLATINUM") is False

    def test_int_level_denied(self, evaluator):
        """数字等级（越界值）必须拒绝，不能被静默当成最低信任。"""
        assert evaluator.can_access(TrustLevel.PLATINUM, 9) is False


class TestValidLevelsPreserved:
    """归一化不得改变已登记等级的判定（防过度收紧）。"""

    @pytest.mark.parametrize(
        "subject,required,expected",
        [
            (TrustLevel.PLATINUM, TrustLevel.PLATINUM, True),
            (TrustLevel.PLATINUM, TrustLevel.GOLD, True),
            (TrustLevel.GOLD, TrustLevel.GOLD, True),
            (TrustLevel.GOLD, TrustLevel.SILVER, True),
            (TrustLevel.SILVER, TrustLevel.GOLD, False),
            (TrustLevel.NEW, TrustLevel.NEW, True),
            (TrustLevel.NEW, TrustLevel.SILVER, False),
            (TrustLevel.NEW, TrustLevel.PLATINUM, False),
        ],
    )
    def test_enum_matrix(self, evaluator, subject, required, expected):
        assert evaluator.can_access(subject, required) is expected

    @pytest.mark.parametrize(
        "subject,required,expected",
        [
            ("platinum", "gold", True),
            ("gold", "gold", True),
            ("silver", "gold", False),
            ("new", "gold", False),
        ],
    )
    def test_wire_string_levels(self, evaluator, subject, required, expected):
        """`evaluate_trust_score()` 回传的是 `level.value` 字符串，必须照常可比。"""
        assert evaluator.can_access(subject, required) is expected

    def test_round_trip_with_evaluate(self, evaluator):
        """evaluate() 的产物直接喂 can_access() 必须成立（活路径形状）。"""
        score = evaluator.evaluate(
            data_quality_score=95,
            compliance_score=95,
            reputation_score=95,
            security_score=95,
        )
        assert evaluator.can_access(score.level.value, "gold") is True
        assert evaluator.can_access(score.level.value, "platinum") is True
