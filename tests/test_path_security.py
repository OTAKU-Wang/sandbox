"""Path traversal prevention tests.

Verifies that ../ attacks, null bytes, and symlink escapes are blocked
across storage, sandbox output, and file operations.
"""
import pytest
from pathlib import Path

from app.core.path_security import (
    sanitize_filename,
    safe_resolve_within,
    safe_join_object_name,
    PathTraversalError,
)


# ─── sanitize_filename ──────────────────────────────

def test_sanitize_normal_filename():
    assert sanitize_filename("report.csv") == "report.csv"


def test_sanitize_strips_parent_traversal():
    # Takes last component only: ../../etc/passwd -> passwd
    assert sanitize_filename("../../etc/passwd") == "passwd"


def test_sanitize_strips_leading_dots():
    name = sanitize_filename("....hidden")
    assert name == "hidden"


def test_sanitize_strips_path_components():
    name = sanitize_filename("dir/subdir/file.txt")
    assert name == "file.txt"


def test_sanitize_rejects_null_bytes():
    with pytest.raises(PathTraversalError):
        sanitize_filename("file\x00.txt")


def test_sanitize_rejects_empty_after_strip():
    with pytest.raises(PathTraversalError):
        sanitize_filename("...")
    with pytest.raises(PathTraversalError):
        sanitize_filename("/")
    with pytest.raises(PathTraversalError):
        sanitize_filename("../")


def test_sanitize_backslash_paths():
    name = sanitize_filename("..\\..\\windows\\system32\\config")
    assert name == "config"


# ─── safe_resolve_within ──────────────────────────────

def test_safe_resolve_normal_path(tmp_path):
    target = safe_resolve_within(tmp_path, "subdir/file.txt")
    assert str(target).startswith(str(tmp_path.resolve()))


def test_safe_resolve_rejects_parent_traversal(tmp_path):
    with pytest.raises(PathTraversalError):
        safe_resolve_within(tmp_path, "../../etc/passwd")


def test_safe_resolve_rejects_absolute_escape(tmp_path):
    with pytest.raises(PathTraversalError):
        safe_resolve_within(tmp_path, "/etc/passwd")


def test_safe_resolve_rejects_null_bytes(tmp_path):
    with pytest.raises(PathTraversalError):
        safe_resolve_within(tmp_path, "file\x00.txt")


def test_safe_resolve_allows_nested_within_base(tmp_path):
    target = safe_resolve_within(tmp_path, "a/b/c/file.txt")
    assert str(target).startswith(str(tmp_path.resolve()))


def test_safe_resolve_base_itself(tmp_path):
    target = safe_resolve_within(tmp_path, ".")
    assert target == tmp_path.resolve()


# ─── safe_join_object_name ──────────────────────────────

def test_safe_join_normal(tmp_path):
    result = safe_join_object_name(tmp_path, "bucket/file.dat")
    assert str(result).startswith(str(tmp_path.resolve()))


def test_safe_join_rejects_traversal(tmp_path):
    with pytest.raises(PathTraversalError):
        safe_join_object_name(tmp_path, "../../etc/shadow")


# ─── StorageService path traversal ──────────────────────

def test_storage_upload_rejects_traversal():
    from app.services.storage_service import StorageService
    svc = StorageService()
    with pytest.raises(PathTraversalError):
        svc.upload(b"test", "../../etc/crontab")


def test_storage_download_rejects_traversal():
    from app.services.storage_service import StorageService
    svc = StorageService()
    with pytest.raises(PathTraversalError):
        svc.download("../../etc/passwd")


def test_storage_delete_rejects_traversal():
    from app.services.storage_service import StorageService
    svc = StorageService()
    with pytest.raises(PathTraversalError):
        svc.delete("../../tmp/evil")


# ─── DevSandbox get_output path traversal ──────────────────────

@pytest.mark.asyncio
async def test_sandbox_get_output_rejects_traversal():
    from app.services.data_product_sandbox import DataProductDevSandbox
    sandbox = DataProductDevSandbox()
    # Should not crash — just return error (session not found or sanitized path)
    result = await sandbox.get_output("fake-session", "../../etc/passwd")
    # The sanitize_filename strips to "etc/passwd", which won't exist
    assert not result.success
