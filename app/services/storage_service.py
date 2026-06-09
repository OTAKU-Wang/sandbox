"""MinIO-based encrypted storage service with envelope encryption.

All data is encrypted at rest using envelope encryption:
- KMS generates a per-object DEK (Data Encryption Key)
- DEK encrypts the data with SM4-GCM (authenticated encryption)
- Key ID is stored alongside the ciphertext for decryption
- DEK is held in KMS (Vault Transit or local), never in storage

Format: [key_id_len:4][key_id][nonce:12][tag:16][ciphertext]
"""
import hashlib
import io
import struct
from pathlib import Path

from app.core.config import get_settings
from app.core.path_security import safe_join_object_name
from app.utils.crypto import SM4Cipher, sm3_hash

settings = get_settings()

# Envelope encryption magic bytes
_ENVELOPE_VERSION = 1


class StorageService:
    """Object storage with envelope encryption (KMS-managed DEKs).

    Default behavior: all uploads are encrypted. Use upload_plaintext()
    explicitly for non-sensitive data.
    """

    def __init__(self):
        self._client = None
        self._local_dir = Path("/tmp/cds-storage")
        self._local_dir.mkdir(parents=True, exist_ok=True)
        self._kms = None

    def _get_kms(self):
        """Lazy-load KMS service."""
        if self._kms is None:
            from app.services.kms_service import kms_service
            self._kms = kms_service
        return self._kms

    def _pack_envelope(self, key_id: str, nonce: bytes, tag: bytes, ciphertext: bytes) -> bytes:
        """Pack encrypted data into envelope format."""
        key_id_bytes = key_id.encode("utf-8")
        # version(1) + key_id_len(4) + key_id + nonce(12) + tag(16) + ciphertext
        header = struct.pack("!BI", _ENVELOPE_VERSION, len(key_id_bytes))
        return header + key_id_bytes + nonce + tag + ciphertext

    def _unpack_envelope(self, data: bytes) -> tuple[str, bytes, bytes, bytes]:
        """Unpack envelope format. Returns (key_id, nonce, tag, ciphertext)."""
        if len(data) < 33:  # 1 + 4 + 0 + 12 + 16 = 33 minimum
            raise ValueError("Data too short for envelope format")
        version = data[0]
        if version != _ENVELOPE_VERSION:
            raise ValueError(f"Unsupported envelope version: {version}")
        key_id_len = struct.unpack("!I", data[1:5])[0]
        if len(data) < 5 + key_id_len + 28:
            raise ValueError("Envelope truncated")
        key_id = data[5:5 + key_id_len].decode("utf-8")
        offset = 5 + key_id_len
        nonce = data[offset:offset + 12]
        tag = data[offset + 12:offset + 28]
        ciphertext = data[offset + 28:]
        return key_id, nonce, tag, ciphertext

    def _get_minio_client(self):
        if self._client is None:
            try:
                from minio import Minio
                self._client = Minio(
                    settings.MINIO_ENDPOINT,
                    access_key=settings.MINIO_ACCESS_KEY,
                    secret_key=settings.MINIO_SECRET_KEY,
                    secure=False,
                )
                if not self._client.bucket_exists(settings.MINIO_BUCKET):
                    self._client.make_bucket(settings.MINIO_BUCKET)
            except Exception:
                self._client = None
        return self._client

    def upload(self, data: bytes, object_name: str, content_type: str = "application/octet-stream") -> dict:
        """Upload data with envelope encryption (default).

        Uses KMS to generate a per-object DEK, encrypts with SM4-GCM,
        and stores the envelope format. Returns {path, checksum, size, key_id}.
        """
        kms = self._get_kms()
        key_result = kms.generate_data_key(f"storage-{object_name}")
        key_id = key_result["key_id"]
        dek = key_result["key_bytes"]

        cipher = SM4Cipher(dek)
        ciphertext, nonce, tag = cipher.encrypt_gcm(data)
        envelope = self._pack_envelope(key_id, nonce, tag, ciphertext)

        checksum = sm3_hash(data)
        size = len(data)

        minio = self._get_minio_client()
        if minio:
            minio.put_object(
                settings.MINIO_BUCKET,
                object_name,
                io.BytesIO(envelope),
                len(envelope),
                content_type=content_type,
            )
            path = f"minio://{settings.MINIO_BUCKET}/{object_name}"
        else:
            local_path = safe_join_object_name(self._local_dir, object_name)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(envelope)
            path = str(local_path)

        return {"path": path, "checksum": checksum, "size": size, "key_id": key_id, "encrypted": True}

    def upload_plaintext(self, data: bytes, object_name: str, content_type: str = "application/octet-stream") -> dict:
        """Upload data WITHOUT encryption. For non-sensitive data only.

        Returns {path, checksum, size, encrypted=False}.
        """
        checksum = sm3_hash(data)
        size = len(data)

        minio = self._get_minio_client()
        if minio:
            minio.put_object(
                settings.MINIO_BUCKET,
                object_name,
                io.BytesIO(data),
                size,
                content_type=content_type,
            )
            path = f"minio://{settings.MINIO_BUCKET}/{object_name}"
        else:
            local_path = safe_join_object_name(self._local_dir, object_name)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(data)
            path = str(local_path)

        return {"path": path, "checksum": checksum, "size": size, "encrypted": False}

    def download(self, object_name: str) -> bytes:
        """Download and decrypt data from storage.

        Auto-detects envelope format and decrypts using KMS.
        For plaintext objects, returns raw bytes.
        """
        raw = self._download_raw(object_name)

        # Try envelope format detection
        if len(raw) > 5 and raw[0] == _ENVELOPE_VERSION:
            try:
                key_id, nonce, tag, ciphertext = self._unpack_envelope(raw)
                kms = self._get_kms()
                dek = kms.get_key(key_id)
                if dek is None:
                    raise ValueError(f"Key {key_id} not found in KMS — data cannot be decrypted")
                cipher = SM4Cipher(dek)
                return cipher.decrypt_gcm(ciphertext, nonce, tag)
            except (ValueError, struct.error):
                pass  # Not envelope format, return raw

        return raw

    def download_decrypted(self, object_name: str) -> bytes:
        """Alias for download() — all downloads are auto-decrypted."""
        return self.download(object_name)

    def download_raw(self, object_name: str) -> bytes:
        """Download raw bytes without decryption. For inspection only."""
        return self._download_raw(object_name)

    def get_encryption_info(self, object_name: str) -> dict | None:
        """Get encryption metadata without downloading full object.

        Returns {key_id, encrypted: True} or None if not encrypted.
        """
        raw = self._download_raw(object_name)
        if len(raw) > 5 and raw[0] == _ENVELOPE_VERSION:
            try:
                key_id, _, _, _ = self._unpack_envelope(raw)
                return {"key_id": key_id, "encrypted": True}
            except (ValueError, struct.error):
                pass
        return None

    def _download_raw(self, object_name: str) -> bytes:
        """Download raw bytes from storage."""
        minio = self._get_minio_client()
        if minio:
            response = minio.get_object(settings.MINIO_BUCKET, self._object_name_from_reference(object_name))
            return response.read()
        else:
            local_path = self._local_path_from_reference(object_name)
            return local_path.read_bytes()

    def delete(self, object_name: str) -> bool:
        """Delete data from storage."""
        minio = self._get_minio_client()
        if minio:
            minio.remove_object(settings.MINIO_BUCKET, self._object_name_from_reference(object_name))
            return True
        else:
            local_path = self._local_path_from_reference(object_name)
            if local_path.exists():
                local_path.unlink()
                return True
            return False

    def _object_name_from_reference(self, object_ref: str) -> str:
        """Normalize object names returned by upload() or stored in DB."""
        if object_ref.startswith("minio://"):
            prefix = f"minio://{settings.MINIO_BUCKET}/"
            if not object_ref.startswith(prefix):
                raise ValueError("MinIO reference points to an unexpected bucket")
            return object_ref[len(prefix):]
        path = Path(object_ref)
        if path.is_absolute():
            base = self._local_dir.resolve()
            resolved = path.resolve()
            if not (resolved == base or str(resolved).startswith(str(base) + "/")):
                raise ValueError("Local storage reference escapes storage root")
            return str(resolved.relative_to(base))
        return object_ref

    def _local_path_from_reference(self, object_ref: str) -> Path:
        if object_ref.startswith("minio://"):
            object_ref = self._object_name_from_reference(object_ref)
        path = Path(object_ref)
        if path.is_absolute():
            base = self._local_dir.resolve()
            resolved = path.resolve()
            if not (resolved == base or str(resolved).startswith(str(base) + "/")):
                raise ValueError("Local storage reference escapes storage root")
            return resolved
        return safe_join_object_name(self._local_dir, object_ref)


storage_service = StorageService()
