"""A126 (ocr CR 2026-10-18): secure_checkpoint 从未落盘 —— 进程字典 + 每次启动随机主密钥 + 无容量上限。

修复前 `SecureCheckpointStore` 把密文放在 `self._checkpoints`（进程字典），主密钥
`os.urandom(16)` 每次构造重生（app/services/secure_checkpoint.py:59），且没有任何
总量上限。`app/api/training.py:170` 真的在写、`:475` 真的在读，所以 api 重启后
`/api/v1/training/jobs/{id}/checkpoints` 只会返回空列表，落盘的 `.pt` 也再没有
可解的对应物。

本文件钉的是修复后的性质：跨重启可解、id 跨重启稳定、有硬上限、写不进就报错、
路径不可被 job_id 注入、未配置根目录的实例自己承认不落盘。
"""
import stat

import pytest

from app.services.secure_checkpoint import (
    MASTER_KEY_FILENAME,
    CheckpointCapacityError,
    CheckpointStoreError,
    SecureCheckpointStore,
    checkpoint_capacity_bytes,
    default_store_root,
    secure_checkpoint_store,
)

DATA = b"model-weights-" * 4096  # ~48 KB, 一次真实权重张量的量级


def _store(root, **kwargs):
    return SecureCheckpointStore(root=root, **kwargs)


class TestDurabilityAcrossRestart:
    def test_checkpoint_survives_a_process_restart(self, tmp_path):
        root = tmp_path / "store"
        written = _store(root).encrypt_checkpoint("job-1", 1, 100, DATA)

        # 全新实例 = api 进程重启后的状态：没有进程字典，主密钥也必须还是同一把。
        restarted = _store(root)
        assert restarted.decrypt_checkpoint(written.metadata.checkpoint_id) == DATA

    def test_checkpoint_id_is_stable_for_identical_weights(self, tmp_path):
        root = tmp_path / "store"
        first = _store(root).encrypt_checkpoint("job-1", 1, 100, DATA)
        second = _store(root).encrypt_checkpoint("job-1", 1, 100, DATA)

        assert first.metadata.checkpoint_id == second.metadata.checkpoint_id
        assert len(_store(root).list_checkpoints("job-1")) == 1

    def test_listing_comes_from_disk_not_from_the_process_dict(self, tmp_path):
        root = tmp_path / "store"
        store = _store(root)
        store.encrypt_checkpoint("job-a", 1, 1, DATA)
        store.encrypt_checkpoint("job-b", 2, 2, DATA)

        restarted = _store(root)
        jobs = {meta.job_id for meta in restarted.list_checkpoints()}
        assert jobs == {"job-a", "job-b"}
        assert restarted.list_checkpoints("job-a")[0].size_bytes == len(DATA)

    def test_master_key_is_reused_from_the_keystore_file(self, tmp_path):
        root = tmp_path / "store"
        cp = _store(root).encrypt_checkpoint("job-1", 1, 100, DATA)

        key_file = root / MASTER_KEY_FILENAME
        assert key_file.exists(), "the master key was never persisted"
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

        sealed_by_other = _store(root).encrypt_checkpoint("job-2", 1, 100, DATA)
        assert _store(root).decrypt_checkpoint(sealed_by_other.metadata.checkpoint_id) == DATA
        assert _store(root).decrypt_checkpoint(cp.metadata.checkpoint_id) == DATA

    def test_loose_master_key_permissions_are_refused(self, tmp_path):
        root = tmp_path / "store"
        cp = _store(root).encrypt_checkpoint("job-1", 1, 100, DATA)
        (root / MASTER_KEY_FILENAME).chmod(0o644)

        with pytest.raises(CheckpointStoreError):
            _store(root).decrypt_checkpoint(cp.metadata.checkpoint_id)

    def test_delete_removes_the_on_disk_blob(self, tmp_path):
        root = tmp_path / "store"
        cp = _store(root).encrypt_checkpoint("job-1", 1, 100, DATA)

        assert _store(root).delete_checkpoint(cp.metadata.checkpoint_id) is True
        assert list(root.rglob("*.enc")) == []
        assert _store(root).decrypt_checkpoint(cp.metadata.checkpoint_id) is None
        assert _store(root).list_checkpoints("job-1") == []


