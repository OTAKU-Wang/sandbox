"""Storage service tests — upload, download, delete, checksum."""
import pytest
from app.services.storage_service import StorageService


def test_upload_download():
    svc = StorageService()
    data = b"hello world test data"
    result = svc.upload(data, "test.txt", content_type="text/plain")
    assert result["path"]
    assert result["checksum"]
    assert result["size"] == len(data)

    # download uses the object_name, not the full path
    downloaded = svc.download("test.txt")
    assert downloaded == data


def test_upload_checksum():
    from app.utils.crypto import sm3_hash
    svc = StorageService()
    data = b"checksum test"
    result = svc.upload(data, "chk.txt")
    expected = sm3_hash(data)
    assert result["checksum"] == expected


def test_delete():
    svc = StorageService()
    result = svc.upload(b"delete me", "del.txt")
    assert svc.delete("del.txt") is True


def test_upload_empty():
    svc = StorageService()
    result = svc.upload(b"", "empty.txt")
    assert result["size"] == 0


def test_upload_large():
    svc = StorageService()
    data = b"x" * (1024 * 100)  # 100KB
    result = svc.upload(data, "large.bin")
    assert result["size"] == 102400
    downloaded = svc.download("large.bin")
    assert len(downloaded) == 102400


def test_delete_nonexistent():
    svc = StorageService()
    # Should not raise
    result = svc.delete("nonexistent_file_12345.txt")
    # Returns False for local filesystem when file doesn't exist
    assert result is False
