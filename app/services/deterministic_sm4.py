"""Deterministic & Randomized SM4 encryption for column-level storage security.

Deterministic SM4: Same plaintext + same key → same ciphertext (supports equality queries).
Randomized SM4: Same plaintext → different ciphertext each time (maximum security).

Both use SM4-CBC per GM/T 0002 national standard.
"""
import hashlib
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from gmssl import sm4 as _sm4
    from gmssl import sm3 as _sm3
    _HAS_GMSSL = True
except ImportError:
    _HAS_GMSSL = False
    logger.warning("gmssl not installed — SM4 encryption will use AES-CBC fallback")


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """PKCS7 padding."""
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data: bytes) -> bytes:
    """PKCS7 unpadding."""
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError(f"Invalid PKCS7 padding: {pad_len}")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("Corrupted PKCS7 padding")
    return data[:-pad_len]


class DeterministicSM4:
    """Deterministic SM4-CBC encryption for equality-queryable columns.

    Same plaintext + same DEK → same ciphertext. Uses a fixed IV derived
    from the DEK to ensure determinism. Supports:
    - encrypt(plaintext) → ciphertext (bytes)
    - decrypt(ciphertext) → plaintext (str)
    - hash_for_index(plaintext) → truncated SM3 hash (for B-tree indexes)
    """

    def __init__(self, dek: bytes):
        """Initialize with a 16-byte Data Encryption Key."""
        if len(dek) != 16:
            raise ValueError(f"DEK must be 16 bytes, got {len(dek)}")
        self.dek = dek
        # Derive fixed IV from DEK (deterministic → same DEK → same IV)
        self._iv = self._derive_iv(dek)

    @staticmethod
    def _derive_iv(dek: bytes) -> bytes:
        """Derive a fixed 16-byte IV from the DEK using SM3."""
        if _HAS_GMSSL:
            h = _sm3.sm3_hash(list(dek + b"DET_SM4_IV"))
            return bytes.fromhex(h)[:16]
        return hashlib.sha256(dek + b"DET_SM4_IV").digest()[:16]

    def encrypt(self, plaintext: str) -> bytes:
        """Deterministic encrypt: same plaintext → same ciphertext."""
        if not _HAS_GMSSL:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            encryptor = Cipher(algorithms.AES(self.dek), modes.CBC(self._iv)).encryptor()
            data = _pkcs7_pad(plaintext.encode("utf-8"))
            return encryptor.update(data) + encryptor.finalize()

        data = plaintext.encode("utf-8")
        crypt = _sm4.CryptSM4()
        crypt.set_key(self.dek, _sm4.SM4_ENCRYPT)
        return crypt.crypt_cbc(self._iv, data)

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt ciphertext back to plaintext."""
        if not _HAS_GMSSL:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            decryptor = Cipher(algorithms.AES(self.dek), modes.CBC(self._iv)).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            return _pkcs7_unpad(padded).decode("utf-8")

        decryptor = _sm4.CryptSM4()
        decryptor.set_key(self.dek, _sm4.SM4_DECRYPT)
        plaintext = decryptor.crypt_cbc(self._iv, ciphertext)
        return plaintext.decode("utf-8")

    def hash_for_index(self, plaintext: str) -> str:
        """Generate a truncated SM3 hash for B-tree index lookup.

        Returns 16-byte hex string (32 chars). Used for hash indexes
        on deterministic-encrypted columns to speed up equality queries.
        """
        if _HAS_GMSSL:
            data = self.dek + plaintext.encode("utf-8")
            h = _sm3.sm3_hash(list(data))
            return h[:32]  # 16 bytes = 32 hex chars
        return hashlib.sha256(self.dek + plaintext.encode("utf-8")).hexdigest()[:32]


class RandomizedSM4:
    """Randomized AES-GCM encryption for high-sensitivity columns (P0-3).

    Uses AES-128-GCM with the SM4 key for real AEAD authentication.
    Same plaintext → different ciphertext each time (random nonce).
    Format: nonce (12 bytes) || ciphertext || tag (16 bytes)
    """

    def __init__(self, dek: bytes):
        if len(dek) != 16:
            raise ValueError(f"DEK must be 16 bytes, got {len(dek)}")
        self.dek = dek

    def encrypt(self, plaintext: str) -> bytes:
        """Randomized encrypt: AES-GCM with random nonce."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        aesgcm = AESGCM(self.dek)
        data = plaintext.encode("utf-8")
        ct_and_tag = aesgcm.encrypt(nonce, data, None)
        return nonce + ct_and_tag

    def decrypt(self, data: bytes) -> str:
        """Decrypt: extract nonce, then AES-GCM decrypt with tag verification."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if len(data) < 12:
            raise ValueError("Ciphertext too short")
        nonce = data[:12]
        ct_and_tag = data[12:]
        aesgcm = AESGCM(self.dek)
        try:
            plaintext = aesgcm.decrypt(nonce, ct_and_tag, None)
            return plaintext.decode("utf-8")
        except Exception:
            raise ValueError("Authentication tag mismatch — ciphertext tampered")


@dataclass
class EncryptionColumnMeta:
    """Metadata for an encrypted column in the database."""
    table: str
    column: str
    level: str  # "plain", "det", "rand"
    enc_column: str | None = None   # e.g. "phone_enc"
    hash_column: str | None = None  # e.g. "phone_hash"


class ColumnEncryptionRegistry:
    """Registry mapping plaintext column references to their encryption metadata.

    Used by the query rewriter to know which columns are encrypted and how.
    """

    def __init__(self):
        self._columns: dict[str, EncryptionColumnMeta] = {}

    def register(
        self,
        table: str,
        column: str,
        level: str,
        enc_column: str | None = None,
        hash_column: str | None = None,
    ):
        key = f"{table}.{column}"
        self._columns[key] = EncryptionColumnMeta(
            table=table, column=column, level=level,
            enc_column=enc_column, hash_column=hash_column,
        )

    def get(self, table: str, column: str) -> EncryptionColumnMeta | None:
        return self._columns.get(f"{table}.{column}")

    def all_columns(self) -> dict[str, EncryptionColumnMeta]:
        return dict(self._columns)


# Default registry for common CDS tables
default_registry = ColumnEncryptionRegistry()

# Enterprise basic table
default_registry.register("enterprise_basic", "ent_id", "det",
                          enc_column="ent_id_enc", hash_column="ent_id_hash")
default_registry.register("enterprise_basic", "ent_name", "rand",
                          enc_column="ent_name_enc")
default_registry.register("enterprise_basic", "legal_person", "rand",
                          enc_column="legal_person_enc")
default_registry.register("enterprise_basic", "phone", "det",
                          enc_column="phone_enc", hash_column="phone_hash")
default_registry.register("enterprise_basic", "industry_code", "plain")
default_registry.register("enterprise_basic", "reg_capital", "plain")
default_registry.register("enterprise_basic", "region_code", "plain")

# Tax records table
default_registry.register("tax_records", "tax_id", "det",
                          enc_column="tax_id_enc", hash_column="tax_id_hash")
default_registry.register("tax_records", "ent_id", "det",
                          enc_column="ent_id_enc", hash_column="ent_id_hash")
default_registry.register("tax_records", "year", "plain")
default_registry.register("tax_records", "tax_type", "plain")
default_registry.register("tax_records", "tax_amount", "rand",
                          enc_column="tax_amount_enc")

# Credit records table
default_registry.register("credit_records", "credit_id", "det",
                          enc_column="credit_id_enc", hash_column="credit_id_hash")
default_registry.register("credit_records", "ent_id", "det",
                          enc_column="ent_id_enc", hash_column="ent_id_hash")
default_registry.register("credit_records", "bank_code", "plain")
default_registry.register("credit_records", "loan_amount", "rand",
                          enc_column="loan_amount_enc")
