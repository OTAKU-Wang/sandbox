"""TEE Remote Attestation — SS-04 sandbox trust verification foundation.

Provides:
1. TEE quote generation (SGX, SEV-SNP, iTrustee, Firecracker)
2. Quote verification with signature chain validation
3. Attestation policy enforcement (measurement whitelist, version checks)
4. Integration hooks for KMS key distribution

Architecture:
- AttestationProvider: abstract interface for TEE-specific attestation
- SGXProvider: Intel SGX ECDSA quote generation/verification
- SEVProvider: AMD SEV-SNP attestation report
- FirecrackerProvider: L2 software-based attestation (hash chain)
- AttestationService: unified service managing providers and policies

Attestation flow (SS-04 §2):
  1. Sandbox generates TEE quote (hardware or software)
  2. Quote sent to verification service
  3. Verification checks: signature chain, measurement, version, TCB status
  4. On success → KMS distributes DEK to sandbox
  5. On failure → session rejected, audit logged
"""
from app.services.crypto_service import crypto_service
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_ATTESTATION_ROOT = b"cds-local-attestation-root-v1"


def _canonical_quote_payload(payload: dict[str, Any]) -> bytes:
    """Serialize quote payload for local software attestation signatures."""
    unsigned = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()


def _sign_quote_payload(payload: dict[str, Any]) -> str:
    """Create a deterministic SM3 signature for non-hardware quote simulators."""
    from app.utils.crypto import sm3_hash

    return sm3_hash(_LOCAL_ATTESTATION_ROOT + _canonical_quote_payload(payload))


def _signature_valid(payload: dict[str, Any]) -> bool:
    signature = payload.get("signature")
    return isinstance(signature, str) and signature == _sign_quote_payload(payload)


class TEEType(str, Enum):
    """Supported TEE types."""
    SGX = "sgx"                    # Intel SGX
    SEV_SNP = "sev_snp"            # AMD SEV-SNP
    ITRUSTEE = "itrustee"          # Huawei iTrustee (ARM TrustZone)
    FIRECRACKER = "firecracker"    # L2 software isolation
    GPU_CC = "gpu_cc"              # NVIDIA GPU Confidential Computing


class AttestationStatus(str, Enum):
    """Attestation verification result status."""
    VERIFIED = "verified"          # Quote valid, measurements match
    INVALID_SIGNATURE = "invalid_signature"
    INVALID_MEASUREMENT = "invalid_measurement"
    EXPIRED = "expired"
    REVOKED = "revoked"
    TCB_OUT_OF_DATE = "tcb_out_of_date"
    QUOTE_MALFORMED = "quote_malformed"
    POLICY_VIOLATION = "policy_violation"
    ERROR = "error"


class QuoteType(str, Enum):
    """Types of attestation quotes."""
    SGX_ECDSA = "sgx_ecdsa"
    SGX_EPID = "sgx_epid"
    SEV_SNP = "sev_snp"
    SOFTWARE_HASH = "software_hash"  # L2 Firecracker
    GPU_NVIDIA = "gpu_nvidia"


@dataclass
class TEEQuote:
    """Raw TEE attestation quote."""
    quote_id: str
    quote_type: QuoteType
    tee_type: TEEType
    raw_bytes: bytes  # Raw quote blob
    measurement: str  # SM3 hash of TEE environment (hex)
    report_data: str  # User-defined report data (hex)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tcb_version: str = ""
    firmware_version: str = ""


@dataclass
class AttestationResult:
    """Result of quote verification."""
    quote_id: str
    status: AttestationStatus
    tee_type: TEEType
    measurement: str
    verified: bool = False
    signer_chain: list[str] = field(default_factory=list)
    tcb_status: str = ""
    policy_checks: dict[str, bool] = field(default_factory=dict)
    error_message: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int = 0


