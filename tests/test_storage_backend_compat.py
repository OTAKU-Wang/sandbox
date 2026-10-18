"""SS-19：对象存储后端切换期的兼容面与诚实降级。

两条真事：
1. 装上 minio 客户端后，老行里记的 `/tmp/cds-storage/...` 绝对路径不能被判死；
2. minio 客户端缺失/不可达时降级到容器内本地目录，必须在日志里看得见（铁律：禁静默降级）。
"""
from __future__ import annotations

import logging

import pytest

from app.core.config import get_settings
from app.services import storage_service as ss_mod
from app.services.storage_service import storage_service


class _BoomMinio:
    def get_object(self, bucket, name):
        raise AssertionError(f"不该走 MinIO（bucket={bucket}, name={name}）")


class _FakeMinio:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def get_object(self, bucket, name):
        self.calls.append((bucket, name))

        class _R:
            def __init__(self, data):
                self._d = data

            def read(self):
                return self._d

        return _R(self.payload)


@pytest.fixture
def _isolate_client():
    saved = storage_service._client
    saved_dir = storage_service._local_dir
    saved_warned = ss_mod._MINIO_FALLBACK_WARNED
    yield
    storage_service._client = saved
    storage_service._local_dir = saved_dir
    ss_mod._MINIO_FALLBACK_WARNED = saved_warned


class TestMixedBackendReads:
    def test_absolute_local_reference_reads_disk_even_with_minio(self, tmp_path, monkeypatch, _isolate_client):
        rel = tmp_path / "data-resources" / "7ab7c79d" / "orders.csv"
        rel.parent.mkdir(parents=True)
        rel.write_bytes(b"order_id,amount\nO1,10\n")
        monkeypatch.setattr(storage_service, "_local_dir", tmp_path)
        monkeypatch.setattr(storage_service, "_get_minio_client", lambda: _BoomMinio())

        assert storage_service._download_raw(str(rel)) == b"order_id,amount\nO1,10\n"

    def test_minio_reference_still_goes_to_object_storage(self, monkeypatch, _isolate_client):
        fake = _FakeMinio(b"from-minio")
        monkeypatch.setattr(storage_service, "_get_minio_client", lambda: fake)
        ref = f"minio://{get_settings().MINIO_BUCKET}/data-resources/x.csv"

        assert storage_service._download_raw(ref) == b"from-minio"
        assert fake.calls == [(get_settings().MINIO_BUCKET, "data-resources/x.csv")]

    def test_foreign_absolute_path_is_not_read_as_local(self, monkeypatch, _isolate_client):
        monkeypatch.setattr(storage_service, "_local_dir", __import__("pathlib").Path("/tmp/cds-storage"))
        assert storage_service._is_local_reference("/etc/passwd") is False
        assert storage_service._is_local_reference("data-resources/x.csv") is False
        assert storage_service._is_local_reference(f"minio://{get_settings().MINIO_BUCKET}/a.csv") is False


class TestHonestFallback:
    def test_local_fallback_is_logged_once_with_reason(self, monkeypatch, caplog, _isolate_client):
        import sys

        monkeypatch.setattr(ss_mod, "_MINIO_FALLBACK_WARNED", False)
        storage_service._client = None
        # `from minio import Minio` 在函数里执行：把模块位置空即等价于「包没装」。
        monkeypatch.setitem(sys.modules, "minio", None)

        with caplog.at_level(logging.WARNING, logger="app.services.storage_service"):
            assert storage_service._get_minio_client() is None
            assert storage_service._get_minio_client() is None

        msgs = [r.getMessage() for r in caplog.records if "对象存储降级为本地目录" in r.getMessage()]
        assert len(msgs) == 1, "降级必须说一次，且不能每上传刷一条"
        assert "/tmp/cds-storage" in msgs[0] or "cds-storage" in msgs[0]
