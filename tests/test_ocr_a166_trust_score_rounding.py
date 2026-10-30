"""A166 — `evaluate()` 的加权信任分必须四舍五入，不得向零截断（会翻转准入）。

OCR ledger: docs/ocr-review-2026-10-18/ocr-verified-findings.md §A166.

旧写法 `total = int(min(100, max(0, total)))` 把 49.8 直接截成 49 ⇒ 等级从 SILVER
掉到 NEW；`federation_connector.py` 拿这个整数与 `_min_trust_by_op` 比较
（`if trust_result.score < min_score` → 403），所以截断是准入判断的一部分。
权重 0.30/0.30/0.20/0.20 ⇒ 精确加权分只有 0.1 的分辨率、不可能落在 .5，
因此定点整数 `(30·dq + 30·co + 20·rep + 20·sec + 50) // 100` 与 round() 等价且无浮点漂移。
"""
from fractions import Fraction

import pytest

from app.services.trust_evaluator import TrustEvaluator, TrustLevel

VALUES = [0, 7, 19, 23, 29, 41, 50, 59, 70, 83, 90, 100]

EXPECTED_LEVEL = [
    (90, TrustLevel.PLATINUM),
    (70, TrustLevel.GOLD),
    (50, TrustLevel.SILVER),
    (0, TrustLevel.NEW),
]


def exact_weighted(dq: int, co: int, rep: int, sec: int) -> Fraction:
    return (
        Fraction(dq) * Fraction("0.30")
        + Fraction(co) * Fraction("0.30")
        + Fraction(rep) * Fraction("0.20")
        + Fraction(sec) * Fraction("0.20")
    )


def exact_score(dq: int, co: int, rep: int, sec: int) -> int:
    """Independent oracle: exact weighted mean via Fractions, rounded half-up."""
    scaled = min(Fraction(100), max(Fraction(0), exact_weighted(dq, co, rep, sec))) * 2
    return int((scaled + 1) // 2)


def level_of(score: int) -> TrustLevel:
    for floor, level in EXPECTED_LEVEL:
        if score >= floor:
            return level
    return TrustLevel.NEW


@pytest.fixture()
def evaluator() -> TrustEvaluator:
    return TrustEvaluator()


class TestThresholdInversion:
    """截断跨过等级阈的具体输入（每个用例先用精确分数自证确实落在 `[floor-1, floor)`，否则跨阈断言是空跑）。"""

    @pytest.mark.parametrize(
        "case,floor,level",
        [
            ((23, 77, 0, 99), 50, TrustLevel.SILVER),
            ((85, 48, 64, 85), 70, TrustLevel.GOLD),
            ((97, 97, 79, 78), 90, TrustLevel.PLATINUM),
        ],
    )
    def test_truncation_must_not_cross_level_threshold(self, evaluator, case, floor, level):
        weighted = exact_weighted(*case)
        assert Fraction(floor - 1) <= weighted < Fraction(floor), case
        result = evaluator.evaluate(*case)
        assert result.score == floor
        assert result.level is level

    def test_ledger_example_is_exactly_fifty(self, evaluator):
        """ledger 举的 29/29/86/77 经复算精确等于 50.0（不跨阈），但旧浮点写法下仍须稳定为 50/SILVER。"""
        assert exact_weighted(29, 29, 86, 77) == 50
        result = evaluator.evaluate(29, 29, 86, 77)
        assert result.score == 50
        assert result.level is TrustLevel.SILVER


class TestFederationAdmissionShape:
    """信任分是准入判据：同一组信号不得因取整方式不同而跨阈。"""

    def test_score_is_never_below_exact_weighted_by_more_than_half(self, evaluator):
        dq, co, rep, sec = 23, 77, 0, 99
        exact = float(
            Fraction(dq) * Fraction("0.30")
            + Fraction(co) * Fraction("0.30")
            + Fraction(rep) * Fraction("0.20")
            + Fraction(sec) * Fraction("0.20")
        )
        result = evaluator.evaluate(dq, co, rep, sec)
        assert exact - result.score < 0.5
        assert result.score >= round(exact)


class TestExactRoundingSweep:
    """成对枚举：分数与等级都必须等于定点四舍五入的结果。"""

    @pytest.mark.parametrize("dq", VALUES)
    def test_sweep_matches_exact_oracle(self, evaluator, dq):
        for co in VALUES:
            for rep in VALUES:
                for sec in VALUES:
                    expected = exact_score(dq, co, rep, sec)
                    result = evaluator.evaluate(dq, co, rep, sec)
                    assert result.score == expected, (dq, co, rep, sec, result.score, expected)
                    assert result.level is level_of(expected), (dq, co, rep, sec, result.level)


class TestScoreBounds:
    """取整后分数仍被夹在 0..100。"""

    def test_zero_floor(self, evaluator):
        assert evaluator.evaluate(0, 0, 0, 0).score == 0

    def test_hundred_ceiling(self, evaluator):
        assert evaluator.evaluate(100, 100, 100, 100).score == 100

    def test_out_of_range_inputs_clamped(self, evaluator):
        assert evaluator.evaluate(500, 500, 500, 500).score == 100
        assert evaluator.evaluate(-50, -50, -50, -50).score == 0

    def test_default_still_fifty_silver(self, evaluator):
        result = evaluator.evaluate()
        assert result.score == 50
        assert result.level is TrustLevel.SILVER