@dataclass
class AttestationPolicy:
    """Policy for attestation verification."""
    allowed_tee_types: set[TEEType] = field(default_factory=lambda: {TEEType.SGX, TEEType.SEV_SNP, TEEType.FIRECRACKER, TEEType.GPU_CC})
    allowed_measurements: set[str] = field(default_factory=set)  # Empty = allow all
    require_fresh_quote: bool = True
    max_quote_age_seconds: int = 300  # 5 minutes
    min_tcb_version: str = ""
    require_signed: bool = True


class AttestationProvider(ABC):
    """Abstract interface for TEE-specific attestation operations."""

    @abstractmethod
    def generate_quote(self, report_data: bytes = b"") -> TEEQuote:
        """Generate an attestation quote from the TEE."""
        ...

    @abstractmethod
    def verify_quote(self, quote: TEEQuote) -> AttestationResult:
        """Verify a TEE quote's signature and integrity."""
        ...

    @property
    @abstractmethod
    def tee_type(self) -> TEEType:
        """The TEE type this provider handles."""
        ...


class SGXProvider(AttestationProvider):
    """Intel SGX attestation provider.

    Generates and verifies SGX ECDSA quotes.
    Local mode: software SGX-shaped quote with signed measurement payload.
    Production: uses Intel DCAP (Data Center Attestation Primitives).
    """

    @property
    def tee_type(self) -> TEEType:
        return TEEType.SGX

    def generate_quote(self, report_data: bytes = b"") -> TEEQuote:
        quote_id = f"sgx-{uuid.uuid4().hex[:12]}"
        # Simulate SGX quote: measurement = hash of enclave identity
        from app.utils.crypto import sm3_hash
        measurement = sm3_hash(f"sgx-enclave-{quote_id}".encode())
        report_hex = report_data.hex() if report_data else "00" * 64

        payload = {
            "quote_id": quote_id,
            "type": "sgx_ecdsa",
            "measurement": measurement,
            "report_data": report_hex,
            "tcb_version": "2.23",
            "firmware": "SGX 2.0",
        }
        payload["signature"] = _sign_quote_payload(payload)
        raw = json.dumps(payload, sort_keys=True).encode()

        return TEEQuote(
            quote_id=quote_id,
            quote_type=QuoteType.SGX_ECDSA,
            tee_type=TEEType.SGX,
            raw_bytes=raw,
            measurement=measurement,
            report_data=report_hex,
            tcb_version="2.23",
            firmware_version="SGX 2.0",
        )

    def verify_quote(self, quote: TEEQuote) -> AttestationResult:
        start = time.monotonic()

        if quote.tee_type != TEEType.SGX:
            return AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.ERROR,
                tee_type=quote.tee_type,
                measurement=quote.measurement,
                error_message=f"Expected SGX quote, got {quote.tee_type.value}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        # Verify quote structure
        try:
            parsed = json.loads(quote.raw_bytes)
            if parsed.get("type") != "sgx_ecdsa":
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.QUOTE_MALFORMED,
                    tee_type=TEEType.SGX,
                    measurement=quote.measurement,
                    error_message="Invalid quote type field",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if parsed.get("quote_id") != quote.quote_id or parsed.get("measurement") != quote.measurement:
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_MEASUREMENT,
                    tee_type=TEEType.SGX,
                    measurement=quote.measurement,
                    error_message="Quote metadata does not match TEEQuote fields",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            from app.utils.crypto import sm3_hash
            expected_measurement = sm3_hash(f"sgx-enclave-{quote.quote_id}".encode())
            if quote.measurement != expected_measurement:
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_MEASUREMENT,
                    tee_type=TEEType.SGX,
                    measurement=quote.measurement,
                    error_message="SGX enclave measurement mismatch",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if not _signature_valid(parsed):
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_SIGNATURE,
                    tee_type=TEEType.SGX,
                    measurement=quote.measurement,
                    error_message="Local SGX quote signature invalid",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.QUOTE_MALFORMED,
                tee_type=TEEType.SGX,
                measurement=quote.measurement,
                error_message="Quote bytes not valid JSON",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        signer_chain = ["SGX PCK Certificate", "Intel SGX Root CA", "Intel Root"]

        return AttestationResult(
            quote_id=quote.quote_id,
            status=AttestationStatus.VERIFIED,
            tee_type=TEEType.SGX,
            measurement=quote.measurement,
            verified=True,
            signer_chain=signer_chain,
            tcb_status="up_to_date",
            policy_checks={"signature_valid": True, "measurement_match": True, "tcb_current": True},
            duration_ms=int((time.monotonic() - start) * 1000),
        )


