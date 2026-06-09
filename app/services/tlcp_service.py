"""TLCP (国密 TLS) Service — SM2 dual-certificate TLS support.

Implements GM/T 0024 TLCP protocol using SM2 signing + encryption certificates.
Falls back to standard TLS when Tongsuo/OpenSSL-SM2 is not available.
"""
import os
import ssl
import uuid
import logging
import tempfile
import base64
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# SM2 curve parameters (sm2p256v1)
_SM2_P = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFF
_SM2_A = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC
_SM2_N = 0xFFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFF7203DF6B21C6052B53BBF40939D54123
_SM2_GX = 0x32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7
_SM2_GY = 0xBC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0
_SM2_G = (_SM2_GX, _SM2_GY)

# Check for gmssl (SM2 operations)
try:
    from gmssl import sm2 as _sm2, sm3 as _sm3
    _HAS_GMSSL = True
except ImportError:
    _HAS_GMSSL = False
    logger.warning("gmssl not installed — TLCP certificate generation uses software SM2 key math; SM2 signing requires gmssl")


def _inv_mod(a: int, m: int) -> int:
    """Modular inverse using extended Euclidean algorithm."""
    if a < 0:
        a = a % m
    g, x, _ = _extended_gcd(a, m)
    if g != 1:
        raise ValueError("No modular inverse")
    return x % m


def _extended_gcd(a: int, b: int) -> tuple[int, int, int]:
    if a == 0:
        return b, 0, 1
    g, x, y = _extended_gcd(b % a, a)
    return g, y - (b // a) * x, x


def _point_add(p1: tuple[int, int] | None, p2: tuple[int, int] | None) -> tuple[int, int] | None:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and y1 != y2:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + _SM2_A) * _inv_mod(2 * y1, _SM2_P) % _SM2_P
    else:
        lam = (y2 - y1) * _inv_mod(x2 - x1, _SM2_P) % _SM2_P
    x3 = (lam * lam - x1 - x2) % _SM2_P
    y3 = (lam * (x1 - x3) - y1) % _SM2_P
    return (x3, y3)


def _point_mul(k: int, point: tuple[int, int]) -> tuple[int, int] | None:
    result = None
    addend = point
    while k > 0:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def generate_sm2_keypair() -> tuple[str, str]:
    """Generate SM2 key pair (private_key_hex, public_key_hex).

    Returns:
        (private_key: 64 hex chars, public_key: 128 hex chars uncompressed)
    """
    private_key_bytes = os.urandom(32)
    private_key_int = int.from_bytes(private_key_bytes, "big")
    private_key_int = private_key_int % (_SM2_N - 1) + 1

    pub_point = _point_mul(private_key_int, _SM2_G)
    if pub_point is None:
        raise RuntimeError("SM2 key generation failed: point at infinity")

    private_hex = format(private_key_int, "064x")
    public_hex = format(pub_point[0], "064x") + format(pub_point[1], "064x")
    return private_hex, public_hex


@dataclass
class SM2Certificate:
    """SM2 X.509 certificate representation."""
    cert_id: str
    subject: str
    issuer: str
    serial_number: str
    public_key: str  # 128 hex chars (uncompressed point)
    not_before: datetime
    not_after: datetime
    fingerprint: str  # SM3 hash of cert DER
    cert_type: str = "signing"  # signing | encryption
    cert_pem: str = ""
    key_pem: str = ""


@dataclass
class TLCPConfig:
    """TLCP connection configuration."""
    sign_cert_path: str | None = None
    sign_key_path: str | None = None
    enc_cert_path: str | None = None
    enc_key_path: str | None = None
    ca_cert_path: str | None = None
    ciphers: str = "ECDHE-SM2-WITH-SM4-SM3:SM2-WITH-SM4-SM3"
    verify_mode: int = ssl.CERT_REQUIRED


