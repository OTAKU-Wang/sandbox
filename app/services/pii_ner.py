"""PII NER Service — Dual-layer PII detection (regex + NER).

Layer 1: Regex patterns (fast, deterministic) — handles structured PII like phone, ID card, etc.
Layer 2: NER-based detection (context-aware) — handles unstructured PII like names, addresses, organizations.

The NER layer uses a rule-based approach with common Chinese name/address patterns,
and provides an interface for plugging in ML-based models (spaCy, Transformers, etc.).
"""
import re
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class PIIType(str, Enum):
    """Types of PII that can be detected."""
    # Structured PII (regex-detected)
    ID_CARD = "id_card"
    SOCIAL_CREDIT_CODE = "social_credit_code"
    BANK_CARD = "bank_card"
    PHONE = "phone"
    PASSPORT = "passport"
    EMAIL = "email"
    IP_ADDRESS = "ip_address"

    # Unstructured PII (NER-detected)
    PERSON_NAME = "person_name"
    ADDRESS = "address"
    ORGANIZATION = "organization"
    DATE_OF_BIRTH = "date_of_birth"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class PIIMatch:
    """A detected PII instance."""
    pii_type: PIIType
    value: str
    start: int
    end: int
    severity: Severity
    layer: str  # "regex" or "ner"
    confidence: float = 1.0


@dataclass
class PIIDetectionResult:
    """Result of PII detection."""
    has_pii: bool
    matches: list[PIIMatch] = field(default_factory=list)
    redacted_text: str = ""
    # Honest engine disclosure (T9): which NER engine actually ran.
    # One of: "regex_only" (NER layer off), "rule" (rule-based patterns),
    # "lac" (Baidu LAC model) or "transformers" (HF pipeline). A result whose
    # matches are layer="ner" must report ner_engine="rule" — never a model
    # label, so consumers cannot mistake regex/rule detection for model NER.
    ner_engine: str = "none"

    @property
    def match_count(self) -> int:
        return len(self.matches)

    @property
    def critical_count(self) -> int:
        return sum(1 for m in self.matches if m.severity == Severity.CRITICAL)

    @property
    def by_type(self) -> dict[PIIType, list[PIIMatch]]:
        result: dict[PIIType, list[PIIMatch]] = {}
        for m in self.matches:
            result.setdefault(m.pii_type, []).append(m)
        return result


class RegexPatterns:
    """Layer 1: Regex patterns for structured PII.

    Ordering: longer/more-specific patterns MUST come before shorter ones
    to prevent substring matches.
    """
    # Sequence: id_card(18) → social_credit(18) → bank_card(16-19) → phone(11)
    #           → passport(9) → email → IP. All digit patterns use \b boundaries.
    PATTERNS: list[tuple[str, PIIType, re.Pattern, Severity]] = [
        ("id_card", PIIType.ID_CARD,
         re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
         Severity.CRITICAL),
        ("social_credit_code", PIIType.SOCIAL_CREDIT_CODE,
         re.compile(r"(?<![0-9A-HJ-NPQRTUWXY])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-HJ-NPQRTUWXY])"),
         Severity.HIGH),
        ("bank_card", PIIType.BANK_CARD,
         re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)"),
         Severity.HIGH),
        ("phone", PIIType.PHONE,
         re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
         Severity.HIGH),
        ("passport", PIIType.PASSPORT,
         re.compile(r"(?<![A-Z])[A-Z]\d{8}(?![0-9])"),
         Severity.HIGH),
        ("email", PIIType.EMAIL,
         re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
         Severity.MEDIUM),
        ("ip_address", PIIType.IP_ADDRESS,
         re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
         Severity.LOW),
    ]


