"""A131 — k_anonymity 准标识符存在性必须覆盖全部行，缺值行不得互归「满足 k」等价类。

潜伏缺陷（docs/ocr-review-2026-10-18 fix-queue）：`k_anonymity.py` 旧实现只校验
**首行**的 QI 存在性（`set(qi) - set(data[0].keys())`），后续行缺列被 `row.get()`
读成 `None`；随后所有缺值行又在 `(age=None, zip=None, …)` 上聚成同一个等价类，
脏数据可人为把小组凑到 k 以上 ⇒ 隐私门虚假通过。

修法（SS-18 §8 拍板口径：闸门层 fail-closed）：
- 任一行缺 QI 列 ⇒ 明确拒绝 + reason（沿用既有「缺列即 ValueError」的首行契约，
  只是把覆盖面从首行扩到全部行）；
- 缺值行不进正常等价类，单独统计为 `missing_groups` 披露；存在缺值即 `passed=False`
  + reason（隐私门 fail-closed，缺值不当合法泛化值）；
- 真空为真语义（空输入 `passed=True` + `evaluated=False`）与 DP/合规语义原样保留，
  本文件同时钉住这条反向护栏。
"""
import pytest

from app.services.k_anonymity import KAnonymityChecker


@pytest.fixture
def checker():
    return KAnonymityChecker(min_k=5)


# ── a) 准标识符存在性覆盖全部行 ─────────────────────────────────────

class TestAllRowColumnPresence:
    def test_first_row_has_column_later_row_missing_rejected(self, checker):
        """首行有列、次行缺列：旧实现静默读 None 虚假通过，现在必须拒绝 + 指出行号。"""
        data = [
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
            {"gender": "M"},  # 缺 age —— 旧代码只看 data[0]，这里静默变 None
        ]
        with pytest.raises(ValueError, match="Missing columns") as exc:
            checker.check(data, ["age", "gender"])
        msg = str(exc.value)
        assert "age" in msg
        assert "2" in msg  # 缺列的行号（0-based）必须出现在 reason 里

    def test_multiple_rows_missing_different_columns_rejected(self, checker):
        """多行各缺不同列：一次性披露全部缺列与行号，不许只报第一个。"""
        data = [
            {"age": 25, "gender": "M"},
            {"gender": "M"},   # 缺 age
            {"age": 30},       # 缺 gender
        ]
        with pytest.raises(ValueError, match="Missing columns") as exc:
            checker.check(data, ["age", "gender"])
        msg = str(exc.value)
        assert "age" in msg and "gender" in msg

    def test_csv_short_row_missing_column_value_rejected(self, checker):
        """CSV 短行（restval None）按缺值 fail-closed 处理，不得凑类。"""
        csv_text = "age,gender\n25,M\n25,M\n25,\n25,\n25,\n25,\n25,\n"
        result = checker.check_csv(csv_text, ["age", "gender"])
        assert result.passed is False
        assert result.missing_rows == 5
        assert result.reason != ""


# ── b) 缺值等价类 fail-closed ──────────────────────────────────────

class TestMissingValuesFailClosed:
    def test_missing_values_never_form_passing_class(self, checker):
        """6 行 age 全缺值：旧实现聚成 1 个 k=6 的等价类虚假通过。"""
        data = [{"age": None, "gender": f"M{i}"} for i in range(6)]
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False, "缺值行互相归类冒充满足 k —— A131 虚假通过"
        assert result.missing_rows == 6
        assert sum(g.count for g in result.missing_groups) == 6
        assert any("age" in g.missing_fields for g in result.missing_groups)
        assert "missing" in result.reason.lower()

    def test_missing_rows_do_not_rescue_valid_small_group(self, checker):
        """缺值行不并入正常等价类：3 行有效 + 3 行缺值 ⇒ 有效组照旧违例、缺值另计。"""
        data = [{"age": 25, "gender": "M"}] * 3 + [{"age": None, "gender": "M"}] * 3
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        assert result.unique_groups == 1          # 只有那个 3 行的正常组
        assert result.missing_rows == 3
        assert result.violation_count == 1        # 正常组 <k 的违例不被缺值掩盖

    def test_missing_values_fail_closed_even_when_valid_groups_pass(self, checker):
        """有效组全部 ≥k 仍不通过：存在缺值即 fail-closed + reason。"""
        data = [{"age": 25, "gender": "M"}] * 5 + [{"age": None, "gender": "M"}]
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        assert result.violation_count == 0        # 违例来自缺值，不是正常组
        assert result.missing_rows == 1
        assert "fail" in result.reason.lower() or "missing" in result.reason.lower()
        assert result.evaluated is True           # 评估发生了，结论是拒

    def test_full_missing_matrix(self, checker):
        """全缺值矩阵：没有任何可比对的等价类，绝不产出通过。"""
        data = [{"age": None, "zip": None} for _ in range(10)]
        result = checker.check(data, ["age", "zip"])
        assert result.passed is False
        assert result.unique_groups == 0
        assert result.k_value == 0
        assert result.missing_rows == 10
        assert result.violation_count == 0

    def test_empty_string_and_nan_count_as_missing(self, checker):
        """空串/纯空白/NaN 与 None 同为缺值（CSV 空单元格即 ''）。"""
        data = [
            {"age": 25, "city": "Beijing"},
            {"age": 26, "city": ""},        # CSV 空单元格
            {"age": 27, "city": "   "},     # 纯空白
            {"age": float("nan"), "city": "Beijing"},
        ]
        result = checker.check(data, ["age", "city"])
        assert result.passed is False
        assert result.missing_rows == 3
        assert result.evaluated is True

    def test_missing_group_disclosure_shape(self, checker):
        """missing_groups 披露结构：按缺值签名分组，count 求和 == missing_rows。"""
        data = [{"age": None, "zip": None} for _ in range(4)] + [
            {"age": 25, "zip": None}
        ]
        result = checker.check(data, ["age", "zip"])
        assert result.missing_rows == 5
        assert sum(g.count for g in result.missing_groups) == result.missing_rows
        assert all(g.missing_fields for g in result.missing_groups)


# ── c) 向后兼容 + 真空为真口径反向护栏 ─────────────────────────────

class TestBackwardCompatAndVacuum:
    def test_clean_data_unchanged(self, checker):
        """无缺值数据行为完全不变：通过、reason 空、missing_* 为中性值。"""
        data = [{"age": 25, "gender": "M"}] * 6 + [{"age": 30, "gender": "F"}] * 7
        result = checker.check(data, ["age", "gender"])
        assert result.passed is True
        assert result.k_value == 6
        assert result.missing_rows == 0
        assert result.missing_groups == []
        assert result.reason == ""
        assert result.evaluated is True

    def test_vacuum_semantics_preserved(self, checker):
        """SS-18 §8 拍板不许翻：真空仍 passed=True + evaluated=False + reason 披露。"""
        result = checker.check([], ["age", "gender"])
        assert result.passed is True
        assert result.evaluated is False
        assert result.reason != ""
        assert result.missing_rows == 0

    def test_result_fields_additive(self, checker):
        """新增字段全部 additive（带默认值），旧式构造不受影响。"""
        from app.services.k_anonymity import KAnonymityResult

        legacy = KAnonymityResult(
            passed=True, k_value=5, min_required=5, total_rows=5, unique_groups=1,
        )
        assert legacy.evaluated is True
        assert legacy.reason == ""
        assert legacy.missing_rows == 0
        assert legacy.missing_groups == []
