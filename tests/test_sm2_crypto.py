"""SM4/SM3 crypto tests — encrypt/decrypt, hash, tamper detection."""
import pytest
from app.utils.crypto import SM4Cipher, sm3_hash


def test_sm4_encrypt_decrypt():
    cipher = SM4Cipher()
    plaintext = b"sensitive data for testing"
    ciphertext, nonce, tag = cipher.encrypt_gcm(plaintext)
    decrypted = cipher.decrypt_gcm(ciphertext, nonce, tag)
    assert decrypted == plaintext


def test_sm4_encrypt_different_ciphertexts():
    cipher = SM4Cipher()
    c1, n1, t1 = cipher.encrypt_gcm(b"same text")
    c2, n2, t2 = cipher.encrypt_gcm(b"same text")
    # Nonces should differ (random)
    assert n1 != n2
    # Both should decrypt correctly
    assert cipher.decrypt_gcm(c1, n1, t1) == cipher.decrypt_gcm(c2, n2, t2)


def test_sm4_tamper_detection():
    cipher = SM4Cipher()
    ciphertext, nonce, tag = cipher.encrypt_gcm(b"tamper test")
    # Tamper with ciphertext
    tampered = bytearray(ciphertext)
    tampered[0] ^= 0xFF
    with pytest.raises(ValueError, match="authentication tag"):
        cipher.decrypt_gcm(bytes(tampered), nonce, tag)


def test_sm4_different_keys():
    cipher1 = SM4Cipher()
    cipher2 = SM4Cipher()
    ciphertext, nonce, tag = cipher1.encrypt_gcm(b"key test")
    with pytest.raises(ValueError):
        cipher2.decrypt_gcm(ciphertext, nonce, tag)


def test_sm4_associated_data():
    cipher = SM4Cipher()
    plaintext = b"authenticated data"
    aad = b"metadata"
    ciphertext, nonce, tag = cipher.encrypt_gcm(plaintext, associated_data=aad)
    # Correct AAD should decrypt
    assert cipher.decrypt_gcm(ciphertext, nonce, tag, associated_data=aad) == plaintext
    # Wrong AAD should fail
    with pytest.raises(ValueError):
        cipher.decrypt_gcm(ciphertext, nonce, tag, associated_data=b"wrong")


def test_sm4_empty_plaintext():
    cipher = SM4Cipher()
    ciphertext, nonce, tag = cipher.encrypt_gcm(b"")
    assert cipher.decrypt_gcm(ciphertext, nonce, tag) == b""


def test_sm3_hash_deterministic():
    h1 = sm3_hash(b"test data")
    h2 = sm3_hash(b"test data")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_sm3_hash_different_inputs():
    h1 = sm3_hash(b"input A")
    h2 = sm3_hash(b"input B")
    assert h1 != h2