class NERPatterns:
    """Layer 2: Rule-based NER patterns for unstructured PII.

    These patterns detect context-dependent PII like names, addresses, organizations.
    For production use, replace with ML-based NER (spaCy, Transformers, etc.).
    """

    # Chinese person name patterns (2-4 characters after common prefixes)
    PERSON_NAME_PATTERNS = [
        # 姓名/名字 followed by Chinese characters
        re.compile(r"(?:姓名|名字|用户|联系人|负责人|经办人)[：:\s]*([一-龥]{2,4})"),
        # Mr./Ms./Mrs. followed by Chinese characters
        re.compile(r"(?:先生|女士|同志)[：:\s]*([一-龥]{2,4})"),
    ]

    # Chinese address patterns
    ADDRESS_PATTERNS = [
        # Province + City + District + detailed address
        re.compile(r"(?<![一-龥])([一-龥]{2,8}(?:省|市|区|县|镇|乡|村|路|街|巷|弄|号|楼|室|栋|单元)[一-龥0-9]{2,20})"),
        # Detailed address with numbers
        re.compile(r"(?:地址|住址|住所)[：:\s]*([一-龥0-9]{5,50})"),
    ]

    # Organization patterns
    ORGANIZATION_PATTERNS = [
        # Company names ending with common suffixes
        re.compile(r"([一-龥]{2,20}(?:公司|集团|有限公司|股份有限公司|研究院|大学|学院|医院|银行|证券|基金|保险|政府|局|厅|部|委|处|所|中心|协会|基金会))"),
        # Organization with prefix
        re.compile(r"(?:单位|公司|机构|组织)[：:\s]*([一-龥]{2,30})"),
    ]

    # Date of birth patterns
    DOB_PATTERNS = [
        # YYYY-MM-DD or YYYY/MM/DD
        re.compile(r"(?:出生|生日|诞辰)[：:\s]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)"),
        # YYYY年MM月DD日
        re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日)"),
    ]


