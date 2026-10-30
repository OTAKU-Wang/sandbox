"""OCR A159 —— 记忆化检测的分母口径、risk_level 与可配阈值的耦合、percentile 下界。

台账 A159 的三处缺陷：
1. `check()` 用 `generated_text.split()` 的**含重复位置数**当 `total_ngrams`，而
   `mia_probe.check_text_memorization` 的分母是**去重后的 n-gram 集合大小** ⇒
   `int(ratio * total_ngrams)` 反推出来的 `matched_ngrams` 与探针真实匹配数对不上；
2. `risk_level` 按硬编码 0.1/0.3/0.5/0.8 分档，与 `memorization_threshold` 无关 ⇒
   阈值 0.8 时 ratio=0.45 标 medium 却 `is_memorized=False`；阈值 0.05 时已判记忆化却标
   safe。调用方通常同时读这两个字段，两个字段互相打脸等于没有结论。
3. `compute_perplexity_threshold(percentile=…)` 不钳制下界：负 percentile 走 Python
   负索引，取到的是**接近最大**困惑度（方向完全反过来）。
"""
import pytest

from app.services.memorization_check import MemorizationChecker
from app.services.mia_probe import mia_probe


def _distinct(text: str, n: int) -> set[tuple[str, ...]]:
    words = text.split()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


GEN = "a b c d a b c d a b c d"
REF = ["a b c d"]


def test_a159_denominator_matches_the_mia_probe():
    """total_ngrams / matched_ngrams 必须与探针的去重口径一致（含重复文本上会露馅）。"""
    checker = MemorizationChecker(ngram_size=4, memorization_threshold=0.3)
    res = checker.check(GEN, REF)

    expected_total = len(_distinct(GEN, 4))          # 去重口径
    ratio, _ = mia_probe.check_text_memorization(GEN, REF, 4)
    expected_matched = round(ratio * expected_total)

    assert res.total_ngrams == expected_total
    assert res.matched_ngrams == expected_matched
    assert res.memorization_ratio == pytest.approx(ratio)


def test_a159_risk_level_tracks_configured_threshold():
    """同一个 ratio 在不同阈值下的 risk_level 必须与 is_memorized 同向。"""
    ratio = 0.45

    below = MemorizationChecker(memorization_threshold=0.8)
    above = MemorizationChecker(memorization_threshold=0.05)

    orig = mia_probe.check_text_memorization
    mia_probe.check_text_memorization = lambda *a, **k: (ratio, ["a b c d"])
    try:
        r_low_signal = below.check(GEN, REF)     # 0.45 < 0.8 ⇒ 未判记忆化
        r_hit = above.check(GEN, REF)            # 0.45 > 0.05 ⇒ 已判记忆化
    finally:
        mia_probe.check_text_memorization = orig

    assert r_low_signal.is_memorized is False
    assert r_hit.is_memorized is True
    assert r_low_signal.risk_level in ("safe", "low"), r_low_signal.risk_level
    assert r_hit.risk_level in ("medium", "high", "critical"), r_hit.risk_level


@pytest.mark.parametrize("threshold", [0.02, 0.1, 0.3, 0.5, 0.8, 0.95])
def test_a159_risk_level_is_monotone_in_ratio_for_every_threshold(threshold):
    """risk_level 随 ratio 单调不降，且分档点跟着 threshold 走。"""
    order = ["safe", "low", "medium", "high", "critical"]
    orig = mia_probe.check_text_memorization
    levels = []
    try:
        for ratio in (0.0, 0.1, 0.25, 0.45, 0.6, 0.75, 0.9, 1.0):
            mia_probe.check_text_memorization = lambda *a, _r=ratio, **k: (_r, [])
            res = MemorizationChecker(
                ngram_size=4, memorization_threshold=threshold,
            ).check(GEN, REF)
            levels.append(res.risk_level)
            if res.is_memorized:
                assert res.risk_level in ("medium", "high", "critical")
            else:
                assert res.risk_level in ("safe", "low")
    finally:
        mia_probe.check_text_memorization = orig

    assert levels == sorted(levels, key=order.index), levels


def test_a159_percentile_lower_bound_is_rejected():
    """负 percentile 今天会走负索引取到接近最大困惑度——方向相反且静默。"""
    checker = MemorizationChecker()
    ppls = [float(i) for i in range(1, 101)]

    with pytest.raises(ValueError):
        checker.compute_perplexity_threshold(ppls, percentile=-10.0)
    with pytest.raises(ValueError):
        checker.compute_perplexity_threshold(ppls, percentile=150.0)

    assert checker.compute_perplexity_threshold(ppls, percentile=95.0) == 96.0
    assert checker.compute_perplexity_threshold(ppls, percentile=0.0) == 1.0
    assert checker.compute_perplexity_threshold(ppls, percentile=100.0) == 100.0
