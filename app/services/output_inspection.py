"""Output Inspection Gateway — 6-stage review pipeline.
Stages: Format Validation → DLP Scan → Differential Privacy → Watermark → Signature → Final Approval.
"""
import logging
import re
import math
import hashlib
import json
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

from app.services.dp_budget import dp_budget_ledger
from app.utils.watermark import (
    generate_watermark,
    embed_text_watermark,
    extract_text_watermark,
    verify_text_watermark,
    embed_image_watermark,
    extract_image_watermark,
    verify_image_watermark,
)

logger = logging.getLogger(__name__)


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


def _mask_secret(value: object) -> str:
    """A189: 命中值进入 findings.samples 前的掩码——只留可核对的首尾片段。

    findings.samples 会随 security_report 回给调用方并写进审计：正文脱敏而样本留原值，
    等于「输出管控自己把被禁止外发的 PII 再送一遍」。短值（<=6）整体掩码，
    长值留 len/3 前缀 + len/4 后缀（18 位身份证号 -> 110101****7758）。
    """
    if isinstance(value, str):
        text = value
    elif isinstance(value, tuple):
        text = " ".join(str(part) for part in value)
    else:
        text = str(value)
    if len(text) <= 6:
        return "****"
    return f"{text[:len(text) // 3]}****{text[-(len(text) // 4):]}"


# A190: 纯整数字符串只有在「写成 int 再写回来一模一样」时才与数值同形，
# 这个条件就是台账的「字符串 ID 不去前导零」："123" == 123，而 "007" != 7。
_PLAIN_INT_RE = re.compile(r"^[+-]?[0-9]+$")


def _correlation_digest(text: str) -> str | None:
    """A193: 未签名报告的关联摘要——只用 stdlib hashlib，绝不调用可抛的国密运算。

    降级路径上再调 `crypto_service.sm3_hash`（gmssl 缺失且 CDS_SM3_FALLBACK_ALLOWED=false
    时 raise RuntimeError）会让异常从异常处理里逸出，整个审查接口 500。
    """
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except Exception:
        return None


@dataclass
class InspectionResult:
    passed: bool
    stage_results: dict[str, bool]
    findings: list[InspectionFinding]
    redacted_output: str | None = None
    watermark: str | None = None
    dp_applied: bool = False
    # PZ-02 (MR-A3): DP budget honesty fields.
    # dp_budget_remaining: remaining budget after this call's consumption;
    #   None = no budget tracked, DP not applied, or the remaining ε could not be
    #   verified — which is why the three-state dp_budget_state below is the field a
    #   compliance reader must look at (A192e).
    # dp_budget_state: "sufficient" 计账成功 / "exhausted" 核实过且确实不够 /
    #   "unverified" 预算后端读不到（剩余量未知，既不是 0 也不是「没预算」，且绝不因此
    #   走不计账的免费 DP 旁路）/ "unallocated" 未分配预算的 ad-hoc 加噪 / "not_applied"。
    dp_budget_state: str | None = None
    # noisy_output_rows: the structured rows AFTER noise was applied
    #   (None when no structured rows were provided).
    dp_budget_remaining: float | None = None
    noisy_output_rows: list[dict] | None = None
    signature: str | None = None  # SM2 digital signature (hex r||s)
    # A193: SM2 签名失败时的降级摘要只能落在这里，绝不占用 signature：
    # signature 一旦装了摘要，`inspection_to_report` 的 11 个调用点与合规报告就把
    # 「一个哈希」当「一个可抵赖签名」用。None = 本轮确实产出了 SM2 签名。
    unsigned_digest: str | None = None
    # SS-18 §8 P0' 真空披露：某阶段被场景启用但没有任何输入可查时，stage_results 里的 True
    # 是「无从检查」而不是「检查通过」。这里把它列出来，判定方无法再把真空读成通过。
    # 口径（2026-10-16 拍板，spec §8）：成果闸对阶段级真空**只披露不拦**——别往 should_block() 里加条件。
    not_evaluated: list[str] = field(default_factory=list)
    # 降级披露：阶段给了结论但走了弱一档的实现（如 SM2 签名失败退化成 SM3 摘要）。
    degraded: list[str] = field(default_factory=list)
    # A191: 报告签名密钥的来源/公钥。"configured" = 来自部署配置，跨进程与重启稳定，
    # 验签方可拿 signature_public_key 核验；"ephemeral" = 进程内随机临时密钥，
    # 不可抵赖性不成立，必须显式披露而不是让调用方以为这份报告可审计。
    signature_key_source: str | None = None
    signature_public_key: str | None = None