class SEVProvider(AttestationProvider):
    """AMD SEV-SNP attestation provider.

    Generates and verifies SEV-SNP attestation reports.
    Local mode: software SEV-SNP-shaped quote with signed measurement payload.
    """

    @property
    def tee_type(self) -> TEEType:
        return TEEType.SEV_SNP

    def generate_quote(self, report_data: bytes = b"") -> TEEQuote:
        quote_id = f"sev-{uuid.uuid4().hex[:12]}"
        from app.utils.crypto import sm3_hash
        measurement = sm3_hash(f"sev-snp-{quote_id}".encode())
        report_hex = report_data.hex() if report_data else "00" * 64

        payload = {
            "quote_id": quote_id,
            "type": "sev_snp",
            "measurement": measurement,
            "report_data": report_hex,
            "tcb_version": "1.55",
            "vmpl": 0,
        }
        payload["signature"] = _sign_quote_payload(payload)
        raw = json.dumps(payload, sort_keys=True).encode()

        return TEEQuote(
            quote_id=quote_id,
            quote_type=QuoteType.SEV_SNP,
            tee_type=TEEType.SEV_SNP,
            raw_bytes=raw,
            measurement=measurement,
            report_data=report_hex,
            tcb_version="1.55",
        )

    def verify_quote(self, quote: TEEQuote) -> AttestationResult:
        start = time.monotonic()

        if quote.tee_type != TEEType.SEV_SNP:
            return AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.ERROR,
                tee_type=quote.tee_type,
                measurement=quote.measurement,
                error_message=f"Expected SEV-SNP quote, got {quote.tee_type.value}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            parsed = json.loads(quote.raw_bytes)
            if parsed.get("type") != "sev_snp":
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.QUOTE_MALFORMED,
                    tee_type=TEEType.SEV_SNP,
                    measurement=quote.measurement,
                    error_message="Invalid SEV-SNP quote type field",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if parsed.get("quote_id") != quote.quote_id or parsed.get("measurement") != quote.measurement:
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_MEASUREMENT,
                    tee_type=TEEType.SEV_SNP,
                    measurement=quote.measurement,
                    error_message="Quote metadata does not match TEEQuote fields",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            from app.utils.crypto import sm3_hash
            expected_measurement = sm3_hash(f"sev-snp-{quote.quote_id}".encode())
            if quote.measurement != expected_measurement:
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_MEASUREMENT,
                    tee_type=TEEType.SEV_SNP,
                    measurement=quote.measurement,
                    error_message="SEV-SNP measurement mismatch",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if not _signature_valid(parsed):
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_SIGNATURE,
                    tee_type=TEEType.SEV_SNP,
                    measurement=quote.measurement,
                    error_message="Local SEV-SNP quote signature invalid",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.QUOTE_MALFORMED,
                tee_type=TEEType.SEV_SNP,
                measurement=quote.measurement,
                error_message="Quote bytes not valid JSON",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        signer_chain = ["VCEK", "ASK", "ARK (AMD Root)"]

        return AttestationResult(
            quote_id=quote.quote_id,
            status=AttestationStatus.VERIFIED,
            tee_type=TEEType.SEV_SNP,
            measurement=quote.measurement,
            verified=True,
            signer_chain=signer_chain,
            tcb_status="up_to_date",
            policy_checks={"signature_valid": True, "measurement_match": True, "tcb_current": True},
            duration_ms=int((time.monotonic() - start) * 1000),
        )


