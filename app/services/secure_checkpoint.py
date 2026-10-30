"""Secure Checkpoint — SM4-encrypted, durable model-checkpoint storage.

Provides encrypted checkpoint management for ML training:
1. SM4-GCM encryption — model weights encrypted at rest
2. Key derivation — per-checkpoint keys from a durable master key + job context
3. Integrity verification — SM3 hash of plaintext before encryption
4. Bounded storage — an explicit capacity ceiling that refuses writes
5. Secure deletion — remove the on-disk blob and drop the cached key material

OCR-A126 (2026-10-18): this class used to be a process dict plus a fresh
`os.urandom` master key per boot. `app/api/training.py:170` writes a real
checkpoint through it and `app/api/training.py:475` lists it back, so every
restart silently emptied `/api/v1/training/jobs/{id}/checkpoints` and left the
persisted `.pt` files with no decryptable counterpart. Storage is now a
content-addressed on-disk store under the configured results dir, with the
master key kept in a 0600 key file in the same store root, so the key and the
blobs have the same lifetime and the same trust boundary.

Honesty boundary (禁伪造): the master key is a filesystem keystore entry, not a
hardware-rooted one. Wrapping it with the KMS KEK is the remaining hardening and
needs a KEK that outlives the process (see OCR-A139/P-GAP-004); until then this
module does not claim HSM-rooted key protection.
"""
import json
import logging
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.services.crypto_service import crypto_service

logger = logging.getLogger(__name__)

# SM4-compatible key size used with AES-GCM in local software mode. gmssl does
# not provide SM4-GCM, so the project standardizes on AES-GCM as the AEAD
# fallback for unit-testable authenticated encryption.
SM4_BLOCK_SIZE = 16
SM4_KEY_SIZE = 16
GCM_NONCE_SIZE = 12

META_SUFFIX = ".meta.json"
BLOB_SUFFIX = ".enc"
MASTER_KEY_FILENAME = "master.key"
# 2 GiB default ceiling for the whole store (OCR-A126: 写爆比拒绝严重).
DEFAULT_CAPACITY_BYTES = 2 * 1024 * 1024 * 1024
CHECKPOINT_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# Sentinel: resolve the store root from configuration at first use, so building
# the module singleton does not read settings or touch the filesystem at import.
DEFAULT_ROOT = object()


class CheckpointStoreError(RuntimeError):
    """The checkpoint store could not honour a write durably (fail closed)."""


