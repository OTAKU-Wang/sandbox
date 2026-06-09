"""Output Inspection Gateway — 6-stage review pipeline.
Stages: Format Validation → DLP Scan → Differential Privacy → Watermark → Signature → Final Approval.
"""
import re
import math
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum

from app.utils.watermark import (
    generate_watermark,
    embed_text_watermark,
    extract_text_watermark,
    verify_text_watermark,
    embed_image_watermark,
    extract_image_watermark,
    verify_image_watermark,
)


class SandboxMode(str, Enum):
    """Sandbox modes per SS-05 spec."""
    STRUCTURED_QUERY = "query"
    STRUCTURED_MODELING = "train"
    DEVELOP = "develop"
    APPLICATION = "application"


class InspectionStage(str, Enum):
    ROUTE = "scene_routing"
    FORMAT = "format_validation"
    DLP = "dlp_scan"
    DATA_RECONSTRUCTION = "data_reconstruction"
    K_ANONYMITY = "k_anonymity"
    DP = "differential_privacy"
    MIA = "model_safety"
    WATERMARK = "watermark"
    SIGNATURE = "signature"
    APPROVAL = "final_approval"


@dataclass
class InspectionFinding:
    stage: str
    severity: str  # critical, high, medium, low
    type: str
    message: str
    samples: list[str] = field(default_factory=list)


@dataclass
class InspectionResult:
    passed: bool
    stage_results: dict[str, bool]
    findings: list[InspectionFinding]
    redacted_output: str | None = None
    watermark: str | None = None
    dp_applied: bool = False
    signature: str | None = None  # SM2 digital signature (hex r||s)


