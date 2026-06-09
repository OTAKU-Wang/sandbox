"""k-Anonymity Check Service — ensures structured output meets k-anonymity requirements.

k-Anonymity: Each combination of quasi-identifier values (e.g., age, zip code, gender)
must appear at least k times in the dataset to prevent re-identification.

Default threshold: k ≥ 5 (configurable per contract).

Usage:
    checker = KAnonymityChecker(min_k=5)
    result = checker.check(data, quasi_identifiers=["age", "zip_code", "gender"])
"""
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class KAnonymityViolation:
    """A group that violates k-anonymity."""
    quasi_identifier_values: dict[str, Any]
    count: int
    min_required: int


@dataclass
class KAnonymityResult:
    """Result of k-anonymity check."""
    passed: bool
    k_value: int  # Actual minimum group size
    min_required: int  # Required minimum k
    total_rows: int
    unique_groups: int
    violating_groups: list[KAnonymityViolation] = field(default_factory=list)

    @property
    def violation_count(self) -> int:
        return len(self.violating_groups)

    @property
    def violation_rate(self) -> float:
        if self.total_rows == 0:
            return 0.0
        violating_rows = sum(v.count for v in self.violating_groups)
        return violating_rows / self.total_rows


class KAnonymityChecker:
    """Checks if structured data meets k-anonymity requirements.

    For tabular data, groups rows by quasi-identifier columns and verifies
    each group has at least k members.
    """

    def __init__(self, min_k: int = 5):
        if min_k < 2:
            raise ValueError("min_k must be at least 2")
        self.min_k = min_k

    def check(
        self,
        data: list[dict[str, Any]],
        quasi_identifiers: list[str],
    ) -> KAnonymityResult:
        """Check k-anonymity for structured data.

        Args:
            data: List of row dicts
            quasi_identifiers: Column names to use as quasi-identifiers

        Returns:
            KAnonymityResult with pass/fail and details
        """
        if not data:
            return KAnonymityResult(
                passed=True,
                k_value=0,
                min_required=self.min_k,
                total_rows=0,
                unique_groups=0,
            )

        if not quasi_identifiers:
            raise ValueError("quasi_identifiers cannot be empty")

        # Validate that all quasi-identifiers exist in the data
        missing_cols = set(quasi_identifiers) - set(data[0].keys())
        if missing_cols:
            raise ValueError(f"Missing columns in data: {missing_cols}")

        # Group by quasi-identifier combination
        groups: Counter[tuple[Any, ...]] = Counter()
        group_values: dict[tuple[Any, ...], dict[str, Any]] = {}

        for row in data:
            key = tuple(row.get(qi) for qi in quasi_identifiers)
            groups[key] += 1
            if key not in group_values:
                group_values[key] = {qi: row.get(qi) for qi in quasi_identifiers}

        # Find violations
        violations: list[KAnonymityViolation] = []
        min_group_size = float('inf')

        for key, count in groups.items():
            min_group_size = min(min_group_size, count)
            if count < self.min_k:
                violations.append(KAnonymityViolation(
                    quasi_identifier_values=group_values[key],
                    count=count,
                    min_required=self.min_k,
                ))

        # Sort violations by count (ascending) for reporting
        violations.sort(key=lambda v: v.count)

        actual_k = int(min_group_size) if min_group_size != float('inf') else len(data)

        return KAnonymityResult(
            passed=len(violations) == 0,
            k_value=actual_k,
            min_required=self.min_k,
            total_rows=len(data),
            unique_groups=len(groups),
            violating_groups=violations,
        )

    def check_csv(
        self,
        csv_text: str,
        quasi_identifiers: list[str],
        delimiter: str = ",",
    ) -> KAnonymityResult:
        """Check k-anonymity for CSV-formatted text.

        Args:
            csv_text: CSV content as string
            quasi_identifiers: Column names to use as quasi-identifiers
            delimiter: CSV delimiter

        Returns:
            KAnonymityResult
        """
        import csv
        import io

        reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
        data = list(reader)

        if not data:
            return KAnonymityResult(
                passed=True,
                k_value=0,
                min_required=self.min_k,
                total_rows=0,
                unique_groups=0,
            )

        return self.check(data, quasi_identifiers)

    def generalize(
        self,
        data: list[dict[str, Any]],
        quasi_identifiers: list[str],
        generalization_rules: dict[str, callable] | None = None,
    ) -> tuple[list[dict[str, Any]], KAnonymityResult]:
        """Apply generalization to achieve k-anonymity.

        Generalization reduces precision of quasi-identifiers to increase group sizes.
        Example: exact age → age range, full zip code → first 3 digits.

        Args:
            data: Input data
            quasi_identifiers: Columns to generalize
            generalization_rules: Dict mapping column name to generalization function

        Returns:
            Tuple of (generalized_data, result_after_generalization)
        """
        if not generalization_rules:
            # Default generalization: round numeric values
            generalization_rules = {}
            for qi in quasi_identifiers:
                if data and isinstance(data[0].get(qi), (int, float)):
                    generalization_rules[qi] = lambda x, qi=qi: self._default_generalize_numeric(x)

        # Apply generalization
        generalized = []
        for row in data:
            new_row = dict(row)
            for qi in quasi_identifiers:
                if qi in generalization_rules and qi in new_row:
                    new_row[qi] = generalization_rules[qi](new_row[qi])
            generalized.append(new_row)

        # Check k-anonymity on generalized data
        result = self.check(generalized, quasi_identifiers)
        return generalized, result

    def _default_generalize_numeric(self, value: Any) -> str | Any:
        """Default generalization for numeric values: bin into ranges."""
        if not isinstance(value, (int, float)):
            return value
        # Bin into ranges of 10
        lower = int(value // 10) * 10
        return f"{lower}-{lower + 9}"


# Singleton with default k=5
k_anonymity_checker = KAnonymityChecker(min_k=5)
