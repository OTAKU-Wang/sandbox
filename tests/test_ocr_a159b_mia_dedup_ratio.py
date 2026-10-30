"""OCR A159b —— `check_text_memorization` 跨 reference 重复计数，ratio 可以 >1。

台账 A159 的分母口径那一半：旧实现按 reference 逐条 `total_matched += len(overlap)`，
而每条 overlap 都是 `gen_ngrams` 的子集 ⇒ 同一段文本被 R 份 reference 覆盖就计 R 次。
后果不是“偏大一点”：把生成文本原样抄进 3 份 reference，ratio = 3.0；而
`memorization_check.check()` 拿这个 ratio 直接反推 `matched_ngrams` 并参与 `risk_level`
分档，等于对外报出一个不存在的匹配数。

钉住三件事：重复 reference 只计一次、不同 reference 仍然累加、ratio 恒在 [0,1]。
"""
import pytest

from app.services.mia_probe import MIAProbe

GEN = "a b c d e f g h"
R1 = "a b c d e"
R2 = "d e f g h"


def _ratio(probe, gen, refs, n=4):
    return probe.check_text_memorization(gen, refs, n)[0]


def test_repeated_reference_is_counted_once():
    probe = MIAProbe()
    single = _ratio(probe, GEN, [R1])
    triple = _ratio(probe, GEN, [R1, R1, R1])
    assert single == pytest.approx(0.4)
    assert triple == pytest.approx(single), (
        f"同一份 reference 抄三遍不该翻三倍: {single} -> {triple}"
    )


def test_verbatim_copy_across_duplicate_references_is_exactly_one():
    probe = MIAProbe()
    ratio = _ratio(probe, GEN, [GEN, GEN, GEN])
    assert ratio == pytest.approx(1.0), f"整段抄写只可能是 1.0，实际 {ratio}"
    assert ratio <= 1.0


def test_distinct_references_still_accumulate():
    """去重不能顺手把「两份不同 reference 各覆盖一半」也抹平。"""
    probe = MIAProbe()
    assert _ratio(probe, GEN, [R1]) == pytest.approx(0.4)
    assert _ratio(probe, GEN, [R2]) == pytest.approx(0.4)
    assert _ratio(probe, GEN, [R1, R2]) == pytest.approx(0.8)


def test_snippets_are_not_repeated_per_reference():
    probe = MIAProbe()
    _, snippets = probe.check_text_memorization(GEN, [R1, R1, R1], 4)
    assert snippets
    assert len(set(snippets)) == len(snippets), f"命中片段被逐份 reference 重复: {snippets}"


@pytest.mark.parametrize(
    "gen,refs",
    [
        (GEN, [GEN] * 5),
        (GEN, [GEN, GEN, R1, R1, R2]),
        (GEN, []),
        ("", [GEN]),
        ("one two", [GEN, GEN]),
        ("x y z w x y z w", ["x y z w"] * 4),
    ],
)
def test_ratio_is_always_a_fraction(gen, refs):
    probe = MIAProbe()
    for n in (2, 3, 4, 5):
        ratio = _ratio(probe, gen, refs, n)
        assert 0.0 <= ratio <= 1.0, f"n={n} gen={gen!r} refs={len(refs)} 份 -> {ratio}"
