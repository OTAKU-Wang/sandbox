"""Tests for Secure Checkpoint — SM4 encrypted model checkpoints (S6-6)."""
import pytest

from app.services.secure_checkpoint import (
    SecureCheckpointStore, CheckpointMetadata, EncryptedCheckpoint,
    secure_checkpoint_store,
)


@pytest.fixture
def store():
    return SecureCheckpointStore()


@pytest.fixture
def sample_data():
    return b"model weights: " + bytes(range(256)) * 100


class TestEncryptDecrypt:
    def test_encrypt_creates_checkpoint(self, store, sample_data):
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        assert cp.metadata.job_id == "job-1"
        assert cp.metadata.epoch == 1
        assert cp.metadata.step == 100
        assert cp.metadata.size_bytes == len(sample_data)
        assert cp.ciphertext != sample_data  # Actually encrypted

    def test_decrypt_recovers_data(self, store, sample_data):
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        decrypted = store.decrypt_checkpoint(cp.metadata.checkpoint_id)
        assert decrypted == sample_data

    def test_different_checkpoints_different_ciphertext(self, store, sample_data):
        cp1 = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        cp2 = store.encrypt_checkpoint("job-1", epoch=2, step=200, model_data=sample_data)
        assert cp1.ciphertext != cp2.ciphertext  # Different IVs

    def test_plaintext_hash_matches(self, store, sample_data):
        from app.services.crypto_service import crypto_service
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        expected = crypto_service.sm3_hash(sample_data)
        assert cp.metadata.plaintext_hash == expected

    def test_ciphertext_hash_matches(self, store, sample_data):
        from app.services.crypto_service import crypto_service
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        expected = crypto_service.sm3_hash(cp.ciphertext)
        assert cp.metadata.ciphertext_hash == expected

    def test_ciphertext_contains_aead_tag(self, store, sample_data):
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        assert len(cp.metadata.encryption_iv) == 24  # 12-byte GCM nonce hex
        assert len(cp.ciphertext) == len(sample_data) + 16  # GCM tag appended


class TestListCheckpoints:
    def test_list_all(self, store, sample_data):
        store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        store.encrypt_checkpoint("job-2", epoch=1, step=100, model_data=sample_data)
        cps = store.list_checkpoints()
        assert len(cps) == 2

    def test_list_by_job(self, store, sample_data):
        store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        store.encrypt_checkpoint("job-2", epoch=1, step=100, model_data=sample_data)
        cps = store.list_checkpoints(job_id="job-1")
        assert len(cps) == 1
        assert cps[0].job_id == "job-1"


class TestDelete:
    def test_delete_removes_checkpoint(self, store, sample_data):
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        assert store.delete_checkpoint(cp.metadata.checkpoint_id) is True
        assert store.decrypt_checkpoint(cp.metadata.checkpoint_id) is None

    def test_delete_nonexistent(self, store):
        assert store.delete_checkpoint("nonexistent") is False


class TestDecryptNotFound:
    def test_decrypt_nonexistent(self, store):
        assert store.decrypt_checkpoint("nonexistent") is None


class TestIntegrityVerification:
    def test_tampered_ciphertext_fails_decrypt(self, store, sample_data):
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=sample_data)
        # Tamper with ciphertext
        tampered = EncryptedCheckpoint(
            metadata=cp.metadata,
            ciphertext=cp.ciphertext[:10] + b"\xff" + cp.ciphertext[11:],
        )
        store._checkpoints[cp.metadata.checkpoint_id] = tampered
        result = store.decrypt_checkpoint(cp.metadata.checkpoint_id)
        assert result is None  # Integrity check fails


class TestEdgeCases:
    def test_empty_data(self, store):
        cp = store.encrypt_checkpoint("job-1", epoch=0, step=0, model_data=b"")
        decrypted = store.decrypt_checkpoint(cp.metadata.checkpoint_id)
        assert decrypted == b""

    def test_large_data(self, store):
        data = b"x" * 1_000_000  # 1MB
        cp = store.encrypt_checkpoint("job-1", epoch=1, step=100, model_data=data)
        decrypted = store.decrypt_checkpoint(cp.metadata.checkpoint_id)
        assert decrypted == data
        assert len(decrypted) == 1_000_000


class TestSingleton:
    def test_singleton_exists(self):
        assert secure_checkpoint_store is not None
        assert isinstance(secure_checkpoint_store, SecureCheckpointStore)