class TestCapacityCeiling:
    def test_write_over_the_ceiling_is_refused(self, tmp_path):
        root = tmp_path / "store"
        allowance = (len(DATA) + 16) * 2 + 1
        store = _store(root, capacity_bytes=allowance)
        store.encrypt_checkpoint("job-1", 1, 1, DATA)
        store.encrypt_checkpoint("job-1", 1, 2, DATA)

        with pytest.raises(CheckpointCapacityError):
            store.encrypt_checkpoint("job-1", 1, 3, DATA)

        # 拒绝＝什么都没写：既没有第三条记录，也没有留下半截文件。
        assert len(store.list_checkpoints("job-1")) == 2
        assert list(root.rglob("*.tmp")) == []
        assert store.stored_bytes() <= allowance

    def test_ceiling_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("CDS_CHECKPOINT_MAX_MB", "3")
        assert checkpoint_capacity_bytes() == 3 * 1024 * 1024
        monkeypatch.setenv("CDS_CHECKPOINT_MAX_MB", "0")
        with pytest.raises(CheckpointStoreError):
            checkpoint_capacity_bytes()
        monkeypatch.setenv("CDS_CHECKPOINT_MAX_MB", "many")
        with pytest.raises(CheckpointStoreError):
            checkpoint_capacity_bytes()

    def test_store_root_comes_from_the_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CDS_CHECKPOINT_STORE_DIR", str(tmp_path / "explicit"))
        assert default_store_root() == tmp_path / "explicit"
        monkeypatch.delenv("CDS_CHECKPOINT_STORE_DIR")
        monkeypatch.setenv("CDS_RESULTS_DIR", str(tmp_path / "results"))
        assert default_store_root() == tmp_path / "results" / "checkpoints"


class TestPathSafety:
    def test_injected_job_id_cannot_escape_the_store_root(self, tmp_path):
        root = tmp_path / "store"
        store = _store(root)
        evil_job = "../../../../etc/escape"

        cp = store.encrypt_checkpoint(evil_job, 1, 1, DATA)

        assert (tmp_path / "etc").exists() is False
        assert (tmp_path / "escape").exists() is False
        for path in root.rglob("*"):
            assert path.resolve().is_relative_to(root.resolve())
        assert store.decrypt_checkpoint(cp.metadata.checkpoint_id) == DATA
        assert [m.job_id for m in store.list_checkpoints(evil_job)] == [evil_job]

    def test_non_hex_checkpoint_id_never_reaches_the_filesystem(self, tmp_path):
        store = _store(tmp_path / "store")
        assert store.decrypt_checkpoint("../../etc/passwd") is None
        assert store.delete_checkpoint("../../etc/passwd") is False


class TestHonestErrors:
    def test_unwritable_root_raises_instead_of_claiming_success(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"not a directory")

        with pytest.raises(CheckpointStoreError):
            _store(blocker / "store").encrypt_checkpoint("job-1", 1, 1, DATA)

    def test_corrupt_metadata_is_not_reported_as_a_checkpoint(self, tmp_path):
        root = tmp_path / "store"
        store = _store(root)
        cp = store.encrypt_checkpoint("job-1", 1, 1, DATA)
        meta = next(iter(root.rglob("*.meta.json")))
        assert meta.name == f"{cp.metadata.checkpoint_id}.meta.json"
        meta.write_text("{ not json", encoding="utf-8")

        assert store.list_checkpoints("job-1") == []

    def test_tampered_blob_on_disk_fails_authentication(self, tmp_path):
        root = tmp_path / "store"
        cp = _store(root).encrypt_checkpoint("job-1", 1, 1, DATA)
        blob = next(iter(root.rglob("*.enc")))
        blob.write_bytes(blob.read_bytes()[:-1] + bytes([blob.read_bytes()[-1] ^ 0xFF]))

        assert _store(root).decrypt_checkpoint(cp.metadata.checkpoint_id) is None


class TestEphemeralDisclosure:
    def test_rootless_instance_admits_it_is_not_durable(self):
        store = SecureCheckpointStore()
        assert store.durable is False
        cp = store.encrypt_checkpoint("job-1", 0, 0, b"payload")
        assert store.decrypt_checkpoint(cp.metadata.checkpoint_id) == b"payload"

    def test_live_store_is_the_configured_durable_one(self):
        assert secure_checkpoint_store.durable is True
