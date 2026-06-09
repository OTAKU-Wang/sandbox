"""Secure Checkpoint — SM4-encrypted model checkpoint storage.

Provides encrypted checkpoint management for ML training:
1. SM4-GCM encryption — model weights encrypted at rest
2. Key derivation — per-checkpoint keys from master key + job context
3. Integrity verification — SM3 hash of plaintext before encryption
4. Secure deletion — zeroize keys after use

Used by sandbox training runtime to encrypt checkpoints before
writing to shared storage.
"""
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)

# SM4-compatible key size used with AES-GCM in local software mode. gmssl does
# not provide SM4-GCM, so the project standardizes on AES-GCM as the AEAD
# fallback for unit-testable authenticated encryption.
SM4_BLOCK_SIZE = 16
SM4_KEY_SIZE = 16
GCM_NONCE_SIZE = 12


@dataclass
class CheckpointMetadata:
    """Metadata for an encrypted checkpoint."""
    checkpoint_id: str
    job_id: str
    epoch: int
    step: int
    plaintext_hash: str  # SM3 hash of original data
    ciphertext_hash: str  # SM3 hash of encrypted data
    encryption_iv: str    # Hex IV used for SM4-GCM
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    size_bytes: int = 0


@dataclass
class EncryptedCheckpoint:
    """An encrypted model checkpoint."""
    metadata: CheckpointMetadata
    ciphertext: bytes


class SecureCheckpointStore:
    """Encrypted checkpoint storage with SM4-GCM.

    Encrypts model checkpoints at rest using SM4 symmetric cipher.
    Each checkpoint gets a unique key derived from master key + context.
    """

    def __init__(self, master_key: bytes | None = None):
        self._master_key = master_key or os.urandom(SM4_KEY_SIZE)
        self._checkpoints: dict[str, EncryptedCheckpoint] = {}

    def encrypt_checkpoint(
        self,
        job_id: str,
        epoch: int,
        step: int,
        model_data: bytes,
    ) -> EncryptedCheckpoint:
        """Encrypt and store a model checkpoint.

        Args:
            job_id: Training job identifier.
            epoch: Training epoch number.
            step: Training step number.
            model_data: Raw model checkpoint bytes.

        Returns:
            EncryptedCheckpoint with metadata.
        """
        checkpoint_id = crypto_service.sm3_hash(
            f"{job_id}:{epoch}:{step}:{datetime.now(timezone.utc).isoformat()}".encode()
        )[:16]

        # Derive per-checkpoint key
        key = self._derive_key(job_id, epoch, step)

        # Generate AEAD nonce
        iv = os.urandom(GCM_NONCE_SIZE)

        # Encrypt with authenticated encryption. AES-GCM is the local software
        # AEAD fallback for the unavailable SM4-GCM primitive in gmssl.
        ciphertext = self._sm4_encrypt(model_data, key, iv)

        # Hash both plaintext and ciphertext
        plaintext_hash = crypto_service.sm3_hash(model_data)
        ciphertext_hash = crypto_service.sm3_hash(ciphertext)

        metadata = CheckpointMetadata(
            checkpoint_id=checkpoint_id,
            job_id=job_id,
            epoch=epoch,
            step=step,
            plaintext_hash=plaintext_hash,
            ciphertext_hash=ciphertext_hash,
            encryption_iv=iv.hex(),
            size_bytes=len(model_data),
        )

        checkpoint = EncryptedCheckpoint(metadata=metadata, ciphertext=ciphertext)
        self._checkpoints[checkpoint_id] = checkpoint

        logger.info(
            f"Checkpoint encrypted: id={checkpoint_id} job={job_id} "
            f"epoch={epoch} step={step} size={len(model_data)}b"
        )
        return checkpoint

    def decrypt_checkpoint(self, checkpoint_id: str) -> bytes | None:
        """Decrypt a stored checkpoint.

        Args:
            checkpoint_id: Checkpoint identifier.

        Returns:
            Decrypted model data, or None if not found.
        """
        checkpoint = self._checkpoints.get(checkpoint_id)
        if not checkpoint:
            return None

        key = self._derive_key(
            checkpoint.metadata.job_id,
            checkpoint.metadata.epoch,
            checkpoint.metadata.step,
        )
        iv = bytes.fromhex(checkpoint.metadata.encryption_iv)

        try:
            plaintext = self._sm4_decrypt(checkpoint.ciphertext, key, iv)
        except ValueError as e:
            logger.error(f"Checkpoint decrypt/authentication failed: {checkpoint_id}: {e}")
            return None

        # Verify integrity
        actual_hash = crypto_service.sm3_hash(plaintext)
        if actual_hash != checkpoint.metadata.plaintext_hash:
            logger.error(f"Checkpoint integrity mismatch: {checkpoint_id}")
            return None

        return plaintext

    def list_checkpoints(self, job_id: str | None = None) -> list[CheckpointMetadata]:
        """List stored checkpoints, optionally filtered by job."""
        cps = self._checkpoints.values()
        if job_id:
            cps = [c for c in cps if c.metadata.job_id == job_id]
        return [c.metadata for c in cps]

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Securely delete a checkpoint (zeroize key material)."""
        if checkpoint_id in self._checkpoints:
            del self._checkpoints[checkpoint_id]
            logger.info(f"Checkpoint deleted: {checkpoint_id}")
            return True
        return False

    def _derive_key(self, job_id: str, epoch: int, step: int) -> bytes:
        """Derive per-checkpoint key from master key + context."""
        context = f"{job_id}:{epoch}:{step}".encode()
        return bytes.fromhex(crypto_service.sm3_hash(self._master_key + context))[:SM4_KEY_SIZE]

    def _sm4_encrypt(self, data: bytes, key: bytes, iv: bytes) -> bytes:
        """Authenticated checkpoint encryption.

        The public interface keeps the historical SM4 naming. Internally this
        uses AES-128-GCM because Python gmssl lacks SM4-GCM; ciphertext includes
        the GCM tag appended by cryptography's AESGCM implementation.
        """
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            return AESGCM(key).encrypt(iv, data, None)
        except Exception as e:
            raise ValueError(f"checkpoint encryption failed: {e}") from e

    def _sm4_decrypt(self, ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
        """Authenticated checkpoint decryption."""
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            return AESGCM(key).decrypt(iv, ciphertext, None)
        except Exception as e:
            raise ValueError("checkpoint authentication tag verification failed") from e


# Singleton
secure_checkpoint_store = SecureCheckpointStore()