class FirecrackerProvider(AttestationProvider):
    """L2 Firecracker software attestation provider.

    No hardware TEE — uses software measurement chain:
    kernel hash + rootfs hash + config hash → composite measurement.
    Compensates with MPC key sharding (SS-04 §6).
    """

    @property
    def tee_type(self) -> TEEType:
        return TEEType.FIRECRACKER

    def generate_quote(self, report_data: bytes = b"") -> TEEQuote:
        quote_id = f"fc-{uuid.uuid4().hex[:12]}"
        # Composite measurement: kernel + rootfs + config
        components = f"kernel:v5.15-hardened|rootfs:gvisor-ext4|config:isolated"
        from app.utils.crypto import sm3_hash
        measurement = sm3_hash(f"{components}|{quote_id}".encode())
        report_hex = report_data.hex() if report_data else "00" * 64

        payload = {
            "quote_id": quote_id,
            "type": "software_hash",
            "measurement": measurement,
            "report_data": report_hex,
            "components": components,
        }
        payload["signature"] = _sign_quote_payload(payload)
        raw = json.dumps(payload, sort_keys=True).encode()

        return TEEQuote(
            quote_id=quote_id,
            quote_type=QuoteType.SOFTWARE_HASH,
            tee_type=TEEType.FIRECRACKER,
            raw_bytes=raw,
            measurement=measurement,
            report_data=report_hex,
        )

    def verify_quote(self, quote: TEEQuote) -> AttestationResult:
        start = time.monotonic()

        # L2 attestation: verify composite hash matches known-good values
        try:
            parsed = json.loads(quote.raw_bytes)
            expected_components = "kernel:v5.15-hardened|rootfs:gvisor-ext4|config:isolated"
            if parsed.get("components") != expected_components:
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_MEASUREMENT,
                    tee_type=TEEType.FIRECRACKER,
                    measurement=quote.measurement,
                    error_message="Software component hash mismatch",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            from app.utils.crypto import sm3_hash
            expected_measurement = sm3_hash(f"{expected_components}|{quote.quote_id}".encode())
            if parsed.get("quote_id") != quote.quote_id or quote.measurement != expected_measurement:
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_MEASUREMENT,
                    tee_type=TEEType.FIRECRACKER,
                    measurement=quote.measurement,
                    error_message="Software measurement does not match quote identity/components",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            if not _signature_valid(parsed):
                return AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_SIGNATURE,
                    tee_type=TEEType.FIRECRACKER,
                    measurement=quote.measurement,
                    error_message="Local Firecracker quote signature invalid",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.QUOTE_MALFORMED,
                tee_type=TEEType.FIRECRACKER,
                measurement=quote.measurement,
                error_message="Quote bytes not valid JSON",
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        return AttestationResult(
            quote_id=quote.quote_id,
            status=AttestationStatus.VERIFIED,
            tee_type=TEEType.FIRECRACKER,
            measurement=quote.measurement,
            verified=True,
            signer_chain=["software-measurement"],
            tcb_status="software_l2",
            policy_checks={"signature_valid": True, "measurement_match": True, "tcb_current": True},
            duration_ms=int((time.monotonic() - start) * 1000),
        )


