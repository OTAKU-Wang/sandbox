"""Cryptographic utilities for CDS.
Dev/test: AES-256-GCM (via pycryptodomex) as SM4-GCM equivalent.
Production: Tongsuo SM4-GCM via system OpenSSL.
"""
import os
import hashlib

from Cryptodome.Cipher import AES


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


def sm3_hash(data: bytes) -> str:
    """SM3 hash (returns hex string). Uses gmssl SM3 when available; SHA-256 as dev fallback."""
    try:
        from gmssl import sm3 as _sm3
        return _sm3.sm3_hash(list(data))
    except ImportError:
        return hashlib.sha256(data).hexdigest()