class OutputInspector:
    """6-stage output inspection gateway."""

    # A191: 签名密钥是审计凭据，不能每次进程启动现编一把；锁住惰初始化，
    # 否则同步审查路径（deliveries.py / task_worker.py）并发时同进程会出现两把公钥。
    _signing_keypair = None
    _signing_key_source: str | None = None  # "configured" | "ephemeral"
    _signing_key_lock = threading.Lock()

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

    @classmethod
    def _load_signing_keypair(cls):
        """Return ``(keypair, source)`` used to sign inspection reports (A191)."""
        if cls._signing_keypair is not None:
            return cls._signing_keypair, cls._signing_key_source
        with cls._signing_key_lock:
            if cls._signing_keypair is None:
                keypair, source = cls._resolve_signing_keypair()
                cls._signing_keypair = keypair
                cls._signing_key_source = source
            return cls._signing_keypair, cls._signing_key_source

    @classmethod
    def _resolve_signing_keypair(cls):
        """部署配置里的 SM2 密钥优先（CDS_SM2_PRIVATE_KEY_PATH，公钥可选，缺则推出）。

        配了却读不出来时不假装签名可靠：记 error 并退到临时密钥，由
        ``signature_key_source="ephemeral"`` 向调用方披露。
        """
        from app.core.config import get_settings
        from app.services.crypto_service import SM2KeyPair, crypto_service

        settings = get_settings()
        priv_path = (getattr(settings, "SM2_PRIVATE_KEY_PATH", "") or "").strip()
        pub_path = (getattr(settings, "SM2_PUBLIC_KEY_PATH", "") or "").strip()
        if priv_path:
            try:
                private_key = cls._read_key_file(priv_path, 64)
                public_key = (
                    cls._read_key_file(pub_path, 128)
                    if pub_path
                    else cls._derive_sm2_public_key(private_key)
                )
                return (
                    SM2KeyPair(private_key=private_key, public_key=public_key),
                    "configured",
                )
            except Exception as exc:
                logger.error(
                    "Configured SM2 signing key is unusable (%s) — falling back to an "
                    "ephemeral in-process keypair; report signatures are NOT auditable", exc,
                )
        try:
            return crypto_service.generate_keypair(), "ephemeral"
        except Exception as exc:
            logger.error("No SM2 signing key available (%s)", exc)
            return None, "unavailable"

    @staticmethod
    def _read_key_file(path: str, expected_len: int) -> str:
        from pathlib import Path

        raw = "".join(Path(path).read_text(encoding="utf-8").split())
        if len(raw) == expected_len + 2 and raw.startswith("04"):
            raw = raw[2:]
        if len(raw) != expected_len or any(char not in "0123456789abcdefABCDEF" for char in raw):
            raise ValueError(f"{path}: expected {expected_len} hex chars, got {len(raw)}")
        return raw.lower()

    @staticmethod
    def _derive_sm2_public_key(private_key: str) -> str:
        from gmssl import sm2 as _sm2

        table = _sm2.default_ecc_table
        scalar = int(private_key, 16)
        if not 1 <= scalar < int(table["n"], 16):
            raise ValueError("SM2 private key scalar out of range")
        return _sm2.CryptSM2(private_key=private_key, public_key="")._kg(scalar, table["g"])[:128]

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
        not_evaluated: list[str] = []
        degraded: list[str] = []
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
                    samples=[_mask_secret(match) for match in matches[:3]],
                ))
                redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
        stage_results[InspectionStage.DLP.value] = dlp_passed

        # Stage 3: Data Reconstruction Detection
        if InspectionStage.DATA_RECONSTRUCTION.value in active_stages:
            stage_results[InspectionStage.DATA_RECONSTRUCTION.value] = True
            source_rows = (detail or {}).get("source_rows")
            recon_comparable = bool(output_rows) and bool(source_rows)
            if not recon_comparable:
                not_evaluated.append(InspectionStage.DATA_RECONSTRUCTION.value)
            if recon_comparable:
                passed_recon, match_rate = self.check_data_reconstruction(output_rows, source_rows)
                if not passed_recon:
                    stage_results[InspectionStage.DATA_RECONSTRUCTION.value] = False
                    findings.append(InspectionFinding(
                        InspectionStage.DATA_RECONSTRUCTION.value, "critical",
                        "data_reconstruction",
                        f"Output row match rate {match_rate:.1%} exceeds threshold {self.RECONSTRUCTION_THRESHOLD:.1%}",
                    ))

                # A190: 整行比对只拦「整行搬走」；SELECT DISTINCT 把敏感列整体拖走时
                # 整行并不相等，只有逐字段集交口径看得见——以前它根本没被主阶段调用。
                field_hits = self._field_reconstruction_hits(
                    output_rows, source_rows, self.RECONSTRUCTION_THRESHOLD
                )
                if field_hits:
                    stage_results[InspectionStage.DATA_RECONSTRUCTION.value] = False
                    findings.append(InspectionFinding(
                        InspectionStage.DATA_RECONSTRUCTION.value, "high",
                        "field_reconstruction",
                        "Column-level reconstruction: "
                        + ", ".join(f"{col}={rate:.1%}" for col, rate, _hit, _src in field_hits[:5])
                        + f" of the reference column's distinct values reproduced "
                        f"(threshold {self.RECONSTRUCTION_THRESHOLD:.1%})",
                        # 只报列名与计数：findings.samples 会随 security_report 回给调用方（A189）
                        samples=[
                            f"{col}: {hit}/{src} distinct reference values reproduced"
                            for col, _rate, hit, src in field_hits[:5]
                        ],
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
            not_evaluated.append(InspectionStage.K_ANONYMITY.value)

        # Stage 4: Differential Privacy — REAL noise application (PZ-02 / MR-A3)
        #
        # dp_applied must reflect what actually happened:
        #   - epsilon is None or <= 0 -> DP NOT applied (epsilon=0 means
        #     "DP not applied", not an infinite budget);
        #   - epsilon > 0            -> Laplace noise is applied to the
        #     numeric fields of the structured output rows and the session's
        #     DP budget is consumed once. The budget lives in the persistent
        #     dp_budget_ledger (single source of truth — M-03 双预算 unification);
        #     the engine's own _budgets dict is gone.
        # A tracked budget has three possible verdicts (A192e): enough left, verified
        # and used up, or unreadable. The last one reports "unverified" with an unknown
        # remaining — never 0.0 and never "no budget" — because it must not open the
        # unaccounted-DP bypass. Both a verified shortfall and an unverifiable budget
        # block the output (fail-closed: release without the promised DP protection is
        # not allowed).
        dp_applied = False
        dp_budget_remaining: float | None = None
        # A192e 三态口径：sufficient / exhausted / unverified（另加 unallocated、not_applied）。
        dp_budget_state: str | None = None
        noisy_rows: list[dict] | None = None
        if InspectionStage.DP.value in active_stages:
            stage_results[InspectionStage.DP.value] = True
            if dp_epsilon is not None and dp_epsilon > 0:
                budget_before = dp_budget_ledger.budget_state_sync(session_id)
                if budget_before["tracked"]:
                    if not dp_engine.consume(session_id, dp_epsilon):
                        # 消耗被拒有两种完全不同的原因（A192e）：预算核实过确实不够，和
                        # 预算后端读不到所以无从核实。dp_budget 用 source="unverified" 区分
                        # 两者；把它压成 exhausted 就是把「无法核实」谎报成「已耗尽」。
                        budget_after = dp_budget_ledger.budget_state_sync(session_id)
                        unverifiable = (
                            budget_before["source"] == "unverified"
                            or budget_after["source"] == "unverified"
                        )
                        stage_results[InspectionStage.DP.value] = False
                        if unverifiable:
                            # 剩余量未知 ⇒ 既不报 0.0（那是「已耗尽」的读数），也绝不退到
                            # 「没预算」分支去走不计账的免费 DP 旁路：照样 fail-closed。
                            dp_budget_state = "unverified"
                            backend_error = (
                                budget_after["error"]
                                or budget_before["error"]
                                or "the budget backend returned no readable value"
                            )
                            findings.append(InspectionFinding(
                                InspectionStage.DP.value, "high", "dp_budget_unverified",
                                f"DP epsilon budget UNVERIFIED — the shared ε budget backend "
                                f"could not be read ({backend_error}; requested={dp_epsilon:.4f}). "
                                f"Remaining ε is UNKNOWN, not zero: the DP accounting promised for "
                                f"this output can be neither confirmed nor ruled out, so the output "
                                f"is withheld fail-closed and the unaccounted-DP bypass stays "
                                f"refused. Retry once budget_backend_health() reports an "
                                f"authoritative backend; 「无法核实」不是「预算已用光」。",
                            ))
                        else:
                            # 预算可读且确实不够 -> fail closed。
                            dp_budget_state = "exhausted"
                            dp_budget_remaining = dp_engine.get_remaining(session_id)
                            findings.append(InspectionFinding(
                                InspectionStage.DP.value, "high", "dp_budget_exhausted",
                                f"DP epsilon budget exhausted (remaining="
                                f"{dp_budget_remaining:.4f}, requested={dp_epsilon:.4f})",
                            ))
                    else:
                        noisy_rows, _noised = self._apply_dp_noise(output_rows, dp_epsilon)
                        if output_rows is not None:
                            output_rows = noisy_rows
                        dp_applied = True
                        dp_budget_state = "sufficient"
                        dp_budget_remaining = dp_engine.get_remaining(session_id)
                        logger.info(
                            "DP applied: session=%s epsilon=%.4f remaining=%.4f",
                            session_id, dp_epsilon, dp_budget_remaining,
                        )
                else:
                    # No budget allocated for this session: the noise is
                    # still real, but nothing is consumed (no budget exists
                    # to consume). Session budgets are allocated via
                    # /output-control/dp/budget/init or the contract ledger.
                    noisy_rows, _noised = self._apply_dp_noise(output_rows, dp_epsilon)
                    if output_rows is not None:
                        output_rows = noisy_rows
                    dp_applied = True
                    dp_budget_state = "unallocated"
                    dp_budget_remaining = None
                    logger.info(
                        "DP applied ad-hoc (no budget tracked for session %s); "
                        "epsilon=%.4f not consumed", session_id, dp_epsilon,
                    )
                    # A192: 噪声是真的加了，但不计账的旁路无限次可用且只在当前进程可见，
                    # 只在日志里说一句调用方看不到，所以进 degraded 随报告带出。
                    degraded.append(
                        f"{InspectionStage.DP.value}: dp_no_budget (Laplace noise applied, but no "
                        f"epsilon budget is allocated for this session — epsilon={dp_epsilon:.4f} "
                        f"was not accounted, so this bypass is unlimited and process-local; "
                        f"allocate a budget via /output-control/dp/budget/init)"
                    )
            else:
                # A192: 阶段启用却没跑（epsilon 缺省或 <=0，即「DP not applied」）。
                # SS-18 §8 口径 = 阶段级真空只披露不拦，所以 stage_results 保持 True、
                # passed 不变，但必须登记 not_evaluated：其余三个阶段都登记了，
                # 唯独 DP 不进披露渠道就是谎报。
                dp_budget_state = "not_applied"
                not_evaluated.append(InspectionStage.DP.value)

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
                not_evaluated.append(InspectionStage.MIA.value)

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
        unsigned_digest: str | None = None
        kp, signing_key_source = self._load_signing_keypair()
        try:
            if kp is None:
                raise RuntimeError(f"no SM2 signing keypair available (source={signing_key_source})")
            sm2_sig = crypto_service.sign(redacted.encode(), kp.private_key, kp.public_key)
            sig = sm2_sig.signature
            stage_results[InspectionStage.SIGNATURE.value] = bool(sig)
            if not sig:
                unsigned_digest = _correlation_digest(redacted)
                degraded.append(
                    f"{InspectionStage.SIGNATURE.value}: unsigned (sign produced an empty value — "
                    f"NOT signed, no GM/T 0009 signature; unsigned_digest is a plain SHA-256 "
                    f"correlation digest, not a signature)"
                )
        except Exception as exc:
            # A193: 签名没做成就是签名阶段失败。以前这里塞一个 SM3 摘要进 signature 并把
            # 阶段记 True，而 passed 是 stage_results 相与、degraded 不参与相与，于是对外
            # 报「签名已做」。现在：阶段 False、signature 留空、降级摘要另存字段，
            # 且 except 路径里不做任何可抛的加密运算。
            stage_results[InspectionStage.SIGNATURE.value] = False
            sig = None
            unsigned_digest = _correlation_digest(redacted)
            degraded.append(
                f"{InspectionStage.SIGNATURE.value}: unsigned (SM2 signing failed: {exc} — "
                f"NOT signed, no GM/T 0009 signature; unsigned_digest is a plain SHA-256 "
                f"correlation digest, not a signature)"
            )
            logger.warning(
                "SM2 signing failed (session=%s key_source=%s): %s", session_id, signing_key_source, exc,
            )

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
            dp_applied=dp_applied,
            dp_budget_state=dp_budget_state,
            dp_budget_remaining=dp_budget_remaining,
            noisy_output_rows=noisy_rows,
            signature=sig,
            unsigned_digest=unsigned_digest,
            not_evaluated=not_evaluated,
            degraded=degraded,
            signature_key_source=signing_key_source,
            signature_public_key=(kp.public_key if kp is not None else None),
        )

    def _generate_watermark(self, user_id: str, session_id: str) -> str:
        return generate_watermark(user_id, session_id)

    def _apply_dp_noise(
        self, output_rows: list[dict] | None, epsilon: float
    ) -> tuple[list[dict] | None, int]:
        """Add Laplace noise to the numeric fields of structured output rows.

        PZ-02 (MR-A3): the DP stage used to be a no-op; this is the real
        mechanism. Sensitivity 1.0 (unit L1 sensitivity of count/aggregate
        query answers — the standard Laplace mechanism setting).

        Returns ``(noisy_rows, num_noisy_cells)``. Free-text outputs have no
        reliable numeric fields, so nothing is perturbed there.
        """
        if not output_rows:
            return None, 0
        noisy = []
        count = 0
        for row in output_rows:
            new_row = {}
            for key, value in row.items():
                if isinstance(value, bool):
                    new_row[key] = value
                elif isinstance(value, (int, float)):
                    new_row[key] = dp_engine.add_laplace_noise(
                        float(value), sensitivity=1.0, epsilon=epsilon
                    )
                    count += 1
                else:
                    new_row[key] = value
            noisy.append(new_row)
        return noisy, count

    def check_data_reconstruction(
        self,
        output_rows: list[dict],
        source_rows: list[dict],
        threshold: float | None = None,
    ) -> tuple[bool, float]:
        """
        Check if output data could reconstruct source data.

        A190: 比对按**列名**对齐、单元格走 `_canon_cell` 归一化（旧实现把
        `row.values()` 拼成元组，改列序 / 改 `2` vs `2.0` / 少几列就零成本绕过）。
        输出行只要能在**某一行参照行**上按列名全部命中（含只取部分列的投影），
        就算搬走一条原始参照行。匹配率仍是行口径。

        Returns: (passed: bool, match_rate: float)
        """
        if threshold is None:
            threshold = self.RECONSTRUCTION_THRESHOLD

        if not output_rows or not source_rows:
            return True, 0.0

        # 参照行按列名签名分组；输出行取的是哪几列，就把参照行投影到那几列比。
        source_by_cols: dict[frozenset, list[frozenset]] = {}
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            canon = self._canon_row(row)
            source_by_cols.setdefault(frozenset(col for col, _ in canon), []).append(canon)

        projected: dict[tuple[frozenset, frozenset], set[frozenset]] = {}
        matches = 0
        total = len(output_rows)
        for row in output_rows:
            if not isinstance(row, dict):
                continue
            canon = self._canon_row(row)
            cols = frozenset(col for col, _ in canon)
            for sig, rows in source_by_cols.items():
                if not cols.issubset(sig):
                    continue
                key = (sig, cols)
                bucket = projected.get(key)
                if bucket is None:
                    bucket = {
                        frozenset(pair for pair in src if pair[0] in cols) for src in rows
                    }
                    projected[key] = bucket
                if canon in bucket:
                    matches += 1
                    break

        match_rate = matches / total if total > 0 else 0.0
        passed = match_rate <= threshold

        return passed, match_rate

    @classmethod
    def _canon_cell(cls, value: object) -> tuple[str, object]:
        """参照比对用的单元格归一化（A190）。

        - 数值一律用 `Decimal` 比：`2` 与 `2.0`、`"123"` 与 `123` 是同一个值，
          改字面量写法不该绕开检测；用 Decimal 而不是 float 是为了大整数 ID
          （银行卡号这类 16-19 位）不因为 2**53 精度丢失而互相误判。
        - **字符串 ID 不去前导零**（台账口径）：`"007"` 与 `7` 保持不等，
          否则两个真实存在的不同编号会被并成一个值，反过来制造误报。
        """
        if isinstance(value, bool):
            return ("s", str(value).lower())
        if isinstance(value, (int, Decimal)):
            return ("n", Decimal(value))
        if isinstance(value, float):
            try:
                return ("n", Decimal(str(value)))
            except (InvalidOperation, ValueError):
                return ("s", str(value))
        if isinstance(value, str):
            text = value.strip()
            if _PLAIN_INT_RE.match(text) and str(int(text)) == text.lstrip("+"):
                return ("n", Decimal(text))
            return ("s", value)
        return ("s", str(value))

    @classmethod
    def _canon_row(cls, row: dict) -> frozenset:
        return frozenset((str(col), cls._canon_cell(val)) for col, val in row.items())

    @classmethod
    def _field_key(cls, value: object) -> str:
        tag, payload = cls._canon_cell(value)
        return f"{tag}:{payload}"

    def _field_reconstruction_hits(
        self,
        output_rows: list[dict],
        source_rows: list[dict],
        threshold: float,
    ) -> list[tuple[str, float, int, int]]:
        """A190: 逐字段（集交）口径——整行比对看不见的 SELECT DISTINCT 拖列。

        只在参照列 distinct 值数量 >= 1/阈值 时判别：低基数列碰一下就越过集交阈值，
        那种泄漏归 k-匿名阶段管，在这里报警只会误杀正常聚合。
        返回 `(列名, 重合率, 被搬走的值数, 参照列 distinct 数)`。
        """
        floor = max(2, math.ceil(1.0 / threshold)) if threshold > 0 else 2
        source_values: dict[str, set[str]] = {}
        output_values: dict[str, set[str]] = {}
        for rows, bucket in ((source_rows, source_values), (output_rows, output_values)):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for col, val in row.items():
                    bucket.setdefault(str(col), set()).add(self._field_key(val))

        hits: list[tuple[str, float, int, int]] = []
        for col, out_vals in output_values.items():
            src_vals = source_values.get(col)
            if not src_vals or len(src_vals) < floor:
                continue
            passed, rate = self.check_field_reconstruction(
                sorted(out_vals), sorted(src_vals), threshold
            )
            if not passed:
                hits.append((col, rate, len(src_vals & out_vals), len(src_vals)))
        return hits

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
                masked_group = tuple(_mask_secret(value) for value in qi_values)
                violations.append(
                    f"Group {masked_group} has {count} rows (min: {k_min})"
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
    """Differential privacy noise + budget facade (PZ-02 / MR-A3).

    Budget unification (M-03 双预算): the per-session ``_budgets`` dict that
    used to live here is deleted — the persistent ``dp_budget_ledger`` is
    the single source of truth, and every budget read/write in this class
    delegates to it (its in-process write-through cache is the synchronous
    fast path used from sync contexts; the ledger's DB rows remain the
    persistent authority). epsilon=0 means "DP not applied" and is never
    consumed.
    """

    def init_budget(self, session_id: str, epsilon: float) -> float:
        return dp_budget_ledger.allocate_sync(session_id, epsilon)

    def get_remaining(self, session_id: str) -> float:
        return dp_budget_ledger.get_remaining_sync(session_id)

    def consume(self, session_id: str, epsilon: float) -> bool:
        """Consume epsilon budget via the ledger. Returns False when the
        budget is insufficient or epsilon is not a meaningful consumption
        (epsilon=0 -> "DP not applied")."""
        return dp_budget_ledger.consume_sync(session_id, epsilon)

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
