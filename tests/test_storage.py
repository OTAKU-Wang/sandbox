"""StorageService tests — envelope encryption with KMS-managed DEKs."""
import pytest
from app.services.storage_service import StorageService
from app.utils.crypto import sm3_hash


def test_upload_download_encrypted():
    """Default upload encrypts; download auto-decrypts."""
    storage = StorageService()
    data = b"Test file content for CDS storage"
    result = storage.upload(data, "test/file.txt")
    assert result["checksum"]
    assert result["size"] == len(data)
    assert result["encrypted"] is True
    assert result["key_id"]

    downloaded = storage.download("test/file.txt")
    assert downloaded == data


def test_upload_checksum():
    storage = StorageService()
    data = b"Checksum verification"
    result = storage.upload(data, "test/checksum.bin")
    assert result["checksum"] == sm3_hash(data)


def test_upload_plaintext():
    """Explicit plaintext upload — no encryption."""
    storage = StorageService()
    data = b"Non-sensitive data"
    result = storage.upload_plaintext(data, "test/plain.txt")
    assert result["encrypted"] is False
    assert "key_id" not in result

    downloaded = storage.download("test/plain.txt")
    assert downloaded == data


def test_encrypted_differs_from_plaintext():
    """Encrypted storage differs from plaintext storage."""
    storage = StorageService()
    data = b"plaintext content"
    result_plain = storage.upload_plaintext(data, "test/plain.bin")
    result_enc = storage.upload(data, "test/enc.bin")

    raw_plain = storage.download_raw("test/plain.bin")
    raw_enc = storage.download_raw("test/enc.bin")
    assert raw_plain == data  # plaintext stored as-is
    assert raw_enc != data  # encrypted data differs


def test_download_auto_decrypts():
    """download() auto-detects envelope format and decrypts."""
    storage = StorageService()
    data = b"Auto-decrypt test"
    storage.upload(data, "test/auto.bin")
    assert storage.download("test/auto.bin") == data


def test_download_accepts_returned_local_path():
    """The path returned by upload() can be stored and used directly."""
    storage = StorageService()
    data = b"Stored path roundtrip"
    result = storage.upload(data, "test/path-roundtrip.bin")
    assert storage.download(result["path"]) == data


def test_download_raw_returns_envelope():
    """download_raw() returns raw envelope bytes without decryption."""
    storage = StorageService()
    data = b"Raw test"
    storage.upload(data, "test/raw.bin")
    raw = storage.download_raw("test/raw.bin")
    assert raw != data  # Should be envelope-wrapped
    assert raw[0] == 1  # Envelope version byte


def test_get_encryption_info():
    """get_encryption_info() returns key_id without downloading full object."""
    storage = StorageService()
    data = b"Info test"
    result = storage.upload(data, "test/info.bin")

    info = storage.get_encryption_info("test/info.bin")
    assert info is not None
    assert info["encrypted"] is True
    assert info["key_id"] == result["key_id"]


def test_get_encryption_info_plaintext():
    """get_encryption_info() returns None for plaintext objects."""
    storage = StorageService()
    storage.upload_plaintext(b"plain", "test/info_plain.bin")
    info = storage.get_encryption_info("test/info_plain.bin")
    assert info is None


def test_delete():
    storage = StorageService()
    storage.upload(b"delete me", "test/delete.txt")
    assert storage.delete("test/delete.txt") is True


def test_large_file_encrypted():
    """Large file (1MB) encrypted upload/download roundtrip."""
    storage = StorageService()
    data = b"x" * (1024 * 1024)
    result = storage.upload(data, "test/large.bin")
    assert result["size"] == 1024 * 1024
    assert storage.download("test/large.bin") == data


def test_empty_file():
    """Empty file handling."""
    storage = StorageService()
    result = storage.upload(b"", "test/empty.bin")
    assert result["size"] == 0
    assert storage.download("test/empty.bin") == b""
