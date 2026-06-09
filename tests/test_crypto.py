import pytest
from app.utils.crypto import SM4Cipher, sm3_hash


def test_sm4_encrypt_decrypt():
    cipher = SM4Cipher()
    plaintext = b"Hello, CDS! This is secret data."
    ciphertext, nonce, tag = cipher.encrypt_gcm(plaintext)
    decrypted = cipher.decrypt_gcm(ciphertext, nonce, tag)
    assert decrypted == plaintext


def test_sm4_tamper_detection():
    cipher = SM4Cipher()
    plaintext = b"Sensitive data"
    ciphertext, nonce, tag = cipher.encrypt_gcm(plaintext)
    # Tamper with ciphertext
    tampered = bytearray(ciphertext)
    tampered[0] ^= 0xFF
    with pytest.raises(ValueError, match="tag mismatch"):
        cipher.decrypt_gcm(bytes(tampered), nonce, tag)


def test_sm4_different_keys():
    cipher1 = SM4Cipher()
    cipher2 = SM4Cipher()
    plaintext = b"Cross-key test"
    ciphertext, nonce, tag = cipher1.encrypt_gcm(plaintext)
    with pytest.raises(ValueError):
        cipher2.decrypt_gcm(ciphertext, nonce, tag)


def test_sm3_hash():
    h = sm3_hash(b"test data")
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256 hex length


def test_sm4_empty_plaintext():
    cipher = SM4Cipher()
    ciphertext, nonce, tag = cipher.encrypt_gcm(b"")
    decrypted = cipher.decrypt_gcm(ciphertext, nonce, tag)
    assert decrypted == b""
