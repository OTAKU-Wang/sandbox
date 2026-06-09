"""Field-Level Masking Service — HASH/REDACT/GENERALIZE modes.

Provides data masking for structured output to prevent sensitive data leakage.

Masking Modes:
- HASH: SM3 hash of the value (irreversible, deterministic for joins)
- REDACT: Replace with [REDACTED] or custom placeholder
- GENERALIZE: Reduce precision (e.g., age→range, date→month, city→region)
"""
import re
import logging
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


class MaskMode(str, Enum):
    HASH = "hash"
    REDACT = "redact"
    GENERALIZE = "generalize"


@dataclass
class MaskRule:
    """A masking rule for a specific field."""
    field_name: str
    mode: MaskMode
    # REDACT mode options
    placeholder: str = "[REDACTED]"
    # GENERALIZE mode options
    generalize_type: str | None = None  # age_range, date_month, region, numeric_bucket
    generalize_params: dict = field(default_factory=dict)


@dataclass
class MaskResult:
    """Result of applying mask rules to a row."""
    masked_row: dict
    rules_applied: list[str]  # field names that were masked


class FieldMasker:
    """Applies field-level masking to structured data.

    Usage:
        masker = FieldMasker()
        rules = [
            MaskRule("name", MaskMode.REDACT),
            MaskRule("age", MaskMode.GENERALIZE, generalize_type="age_range"),
            MaskRule("email", MaskMode.HASH),
        ]
        result = masker.mask_row({"name": "Alice", "age": 25, "email": "a@b.com"}, rules)
    """

    # Generalization ranges
    AGE_RANGES = [
        (0, 18, "0-17"),
        (18, 30, "18-29"),
        (30, 45, "30-44"),
        (45, 60, "45-59"),
        (60, 120, "60+"),
    ]

    # Chinese province → region mapping (subset)
    REGION_MAP = {
        "北京": "华北", "天津": "华北", "河北": "华北", "山西": "华北", "内蒙古": "华北",
        "上海": "华东", "江苏": "华东", "浙江": "华东", "安徽": "华东", "福建": "华东",
        "江西": "华东", "山东": "华东",
        "广东": "华南", "广西": "华南", "海南": "华南",
        "湖北": "华中", "湖南": "华中", "河南": "华中",
        "四川": "西南", "重庆": "西南", "贵州": "西南", "云南": "西南", "西藏": "西南",
        "陕西": "西北", "甘肃": "西北", "青海": "西北", "宁夏": "西北", "新疆": "西北",
        "辽宁": "东北", "吉林": "东北", "黑龙江": "东北",
    }

    def mask_row(self, row: dict, rules: list[MaskRule]) -> MaskResult:
        """Apply masking rules to a single row.

        Args:
            row: Data row as dict
            rules: List of masking rules to apply

        Returns:
            MaskResult with masked row and list of applied field names
        """
        masked = dict(row)
        applied = []

        for rule in rules:
            if rule.field_name not in masked:
                continue
            value = masked[rule.field_name]
            if value is None:
                continue

            masked[rule.field_name] = self._apply_rule(str(value), rule)
            applied.append(rule.field_name)

        return MaskResult(masked_row=masked, rules_applied=applied)

    def mask_rows(self, rows: list[dict], rules: list[MaskRule]) -> list[MaskResult]:
        """Apply masking rules to multiple rows."""
        return [self.mask_row(row, rules) for row in rows]

    def _apply_rule(self, value: str, rule: MaskRule) -> str:
        """Apply a single mask rule to a value."""
        if rule.mode == MaskMode.HASH:
            return self._hash(value)
        elif rule.mode == MaskMode.REDACT:
            return self._redact(value, rule.placeholder)
        elif rule.mode == MaskMode.GENERALIZE:
            return self._generalize(value, rule)
        return value

    def _hash(self, value: str) -> str:
        """SM3 hash of the value (GB/T 32905)."""
        from app.services.crypto_service import crypto_service
        return crypto_service.sm3_hash(value.encode())

    def _redact(self, value: str, placeholder: str = "[REDACTED]") -> str:
        """Replace value with placeholder."""
        return placeholder

    def _generalize(self, value: str, rule: MaskRule) -> str:
        """Generalize value based on type."""
        gen_type = rule.generalize_type

        if gen_type == "age_range":
            return self._generalize_age(value)
        elif gen_type == "date_month":
            return self._generalize_date_month(value)
        elif gen_type == "date_year":
            return self._generalize_date_year(value)
        elif gen_type == "region":
            return self._generalize_region(value)
        elif gen_type == "numeric_bucket":
            return self._generalize_numeric_bucket(value, rule.generalize_params)
        elif gen_type == "truncate":
            return self._generalize_truncate(value, rule.generalize_params)
        return value

    def _generalize_age(self, value: str) -> str:
        """Map age to age range."""
        try:
            age = int(float(value))
        except (ValueError, TypeError):
            return value

        for low, high, label in self.AGE_RANGES:
            if low <= age < high:
                return label
        return "unknown"

    def _generalize_date_month(self, value: str) -> str:
        """Reduce date to year-month precision."""
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(value[:len(fmt.replace('%', ' ').strip()) + 5], fmt)
                return dt.strftime("%Y-%m")
            except (ValueError, IndexError):
                continue
        # Try ISO format prefix
        if len(value) >= 7 and value[4] in ('-', '/'):
            return value[:7]
        return value

    def _generalize_date_year(self, value: str) -> str:
        """Reduce date to year precision."""
        if len(value) >= 4:
            return value[:4]
        return value

    def _generalize_region(self, value: str) -> str:
        """Map province/city to broader region."""
        for province, region in self.REGION_MAP.items():
            if province in value:
                return region
        return value

    def _generalize_numeric_bucket(self, value: str, params: dict) -> str:
        """Bucket numeric values into ranges.

        Params:
            bucket_size: int — size of each bucket
        """
        try:
            num = float(value)
        except (ValueError, TypeError):
            return value

        bucket_size = params.get("bucket_size", 100)
        bucket = int(num // bucket_size) * bucket_size
        return f"[{bucket}, {bucket + bucket_size})"

    def _generalize_truncate(self, value: str, params: dict) -> str:
        """Truncate string to keep only first N characters.

        Params:
            keep_chars: int — characters to keep
        """
        keep = params.get("keep_chars", 3)
        if len(value) <= keep:
            return value
        return value[:keep] + "***"


# Singleton
field_masker = FieldMasker()
