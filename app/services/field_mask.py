"""Field-Level Masking Service — HASH/REDACT/GENERALIZE modes.

Provides data masking for structured output to prevent sensitive data leakage.

Masking Modes:
- HASH: SM3 hash of the value (irreversible, deterministic for joins)
- REDACT: Replace with [REDACTED] or custom placeholder
- GENERALIZE: Reduce precision (e.g., age→range, date→month, city→region)

A149（潜伏缺陷波修复，模块仍未接线——接不接由人拍板）：
- HASH 支持可选密钥（HMAC-SM3，RFC 2104 构造）；无密钥时保留旧行为但在
  MaskResult.notes 里显式披露「未加盐未加密钥，低熵值可被穷举」。
  TODO(A149): 密钥来源（KMS/env）属接线决策，须人拍板后才接进 crypto_service/KMS。
- GENERALIZE 全面 fail-closed：任何泛化失败（解析失败/映射未命中/类型未知）
  一律回退 REDACT 占位并计入 fallback_fields，绝不把明文放行。
- 深拷贝行数据：嵌套容器不再与调用方共享引用（掩码输出可独立交给下游）。
- bucket_size ≤ 0 / 非数字、keep_chars 负数等坏配置不再崩溃或近乎全放行。
- 规则未命中字段/未知模式的处置在 notes 与 rules_not_applied 里如实披露。
"""
import copy
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_REDACTED_FALLBACK = "[REDACTED]"


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
    # A149（additive）：泛化失败被强制 REDACT 的字段——「掩码成功」不许静默说谎。
    fallback_fields: list[str] = field(default_factory=list)
    # A149（additive）：规则指向行内不存在的字段（拼错即静默失效）→ 显式披露。
    rules_not_applied: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


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

    A149: pass ``hash_key`` to make HASH mode a keyed digest. Without it the
    legacy unsalted digest is kept for compatibility but every MaskResult carries
    a note — unsalted digests of low-entropy PII (phones, IDs) are brute-forceable.
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

    _UNKEYED_NOTE = (
        "HASH mode is running UNSALTED and UNKEYED (A149): digests of low-entropy "
        "PII (phone numbers, ID cards) are brute-forceable — supply hash_key before "
        "this output feeds any data product"
    )

    def __init__(self, hash_key: str | bytes | None = None):
        # TODO(A149): key provenance (KMS-managed vs env var) is a wiring decision
        # for the human owner; the engine only consumes a key, it never generates one.
        if isinstance(hash_key, str):
            hash_key = hash_key.encode()
        self._hash_key = hash_key

    def mask_row(self, row: dict, rules: list[MaskRule]) -> MaskResult:
        """Apply masking rules to a single row.

        Args:
            row: Data row as dict
            rules: List of masking rules to apply

        Returns:
            MaskResult with masked row and list of applied field names
        """
        # A149: deep copy — a shallow dict(row) left nested containers aliasing the
        # caller's row, so later caller mutations (or shared-reference hand-offs)
        # could rewrite "masked" output after the fact.
        masked = copy.deepcopy(row)
        applied: list[str] = []
        fallback: list[str] = []
        not_applied: list[str] = []
        notes: list[str] = []
        if self._hash_key is None and any(r.mode == MaskMode.HASH for r in rules):
            notes.append(self._UNKEYED_NOTE)

        for rule in rules:
            if rule.field_name not in masked:
                not_applied.append(rule.field_name)
                continue
            value = masked[rule.field_name]
            if value is None:
                continue

            result = self._apply_rule(str(value), rule)
            masked[rule.field_name] = result.value
            applied.append(rule.field_name)
            if result.fell_back:
                fallback.append(rule.field_name)

        return MaskResult(
            masked_row=masked,
            rules_applied=applied,
            fallback_fields=fallback,
            rules_not_applied=not_applied,
            notes=notes,
        )

    def mask_rows(self, rows: list[dict], rules: list[MaskRule]) -> list[MaskResult]:
        """Apply masking rules to multiple rows."""
        return [self.mask_row(row, rules) for row in rows]

    @dataclass
    class _ApplyResult:
        value: str
        fell_back: bool = False

    def _apply_rule(self, value: str, rule: MaskRule) -> "FieldMasker._ApplyResult":
        """Apply a single mask rule to a value.

        A149: every failure path returns the REDACT fallback (fail-closed), never
        the original value — a declared mask that silently no-ops is worse than no
        mask because downstream consumers trust it.
        """
        if rule.mode == MaskMode.HASH:
            return self._ApplyResult(self._hash(value))
        elif rule.mode == MaskMode.REDACT:
            return self._ApplyResult(self._redact(value, rule.placeholder))
        elif rule.mode == MaskMode.GENERALIZE:
            out = self._generalize(value, rule)
            if out is None:
                return self._ApplyResult(_REDACTED_FALLBACK, fell_back=True)
            return self._ApplyResult(out)
        # Unknown/invalid mode (e.g. raw string "bogus" through the dataclass) —
        # fail-closed instead of passing the plaintext through (A149).
        logger.warning(f"field_mask: unknown mask mode {rule.mode!r} — value redacted (A149)")
        return self._ApplyResult(_REDACTED_FALLBACK, fell_back=True)

    def _hash(self, value: str) -> str:
        """SM3 digest of the value (GB/T 32905), keyed as HMAC-SM3 when hash_key set."""
        from app.services.crypto_service import crypto_service
        if self._hash_key is None:
            return crypto_service.sm3_hash(value.encode())
        # A149: RFC-2104 HMAC over the 32-byte/64-block SM3 digest (SHA-256 fallback
        # shares both sizes) — unkeyed digests are brute-forceable for low-entropy PII.
        key = self._hash_key
        block = 64
        if len(key) > block:
            key = bytes.fromhex(crypto_service.sm3_hash(key))
        key = key.ljust(block, b"\x00")
        o_pad = bytes(b ^ 0x5C for b in key)
        i_pad = bytes(b ^ 0x36 for b in key)
        inner = bytes.fromhex(crypto_service.sm3_hash(i_pad + value.encode()))
        return crypto_service.sm3_hash(o_pad + inner)

    def _redact(self, value: str, placeholder: str = "[REDACTED]") -> str:
        """Replace value with placeholder."""
        return placeholder

    def _generalize(self, value: str, rule: MaskRule) -> str | None:
        """Generalize value based on type. None = fail-closed signal (A149)."""
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
        # Unknown generalize_type: was fail-open passthrough of the raw value.
        logger.warning(f"field_mask: unknown generalize_type {gen_type!r} — value redacted (A149)")
        return None

    def _generalize_age(self, value: str) -> str | None:
        """Map age to age range; unparseable → fail-closed (A149)."""
        try:
            age = int(float(value))
        except (ValueError, TypeError):
            return None

        for low, high, label in self.AGE_RANGES:
            if low <= age < high:
                return label
        # Out-of-observation-range ages collapse to a non-disclosing bucket.
        return "unknown"

    def _generalize_date_month(self, value: str) -> str | None:
        """Reduce date to 'YYYY-MM' precision; unparseable → fail-closed (A149)."""
        if len(value) >= 7 and value[:4].isdigit() and value[4] in ("-", "/") and value[5:7].isdigit():
            return f"{value[:4]}-{value[5:7]}"
        return None

    def _generalize_date_year(self, value: str) -> str | None:
        """Reduce date to 'YYYY' precision; a non-numeric prefix is not a year (A149)."""
        if len(value) >= 4 and value[:4].isdigit():
            return value[:4]
        return None

    def _generalize_region(self, value: str) -> str | None:
        """Map province to broader region; unmapped values must not pass through (A149)."""
        for province, region in self.REGION_MAP.items():
            if province in value:
                return region
        return None

    def _generalize_numeric_bucket(self, value: str, params: dict) -> str | None:
        """Bucket numeric values into ranges.

        Params:
            bucket_size: int — size of each bucket (must be a positive number, A149)
        """
        try:
            num = float(value)
            bucket_size = float(params.get("bucket_size", 100))
        except (ValueError, TypeError):
            return None
        if bucket_size <= 0:
            return None
        if num != num or num in (float("inf"), float("-inf")):  # NaN/inf don't bucket
            return None

        bucket = int(num // bucket_size) * bucket_size
        return f"[{bucket:g}, {bucket + bucket_size:g})"

    def _generalize_truncate(self, value: str, params: dict) -> str | None:
        """Truncate string to keep only first N characters.

        Params:
            keep_chars: int — characters to keep (0 ≤ keep; negative config is
            refused fail-closed — value[:-2]+"***" used to leak all but the tail, A149)
        """
        keep = params.get("keep_chars", 3)
        if not isinstance(keep, int) or isinstance(keep, bool) or keep < 0:
            return None
        if len(value) <= keep:
            # Declared semantics: keep first N; nothing beyond N exists to hide.
            return value
        return value[:keep] + "***"


# Singleton
field_masker = FieldMasker()
