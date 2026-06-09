import pytest
from app.services.kms_service import KMSService


def test_generate_data_key():
    kms = KMSService()
    result = kms.generate_data_key("product-001")
    assert "key_id" in result
    assert "key_bytes" in result
    assert len(result["key_bytes"]) == 32


def test_encrypt_decrypt_with_key():
    kms = KMSService()
    key_info = kms.generate_data_key("product-002")
    plaintext = b"Secret data for encryption"
    ciphertext = kms.encrypt_with_key(key_info["key_id"], plaintext)
    decrypted = kms.decrypt_with_key(key_info["key_id"], ciphertext)
    assert decrypted == plaintext


def test_key_not_found():
    kms = KMSService()
    with pytest.raises(ValueError, match="not found"):
        kms.encrypt_with_key("nonexistent-key", b"data")


def test_rotate_key():
    kms = KMSService()
    key_info = kms.generate_data_key("product-003")
    new_key_id = kms.rotate_key(key_info["key_id"])
    assert new_key_id != key_info["key_id"]
