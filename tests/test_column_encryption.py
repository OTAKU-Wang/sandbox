"""Tests for column-level SM4 encryption.

SS-09#2: SM4-SIV (deterministic) and SM4-GCM (randomized) column encryption.
Ensures field-level encryption for structured data in the sandbox.
"""
import pytest
import os


# ============================================================
# SM4-SIV (Deterministic Encryption)
# ============================================================
class TestSM4SIV:
    """SM4-SIV deterministic encryption — same plaintext + same key = same ciphertext.
    Used for equality queries on encrypted columns."""

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt then decrypt returns original plaintext."""
        from app.services.column_encryption import ColumnEncryption
        key = os.urandom(16)
        enc = ColumnEncryption(key)
        plaintext = "sensitive-value-123"
        ciphertext = enc.encrypt_siv(plaintext)
        assert ciphertext != plaintext
        assert enc.decrypt_siv(ciphertext) == plaintext

    def test_deterministic_same_input(self):
        """Same plaintext produces same ciphertext (deterministic)."""
        from app.services.column_encryption import ColumnEncryption
        key = os.urandom(16)
        enc = ColumnEncryption(key)
        pt = "alice@example.com"
        ct1 = enc.encrypt_siv(pt)
        ct2 = enc.encrypt_siv(pt)
        assert ct1 == ct2

    def test_different_plaintext_different_ciphertext(self):
        """Different plaintext produces different ciphertext."""
        from app.services.column_encryption import ColumnEncryption
        key = os.urandom(16)
        enc = ColumnEncryption(key)
        ct1 = enc.encrypt_siv("alice@example.com")
        ct2 = enc.encrypt_siv("bob@example.com")
        assert ct1 != ct2

    def test_different_keys_different_ciphertext(self):
        """Same plaintext with different keys produces different ciphertext."""
        from app.services.column_encryption import ColumnEncryption
        enc1 = ColumnEncryption(os.urandom(16))
        enc2 = ColumnEncryption(os.urandom(16))
        pt = "same-data"
        assert enc1.encrypt_siv(pt) != enc2.encrypt_siv(pt)

    def test_empty_string(self):
        """Empty string encrypts and decrypts correctly."""
        from app.services.column_encryption import ColumnEncryption
        enc = ColumnEncryption(os.urandom(16))
        ct = enc.encrypt_siv("")
        assert enc.decrypt_siv(ct) == ""

    def test_unicode_value(self):
        """Unicode values encrypt and decrypt correctly."""
        from app.services.column_encryption import ColumnEncryption
        enc = ColumnEncryption(os.urandom(16))
        pt = "数据字段-机密信息"
        ct = enc.encrypt_siv(pt)
        assert enc.decrypt_siv(ct) == pt

    def test_long_value(self):
        """Long values (up to column limit) work correctly."""
        from app.services.column_encryption import ColumnEncryption
        enc = ColumnEncryption(os.urandom(16))
        pt = "x" * 10000
        ct = enc.encrypt_siv(pt)
        assert enc.decrypt_siv(ct) == pt


# ============================================================
# SM4-GCM (Randomized Encryption)
# ============================================================
class TestSM4GCM:
    """SM4-GCM randomized encryption — same plaintext = different ciphertext each time.
    Used for non-indexed columns where deterministic matching is not needed."""

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt then decrypt returns original plaintext."""
        from app.services.column_encryption import ColumnEncryption
        enc = ColumnEncryption(os.urandom(16))
        plaintext = "secret-data"
        ct, nonce, tag = enc.encrypt_gcm(plaintext)
        assert enc.decrypt_gcm(ct, nonce, tag) == plaintext

    def test_randomized_different_each_time(self):
        """Same plaintext produces different ciphertext (randomized)."""
        from app.services.column_encryption import ColumnEncryption
        enc = ColumnEncryption(os.urandom(16))
        pt = "same-input"
        ct1, _, _ = enc.encrypt_gcm(pt)
        ct2, _, _ = enc.encrypt_gcm(pt)
        assert ct1 != ct2

    def test_tamper_detection(self):
        """Tampered ciphertext raises error on decrypt."""
        from app.services.column_encryption import ColumnEncryption
        enc = ColumnEncryption(os.urandom(16))
        ct, nonce, tag = enc.encrypt_gcm("tamper-test")
        tampered = bytearray(ct)
        tampered[0] ^= 0xFF
        with pytest.raises(Exception):
            enc.decrypt_gcm(bytes(tampered), nonce, tag)

    def test_wrong_key_fails(self):
        """Decrypt with wrong key fails."""
        from app.services.column_encryption import ColumnEncryption
        enc1 = ColumnEncryption(os.urandom(16))
        enc2 = ColumnEncryption(os.urandom(16))
        ct, nonce, tag = enc1.encrypt_gcm("key-test")
        with pytest.raises(Exception):
            enc2.decrypt_gcm(ct, nonce, tag)


# ============================================================
# Column Encryption Service Integration
# ============================================================
class TestColumnEncryptionService:
    """Integration tests for the column encryption service."""

    def test_encrypt_row_selective_columns(self):
        """Encrypt only specified columns in a row."""
        from app.services.column_encryption import ColumnEncryption
        key = os.urandom(16)
        enc = ColumnEncryption(key)
        row = {"id": 1, "name": "Alice", "phone": "13800138000", "email": "alice@test.com"}
        encrypted_cols = ["phone", "email"]
        result = enc.encrypt_row(row, encrypted_cols)
        assert result["id"] == 1
        assert result["name"] == "Alice"
        assert result["phone"] != "13800138000"
        assert result["email"] != "alice@test.com"

    def test_decrypt_row_selective_columns(self):
        """Decrypt only specified columns in a row."""
        from app.services.column_encryption import ColumnEncryption
        key = os.urandom(16)
        enc = ColumnEncryption(key)
        row = {"id": 1, "name": "Alice", "phone": "13800138000", "email": "alice@test.com"}
        encrypted = enc.encrypt_row(row, ["phone", "email"])
        decrypted = enc.decrypt_row(encrypted, ["phone", "email"])
        assert decrypted["phone"] == "13800138000"
        assert decrypted["email"] == "alice@test.com"

    def test_column_key_derivation(self):
        """Different columns can use different derived keys."""
        from app.services.column_encryption import ColumnEncryption
        master_key = os.urandom(16)
        enc = ColumnEncryption(master_key)
        key1 = enc.derive_column_key("phone")
        key2 = enc.derive_column_key("email")
        assert key1 != key2