class CheckpointCapacityError(CheckpointStoreError):
    """The store is full — the write is refused rather than allowed to fill the disk."""


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

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "job_id": self.job_id,
            "epoch": self.epoch,
            "step": self.step,
            "plaintext_hash": self.plaintext_hash,
            "ciphertext_hash": self.ciphertext_hash,
            "encryption_iv": self.encryption_iv,
            "created_at": self.created_at.isoformat(),
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CheckpointMetadata":
        return cls(
            checkpoint_id=str(payload["checkpoint_id"]),
            job_id=str(payload["job_id"]),
            epoch=int(payload["epoch"]),
            step=int(payload["step"]),
            plaintext_hash=str(payload["plaintext_hash"]),
            ciphertext_hash=str(payload["ciphertext_hash"]),
            encryption_iv=str(payload["encryption_iv"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            size_bytes=int(payload.get("size_bytes", 0)),
        )


@dataclass
class EncryptedCheckpoint:
    """An encrypted model checkpoint."""
    metadata: CheckpointMetadata
    ciphertext: bytes


def default_store_root() -> Path:
    """Store root: CDS_CHECKPOINT_STORE_DIR, else the configured results dir."""
    override = os.getenv("CDS_CHECKPOINT_STORE_DIR", "").strip()
    if override:
        return Path(override)
    base = os.getenv("CDS_RESULTS_DIR", "").strip()
    if not base:
        from app.core.config import get_settings
        base = get_settings().CUBE_RESULTS_DIR
    return Path(base) / "checkpoints"


def checkpoint_capacity_bytes() -> int:
    """Whole-store byte ceiling (CDS_CHECKPOINT_MAX_MB, default 2 GiB).

    Read from the environment because app/core/config.py exposes no field for it
    yet; recorded as remaining work in the OCR-A126 evidence note.
    """
    raw = os.getenv("CDS_CHECKPOINT_MAX_MB", "").strip()
    if not raw:
        return DEFAULT_CAPACITY_BYTES
    try:
        megabytes = int(raw)
    except ValueError as exc:
        raise CheckpointStoreError(f"CDS_CHECKPOINT_MAX_MB 不是整数：{raw!r}") from exc
    if megabytes <= 0:
        raise CheckpointStoreError(f"CDS_CHECKPOINT_MAX_MB 必须为正数，得到 {megabytes}")
    return megabytes * 1024 * 1024


class SecureCheckpointStore:
    """Encrypted checkpoint storage with SM4-GCM, durable across restarts.

    Encrypts model checkpoints at rest and writes them into a content-addressed
    on-disk store (`<root>/<id[:2]>/<id>.enc` + `<id>.meta.json`), so the ids
    handed to callers stay resolvable after a process restart and a job_id can
    never steer the destination path.

    `root=None` keeps the historical process-local behaviour for unit tests and
    discloses it via :attr:`durable` plus a warning; production uses the module
    singleton, which resolves the configured root.
    """

    def __init__(
        self,
        master_key: bytes | None = None,
        root: object | None = None,
        capacity_bytes: int | None = None,
    ):
        self._master_key: bytes | None = master_key
        self._root_setting = root
        self._root: Path | None = None
        self._capacity_bytes = capacity_bytes
        self._ephemeral_warned = False
        # In-process write-through cache. Lookups consult it first, but it is a
        # cache only: the on-disk store is the source of truth after a restart.
        self._checkpoints: dict[str, EncryptedCheckpoint] = {}

    # ─── 存储根目录与主密钥 ────────────────────────────────────────────────

    @property
    def durable(self) -> bool:
        """False when this instance is process-local (no configured root)."""
        return self._root_setting is not None

    @property
    def capacity_bytes(self) -> int:
        if self._capacity_bytes is None:
            self._capacity_bytes = checkpoint_capacity_bytes()
        return self._capacity_bytes

    @property
    def root(self) -> Path:
        return self._ensure_root()

    def _ensure_root(self) -> Path:
        if self._root is not None:
            return self._root
        if self._root_setting is None:
            if not self._ephemeral_warned:
                self._ephemeral_warned = True
                logger.warning(
                    "[CHECKPOINT] Store built without a root: checkpoints live only in "
                    "this process and are lost on restart (OCR-A126). Configure "
                    "CDS_CHECKPOINT_STORE_DIR / CDS_CUBE_RESULTS_DIR for durable storage."
                )
            self._root = Path(tempfile.mkdtemp(prefix="cds-checkpoints-"))
            return self._root
        setting = default_store_root() if self._root_setting is DEFAULT_ROOT else Path(str(self._root_setting))
        try:
            setting.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CheckpointStoreError(f"无法创建 checkpoint 存储目录 {setting}: {exc}") from exc
        self._root = setting
        return setting

    def _master_key_bytes(self) -> bytes:
        if self._master_key is not None:
            return self._master_key
        key_path = self._ensure_root() / MASTER_KEY_FILENAME
        material = os.urandom(SM4_KEY_SIZE)
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._master_key = self._read_master_key(key_path)
            return self._master_key
        except OSError as exc:
            raise CheckpointStoreError(f"无法写入主密钥文件 {key_path}: {exc}") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(material.hex().encode())
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            _unlink_quietly(key_path)
            raise
        self._master_key = material
        logger.info("[CHECKPOINT] Created the durable store master key at %s", key_path)
        return material

    @staticmethod
    def _read_master_key(key_path: Path) -> bytes:
        try:
            mode = os.lstat(key_path).st_mode
        except OSError as exc:
            raise CheckpointStoreError(f"无法读取主密钥文件 {key_path}: {exc}") from exc
        if not stat.S_ISREG(mode):
            raise CheckpointStoreError(f"主密钥路径不是普通文件：{key_path}")
        if mode & 0o077:
            raise CheckpointStoreError(
                f"主密钥文件 {key_path} 权限为 {stat.S_IMODE(mode):o}，必须为 0600 —— "
                "拒绝使用他人可读的密钥材料"
            )
        try:
            material = bytes.fromhex(key_path.read_text().strip())
        except (OSError, ValueError) as exc:
            raise CheckpointStoreError(f"主密钥文件 {key_path} 已损坏：{exc}") from exc
        if len(material) != SM4_KEY_SIZE:
            raise CheckpointStoreError(
                f"主密钥文件 {key_path} 长度为 {len(material)} 字节，期望 {SM4_KEY_SIZE}"
            )
        return material

    # ─── 落盘布局（id 由内容派生 ⇒ 跨重启稳定，且无法被 job_id 注入路径）────

    @staticmethod
    def _validate_job_id(job_id: str) -> str:
        if not job_id or len(job_id) > 255 or "\x00" in job_id:
            raise CheckpointStoreError(f"非法 job_id：{job_id!r}")
        return job_id

    @staticmethod
    def _shard_dir(root: Path, checkpoint_id: str) -> Path:
        return root / checkpoint_id[:2]

    @classmethod
    def _blob_path(cls, root: Path, checkpoint_id: str) -> Path:
        if not CHECKPOINT_ID_RE.match(checkpoint_id):
            raise CheckpointStoreError(f"非法 checkpoint_id：{checkpoint_id!r}")
        return cls._shard_dir(root, checkpoint_id) / f"{checkpoint_id}{BLOB_SUFFIX}"

    @classmethod
    def _meta_path(cls, root: Path, checkpoint_id: str) -> Path:
        if not CHECKPOINT_ID_RE.match(checkpoint_id):
            raise CheckpointStoreError(f"非法 checkpoint_id：{checkpoint_id!r}")
        return cls._shard_dir(root, checkpoint_id) / f"{checkpoint_id}{META_SUFFIX}"

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                _unlink_quietly(tmp)
                raise
            os.replace(tmp, path)
        except OSError as exc:
            _unlink_quietly(tmp)
            raise CheckpointStoreError(f"checkpoint 落盘失败 {path}: {exc}") from exc

    def stored_bytes(self) -> int:
        """Bytes currently occupied by stored ciphertexts (the capacity ledger)."""
        root = self._root
        if root is None or not root.exists():
            return 0
        total = 0
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(BLOB_SUFFIX):
                    try:
                        total += os.path.getsize(os.path.join(dirpath, name))
                    except OSError:
                        continue
        return total

    # ─── Public API ────────────────────────────────────────────────────────

    def encrypt_checkpoint(
        self,
        job_id: str,
        epoch: int,
        step: int,
        model_data: bytes,
    ) -> EncryptedCheckpoint:
        """Encrypt a checkpoint and persist it under the store root.

        Args:
            job_id: Training job identifier.
            epoch: Training epoch number.
            step: Training step number.
            model_data: Raw model checkpoint bytes.

        Returns:
            EncryptedCheckpoint with metadata.

        Raises:
            CheckpointCapacityError: the write would exceed the store ceiling.
            CheckpointStoreError: the write could not be made durable.
        """
        self._validate_job_id(job_id)
        root = self._ensure_root()
        key = self._derive_key(job_id, epoch, step)
        iv = os.urandom(GCM_NONCE_SIZE)
        ciphertext = self._sm4_encrypt(model_data, key, iv)
        plaintext_hash = crypto_service.sm3_hash(model_data)
        ciphertext_hash = crypto_service.sm3_hash(ciphertext)

        # Content-addressed and stable across restarts: the same weights for the
        # same (job, epoch, step) resolve to the same id instead of minting a new
        # one from wall-clock time.
        checkpoint_id = crypto_service.sm3_hash(
            f"{job_id}|{epoch}|{step}|{plaintext_hash}".encode()
        )[:16]

        capacity = self.capacity_bytes
        used = self.stored_bytes()
        if used + len(ciphertext) > capacity:
            raise CheckpointCapacityError(
                f"checkpoint 存储已达上限：已用 {used} 字节，本次需要 {len(ciphertext)} "
                f"字节，上限 {capacity} 字节 —— 拒绝写入（OCR-A126）"
            )

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
        self._atomic_write(self._blob_path(root, checkpoint_id), ciphertext)
        self._atomic_write(
            self._meta_path(root, checkpoint_id),
            json.dumps(metadata.to_dict(), ensure_ascii=False).encode(),
        )
        self._checkpoints[checkpoint_id] = EncryptedCheckpoint(metadata=metadata, ciphertext=ciphertext)

        logger.info(
            f"Checkpoint encrypted: id={checkpoint_id} job={job_id} "
            f"epoch={epoch} step={step} size={len(model_data)}b root={root}"
        )
        return EncryptedCheckpoint(metadata=metadata, ciphertext=ciphertext)

    def decrypt_checkpoint(self, checkpoint_id: str) -> bytes | None:
        """Decrypt a stored checkpoint (cache first, then the on-disk store).

        Args:
            checkpoint_id: Checkpoint identifier.

        Returns:
            Decrypted model data, or None if it is missing or fails
            authentication/integrity verification.
        """
        cached = self._checkpoints.get(checkpoint_id)
        if cached is not None:
            return self._verify_and_open(cached.metadata, cached.ciphertext)

        try:
            root = self._root if self._root is not None else self._ensure_root()
            metadata = self._read_metadata(root, checkpoint_id)
        except CheckpointStoreError as exc:
            logger.error("Checkpoint lookup failed: %s", exc)
            return None
        if metadata is None:
            return None
        try:
            ciphertext = self._blob_path(root, checkpoint_id).read_bytes()
        except OSError as exc:
            logger.error(
                "Checkpoint blob unreadable for %s (%s) — its metadata is on disk but "
                "the ciphertext is not", checkpoint_id, exc,
            )
            return None
        if crypto_service.sm3_hash(ciphertext) != metadata.ciphertext_hash:
            logger.error("Checkpoint ciphertext hash mismatch on disk: %s", checkpoint_id)
            return None
        return self._verify_and_open(metadata, ciphertext)

    def list_checkpoints(self, job_id: str | None = None) -> list[CheckpointMetadata]:
        """List stored checkpoints from the on-disk store, optionally per job."""
        if self._root is None and self._root_setting is None:
            root = self._ensure_root()
        else:
            root = self._root if self._root is not None else self._configured_root_or_none()
        if root is None or not root.exists():
            return []
        found: dict[str, CheckpointMetadata] = {}
        for dirpath, _dirs, files in os.walk(root):
            for name in sorted(files):
                if not name.endswith(META_SUFFIX):
                    continue
                path = Path(dirpath) / name
                try:
                    metadata = CheckpointMetadata.from_dict(json.loads(path.read_text()))
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    logger.error(
                        "Skipping unreadable checkpoint metadata %s: %s — delete it or "
                        "restore the blob, it is not reported as a checkpoint", path, exc,
                    )
                    continue
                found[metadata.checkpoint_id] = metadata
        # Entries written by this process but whose files were removed since are
        # not resurrected; the disk decides what exists.
        checkpoints = sorted(found.values(), key=lambda m: (m.created_at, m.checkpoint_id))
        if job_id:
            checkpoints = [m for m in checkpoints if m.job_id == job_id]
        return checkpoints

    def delete_checkpoint(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint's blob and metadata (crypto-erase its ciphertext)."""
        removed = self._checkpoints.pop(checkpoint_id, None) is not None
        if self._root is None:
            try:
                root = self._ensure_root()
            except CheckpointStoreError:
                return removed
        else:
            root = self._root
        try:
            blob = self._blob_path(root, checkpoint_id)
            meta = self._meta_path(root, checkpoint_id)
        except CheckpointStoreError:
            return removed
        for path in (blob, meta):
            if path.exists():
                try:
                    path.unlink()
                    removed = True
                except OSError as exc:
                    logger.error("Failed to delete checkpoint file %s: %s", path, exc)
        if removed:
            logger.info("Checkpoint deleted: %s", checkpoint_id)
        return removed

    # ─── Internals ─────────────────────────────────────────────────────────

    def _configured_root_or_none(self) -> Path | None:
        if self._root_setting is DEFAULT_ROOT or isinstance(self._root_setting, (str, Path)):
            setting = (
                default_store_root() if self._root_setting is DEFAULT_ROOT else Path(str(self._root_setting))
            )
            return setting if setting.exists() else None
        return self._ensure_root()

    def _read_metadata(self, root: Path, checkpoint_id: str) -> CheckpointMetadata | None:
        path = self._meta_path(root, checkpoint_id)
        try:
            payload = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("Checkpoint metadata unreadable for %s: %s", checkpoint_id, exc)
            return None
        try:
            return CheckpointMetadata.from_dict(payload)
        except (ValueError, KeyError, TypeError) as exc:
            logger.error("Checkpoint metadata malformed for %s: %s", checkpoint_id, exc)
            return None

    def _verify_and_open(self, metadata: CheckpointMetadata, ciphertext: bytes) -> bytes | None:
        key = self._derive_key(metadata.job_id, metadata.epoch, metadata.step)
        iv = bytes.fromhex(metadata.encryption_iv)
        try:
            plaintext = self._sm4_decrypt(ciphertext, key, iv)
        except ValueError as e:
            logger.error(f"Checkpoint decrypt/authentication failed: {metadata.checkpoint_id}: {e}")
            return None

        actual_hash = crypto_service.sm3_hash(plaintext)
        if actual_hash != metadata.plaintext_hash:
            logger.error(f"Checkpoint integrity mismatch: {metadata.checkpoint_id}")
            return None
        return plaintext

    def _derive_key(self, job_id: str, epoch: int, step: int) -> bytes:
        """Derive per-checkpoint key from the durable master key + context."""
        context = f"{job_id}:{epoch}:{step}".encode()
        return bytes.fromhex(crypto_service.sm3_hash(self._master_key_bytes() + context))[:SM4_KEY_SIZE]

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


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# Singleton: the live training path gets the configured, durable, bounded store.
secure_checkpoint_store = SecureCheckpointStore(root=DEFAULT_ROOT)