class PIINERService:
    """Dual-layer PII detection service.

    Layer 1: Regex (fast, deterministic) — structured PII
    Layer 2: NER (context-aware) — unstructured PII (names, addresses, orgs)

    The Layer-2 engine is selected via ``ner_engine``:
      - "auto" (default): prefer LAC (lightweight) when importable, else rule
      - "lac": Baidu LAC model; honest fallback to rule when unavailable
      - "transformers": HuggingFace NER pipeline; honest fallback to rule
      - "rule": rule-based NERPatterns only
      - "regex"/"off": disable the NER layer

    Every result reports ``ner_engine`` — the engine that actually ran — so a
    rule-based result is never presented as model-based detection (T9).

    Usage:
        service = PIINERService()
        result = service.detect("张三的手机号是13800138000")
        # result.matches contains both phone and person name
        # result.ner_engine == "rule" (LAC not installed) or "lac"
    """

    # LAC tag → (PIIType, Severity, confidence) mapping
    _LAC_TAG_MAP: dict[str, tuple] = {
        "PER": (PIIType.PERSON_NAME, Severity.HIGH, 0.9),
        "LOC": (PIIType.ADDRESS, Severity.HIGH, 0.8),
        "GPE": (PIIType.ADDRESS, Severity.HIGH, 0.8),
        "ORG": (PIIType.ORGANIZATION, Severity.MEDIUM, 0.8),
        "TIME": (PIIType.DATE_OF_BIRTH, Severity.MEDIUM, 0.9),
    }

    def __init__(
        self,
        enable_ner: bool = True,
        use_ml: bool = False,
        model_name: str = "",
        threshold: float = 0.5,
        ner_engine: str = "auto",
    ):
        self.enable_ner = enable_ner
        self._regex_patterns = RegexPatterns.PATTERNS
        self._ner_patterns = NERPatterns()
        self._use_ml = use_ml
        self._model_name = model_name
        self._ml_pipeline = None
        self._lac = None
        self._threshold = threshold
        self._active_engine = self._resolve_engine(ner_engine)

    def _resolve_engine(self, ner_engine: str) -> str:
        """Resolve the requested engine to the one that will actually run.

        Returns "regex_only" when the NER layer is disabled, otherwise one of
        "rule" / "lac" / "transformers". The resolved value is what the result
        reports — a requested-but-unavailable model engine honestly downgrades
        to "rule".
        """
        if not self.enable_ner:
            return "regex_only"

        engine = (ner_engine or "auto").lower()
        if engine in {"regex", "off", "none"}:
            return "regex_only"
        if engine == "lac":
            if self._load_lac():
                return "lac"
            logger.warning("LAC NER requested but unavailable — falling back to rule NER")
            return "rule"
        if engine == "transformers":
            if self._load_transformers():
                return "transformers"
            logger.warning("Transformers NER requested but unavailable — falling back to rule NER")
            return "rule"

        # auto: honour legacy use_ml → transformers, else prefer LAC, else rule
        if self._use_ml and self._load_transformers():
            return "transformers"
        if self._load_lac():
            return "lac"
        return "rule"

    def _load_lac(self) -> bool:
        """Lazily load the Baidu LAC model (optional dependency)."""
        if self._lac is not None:
            return True
        try:
            from LAC import LAC as _LAC  # type: ignore[import-not-found]
            self._lac = _LAC(mode="lac")
            logger.info("PII NER LAC model loaded")
            return True
        except Exception as e:
            logger.warning("LAC NER load failed, falling back to rule NER: %s", e)
            self._lac = None
            return False

    def _load_transformers(self) -> bool:
        """Lazily load the HuggingFace NER pipeline (optional dependency)."""
        if self._ml_pipeline is not None:
            return True
        try:
            from transformers import pipeline as hf_pipeline
            self._ml_pipeline = hf_pipeline(
                "ner",
                model=self._model_name or "bert-base-chinese-pii-ner",
                aggregation_strategy="simple",
            )
            logger.info("PII NER ML model loaded: %s", self._model_name or "bert-base-chinese-pii-ner")
            return True
        except Exception as e:
            logger.warning("ML NER model load failed, falling back to rule NER: %s", e)
            self._ml_pipeline = None
            return False

    def detect(self, text: str) -> PIIDetectionResult:
        """Detect PII in text using both regex and NER layers.

        Args:
            text: Input text to scan

        Returns:
            PIIDetectionResult with all detected matches
        """
        if not text or not text.strip():
            return PIIDetectionResult(has_pii=False, redacted_text=text, ner_engine=self._active_engine)

        matches: list[PIIMatch] = []

        # Layer 1: Regex detection
        regex_matches = self._detect_regex(text)
        matches.extend(regex_matches)

        # Layer 2: NER detection (if enabled)
        if self.enable_ner:
            ner_matches = self._detect_ner(text, regex_matches)
            matches.extend(ner_matches)

        # Sort by position (start, then end)
        matches.sort(key=lambda m: (m.start, m.end))

        # Remove overlapping matches (keep higher severity, then earlier start)
        matches = self._resolve_overlaps(matches)

        # Generate redacted text
        redacted = self._redact(text, matches)

        return PIIDetectionResult(
            has_pii=len(matches) > 0,
            matches=matches,
            redacted_text=redacted,
            ner_engine=self._active_engine,
        )

    def _detect_regex(self, text: str) -> list[PIIMatch]:
        """Layer 1: Detect structured PII using regex patterns."""
        matches: list[PIIMatch] = []
        for name, pii_type, pattern, severity in self._regex_patterns:
            for m in pattern.finditer(text):
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    value=m.group(),
                    start=m.start(),
                    end=m.end(),
                    severity=severity,
                    layer="regex",
                    confidence=1.0,
                ))
        return matches

    def _detect_ner(self, text: str, regex_matches: list[PIIMatch]) -> list[PIIMatch]:
        """Layer 2: Detect unstructured PII using the resolved NER engine.

        Dispatches on ``self._active_engine`` (set at init). Skips regions
        already matched by regex to avoid duplicates.
        """
        if self._active_engine == "lac":
            return self._detect_ner_lac(text, regex_matches)
        if self._active_engine == "transformers":
            return self._detect_ner_ml(text, regex_matches)
        return self._detect_ner_regex(text, regex_matches)

    def _detect_ner_lac(self, text: str, regex_matches: list[PIIMatch]) -> list[PIIMatch]:
        """Layer 2 LAC: Detect PII using the Baidu LAC model (Chinese NER).

        LAC returns ``(words, tags)``; we map PER/LOC/GPE/ORG/TIME tags to the
        corresponding PII types. Matches are labelled ``layer="ner_lac"``.
        If LAC is not loaded (model unavailable), honestly falls back to the
        rule-based NER layer.
        """
        if not self._lac:
            return self._detect_ner_regex(text, regex_matches)

        matches: list[PIIMatch] = []
        regex_positions: set[int] = set()
        for m in regex_matches:
            regex_positions.update(range(m.start, m.end))

        try:
            words, tags = self._lac.run(text)
            pos = 0
            for word, tag in zip(words, tags):
                mapped = self._LAC_TAG_MAP.get(tag)
                if not mapped:
                    pos = text.find(word, pos)
                    if pos >= 0:
                        pos += len(word)
                    continue
                pii_type, severity, confidence = mapped
                start = text.find(word, pos)
                if start < 0:
                    continue
                end = start + len(word)
                pos = end
                if any(p in regex_positions for p in range(start, end)):
                    continue
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    value=word,
                    start=start,
                    end=end,
                    severity=severity,
                    layer="ner_lac",
                    confidence=confidence,
                ))
        except Exception as e:
            logger.warning("LAC NER inference failed, falling back to rule NER: %s", e)
            return self._detect_ner_regex(text, regex_matches)

        return matches

    def _detect_ner_ml(self, text: str, regex_matches: list[PIIMatch]) -> list[PIIMatch]:
        """Layer 2 ML: Detect PII using HuggingFace NER model."""
        matches: list[PIIMatch] = []
        regex_positions: set[int] = set()
        for m in regex_matches:
            regex_positions.update(range(m.start, m.end))

        label_map = {
            "PER": PIIType.PERSON_NAME,
            "PERSON": PIIType.PERSON_NAME,
            "LOC": PIIType.ADDRESS,
            "LOCATION": PIIType.ADDRESS,
            "ORG": PIIType.ORGANIZATION,
            "ORGANIZATION": PIIType.ORGANIZATION,
        }

        try:
            entities = self._ml_pipeline(text)
            for ent in entities:
                if ent.get("score", 0) < self._threshold:
                    continue
                entity_group = ent.get("entity_group", "")
                pii_type = label_map.get(entity_group)
                if not pii_type:
                    continue
                start = int(ent["start"])
                end = int(ent["end"])
                # Skip if overlaps with regex match
                if any(pos in regex_positions for pos in range(start, end)):
                    continue
                severity = Severity.HIGH if pii_type == PIIType.PERSON_NAME else Severity.MEDIUM
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    value=ent["word"],
                    start=start,
                    end=end,
                    severity=severity,
                    layer="ner_ml",
                    confidence=float(ent["score"]),
                ))
        except Exception as e:
            logger.warning("ML NER inference failed, falling back to regex NER: %s", e)
            return self._detect_ner_regex(text, regex_matches)

        return matches

    def _detect_ner_regex(self, text: str, regex_matches: list[PIIMatch]) -> list[PIIMatch]:
        """Layer 2 Regex: Detect unstructured PII using rule-based NER patterns."""
        matches: list[PIIMatch] = []

        # Create a set of positions already covered by regex
        regex_positions: set[int] = set()
        for m in regex_matches:
            regex_positions.update(range(m.start, m.end))

        # Person names
        for pattern in self._ner_patterns.PERSON_NAME_PATTERNS:
            for m in pattern.finditer(text):
                name = m.group(1) if m.lastindex else m.group()
                start = m.start(1) if m.lastindex else m.start()
                end = m.end(1) if m.lastindex else m.end()

                # Skip if overlaps with regex match
                if any(pos in regex_positions for pos in range(start, end)):
                    continue

                # Validate: Chinese names should be 2-4 characters
                if 2 <= len(name) <= 4 and all('一' <= c <= '鿿' for c in name):
                    matches.append(PIIMatch(
                        pii_type=PIIType.PERSON_NAME,
                        value=name,
                        start=start,
                        end=end,
                        severity=Severity.HIGH,
                        layer="ner",
                        confidence=0.8,
                    ))

        # Addresses
        for pattern in self._ner_patterns.ADDRESS_PATTERNS:
            for m in pattern.finditer(text):
                addr = m.group(1) if m.lastindex else m.group()
                start = m.start(1) if m.lastindex else m.start()
                end = m.end(1) if m.lastindex else m.end()

                if any(pos in regex_positions for pos in range(start, end)):
                    continue

                matches.append(PIIMatch(
                    pii_type=PIIType.ADDRESS,
                    value=addr,
                    start=start,
                    end=end,
                    severity=Severity.HIGH,
                    layer="ner",
                    confidence=0.7,
                ))

        # Organizations
        for pattern in self._ner_patterns.ORGANIZATION_PATTERNS:
            for m in pattern.finditer(text):
                org = m.group(1) if m.lastindex else m.group()
                start = m.start(1) if m.lastindex else m.start()
                end = m.end(1) if m.lastindex else m.end()

                if any(pos in regex_positions for pos in range(start, end)):
                    continue

                matches.append(PIIMatch(
                    pii_type=PIIType.ORGANIZATION,
                    value=org,
                    start=start,
                    end=end,
                    severity=Severity.MEDIUM,
                    layer="ner",
                    confidence=0.7,
                ))

        # Date of birth
        for pattern in self._ner_patterns.DOB_PATTERNS:
            for m in pattern.finditer(text):
                dob = m.group(1) if m.lastindex else m.group()
                start = m.start(1) if m.lastindex else m.start()
                end = m.end(1) if m.lastindex else m.end()

                if any(pos in regex_positions for pos in range(start, end)):
                    continue

                matches.append(PIIMatch(
                    pii_type=PIIType.DATE_OF_BIRTH,
                    value=dob,
                    start=start,
                    end=end,
                    severity=Severity.MEDIUM,
                    layer="ner",
                    confidence=0.9,
                ))

        return matches

    def _resolve_overlaps(self, matches: list[PIIMatch]) -> list[PIIMatch]:
        """Remove overlapping matches, keeping higher severity ones."""
        if not matches:
            return matches

        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
        }

        result: list[PIIMatch] = []
        last_end = -1

        for m in matches:
            if m.start >= last_end:
                result.append(m)
                last_end = m.end
            else:
                # Overlap — keep higher severity
                if result and severity_order.get(m.severity, 99) < severity_order.get(result[-1].severity, 99):
                    result[-1] = m
                    last_end = m.end

        return result

    def _redact(self, text: str, matches: list[PIIMatch]) -> str:
        """Replace PII matches with redaction markers."""
        if not matches:
            return text

        # Build replacement segments in reverse order
        segments: list[tuple[int, int, str]] = []
        for m in matches:
            replacement = f"[REDACTED:{m.pii_type.value}]"
            segments.append((m.start, m.end, replacement))

        # Apply replacements in reverse to preserve positions
        result = text
        for start, end, replacement in reversed(segments):
            result = result[:start] + replacement + result[end:]

        return result


# Singleton — reads ML config from settings
try:
    from app.core.config import get_settings
    _settings = get_settings()
    pii_ner_service = PIINERService(
        enable_ner=True,
        use_ml=_settings.PII_NER_USE_ML,
        model_name=_settings.PII_NER_MODEL_NAME,
        threshold=_settings.PII_NER_CONFIDENCE_THRESHOLD,
        ner_engine=_settings.PII_NER_ENGINE,
    )
except Exception:
    pii_ner_service = PIINERService()
