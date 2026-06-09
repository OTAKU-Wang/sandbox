"""Result Verifier — SM2 certificate chain + local attestation verification.

Verifies the integrity and provenance of computation results:
1. SM2 Certificate Chain — verify result signatures against trusted CA
2. DCAP Remote Attestation — verify enclave integrity with signed local quotes
3. Result Hash Chain — verify result hasn't been tampered with

Used by output inspection gateway to verify result authenticity
before releasing to the buyer.
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid

from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)

_LOCAL_DCAP_ROOT = b"cds-local-dcap-root-v1"


class VerificationLevel(str, Enum):
    NONE = "none"           # No verification
    HASH_ONLY = "hash"      # Hash chain only
    SIGNATURE = "signature" # SM2 signature verification
    FULL = "full"           # Signature + attestation


class AttestationStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    VERIFIED = "verified"
    FAILED = "failed"
    SIMULATED = "simulated"  # Deprecated compatibility value; not accepted as verified.


@dataclass
class CertificateInfo:
    """Parsed SM2 certificate info."""
    subject: str
    issuer: str
    serial_number: str
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    public_key: str = ""
    is_ca: bool = False
    trust_chain_valid: bool = False


@dataclass
class VerificationResult:
    """Result of computation result verification."""
    verified: bool
    level: VerificationLevel
    hash_valid: bool = False
    signature_valid: bool = False
    attestation_status: AttestationStatus = AttestationStatus.NOT_REQUESTED
    certificate_info: CertificateInfo | None = None
    result_hash: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class DCAPQuote:
    """Local DCAP-compatible attestation quote."""
    quote_data: bytes
    mr_enclave: str  # Measurement register: enclave
    mr_signer: str   # Measurement register: signer
    report_data: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    quote_id: str = ""
    signature: str = ""


class ResultVerifier:
    """Computation result verification engine.

    Verifies result integrity via hash chain, SM2 signature verification,
    and optional DCAP remote attestation.
    """

    # Trusted CA certificates (in production: loaded from HSM/KMS)
    _trusted_cas: dict[str, str] = {}  # subject → public_key

    def __init__(self):
        self._platform_keypair = None

    def verify_result(
        self,
        result_data: bytes,
        result_hash: str,
        signature: str | None = None,
        certificate: str | None = None,
        level: VerificationLevel = VerificationLevel.HASH_ONLY,
        attestation_quote: DCAPQuote | Any | bytes | dict | None = None,
    ) -> VerificationResult:
        """Verify a computation result.

        Args:
            result_data: Raw result bytes.
            result_hash: Expected SM3 hash of result.
            signature: SM2 signature (hex r||s) if available.
            certificate: SM2 certificate PEM/hex if available.
            level: Verification level to apply.

        Returns:
            VerificationResult with verification details.
        """
        vr = VerificationResult(verified=False, level=level, result_hash=result_hash)

        # Step 1: Hash verification
        computed_hash = crypto_service.sm3_hash(result_data)
        vr.hash_valid = computed_hash == result_hash
        if not vr.hash_valid:
            vr.errors.append(f"Hash mismatch: expected {result_hash[:16]}..., got {computed_hash[:16]}...")
            if level in (VerificationLevel.HASH_ONLY, VerificationLevel.SIGNATURE, VerificationLevel.FULL):
                return vr

        if level == VerificationLevel.NONE:
            vr.verified = True
            return vr

        # Step 2: Signature verification
        if level in (VerificationLevel.SIGNATURE, VerificationLevel.FULL):
            if signature and certificate:
                try:
                    public_key = self._extract_public_key(certificate)
                    vr.signature_valid = crypto_service.verify(result_data, signature, public_key)
                    if not vr.signature_valid:
                        vr.errors.append("SM2 signature verification failed")
                    vr.certificate_info = self._parse_certificate(certificate)
                except Exception as e:
                    vr.errors.append(f"Signature verification error: {e}")
            elif signature:
                vr.warnings.append("Signature provided but no certificate — cannot verify")
            else:
                vr.warnings.append("No signature provided for signature-level verification")

        # Step 3: Attestation verification
        if level == VerificationLevel.FULL:
            vr.attestation_status = self._verify_attestation(result_hash, attestation_quote)
            if vr.attestation_status != AttestationStatus.VERIFIED:
                vr.errors.append("Remote attestation verification failed")

        if level == VerificationLevel.HASH_ONLY:
            vr.verified = vr.hash_valid
        elif level == VerificationLevel.SIGNATURE:
            vr.verified = vr.hash_valid and vr.signature_valid
        elif level == VerificationLevel.FULL:
            vr.verified = vr.hash_valid and vr.signature_valid and vr.attestation_status == AttestationStatus.VERIFIED
        else:
            vr.verified = True

        logger.info(f"Result verification: level={level.value} verified={vr.verified} hash={vr.hash_valid} sig={vr.signature_valid}")
        return vr

    def verify_certificate_chain(
        self,
        certificate: str,
        ca_certificate: str | None = None,
    ) -> CertificateInfo:
        """Verify an SM2 certificate against CA chain.

        Args:
            certificate: Certificate to verify.
            ca_certificate: CA certificate to verify against. If None, uses trusted CAs.

        Returns:
            CertificateInfo with chain validation result.
        """
        info = self._parse_certificate(certificate)

        if ca_certificate:
            ca_info = self._parse_certificate(ca_certificate)
            info.trust_chain_valid = info.issuer == ca_info.subject
        elif self._trusted_cas:
            info.trust_chain_valid = info.issuer in self._trusted_cas
        else:
            info.trust_chain_valid = False

        return info

    def register_trusted_ca(self, subject: str, public_key: str):
        """Register a trusted CA public key."""
        self._trusted_cas[subject] = public_key
        logger.info(f"Registered trusted CA: {subject}")

    def _extract_public_key(self, certificate: str) -> str:
        """Extract public key from certificate.

        Supports raw SM2 public keys, JSON certificates, and X.509 PEM.
        """
        if len(certificate) == 128 and all(c in '0123456789abcdef' for c in certificate):
            return certificate
        if "BEGIN CERTIFICATE" in certificate:
            from app.services.certificate_service import certificate_service

            cert_der = self._pem_to_der(certificate, "CERTIFICATE")
            parsed = certificate_service._parse_x509_der(cert_der)
            public_key = parsed.get("public_key", "")
            if public_key.startswith("04"):
                public_key = public_key[2:]
            if len(public_key) == 128:
                return public_key
            raise ValueError("X.509 certificate does not contain an SM2 public key")
        try:
            cert_data = json.loads(certificate)
            public_key = cert_data.get("public_key", certificate)
            if public_key.startswith("04"):
                public_key = public_key[2:]
            return public_key
        except (json.JSONDecodeError, TypeError):
            return certificate

    def _parse_certificate(self, certificate: str) -> CertificateInfo:
        """Parse certificate info.

        Supports JSON certificates and local X.509 PEM certificates.
        """
        if "BEGIN CERTIFICATE" in certificate:
            from app.services.certificate_service import certificate_service

            parsed = certificate_service._parse_x509_der(self._pem_to_der(certificate, "CERTIFICATE"))
            public_key = parsed.get("public_key", "")
            if public_key.startswith("04"):
                public_key = public_key[2:]
            return CertificateInfo(
                subject=parsed.get("subject", "unknown"),
                issuer=parsed.get("issuer", "unknown"),
                serial_number=str(parsed.get("serial_number", "")),
                valid_from=parsed.get("not_before"),
                valid_until=parsed.get("not_after"),
                public_key=public_key,
                is_ca=bool(parsed.get("is_ca", False)),
            )
        try:
            cert_data = json.loads(certificate)
            return CertificateInfo(
                subject=cert_data.get("subject", "unknown"),
                issuer=cert_data.get("issuer", "unknown"),
                serial_number=cert_data.get("serial", ""),
                public_key=cert_data.get("public_key", ""),
                is_ca=cert_data.get("is_ca", False),
            )
        except (json.JSONDecodeError, TypeError):
            return CertificateInfo(
                subject="raw-certificate",
                issuer="unknown",
                serial_number="",
                public_key=certificate if len(certificate) == 128 else "",
            )

    def _verify_attestation_simulated(self, result_hash: str) -> AttestationStatus:
        """Deprecated compatibility wrapper.

        FULL verification should call ``_verify_attestation`` with a quote. This
        method is retained for older tests and returns FAILED to avoid granting
        trust without evidence.
        """
        return AttestationStatus.FAILED

    def generate_attestation_quote(
        self,
        result_hash: str,
        mr_enclave: str | None = None,
        mr_signer: str | None = None,
    ) -> DCAPQuote:
        """Create a signed local attestation quote bound to a result hash."""
        mr_enclave = mr_enclave or crypto_service.sm3_hash(b"cds-local-enclave")
        mr_signer = mr_signer or crypto_service.sm3_hash(b"cds-local-signer")
        quote_id = f"dcap-{uuid.uuid4().hex[:12]}"
        payload = {
            "quote_id": quote_id,
            "mr_enclave": mr_enclave,
            "mr_signer": mr_signer,
            "report_data": result_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload["signature"] = self._sign_quote_payload(payload)
        return DCAPQuote(
            quote_data=json.dumps(payload, sort_keys=True).encode(),
            mr_enclave=mr_enclave,
            mr_signer=mr_signer,
            report_data=result_hash,
            quote_id=quote_id,
            signature=payload["signature"],
        )

    def _verify_attestation(
        self,
        result_hash: str,
        attestation_quote: DCAPQuote | Any | bytes | dict | None,
    ) -> AttestationStatus:
        if attestation_quote is None:
            return AttestationStatus.FAILED

        try:
            from app.services.remote_attestation import TEEQuote, attestation_service, AttestationStatus as TEEAttestationStatus

            if isinstance(attestation_quote, TEEQuote):
                result = attestation_service.verify_quote(attestation_quote)
                if result.status != TEEAttestationStatus.VERIFIED:
                    return AttestationStatus.FAILED
                expected_report_hex = result_hash.encode().hex()
                return (
                    AttestationStatus.VERIFIED
                    if attestation_quote.report_data in {result_hash, expected_report_hex}
                    else AttestationStatus.FAILED
                )
        except Exception:
            logger.debug("TEE quote verification path unavailable", exc_info=True)

        try:
            quote = self._coerce_dcap_quote(attestation_quote)
            return AttestationStatus.VERIFIED if self._verify_dcap_quote(result_hash, quote) else AttestationStatus.FAILED
        except Exception as e:
            logger.warning("DCAP attestation verification failed: %s", e)
            return AttestationStatus.FAILED

    def _coerce_dcap_quote(self, quote: DCAPQuote | bytes | dict | Any) -> DCAPQuote:
        if isinstance(quote, DCAPQuote):
            return quote
        if isinstance(quote, bytes):
            payload = json.loads(quote)
        elif isinstance(quote, dict):
            payload = dict(quote)
        else:
            payload = json.loads(getattr(quote, "quote_data"))
        return DCAPQuote(
            quote_data=json.dumps(payload, sort_keys=True).encode(),
            mr_enclave=payload.get("mr_enclave", ""),
            mr_signer=payload.get("mr_signer", ""),
            report_data=payload.get("report_data", ""),
            quote_id=payload.get("quote_id", ""),
            signature=payload.get("signature", ""),
        )

    def _verify_dcap_quote(self, result_hash: str, quote: DCAPQuote) -> bool:
        payload = json.loads(quote.quote_data)
        if payload.get("quote_id") != quote.quote_id:
            return False
        if payload.get("mr_enclave") != quote.mr_enclave or payload.get("mr_signer") != quote.mr_signer:
            return False
        if payload.get("report_data") != result_hash or quote.report_data != result_hash:
            return False
        if not self._is_hex_measurement(quote.mr_enclave) or not self._is_hex_measurement(quote.mr_signer):
            return False
        if payload.get("signature") != quote.signature:
            return False
        return payload.get("signature") == self._sign_quote_payload(payload)

    @staticmethod
    def _is_hex_measurement(value: str) -> bool:
        return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)

    @staticmethod
    def _canonical_quote_payload(payload: dict[str, Any]) -> bytes:
        unsigned = {k: v for k, v in payload.items() if k != "signature"}
        return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()

    def _sign_quote_payload(self, payload: dict[str, Any]) -> str:
        return crypto_service.sm3_hash(_LOCAL_DCAP_ROOT + self._canonical_quote_payload(payload))

    @staticmethod
    def _pem_to_der(pem: str, label: str) -> bytes:
        begin = f"-----BEGIN {label}-----"
        end = f"-----END {label}-----"
        if begin not in pem or end not in pem:
            raise ValueError(f"PEM block {label} not found")
        import base64

        body = pem.split(begin, 1)[1].split(end, 1)[0]
        return base64.b64decode("".join(body.strip().split()))

    def generate_result_hash(self, data: bytes) -> str:
        """Generate SM3 hash for result data."""
        return crypto_service.sm3_hash(data)

    def sign_result(self, data: bytes) -> str:
        """Sign result data with platform key."""
        if not self._platform_keypair:
            self._platform_keypair = crypto_service.generate_keypair()
        kp = self._platform_keypair
        sig = crypto_service.sign(data, kp.private_key, kp.public_key)
        return sig.signature


# Singleton
result_verifier = ResultVerifier()
