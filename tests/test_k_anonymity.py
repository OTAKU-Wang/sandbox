"""Tests for k-Anonymity check service."""
import pytest
from app.services.k_anonymity import KAnonymityChecker, KAnonymityResult


@pytest.fixture
def checker():
    return KAnonymityChecker(min_k=5)


@pytest.fixture
def checker_k3():
    return KAnonymityChecker(min_k=3)


# ── Basic Checks ──────────────────────────────────────────────────

class TestBasicCheck:
    def test_empty_data(self, checker):
        result = checker.check([], ["age", "gender"])
        assert result.passed is True
        assert result.total_rows == 0

    def test_no_quasi_identifiers(self, checker):
        with pytest.raises(ValueError, match="quasi_identifiers cannot be empty"):
            checker.check([{"age": 25}], [])

    def test_missing_column(self, checker):
        with pytest.raises(ValueError, match="Missing columns"):
            checker.check([{"age": 25}], ["nonexistent"])

    def test_invalid_min_k(self):
        with pytest.raises(ValueError, match="min_k must be at least 2"):
            KAnonymityChecker(min_k=1)


# ── k-Anonymity Pass Cases ────────────────────────────────────────

class TestPassCases:
    def test_single_group_k5(self, checker):
        """5 identical rows → k=5, passes."""
        data = [
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
        ]
        result = checker.check(data, ["age", "gender"])
        assert result.passed is True
        assert result.k_value == 5
        assert result.unique_groups == 1

    def test_multiple_groups_all_pass(self, checker):
        """Two groups, both ≥5."""
        data = []
        for _ in range(6):
            data.append({"age": 25, "gender": "M"})
        for _ in range(7):
            data.append({"age": 30, "gender": "F"})
        result = checker.check(data, ["age", "gender"])
        assert result.passed is True
        assert result.k_value == 6
        assert result.unique_groups == 2

    def test_larger_dataset(self, checker):
        """100 rows with 10 groups of 10."""
        data = []
        for i in range(10):
            for _ in range(10):
                data.append({"age": 20 + i, "gender": "M" if i % 2 == 0 else "F"})
        result = checker.check(data, ["age", "gender"])
        assert result.passed is True
        assert result.k_value == 10


# ── k-Anonymity Fail Cases ────────────────────────────────────────

class TestFailCases:
    def test_single_row_fails(self, checker):
        """1 row → k=1 < 5, fails."""
        data = [{"age": 25, "gender": "M"}]
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        assert result.k_value == 1
        assert result.violation_count == 1

    def test_two_rows_fails(self, checker):
        """2 rows identical → k=2 < 5, fails."""
        data = [
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
        ]
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        assert result.k_value == 2

    def test_mixed_groups(self, checker):
        """One group passes, one fails."""
        data = []
        for _ in range(6):
            data.append({"age": 25, "gender": "M"})
        # Only 3 rows for this group
        for _ in range(3):
            data.append({"age": 30, "gender": "F"})
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        assert result.violation_count == 1
        assert result.violating_groups[0].count == 3

    def test_all_groups_fail(self, checker):
        """All groups below k=5."""
        data = [
            {"age": 25, "gender": "M"},
            {"age": 30, "gender": "F"},
            {"age": 35, "gender": "M"},
        ]
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        assert result.violation_count == 3


# ── Different k Values ────────────────────────────────────────────

class TestDifferentKValues:
    def test_k3_passes(self, checker_k3):
        """k=3: 3 rows should pass."""
        data = [
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
        ]
        result = checker_k3.check(data, ["age", "gender"])
        assert result.passed is True
        assert result.k_value == 3

    def test_k3_fails_with_2(self, checker_k3):
        """k=3: 2 rows should fail."""
        data = [
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
        ]
        result = checker_k3.check(data, ["age", "gender"])
        assert result.passed is False


# ── CSV Check ─────────────────────────────────────────────────────

class TestCSVCheck:
    def test_csv_basic(self, checker):
        csv_text = "age,gender\n25,M\n25,M\n25,M\n25,M\n25,M\n"
        result = checker.check_csv(csv_text, ["age", "gender"])
        assert result.passed is True
        assert result.total_rows == 5

    def test_csv_fails(self, checker):
        csv_text = "age,gender\n25,M\n30,F\n"
        result = checker.check_csv(csv_text, ["age", "gender"])
        assert result.passed is False

    def test_csv_empty(self, checker):
        csv_text = "age,gender\n"
        result = checker.check_csv(csv_text, ["age", "gender"])
        assert result.passed is True
        assert result.total_rows == 0


# ── Generalization ────────────────────────────────────────────────

class TestGeneralization:
    def test_generalize_numeric(self, checker):
        """Generalize ages into ranges to achieve k-anonymity."""
        data = [
            {"age": 25, "gender": "M"},
            {"age": 26, "gender": "M"},
            {"age": 27, "gender": "M"},
            {"age": 28, "gender": "M"},
            {"age": 29, "gender": "M"},
        ]
        # Without generalization: each age is unique → k=1
        result = checker.check(data, ["age"])
        assert result.passed is False

        # With generalization: ages 25-29 → "20-29" → k=5
        generalized, gen_result = checker.generalize(data, ["age"])
        assert gen_result.passed is True

    def test_generalize_custom_rule(self, checker):
        """Custom generalization rule."""
        data = [
            {"age": 25, "city": "Beijing"},
            {"age": 30, "city": "Shanghai"},
            {"age": 35, "city": "Beijing"},
            {"age": 40, "city": "Shanghai"},
            {"age": 45, "city": "Beijing"},
        ]
        # Generalize city to "China"
        rules = {"city": lambda x: "China"}
        generalized, result = checker.generalize(data, ["city"], rules)
        assert result.passed is True
        assert all(row["city"] == "China" for row in generalized)


# ── Violation Details ─────────────────────────────────────────────

class TestViolationDetails:
    def test_violation_values(self, checker):
        data = [
            {"age": 25, "gender": "M"},
            {"age": 25, "gender": "M"},
            {"age": 30, "gender": "F"},
        ]
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        # Should have 2 violations (both groups < 5)
        assert result.violation_count == 2
        # Check violation details
        ages = {v.quasi_identifier_values["age"] for v in result.violating_groups}
        assert ages == {25, 30}

    def test_violation_rate(self, checker):
        """6 out of 8 rows in violation."""
        data = []
        for _ in range(6):
            data.append({"age": 25, "gender": "M"})
        for _ in range(2):
            data.append({"age": 30, "gender": "F"})
        result = checker.check(data, ["age", "gender"])
        assert result.passed is False
        assert abs(result.violation_rate - 2/8) < 1e-9  # 2 rows in violating group

    def test_result_properties(self, checker):
        data = [{"age": 25}] * 5
        result = checker.check(data, ["age"])
        assert isinstance(result, KAnonymityResult)
        assert result.passed is True
        assert result.total_rows == 5
        assert result.unique_groups == 1
        assert result.violation_count == 0
