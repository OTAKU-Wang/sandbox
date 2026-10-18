"""Mutual TLS (mTLS) with SM2 certificates for federation spaces.

Provides certificate generation, signing, and verification for
cross-space communication using the Chinese national standard SM2
elliptic curve algorithm.

Architecture:
  Space A ←→ mTLS handshake ←→ Space B
    ↓                              ↓
  SM2 cert (self-signed or CA)    SM2 cert
    ↓                              ↓
  Verify peer cert chain          Verify peer cert chain

Each federation trust relationship uses mTLS to authenticate both sides
of the connection. Certificates are generated per-space and can be:
- Self-signed (for testing / simple deployments)
- CA-signed (for production with a shared or external CA)
"""
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


@dataclass
class CertificateInfo:
    """Certificate metadata."""
    subject: str
    issuer: str
    serial_number: str
    not_before: datetime
    not_after: datetime
    public_key: str
    signature: str
    is_ca: bool = False
    space_id: str = ""


@dataclass
class MTLSIdentity:
    """An mTLS identity with keypair and certificate."""
    identity_id: str
    space_id: str
    private_key: str
    public_key: str
    certificate: CertificateInfo
    ca_certificate: CertificateInfo | None = None


class SM2CertificateAuthority:
    """SM2-based Certificate Authority for federation mTLS.

    Generates and signs X.509-style certificates using SM2.
    In production, this would use a proper ASN.1/X.509 library;
    this implementation provides the cryptographic primitives
    and certificate chain logic.
    """

    def __init__(self, ca_keypair=None):
        try:
            from app.services.crypto_service import crypto_service
            self._crypto = crypto_service
            if ca_keypair:
                self._ca_keypair = ca_keypair
            else:
                self._ca_keypair = self._crypto.generate_keypair()
        except Exception as e:
            logger.warning(f"[mTLS] SM2 not available: {e}")
            self._crypto = None
            self._ca_keypair = None

    @property
    def available(self) -> bool:
        return self._crypto is not None and self._ca_keypair is not None

    def generate_ca_certificate(self, subject: str = "CDS Federation CA") -> CertificateInfo:
        """Generate a self-signed CA certificate."""
        if not self.available:
            raise RuntimeError("SM2 not available")

        serial = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)

        # Build certificate payload
        cert_data = {
            "subject": subject,
            "issuer": subject,  # self-signed
            "serial": serial,
            "not_before": now.isoformat(),
            "not_after": (now + timedelta(days=3650)).isoformat(),  # 10 years
            "public_key": self._ca_keypair.public_key,
            "is_ca": True,
        }

        # Sign the certificate payload
        payload_str = "|".join(f"{k}={v}" for k, v in sorted(cert_data.items()))
        signature = self._crypto.sign(
            payload_str.encode(),
            self._ca_keypair.private_key,
            self._ca_keypair.public_key,
        )

        return CertificateInfo(
            subject=subject,
            issuer=subject,
            serial_number=serial,
            not_before=now,
            not_after=now + timedelta(days=3650),
            public_key=self._ca_keypair.public_key,
            signature=signature.signature if hasattr(signature, 'signature') else str(signature),
            is_ca=True,
        )

    def issue_certificate(self, space_id: str, public_key: str,
                          subject: str | None = None,
                          validity_days: int = 365) -> CertificateInfo:
        """Issue a certificate for a space, signed by this CA."""
        if not self.available:
            raise RuntimeError("SM2 not available")

        serial = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)
        subj = subject or f"CDS Space {space_id}"

        cert_data = {
            "subject": subj,
            "issuer": "CDS Federation CA",
            "serial": serial,
            "not_before": now.isoformat(),
            "not_after": (now + timedelta(days=validity_days)).isoformat(),
            "public_key": public_key,
            "is_ca": False,
        }
        if space_id:
            cert_data["space_id"] = space_id

        payload_str = "|".join(f"{k}={v}" for k, v in sorted(cert_data.items()))
        signature = self._crypto.sign(
            payload_str.encode(),
            self._ca_keypair.private_key,
            self._ca_keypair.public_key,
        )

        return CertificateInfo(
            subject=subj,
            issuer="CDS Federation CA",
            serial_number=serial,
            not_before=now,
            not_after=now + timedelta(days=validity_days),
            public_key=public_key,
            signature=signature.signature if hasattr(signature, 'signature') else str(signature),
            is_ca=False,
            space_id=space_id,
        )

    def verify_certificate(self, cert: CertificateInfo,
                           ca_cert: CertificateInfo | None = None) -> bool:
        """Verify a certificate's signature against the CA.

        Returns True if the certificate is valid and properly signed.
        """
        if not self.available:
            return False

        ca = ca_cert or self.generate_ca_certificate()

        # Check expiry
        now = datetime.now(timezone.utc)
        if now < cert.not_before or now > cert.not_after:
            logger.warning(f"[mTLS] Certificate expired: {cert.subject}")
            return False

        # Reconstruct the signed payload
        cert_data = {
            "subject": cert.subject,
            "issuer": cert.issuer,
            "serial": cert.serial_number,
            "not_before": cert.not_before.isoformat(),
            "not_after": cert.not_after.isoformat(),
            "public_key": cert.public_key,
            "is_ca": cert.is_ca,
        }
        if cert.space_id:
            cert_data["space_id"] = cert.space_id

        payload_str = "|".join(f"{k}={v}" for k, v in sorted(cert_data.items()))

        # Verify signature using CA's public key
        return self._crypto.verify(
            payload_str.encode(),
            cert.signature,
            ca.public_key,
        )


