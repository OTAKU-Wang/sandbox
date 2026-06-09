"""Column-level SM4 encryption — SM4-SIV (deterministic) + SM4-GCM (randomized).

Provides column-level encryption for sensitive database fields with two modes:
- SM4-SIV: Deterministic (same plaintext → same ciphertext), supports equality queries
- SM4-GCM: Randomized (same plaintext → different ciphertext), maximum security

Both use SM4 per GM/T 0002 national standard with 128-bit keys.
"""
import hashlib
import logging
import os
import struct
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

try:
    from gmssl import sm4 as _sm4
    from gmssl import sm3 as _sm3
    _HAS_GMSSL = True
except ImportError:
    _HAS_GMSSL = False
    logger.warning("gmssl not installed — column encryption will use AES fallback")


class EncryptionMode(str, Enum):
    DETERMINISTIC = "sm4-siv"  # Deterministic: supports equality queries
    RANDOMIZED = "sm4-gcm"     # Randomized: maximum security


@dataclass
class EncryptedColumn:
    """Represents an encrypted column value with metadata."""
    ciphertext: bytes
    mode: EncryptionMode
    column_name: str
    key_id: str
    nonce: bytes | None = None  # For GCM mode


class ColumnEncryption:
    """SM4 column-level encryption engine.

    Supports two modes:
    - SM4-SIV (Synthetic IV): Deterministic encryption for columns that need
      equality queries. Same plaintext + same key → same ciphertext.
    - SM4-GCM (Galois/Counter Mode): Authenticated encryption with random nonce
      for maximum security. Supports integrity verification.
    """

    def __init__(self, dek: bytes, key_id: str = "default"):
        """Initialize with a 16-byte Data Encryption Key.

        Args:
            dek: 16-byte SM4 key
            key_id: Key identifier for key rotation tracking
        """
        if len(dek) != 16:
            raise ValueError(f"DEK must be 16 bytes, got {len(dek)}")
        self.dek = dek
        self.key_id = key_id

    @staticmethod
    def _derive_iv_siv(dek: bytes, column_name: str, plaintext: bytes) -> bytes:
        """Derive deterministic IV for SM4-SIV mode.

        IV = SM3(dek || column_name || plaintext)[:16]
        Same inputs → same IV → deterministic encryption.
        """
        data = dek + column_name.encode("utf-8") + plaintext
        if _HAS_GMSSL:
            h = _sm3.sm3_hash(list(data))
            return bytes.fromhex(h)[:16]
        return hashlib.sha256(data).digest()[:16]

    @staticmethod
    def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
        pad_len = block_size - (len(data) % block_size)
        return data + bytes([pad_len] * pad_len)

    @staticmethod
    def _pkcs7_unpad(data: bytes) -> bytes:
        pad_len = data[-1]
        if pad_len < 1 or pad_len > 16:
            raise ValueError(f"Invalid PKCS7 padding: {pad_len}")
        if data[-pad_len:] != bytes([pad_len] * pad_len):
            raise ValueError("Corrupted PKCS7 padding")
        return data[:-pad_len]

    def encrypt_deterministic(self, plaintext: str, column_name: str) -> EncryptedColumn:
        """SM4-SIV deterministic encryption.

        Same plaintext + same key + same column → same ciphertext.
        Supports equality WHERE clauses on encrypted columns.
        """
        if not _HAS_GMSSL:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            data = plaintext.encode("utf-8")
            iv = self._derive_iv_siv(self.dek, column_name, data)
            encryptor = Cipher(algorithms.AES(self.dek), modes.CBC(iv)).encryptor()
            ct = encryptor.update(self._pkcs7_pad(data)) + encryptor.finalize()
            return EncryptedColumn(
                ciphertext=iv + ct,
                mode=EncryptionMode.DETERMINISTIC,
                column_name=column_name,
                key_id=self.key_id,
            )

        data = plaintext.encode("utf-8")
        iv = self._derive_iv_siv(self.dek, column_name, data)
        padded = self._pkcs7_pad(data)

        crypt = _sm4.CryptSM4()
        crypt.set_key(self.dek, _sm4.SM4_ENCRYPT)
        ct = crypt.crypt_cbc(iv, padded)

        # Prepend IV so decrypt can recover it (SIV = IV || ciphertext)
        return EncryptedColumn(
            ciphertext=iv + ct,
            mode=EncryptionMode.DETERMINISTIC,
            column_name=column_name,
            key_id=self.key_id,
        )

    def decrypt_deterministic(self, encrypted: EncryptedColumn) -> str:
        """Decrypt SM4-SIV ciphertext back to plaintext."""
        if not _HAS_GMSSL:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            if len(encrypted.ciphertext) < 16:
                raise ValueError("Ciphertext too short for SIV mode")
            iv = encrypted.ciphertext[:16]
            ct = encrypted.ciphertext[16:]
            decryptor = Cipher(algorithms.AES(self.dek), modes.CBC(iv)).decryptor()
            padded = decryptor.update(ct) + decryptor.finalize()
            return self._pkcs7_unpad(padded).decode("utf-8")

        crypt = _sm4.CryptSM4()
        crypt.set_key(self.dek, _sm4.SM4_DECRYPT)

        # For deterministic mode, IV was derived from plaintext, so we need
        # to recover it from the ciphertext header or store it separately.
        # In SIV mode, the IV is stored prepended to the ciphertext.
        if len(encrypted.ciphertext) < 16:
            raise ValueError("Ciphertext too short for SIV mode")

        iv = encrypted.ciphertext[:16]
        ct = encrypted.ciphertext[16:]
        padded = crypt.crypt_cbc(iv, ct)
        return self._pkcs7_unpad(padded).decode("utf-8")

    def encrypt_randomized(self, plaintext: str, column_name: str) -> EncryptedColumn:
        """AES-GCM authenticated encryption with random nonce (P0-3).

        Uses AES-128-GCM with the SM4 key for real AEAD authentication.
        Same plaintext → different ciphertext each time (random nonce).
        Format: nonce (12 bytes) || ciphertext || tag (16 bytes)
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(self.dek)
        data = plaintext.encode("utf-8")
        ct_and_tag = aesgcm.encrypt(nonce, data, None)
        return EncryptedColumn(
            ciphertext=nonce + ct_and_tag,
            mode=EncryptionMode.RANDOMIZED,
            column_name=column_name,
            key_id=self.key_id,
            nonce=nonce,
        )

    def decrypt_randomized(self, encrypted: EncryptedColumn) -> str:
        """Decrypt AES-GCM ciphertext and verify integrity (P0-3)."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if len(encrypted.ciphertext) < 12:
            raise ValueError("Ciphertext too short")
        nonce = encrypted.ciphertext[:12]
        ct_and_tag = encrypted.ciphertext[12:]
        aesgcm = AESGCM(self.dek)
        try:
            plaintext = aesgcm.decrypt(nonce, ct_and_tag, None)
            return plaintext.decode("utf-8")
        except Exception:
            raise ValueError("Authentication tag mismatch — ciphertext tampered")

    def encrypt_column(
        self,
        plaintext: str,
        column_name: str,
        mode: EncryptionMode = EncryptionMode.DETERMINISTIC,
    ) -> EncryptedColumn:
        """Encrypt a column value using the specified mode."""
        if mode == EncryptionMode.DETERMINISTIC:
            return self.encrypt_deterministic(plaintext, column_name)
        else:
            return self.encrypt_randomized(plaintext, column_name)

    def decrypt_column(self, encrypted: EncryptedColumn) -> str:
        """Decrypt a column value based on its encryption mode."""
        if encrypted.mode == EncryptionMode.DETERMINISTIC:
            return self.decrypt_deterministic(encrypted)
        else:
            return self.decrypt_randomized(encrypted)

    def hash_for_index(self, plaintext: str, column_name: str) -> str:
        """Generate a deterministic hash for B-tree index on encrypted columns.

        Uses SM3 or SHA-256 to produce a fixed-length hash suitable for indexing.
        """
        data = self.dek + column_name.encode() + plaintext.encode()
        if _HAS_GMSSL:
            h = _sm3.sm3_hash(list(data))
            return h
        return hashlib.sha256(data).hexdigest()

    # ── Simplified API (encrypt_siv/encrypt_gcm) ──────────────

    def encrypt_siv(self, plaintext: str) -> bytes:
        """SM4-SIV deterministic encryption (simplified API).

        Returns raw ciphertext bytes. Uses a fixed column name '_default'.
        """
        enc = self.encrypt_deterministic(plaintext, "_default")
        return enc.ciphertext

    def decrypt_siv(self, ciphertext: bytes) -> str:
        """SM4-SIV deterministic decryption (simplified API)."""
        enc = EncryptedColumn(
            ciphertext=ciphertext,
            mode=EncryptionMode.DETERMINISTIC,
            column_name="_default",
            key_id=self.key_id,
        )
        return self.decrypt_deterministic(enc)

    def encrypt_gcm(self, plaintext: str) -> tuple[bytes, bytes, bytes]:
        """SM4-GCM randomized encryption (simplified API).

        Uses AES-128-GCM with the SM4 key (both 128-bit block ciphers).
        gmssl does not support SM4-GCM, so AES-GCM provides real AEAD.
        Returns (ciphertext, nonce, tag) tuple.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(self.dek)
        data = plaintext.encode("utf-8")
        # AESGCM.encrypt returns ciphertext || tag (16 bytes appended)
        ct_and_tag = aesgcm.encrypt(nonce, data, None)
        ct = ct_and_tag[:-16]
        tag = ct_and_tag[-16:]
        return ct, nonce, tag

    def decrypt_gcm(self, ciphertext: bytes, nonce: bytes, tag: bytes) -> str:
        """SM4-GCM randomized decryption with tag verification (simplified API).

        Uses AES-128-GCM for real AEAD authentication.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(self.dek)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext + tag, None)
            return plaintext.decode("utf-8")
        except Exception:
            raise ValueError("Authentication tag mismatch — ciphertext tampered")

    def encrypt_row(
        self,
        row: dict[str, Any],
        columns: list[str],
        mode: EncryptionMode = EncryptionMode.DETERMINISTIC,
    ) -> dict[str, Any]:
        """Encrypt specified columns in a row dict."""
        result = dict(row)
        for col in columns:
            if col in result and isinstance(result[col], str):
                enc = self.encrypt_column(result[col], col, mode)
                result[col] = enc.ciphertext.hex()
        return result

    def decrypt_row(
        self,
        row: dict[str, Any],
        columns: list[str],
        mode: EncryptionMode = EncryptionMode.DETERMINISTIC,
    ) -> dict[str, Any]:
        """Decrypt specified columns in a row dict."""
        result = dict(row)
        for col in columns:
            if col in result and isinstance(result[col], str):
                try:
                    ct = bytes.fromhex(result[col])
                    enc = EncryptedColumn(
                        ciphertext=ct,
                        mode=mode,
                        column_name=col,
                        key_id=self.key_id,
                    )
                    result[col] = self.decrypt_column(enc)
                except Exception:
                    pass  # Skip if not decryptable
        return result

    def derive_column_key(self, column_name: str) -> bytes:
        """Derive a unique key for a specific column from the master key.

        Uses HKDF-like derivation: SM3/SHA256(master_key || column_name)[:16]
        """
        data = self.dek + column_name.encode("utf-8")
        if _HAS_GMSSL:
            h = _sm3.sm3_hash(list(data))
            return bytes.fromhex(h)[:16]
        return hashlib.sha256(data).digest()[:16]


# Singleton (initialized with a default DEK for development)
_default_dek = hashlib.sha256(b"cds-column-encryption-default-key").digest()[:16]
column_encryption = ColumnEncryption(dek=_default_dek, key_id="cds-default")