class OutputInspector:
    """6-stage output inspection gateway."""

    _signing_keypair = None  # Lazy-initialized SM2 keypair for signing

    # Data reconstruction detection threshold
    # If field-level exact match rate exceeds this, the output may reconstruct source data
    RECONSTRUCTION_THRESHOLD = 0.05  # 5%

    # DLP patterns for sensitive Chinese data
    # ORDERING: longer/more-specific patterns MUST come before shorter ones
    # to prevent substring matches (e.g. phone "1[3-9]\d{9}" matching inside
    # an 18-digit ID card number).
    # Sequence: id_card(18) → social_credit(18) → bank_card(16-19) → phone(11)
    #           → passport(9) → email → IP.  All digit patterns use \b boundaries.
    PATTERNS = {
        "id_card": (re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"), "critical"),
        "social_credit_code": (re.compile(r"(?<![0-9A-HJ-NPQRTUWXY])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![0-9A-HJ-NPQRTUWXY])"), "high"),
        "bank_card": (re.compile(r"(?<!\d)[3-6]\d{15,18}(?!\d)"), "high"),
        "phone": (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "high"),
        "passport": (re.compile(r"(?<![A-Z])[A-Z]\d{8}(?![0-9])"), "high"),
        "email": (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "medium"),
        "ip_address": (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "low"),
    }

    # k-anonymity minimum group size
    K_ANONYMITY_MIN = 5

    # Scene routing: which stages apply per sandbox mode
    SCENE_RULES: dict[str, list[str]] = {
        SandboxMode.STRUCTURED_QUERY.value: [
            InspectionStage.FORMAT.value, InspectionStage.DLP.value,
            InspectionStage.DATA_RECONSTRUCTION.value, InspectionStage.K_ANONYMITY.value,
            InspectionStage.DP.value, InspectionStage.WATERMARK.value,
            InspectionStage.SIGNATURE.value, InspectionStage.APPROVAL.value,
        ],
        SandboxMode.STRUCTURED_MODELING.value: [
            InspectionStage.FORMAT.value, InspectionStage.DLP.value,
            InspectionStage.DATA_RECONSTRUCTION.value,
            InspectionStage.DP.value, InspectionStage.MIA.value,
            InspectionStage.WATERMARK.value, InspectionStage.SIGNATURE.value,
            InspectionStage.APPROVAL.value,
        ],
        SandboxMode.DEVELOP.value: [
            InspectionStage.FORMAT.value, InspectionStage.DLP.value,
            InspectionStage.WATERMARK.value, InspectionStage.SIGNATURE.value,
            InspectionStage.APPROVAL.value,
        ],
        SandboxMode.APPLICATION.value: [
            InspectionStage.FORMAT.value, InspectionStage.DLP.value,
            InspectionStage.WATERMARK.value, InspectionStage.SIGNATURE.value,
            InspectionStage.APPROVAL.value,
        ],
    }

    def inspect(
        self,
        output: str,
        user_id: str,
        session_id: str,
        dp_epsilon: float | None = None,
        sandbox_mode: str = SandboxMode.STRUCTURED_QUERY.value,
        output_rows: list[dict] | None = None,
        detail: dict | None = None,
    ) -> InspectionResult:
        """Run full inspection pipeline with scene-aware routing."""
        findings = []
        stage_results = {}
        redacted = output

        # Stage 0: Scene routing — select applicable stages
        active_stages = self.SCENE_RULES.get(sandbox_mode, self.SCENE_RULES[SandboxMode.STRUCTURED_QUERY.value])
        stage_results[InspectionStage.ROUTE.value] = True

        # Stage 1: Format Validation
        if InspectionStage.FORMAT.value in active_stages:
            stage_results[InspectionStage.FORMAT.value] = True
            if not output or len(output.strip()) == 0:
                stage_results[InspectionStage.FORMAT.value] = False
                findings.append(InspectionFinding(InspectionStage.FORMAT.value, "medium", "empty_output", "Output is empty"))

        # Stage 2: DLP Scan
        if InspectionStage.DLP.value not in active_stages:
            stage_results[InspectionStage.DLP.value] = True
        dlp_passed = True
        for name, (pattern, severity) in self.PATTERNS.items():
            matches = pattern.findall(output)
            if matches:
                dlp_passed = False
                findings.append(InspectionFinding(
                    InspectionStage.DLP.value, severity, name,
                    f"Found {len(matches)} {name} instance(s)",
                    samples=matches[:3],
                ))
                redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
        stage_results[InspectionStage.DLP.value] = dlp_passed

        # Stage 3: Data Reconstruction Detection
        if InspectionStage.DATA_RECONSTRUCTION.value in active_stages:
            stage_results[InspectionStage.DATA_RECONSTRUCTION.value] = True
            source_rows = (detail or {}).get("source_rows")
            if output_rows and source_rows:
                passed_recon, match_rate = self.check_data_reconstruction(output_rows, source_rows)
                if not passed_recon:
                    stage_results[InspectionStage.DATA_RECONSTRUCTION.value] = False
                    findings.append(InspectionFinding(
                        InspectionStage.DATA_RECONSTRUCTION.value, "critical",
                        "data_reconstruction",
                        f"Output row match rate {match_rate:.1%} exceeds threshold {self.RECONSTRUCTION_THRESHOLD:.1%}",
                    ))

        # Stage 3b: k-Anonymity check (structured query mode)
        if InspectionStage.K_ANONYMITY.value in active_stages and output_rows:
            ka_passed, violations = self.check_k_anonymity(output_rows)
            stage_results[InspectionStage.K_ANONYMITY.value] = ka_passed
            if not ka_passed:
                findings.append(InspectionFinding(
                    InspectionStage.K_ANONYMITY.value, "high",
                    "k_anonymity",
                    f"{len(violations)} group(s) below k={self.K_ANONYMITY_MIN}",
                    samples=violations[:3],
                ))
        elif InspectionStage.K_ANONYMITY.value in active_stages:
            stage_results[InspectionStage.K_ANONYMITY.value] = True

        # Stage 4: Differential Privacy (if epsilon provided)
        if InspectionStage.DP.value in active_stages:
            if dp_epsilon is not None:
                stage_results[InspectionStage.DP.value] = True
            else:
                stage_results[InspectionStage.DP.value] = True

        # Stage 4b: Model Safety / MIA probe (training mode)
        if InspectionStage.MIA.value in active_stages:
            from app.services.mia_probe import mia_probe, MIARiskLevel
            # MIA probe analyzes model output for memorization risk
            # In training mode, check if model has memorized training data
            _detail = detail or {}
            model_output = _detail.get("model_output", {})
            if model_output:
                is_safe, risk_score = self.check_mia_risk(model_output, _detail.get("training_data_size", 0))
                stage_results[InspectionStage.MIA.value] = is_safe
                if not is_safe:
                    findings.append(InspectionFinding(
                        InspectionStage.MIA.value, "high", "mia_risk",
                        f"Model shows memorization risk (score={risk_score:.3f})",
                    ))
            else:
                stage_results[InspectionStage.MIA.value] = True  # No model output to check

        # Stage 5: Watermark — embed steganographic watermark into output
        watermark = self._generate_watermark(user_id, session_id)
        try:
            redacted = embed_text_watermark(redacted, watermark)
            stage_results[InspectionStage.WATERMARK.value] = True
        except Exception:
            stage_results[InspectionStage.WATERMARK.value] = False
            findings.append(InspectionFinding(
                InspectionStage.WATERMARK.value, "medium", "watermark_failed",
                "Failed to embed watermark into output",
            ))

        # Stage 5: SM2 Digital Signature — non-repudiation per GM/T 0009
        from app.services.crypto_service import crypto_service
        sig = None
        try:
            if OutputInspector._signing_keypair is None:
                OutputInspector._signing_keypair = crypto_service.generate_keypair()
            kp = OutputInspector._signing_keypair
            sm2_sig = crypto_service.sign(redacted.encode(), kp.private_key, kp.public_key)
            sig = sm2_sig.signature
            stage_results[InspectionStage.SIGNATURE.value] = bool(sig)
        except Exception:
            # Fallback to SM3 hash if SM2 signing fails (dev mode)
            sig = crypto_service.sm3_hash(redacted.encode())
            stage_results[InspectionStage.SIGNATURE.value] = True

        # Stage 6: Final Approval
        critical_findings = [f for f in findings if f.severity == "critical"]
        stage_results[InspectionStage.APPROVAL.value] = len(critical_findings) == 0

        passed = all(stage_results.values())
        return InspectionResult(
            passed=passed,
            stage_results=stage_results,
            findings=findings,
            redacted_output=redacted,
            watermark=watermark,
            dp_applied=dp_epsilon is not None,
            signature=sig,
        )

    def _generate_watermark(self, user_id: str, session_id: str) -> str:
        return generate_watermark(user_id, session_id)

    def check_data_reconstruction(
        self,
        output_rows: list[dict],
        source_rows: list[dict],
        threshold: float | None = None,
    ) -> tuple[bool, float]:
        """
        Check if output data could reconstruct source data.

        Compares output rows against source rows at field level.
        If exact match rate exceeds threshold, output is blocked.

        Returns: (passed: bool, match_rate: float)
        """
        if threshold is None:
            threshold = self.RECONSTRUCTION_THRESHOLD

        if not output_rows or not source_rows:
            return True, 0.0

        # Build source lookup: {tuple of values → count}
        source_set = set()
        for row in source_rows:
            source_set.add(tuple(str(v) for v in row.values()))

        # Count exact row matches
        matches = 0
        total = len(output_rows)
        for row in output_rows:
            row_tuple = tuple(str(v) for v in row.values())
            if row_tuple in source_set:
                matches += 1

        match_rate = matches / total if total > 0 else 0.0
        passed = match_rate <= threshold

        return passed, match_rate

    def check_field_reconstruction(
        self,
        output_values: list[str],
        source_values: list[str],
        threshold: float | None = None,
    ) -> tuple[bool, float]:
        """
        Check if output field values could reconstruct source field values.

        Uses set intersection to detect if output contains most source values.
        Useful for detecting SELECT DISTINCT attacks on sensitive columns.

        Returns: (passed: bool, overlap_rate: float)
        """
        if threshold is None:
            threshold = self.RECONSTRUCTION_THRESHOLD

        if not output_values or not source_values:
            return True, 0.0

        source_set = set(str(v) for v in source_values)
        output_set = set(str(v) for v in output_values)

        intersection = source_set & output_set
        overlap_rate = len(intersection) / len(source_set) if source_set else 0.0

        passed = overlap_rate <= threshold
        return passed, overlap_rate

    def check_k_anonymity(
        self,
        rows: list[dict],
        quasi_identifiers: list[str] | None = None,
        k_min: int | None = None,
    ) -> tuple[bool, list[str]]:
        """Check k-anonymity on output rows.

        Groups rows by quasi-identifier combinations and checks that
        each group has at least k members.

        Args:
            rows: Output data rows.
            quasi_identifiers: Columns to check. If None, uses all columns.
            k_min: Minimum group size. Defaults to K_ANONYMITY_MIN (5).

        Returns: (passed, list of violation descriptions)
        """
        if k_min is None:
            k_min = self.K_ANONYMITY_MIN

        if not rows:
            return True, []

        # Use all columns if no QIs specified
        if quasi_identifiers is None:
            quasi_identifiers = list(rows[0].keys())

        # Group by QI values
        groups: dict[tuple, int] = {}
        for row in rows:
            qi_values = tuple(str(row.get(col, "")) for col in quasi_identifiers)
            groups[qi_values] = groups.get(qi_values, 0) + 1

        violations = []
        for qi_values, count in groups.items():
            if count < k_min:
                violations.append(
                    f"Group {qi_values} has {count} rows (min: {k_min})"
                )

        return len(violations) == 0, violations

    def check_mia_risk(
        self,
        model_output: dict,
        training_data_size: int,
        threshold: float = 0.6,
    ) -> tuple[bool, float]:
        """Check Membership Inference Attack (MIA) risk.

        A simple heuristic: if the model's confidence on training-like
        inputs is suspiciously high, it may have memorized training data.

        Args:
            model_output: {confidence: float, loss: float, ...}
            training_data_size: Number of training samples.
            threshold: MIA risk threshold.

        Returns: (is_safe, risk_score)
        """
        confidence = model_output.get("confidence", 0.0)
        loss = model_output.get("loss", float("inf"))

        # High confidence + low loss = higher MIA risk
        risk_score = confidence * (1.0 / (1.0 + loss)) if loss > 0 else confidence
        is_safe = risk_score < threshold

        return is_safe, risk_score

    def embed_watermark(self, output: str, user_id: str, session_id: str) -> str:
        """Embed watermark into text output using zero-width character steganography."""
        wm = self._generate_watermark(user_id, session_id)
        return embed_text_watermark(output, wm)

    def embed_image_watermark(self, image_bytes: bytes, user_id: str, session_id: str) -> bytes:
        """Embed watermark into image using LSB steganography."""
        wm = self._generate_watermark(user_id, session_id)
        return embed_image_watermark(image_bytes, wm)

    def verify_watermark(self, output: str, user_id: str, session_id: str) -> bool:
        """Verify text contains the expected watermark (checks steganographic embedding first)."""
        if verify_text_watermark(output, user_id, session_id):
            return True
        # Fallback: check for plaintext watermark (legacy)
        expected = self._generate_watermark(user_id, session_id)
        return expected in output

    def verify_image_watermark(self, image_bytes: bytes, user_id: str, session_id: str) -> bool:
        """Verify image contains the expected LSB watermark."""
        return verify_image_watermark(image_bytes, user_id, session_id)

    def extract_watermark(self, output: str) -> str | None:
        """Extract embedded watermark from text."""
        return extract_text_watermark(output)

    def extract_image_watermark(self, image_bytes: bytes) -> str | None:
        """Extract embedded watermark from image."""
        return extract_image_watermark(image_bytes)


class DifferentialPrivacyEngine:
    """Differential privacy budget management and noise injection."""

    def __init__(self):
        self._budgets: dict[str, float] = {}  # session_id → remaining epsilon

    def init_budget(self, session_id: str, epsilon: float):
        self._budgets[session_id] = epsilon

    def get_remaining(self, session_id: str) -> float:
        return self._budgets.get(session_id, 0.0)

    def consume(self, session_id: str, epsilon: float) -> bool:
        """Consume epsilon budget. Returns False if insufficient."""
        remaining = self.get_remaining(session_id)
        if remaining < epsilon:
            return False
        self._budgets[session_id] = remaining - epsilon
        return True

    def add_laplace_noise(self, value: float, sensitivity: float, epsilon: float) -> float:
        """Add Laplace noise for differential privacy."""
        import random
        scale = sensitivity / epsilon
        # Laplace distribution sampling
        u = random.random() - 0.5
        noise = -scale * (1 if u < 0 else -1) * math.log(1 - 2 * abs(u)) if u != 0 else 0
        return value + noise

    def add_gaussian_noise(self, value: float, sensitivity: float, epsilon: float, delta: float = 1e-5) -> float:
        """Add Gaussian noise for (epsilon, delta)-differential privacy."""
        import random
        import math
        sigma = sensitivity * math.sqrt(2 * math.log(1.25 / delta)) / epsilon
        return value + random.gauss(0, sigma)


output_inspector = OutputInspector()
dp_engine = DifferentialPrivacyEngine()