class MTLSManager:
    """Manages mTLS identities and certificate chains for federation.

    Each space gets an MTLSIdentity with:
    - SM2 keypair (private + public)
    - Certificate signed by the federation CA
    - CA certificate for verifying peers
    """

    def __init__(self):
        self._ca = SM2CertificateAuthority()
        self._identities: dict[str, MTLSIdentity] = {}
        self._ca_cert: CertificateInfo | None = None

    @property
    def available(self) -> bool:
        return self._ca.available

    def initialize_ca(self) -> CertificateInfo:
        """Initialize the federation CA and return the CA certificate."""
        if self._ca_cert is None:
            self._ca_cert = self._ca.generate_ca_certificate()
        return self._ca_cert

    def create_identity(self, space_id: str) -> MTLSIdentity:
        """Create an mTLS identity for a space.

        Generates an SM2 keypair and issues a CA-signed certificate.
        """
        if not self.available:
            raise RuntimeError("SM2/mTLS not available")

        # Ensure CA is initialized
        if self._ca_cert is None:
            self.initialize_ca()

        # Generate keypair for this space
        keypair = self._ca._crypto.generate_keypair()

        # Issue certificate
        cert = self._ca.issue_certificate(space_id, keypair.public_key)

        identity = MTLSIdentity(
            identity_id=f"mtls-{uuid.uuid4().hex[:12]}",
            space_id=space_id,
            private_key=keypair.private_key,
            public_key=keypair.public_key,
            certificate=cert,
            ca_certificate=self._ca_cert,
        )

        self._identities[space_id] = identity
        logger.info(f"[mTLS] Created identity for space {space_id}")
        return identity

    def get_identity(self, space_id: str) -> MTLSIdentity | None:
        """Get the mTLS identity for a space."""
        return self._identities.get(space_id)

    def verify_peer_certificate(self, cert: CertificateInfo) -> bool:
        """Verify a peer's certificate against the federation CA."""
        if self._ca_cert is None:
            logger.warning("[mTLS] CA not initialized, cannot verify peer cert")
            return False
        return self._ca.verify_certificate(cert, self._ca_cert)

    def get_tls_context(self, space_id: str) -> dict | None:
        """Get TLS context parameters for a space (for httpx/ssl).

        Returns a dict with cert/key paths or in-memory cert data
        that can be used with httpx or ssl contexts.
        """
        identity = self._identities.get(space_id)
        if not identity:
            return None

        return {
            "space_id": space_id,
            "cert_pem": self._cert_to_pem(identity.certificate),
            "key_der": identity.private_key,
            "ca_cert_pem": self._cert_to_pem(identity.ca_certificate) if identity.ca_certificate else None,
            "verify": True,
        }

    def _cert_to_pem(self, cert: CertificateInfo) -> str:
        """Convert a CertificateInfo to a PEM-like string representation.

        In production, this would produce proper ASN.1 DER → PEM encoding.
        """
        lines = [
            "-----BEGIN CERTIFICATE-----",
            f"Subject: {cert.subject}",
            f"Issuer: {cert.issuer}",
            f"Serial: {cert.serial_number}",
            f"Valid: {cert.not_before.isoformat()} to {cert.not_after.isoformat()}",
            f"PublicKey: {cert.public_key[:64]}...",
            f"Signature: {cert.signature[:64]}...",
            "-----END CERTIFICATE-----",
        ]
        return "\n".join(lines)


# PZ-05 (MR-A8): real federation mTLS material, loaded from
# CDS_FEDERATION_CA_BUNDLE / CDS_FEDERATION_CERT / CDS_FEDERATION_KEY (PEM file
# paths). The MTLSManager identities above remain the SM2 bookkeeping layer;
# this is the actual certificate material the HTTP channel terminates with.
def load_federation_mtls_material() -> dict | None:
    """Load and validate real federation mTLS material from settings.

    Returns None when nothing is configured (dev: the channel keeps working
    with plain verified TLS, and self-signed test certs can be pointed at via
    the CDS_FEDERATION_* settings). When ALL of CA bundle, cert and key are
    configured, validates that the files exist, are PEM, and the cert/key pair
    loads — raises ValueError on invalid material (misconfiguration must fail
    loudly, never degrade silently). The production fail-closed gate (missing
    material while federation is enabled) lives in validate_security_config
    and FederationConnector.
    """
    import ssl
    from pathlib import Path

    from app.core.config import get_settings

    s = get_settings()
    ca_bundle, cert, key = s.FEDERATION_CA_BUNDLE, s.FEDERATION_CERT, s.FEDERATION_KEY
    if not (ca_bundle or cert or key):
        return None
    if not (ca_bundle and cert and key):
        raise ValueError(
            "Federation mTLS material is partially configured: "
            "CDS_FEDERATION_CA_BUNDLE, CDS_FEDERATION_CERT and CDS_FEDERATION_KEY "
            "must all be set together"
        )

    def _read_pem(path: str, label: str, must_contain: str) -> None:
        p = Path(path)
        if not p.is_file():
            raise ValueError(f"Federation {label} not found: {path}")
        text = p.read_text()
        if must_contain not in text:
            raise ValueError(f"Federation {label} is not valid PEM ({must_contain} missing): {path}")

    _read_pem(ca_bundle, "CA bundle", "BEGIN CERTIFICATE")
    _read_pem(cert, "client certificate", "BEGIN CERTIFICATE")
    _read_pem(key, "client key", "PRIVATE KEY")

    # The cert and key must form a loadable pair (ssl will reject mismatched
    # or corrupt material).
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        ctx.load_cert_chain(certfile=cert, keyfile=key)
    except ssl.SSLError as e:
        raise ValueError(f"Federation mTLS cert/key pair is invalid: {e}")

    return {"ca_bundle": ca_bundle, "cert": cert, "key": key}


# Global singleton
mtls_manager = MTLSManager()
