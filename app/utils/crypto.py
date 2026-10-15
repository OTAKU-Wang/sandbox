"""Cryptographic utilities for CDS.
Dev/test: AES-256-GCM (via pycryptodomex) as SM4-GCM equivalent.
Production: Tongsuo SM4-GCM via system OpenSSL.
"""
import os
import hashlib
import logging

from Cryptodome.Cipher import AES

logger = logging.getLogger(__name__)

# PZ-04 (MR-A14): one process-lifetime warning when the SM3→SHA-256 fallback
# actually runs — the API surface must be able to tell which hash produced a
# digest.
_sm3_fallback_warned = False


def _sm3_fallback_allowed() -> bool:
    """Whether the SHA-256 fallback for SM3 (gmssl missing) may be used.

    Fail-closed when CDS_SM3_FALLBACK_ALLOWED=false: GM/T paths must run real
    SM3 instead of silently hashing with SHA-256.
    """
    try:
        from app.core.config import get_settings
        return bool(get_settings().SM3_FALLBACK_ALLOWED)
    except Exception:
        # Settings unavailable (e.g. imported before config is loadable):
        # keep the historical dev behavior of falling back.
        return True


def sm3_hash(data: bytes) -> str:
    """SM3 hash (returns hex string). Uses gmssl SM3 when available.

    Fallback (dev only): SHA-256 when gmssl is missing — gated by
    CDS_SM3_FALLBACK_ALLOWED (PZ-04/MR-A14) and surfaced with one
    process-lifetime warning.
    """
    global _sm3_fallback_warned
    try:
        from gmssl import sm3 as _sm3
        return _sm3.sm3_hash(list(data))
    except ImportError:
        if not _sm3_fallback_allowed():
            raise RuntimeError(
                "gmssl is not installed and CDS_SM3_FALLBACK_ALLOWED=false: "
                "SM3 cannot fall back to SHA-256. Install gmssl (or Tongsuo) "
                "to run real SM3."
            )
        if not _sm3_fallback_warned:
            logger.warning(
                "gmssl not installed — SM3 requests fall back to SHA-256 "
                "(CDS_SM3_FALLBACK_ALLOWED=true). Install gmssl for real SM3."
            )
            _sm3_fallback_warned = True
        return hashlib.sha256(data).hexdigest()


class SM4Cipher:
    """SM4 symmetric encryption (GCM mode).
    Uses AES-256-GCM under the hood for dev/test; production uses Tongsuo SM4-GCM.
    """

    def __init__(self, key: bytes | None = None):
        self.key = key or os.urandom(32)  # 256-bit key

    def encrypt_gcm(self, plaintext: bytes, associated_data: bytes = b"") -> tuple[bytes, bytes, bytes]:
        """Encrypt with SM4-GCM (AES-256-GCM). Returns (ciphertext, nonce, tag)."""
        nonce = os.urandom(12)  # 96-bit nonce
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        if associated_data:
            cipher.update(associated_data)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        return ciphertext, nonce, tag

    def decrypt_gcm(self, ciphertext: bytes, nonce: bytes, tag: bytes, associated_data: bytes = b"") -> bytes:
        """Decrypt SM4-GCM. Raises ValueError if tag mismatch."""
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        if associated_data:
            cipher.update(associated_data)
        try:
            return cipher.decrypt_and_verify(ciphertext, tag)
        except ValueError:
            raise ValueError("SM4-GCM authentication tag mismatch")