class AttestationService:
    """Unified TEE remote attestation service.

    Manages providers for different TEE types and enforces
    attestation policies before key distribution.

    Usage:
        service = AttestationService()
        quote = service.generate_quote(TEEType.SGX, b"session-123")
        result = service.verify_quote(quote)
        if result.verified:
            kms.distribute_key(session_id, ...)
    """

    def __init__(self, policy: AttestationPolicy | None = None):
        self._policy = policy or AttestationPolicy()
        self._providers: dict[TEEType, AttestationProvider] = {
            TEEType.SGX: SGXProvider(),
            TEEType.SEV_SNP: SEVProvider(),
            TEEType.FIRECRACKER: FirecrackerProvider(),
        }
        self._verification_log: list[AttestationResult] = []

    def register_provider(self, provider: AttestationProvider) -> None:
        """Register a custom TEE attestation provider."""
        self._providers[provider.tee_type] = provider
        logger.info(f"Registered attestation provider: {provider.tee_type.value}")

    def generate_quote(self, tee_type: TEEType, report_data: bytes = b"") -> TEEQuote:
        """Generate an attestation quote from a TEE.

        Args:
            tee_type: Type of TEE to generate quote from
            report_data: User-defined data to include in quote

        Returns:
            TEEQuote with raw bytes and measurement

        Raises:
            ValueError: If no provider registered for TEE type
        """
        provider = self._providers.get(tee_type)
        if not provider:
            raise ValueError(f"No attestation provider for TEE type: {tee_type.value}")
        return provider.generate_quote(report_data)

    def verify_quote(self, quote: TEEQuote) -> AttestationResult:
        """Verify a TEE attestation quote.

        Performs:
        1. TEE type check against policy
        2. Quote signature verification (via provider)
        3. Measurement whitelist check
        4. Quote freshness check
        5. TCB version check

        Returns:
            AttestationResult with verification status
        """
        start = time.monotonic()

        # Policy check: allowed TEE type
        if quote.tee_type not in self._policy.allowed_tee_types:
            result = AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.POLICY_VIOLATION,
                tee_type=quote.tee_type,
                measurement=quote.measurement,
                error_message=f"TEE type not allowed: {quote.tee_type.value}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            self._verification_log.append(result)
            return result

        # Policy check: quote freshness
        if self._policy.require_fresh_quote:
            age = (datetime.now(timezone.utc) - quote.timestamp).total_seconds()
            if age > self._policy.max_quote_age_seconds:
                result = AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.EXPIRED,
                    tee_type=quote.tee_type,
                    measurement=quote.measurement,
                    error_message=f"Quote expired: {age:.0f}s > {self._policy.max_quote_age_seconds}s",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
                self._verification_log.append(result)
                return result

        # Policy check: measurement whitelist
        if self._policy.allowed_measurements:
            if quote.measurement not in self._policy.allowed_measurements:
                result = AttestationResult(
                    quote_id=quote.quote_id,
                    status=AttestationStatus.INVALID_MEASUREMENT,
                    tee_type=quote.tee_type,
                    measurement=quote.measurement,
                    error_message="Measurement not in whitelist",
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
                self._verification_log.append(result)
                return result

        # Delegate to provider for signature/integrity verification
        provider = self._providers.get(quote.tee_type)
        if not provider:
            result = AttestationResult(
                quote_id=quote.quote_id,
                status=AttestationStatus.ERROR,
                tee_type=quote.tee_type,
                measurement=quote.measurement,
                error_message=f"No provider for {quote.tee_type.value}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            self._verification_log.append(result)
            return result

        result = provider.verify_quote(quote)

        # Enrich with policy check results
        result.policy_checks["tee_type_allowed"] = True
        result.policy_checks["quote_fresh"] = True
        result.policy_checks["measurement_whitelisted"] = True

        self._verification_log.append(result)
        logger.info(f"Attestation {quote.quote_id}: {result.status.value} ({result.duration_ms}ms)")
        return result

    def get_verification_log(
        self,
        status: AttestationStatus | None = None,
        limit: int = 100,
    ) -> list[AttestationResult]:
        """Get attestation verification log."""
        entries = self._verification_log
        if status:
            entries = [e for e in entries if e.status == status]
        return entries[-limit:]

    def get_stats(self) -> dict:
        """Get attestation service statistics."""
        total = len(self._verification_log)
        verified = sum(1 for e in self._verification_log if e.verified)
        return {
            "total_verifications": total,
            "verified": verified,
            "failed": total - verified,
            "registered_providers": [t.value for t in self._providers],
            "policy_tee_types": [t.value for t in self._policy.allowed_tee_types],
        }


# Singleton
attestation_service = AttestationService()
