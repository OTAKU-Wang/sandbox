"""KMS service tests — Sprint 2 (T4-07)."""
import pytest
from app.services.kms_service import KMSService


@pytest.fixture
def kms():
    return KMSService()


class TestKMSService:
    """KMS key management tests (local fallback mode)."""

    def test_generate_data_key(self, kms):
        result = kms.generate_data_key("product-001")
        assert "key_id" in result
        assert "key_bytes" in result
        assert len(result["key_bytes"]) == 32  # 256-bit key
        assert "product-001" in result["key_id"]

    def test_get_key_after_generate(self, kms):
        result = kms.generate_data_key("product-002")
        key = kms.get_key(result["key_id"])
        assert key == result["key_bytes"]

    def test_get_nonexistent_key(self, kms):
        assert kms.get_key("nonexistent-key") is None

    def test_encrypt_decrypt_with_key(self, kms):
        result = kms.generate_data_key("product-003")
        key_id = result["key_id"]
        plaintext = b"Secret data product content"

        ciphertext = kms.encrypt_with_key(key_id, plaintext)
        assert ciphertext != plaintext

        decrypted = kms.decrypt_with_key(key_id, ciphertext)
        assert decrypted == plaintext

    def test_encrypt_with_nonexistent_key(self, kms):
        with pytest.raises(ValueError, match="not found"):
            kms.encrypt_with_key("fake-key", b"data")

    def test_decrypt_with_nonexistent_key(self, kms):
        with pytest.raises(ValueError, match="not found"):
            kms.decrypt_with_key("fake-key", b"data")

    def test_rotate_key(self, kms):
        result = kms.generate_data_key("product-004")
        old_key_id = result["key_id"]
        new_key_id = kms.rotate_key(old_key_id)

        assert new_key_id != old_key_id
        assert "rotated" in new_key_id

        # New key should be retrievable
        new_key = kms.get_key(new_key_id)
        assert new_key is not None
        assert new_key != result["key_bytes"]

    def test_multiple_products_isolated(self, kms):
        r1 = kms.generate_data_key("product-A")
        r2 = kms.generate_data_key("product-B")
        assert r1["key_bytes"] != r2["key_bytes"]
        assert kms.get_key(r1["key_id"]) != kms.get_key(r2["key_id"])