class TLCPService:
    """TLCP (国密 TLS) service — SM2 dual-certificate management and SSL context."""

    def __init__(self, cert_store_path: str = "/tmp/cds-certs"):
        self.cert_store = Path(cert_store_path)
        self.cert_store.mkdir(parents=True, exist_ok=True)
        self._certificates: dict[str, SM2Certificate] = {}
        self._ca_key_pair: tuple[str, str] | None = None  # (private_key, public_key)
        self._init_ca()

    def _init_ca(self):
        """Initialize CA key pair for self-signed certificates."""
        try:
            private_key, public_key = generate_sm2_keypair()
            self._ca_key_pair = (private_key, public_key)
        except Exception as e:
            logger.warning(f"Failed to initialize CA: {e}")

    def generate_certificate(
        self,
        subject: str,
        cert_type: str = "signing",
        validity_days: int = 365,
        organization: str | None = None,
    ) -> SM2Certificate:
        """Generate an SM2 X.509 certificate with self-signed CA.

        Args:
            subject: Certificate subject (CN)
            cert_type: 'signing' or 'encryption'
            validity_days: Certificate validity period
            organization: Organization name (O field)
        """
        cert_id = str(uuid.uuid4())[:8]
        private_key, public_key = generate_sm2_keypair()

        now = datetime.now()
        not_before = now
        not_after = now + timedelta(days=validity_days)

        cert = SM2Certificate(
            cert_id=cert_id,
            subject=subject,
            issuer="CN=CDS TLCP CA,O=CDS,C=CN",
            serial_number=uuid.uuid4().hex[:16],
            public_key=public_key,
            not_before=not_before,
            not_after=not_after,
            fingerprint="",
            cert_type=cert_type,
        )

        cert.cert_pem = self._build_cert_pem(cert, private_key, public_key, organization)
        cert.key_pem = self._build_key_pem(private_key, public_key)
        cert.fingerprint = self._fingerprint_pem(cert.cert_pem)

        # Save to disk
        cert_dir = self.cert_store / cert_id
        cert_dir.mkdir(exist_ok=True)
        (cert_dir / "cert.pem").write_text(cert.cert_pem)
        (cert_dir / "key.pem").write_text(cert.key_pem)

        self._certificates[cert_id] = cert
        return cert

    def get_certificate(self, cert_id: str) -> SM2Certificate | None:
        return self._certificates.get(cert_id)

    def list_certificates(self, subject: str | None = None) -> list[SM2Certificate]:
        certs = list(self._certificates.values())
        if subject:
            certs = [c for c in certs if c.subject == subject]
        return certs

    def revoke_certificate(self, cert_id: str) -> bool:
        cert = self._certificates.pop(cert_id, None)
        if cert:
            cert_dir = self.cert_store / cert_id
            if cert_dir.exists():
                import shutil
                shutil.rmtree(cert_dir)
            return True
        return False

    def verify_certificate(self, cert_pem: str) -> dict:
        """Verify a certificate's validity."""
        fingerprint = self._fingerprint_pem(cert_pem)
        for cert in self._certificates.values():
            if cert.fingerprint == fingerprint:
                now = datetime.now()
                expired = now > cert.not_after
                not_yet_valid = now < cert.not_before
                return {
                    "valid": not expired and not not_yet_valid,
                    "cert_id": cert.cert_id,
                    "subject": cert.subject,
                    "expired": expired,
                    "not_yet_valid": not_yet_valid,
                    "fingerprint": cert.fingerprint,
                }

        try:
            from app.services.certificate_service import certificate_service

            der = self._pem_to_der(cert_pem, "CERTIFICATE")
            parsed = certificate_service._parse_x509_der(der)
            not_before = parsed.get("not_before")
            not_after = parsed.get("not_after")
            now = datetime.now()
            expired = bool(not_after and now > not_after)
            not_yet_valid = bool(not_before and now < not_before)
            return {
                "valid": not expired and not not_yet_valid,
                "cert_id": None,
                "subject": parsed.get("subject", ""),
                "expired": expired,
                "not_yet_valid": not_yet_valid,
                "fingerprint": fingerprint,
            }
        except Exception as e:
            return {"valid": False, "error": f"Certificate parse failed: {e}"}

    def build_ssl_context(self, config: TLCPConfig | None = None) -> ssl.SSLContext:
        """Build an SSL context with TLCP SM2 cipher suites.

        Attempts to use Tongsuo-backed OpenSSL for SM2 ciphers. If SM2 ciphers
        are not available (standard OpenSSL), logs a warning and falls back.
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False  # Internal service communication

        if config:
            ctx.verify_mode = config.verify_mode

            # Try TLCP SM2 ciphers (requires Tongsuo or OpenSSL 3.x + SM2 engine)
            sm2_ciphers = [
                config.ciphers,
                "TLS_SM4_GCM_SM3",
                "TLS_SM4_CCM_SM3",
                "ECDHE-SM2-WITH-SM4-SM3",
            ]
            cipher_set = False
            for cipher_str in sm2_ciphers:
                try:
                    ctx.set_ciphers(cipher_str)
                    logger.info("TLCP SM2 ciphers configured: %s", cipher_str)
                    cipher_set = True
                    break
                except ssl.SSLError:
                    continue

            if not cipher_set:
                logger.warning(
                    "SM2 ciphers not available (install tongsuo for TLCP support). "
                    "Falling back to standard TLS — NOT suitable for production TLCP."
                )
                ctx.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-GCM-SHA256")

            # Load certificates
            if config.sign_cert_path and config.sign_key_path:
                ctx.load_cert_chain(config.sign_cert_path, config.sign_key_path)
            if config.ca_cert_path:
                ctx.load_verify_locations(config.ca_cert_path)
        else:
            ctx.verify_mode = ssl.CERT_NONE
            ctx.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-GCM-SHA256")

        return ctx

    def build_client_ssl_context(
        self,
        cert_path: str | None = None,
        key_path: str | None = None,
        ca_path: str | None = None,
    ) -> ssl.SSLContext:
        """Build client-side SSL context for connecting to TLCP servers."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False

        # Try SM2 ciphers (Tongsuo or OpenSSL 3.x + SM2)
        sm2_ciphers = [
            "TLS_SM4_GCM_SM3",
            "TLS_SM4_CCM_SM3",
            "ECDHE-SM2-WITH-SM4-SM3",
            "SM2-WITH-SM4-SM3",
        ]
        cipher_set = False
        for cipher_str in sm2_ciphers:
            try:
                ctx.set_ciphers(cipher_str)
                cipher_set = True
                break
            except ssl.SSLError:
                continue
        if not cipher_set:
            logger.warning("SM2 ciphers not available for client, falling back to RSA")
            ctx.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384")

        if cert_path and key_path:
            ctx.load_cert_chain(cert_path, key_path)
        if ca_path:
            ctx.load_verify_locations(ca_path)
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.verify_mode = ssl.CERT_NONE

        return ctx

    def _build_cert_pem(
        self,
        cert: SM2Certificate,
        private_key: str,
        public_key: str,
        organization: str | None,
    ) -> str:
        """Build a DER-encoded X.509 PEM certificate for the SM2 key."""
        from app.services.certificate_service import certificate_service

        subject_dn = f"CN={cert.subject},O={organization or 'CDS'},C=CN"
        issuer_private_key, issuer_public_key = self._ca_key_pair or (private_key, public_key)
        return certificate_service._build_cert_pem(
            serial=cert.serial_number,
            subject=subject_dn,
            issuer=cert.issuer,
            public_key=public_key,
            not_before=cert.not_before,
            not_after=cert.not_after,
            is_ca=False,
            private_key=private_key,
            issuer_private_key=issuer_private_key,
            issuer_public_key=issuer_public_key,
        )

    def _build_key_pem(self, private_key: str, public_key: str) -> str:
        """Build an RFC5915-style EC private key PEM for the SM2 key."""
        from app.services.certificate_service import (
            _SM2_CURVE_OID,
            _encode_bitstring,
            _encode_explicit,
            _encode_integer,
            _encode_octetstring,
            _encode_oid,
            _encode_sequence,
        )

        der = _encode_sequence(
            _encode_integer(1),
            _encode_octetstring(bytes.fromhex(private_key)),
            _encode_explicit(0, _encode_oid(_SM2_CURVE_OID)),
            _encode_explicit(1, _encode_bitstring(bytes.fromhex("04" + public_key))),
        )
        body = base64.b64encode(der).decode()
        lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
        return f"-----BEGIN EC PRIVATE KEY-----\n{lines}\n-----END EC PRIVATE KEY-----\n"

    def _fingerprint_pem(self, pem: str) -> str:
        der = self._pem_to_der(pem, "CERTIFICATE")
        if _HAS_GMSSL:
            return _sm3.sm3_hash(list(der))
        import hashlib

        return hashlib.sha256(der).hexdigest()

    def _pem_to_der(self, pem: str, label: str) -> bytes:
        begin = f"-----BEGIN {label}-----"
        end = f"-----END {label}-----"
        if begin not in pem or end not in pem:
            raise ValueError(f"PEM block {label} not found")
        body = pem.split(begin, 1)[1].split(end, 1)[0]
        return base64.b64decode("".join(body.strip().split()))


tlcp_service = TLCPService()
