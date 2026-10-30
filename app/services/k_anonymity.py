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


def _is_missing_qi_value(value: Any) -> bool:
    """A131: what counts as a missing quasi-identifier value.

    None / 空串与纯空白 / NaN 都算缺值。CSV 短行（DictReader restval=None）与空
    单元格（''）正是本缺陷的经典入口。0、False 等真实取值**不算**缺值。
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, float) and value != value:  # NaN
        return True
    return False


def _fmt_rows(idxs: list[int], cap: int = 10) -> str:
    """Format offending row indices for the rejection reason, capped for size."""
    shown = ", ".join(str(i) for i in idxs[:cap])
    if len(idxs) > cap:
        shown += f", … (+{len(idxs) - cap} more)"
    return shown


@dataclass
class KAnonymityViolation:
    """A group that violates k-anonymity."""
    quasi_identifier_values: dict[str, Any]
    count: int
    min_required: int


@dataclass
class KAnonymityMissingGroup:
    """A131: rows whose quasi-identifiers carry missing values.

    缺值行**永不参与**「满足 k」的等价类判定（旧实现把它们全归进同一个
    `(age=None, zip=None, …)` 类，可人为把小组凑到 k 以上 ⇒ 隐私门虚假通过）。
    这里只按缺值签名分组做**披露**：missing_fields 记录缺了哪些 QI，
    quasi_identifier_values 里缺值槽位以 None 呈现。
    """
    missing_fields: list[str]
    quasi_identifier_values: dict[str, Any]
    count: int


@dataclass
class KAnonymityResult:
    """Result of k-anonymity check."""
    passed: bool
    k_value: int  # Actual minimum group size
    min_required: int  # Required minimum k
    total_rows: int
    unique_groups: int
    violating_groups: list[KAnonymityViolation] = field(default_factory=list)
    # 没有行可分组时 passed=True 只是数学上的真空为真，不能当放行凭据用（SS-18 §8 P0'）：
    # evaluated=False 就是告诉判定方「这一项没有真的评估过」。
    evaluated: bool = True
    reason: str = ""
    # A131（additive）：缺值行的独立统计与披露。隐私门 fail-closed——存在缺值即
    # passed=False，缺值行不并入正常等价类；missing_rows 为中性 0 时语义与旧版完全一致。
    missing_rows: int = 0
    missing_groups: list[KAnonymityMissingGroup] = field(default_factory=list)

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
                evaluated=False,
                reason="no rows to group",
            )

        if not quasi_identifiers:
            raise ValueError("quasi_identifiers cannot be empty")

        # A131: 存在性校验必须覆盖全部行。旧实现只看 data[0]，后续行缺列会被
        # row.get() 静默读成 None，再和别的缺值行聚成同一个等价类 ⇒ 虚假通过。
        # 沿用「缺列即拒绝 + reason」的既有契约（首行 ValueError 语义不变），
        # 只是把覆盖面扩到全部行，并把缺列的行号如实写进 reason。
        missing_cols: dict[str, list[int]] = {}
        for idx, row in enumerate(data):
            for qi in quasi_identifiers:
                if qi not in row:
                    missing_cols.setdefault(qi, []).append(idx)
        if missing_cols:
            detail = "; ".join(
                f"{qi} absent in row(s) {_fmt_rows(idxs)}"
                for qi, idxs in sorted(missing_cols.items())
            )
            raise ValueError(f"Missing columns in data: {set(missing_cols)} — {detail}")

        # Group by quasi-identifier combination. A131: rows carrying missing QI
        # values are counted/disclosed separately and NEVER join an equivalence
        # class — a missing value is not a legitimate generalization value.
        _MISSING = object()  # slot sentinel so missing rows never collide with a real value
        groups: Counter[tuple[Any, ...]] = Counter()
        group_values: dict[tuple[Any, ...], dict[str, Any]] = {}
        missing_counter: Counter[tuple[Any, ...]] = Counter()
        missing_fields_by_key: dict[tuple[Any, ...], list[str]] = {}
        missing_values_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
        missing_rows = 0

        for row in data:
            key_parts: list[Any] = []
            row_missing: list[str] = []
            for qi in quasi_identifiers:
                value = row[qi]
                if _is_missing_qi_value(value):
                    row_missing.append(qi)
                    key_parts.append(_MISSING)
                else:
                    key_parts.append(value)
            key = tuple(key_parts)
            if row_missing:
                missing_rows += 1
                missing_counter[key] += 1
                if key not in missing_fields_by_key:
                    missing_fields_by_key[key] = row_missing
                    missing_values_by_key[key] = {
                        qi: (None if qi in row_missing else row[qi])
                        for qi in quasi_identifiers
                    }
            else:
                groups[key] += 1
                if key not in group_values:
                    group_values[key] = {qi: row[qi] for qi in quasi_identifiers}

        # Find violations (evaluated groups only — missing rows never count)
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

        missing_groups = [
            KAnonymityMissingGroup(
                missing_fields=missing_fields_by_key[key],
                quasi_identifier_values=missing_values_by_key[key],
                count=count,
            )
            for key, count in sorted(missing_counter.items(), key=lambda kv: (kv[1], repr(kv[0])))
        ]

        if groups:
            actual_k = int(min_group_size)
        else:
            # 全部行都缺值 ⇒ 没有任何可比对的等价类，隐私门不给通过找任何数字依据。
            actual_k = 0

        # 隐私门 fail-closed（SS-18 §8 拍板层）：正常组全过 + 存在缺值行，仍不通过。
        passed = len(violations) == 0 and missing_rows == 0
        reason = ""
        if missing_rows:
            all_missing_fields = sorted({f for g in missing_groups for f in g.missing_fields})
            reason = (
                f"{missing_rows} row(s) carry missing quasi-identifier values in "
                f"{all_missing_fields}; missing rows are not grouped into equivalence "
                f"classes and the privacy gate fails closed (A131)"
            )

        return KAnonymityResult(
            passed=passed,
            k_value=actual_k,
            min_required=self.min_k,
            total_rows=len(data),
            unique_groups=len(groups),
            violating_groups=violations,
            reason=reason,
            missing_rows=missing_rows,
            missing_groups=missing_groups,
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
                evaluated=False,
                reason="no rows parsed from input",
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
