"""Workspace file encryption tests — SM4-GCM encrypt/decrypt roundtrip."""
import os
import pytest
from pathlib import Path

from app.services.sandbox_security import (
    encrypt_workspace_file,
    decrypt_workspace_file,
    encrypt_workspace_directory,
    decrypt_workspace_directory,
)
from app.utils.crypto import SM4Cipher


@pytest.fixture
def dek():
    """Generate a random 32-byte DEK for testing."""
    return os.urandom(32)


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace with input directory."""
    ws = tmp_path / "workspace"
    (ws / "input").mkdir(parents=True)
    (ws / "output").mkdir()
    (ws / "tmp").mkdir()
    return ws


def test_encrypt_decrypt_file_roundtrip(dek, workspace):
    """Encrypt then decrypt a file — must recover original content."""
    f = workspace / "input" / "data.csv"
    original = b"name,age\nAlice,30\nBob,25\n"
    f.write_bytes(original)

    encrypt_workspace_file(f, dek)
    assert f.read_bytes() != original  # Encrypted on disk

    decrypted = decrypt_workspace_file(f, dek)
    assert decrypted == original


def test_encrypt_empty_file_skipped(dek, workspace):
    """Empty files are not encrypted (no data to encrypt)."""
    f = workspace / "input" / "empty.txt"
    f.write_bytes(b"")

    result = encrypt_workspace_file(f, dek)
    assert result == f
    assert f.read_bytes() == b""  # Still empty


def test_decrypt_file_too_short(dek, workspace):
    """Decrypting a file that's too short raises ValueError."""
    f = workspace / "input" / "short.bin"
    f.write_bytes(b"\x00" * 10)  # Less than 28 bytes (nonce+tag)

    with pytest.raises(ValueError, match="too short"):
        decrypt_workspace_file(f, dek)


def test_decrypt_wrong_key(workspace):
    """Decrypting with wrong key fails."""
    f = workspace / "input" / "secret.bin"
    f.write_bytes(b"secret data")

    correct_key = os.urandom(32)
    encrypt_workspace_file(f, correct_key)

    wrong_key = os.urandom(32)
    with pytest.raises(Exception):
        decrypt_workspace_file(f, wrong_key)


def test_encrypt_directory_roundtrip(dek, workspace):
    """Encrypt all files in workspace/input/ then decrypt them."""
    files = {
        "data.csv": b"id,value\n1,100\n2,200\n",
        "config.json": b'{"key": "value"}',
        "subdir/nested.txt": b"nested content",
    }
    for name, content in files.items():
        p = workspace / "input" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    encrypted = encrypt_workspace_directory(workspace, dek)
    assert len(encrypted) == 3

    # All files should be encrypted (different from original)
    for name, original in files.items():
        p = workspace / "input" / name
        assert p.read_bytes() != original

    # Decrypt and verify
    decrypted = decrypt_workspace_directory(workspace, dek)
    assert len(decrypted) == 3

    for name, original in files.items():
        p = workspace / "input" / name
        assert p.read_bytes() == original


def test_encrypt_directory_skips_empty(dek, workspace):
    """Empty files in directory are skipped."""
    (workspace / "input" / "data.csv").write_bytes(b"a,b\n1,2\n")
    (workspace / "input" / "empty.txt").write_bytes(b"")

    encrypted = encrypt_workspace_directory(workspace, dek)
    assert len(encrypted) == 1  # Only data.csv


def test_encrypt_no_input_dir(dek, tmp_path):
    """If input/ doesn't exist, returns empty list."""
    ws = tmp_path / "empty_ws"
    ws.mkdir()

    result = encrypt_workspace_directory(ws, dek)
    assert result == []


def test_encrypted_format_structure(dek, workspace):
    """Encrypted file has format: [nonce(12)][tag(16)][ciphertext]."""
    f = workspace / "input" / "test.bin"
    original = b"A" * 100
    f.write_bytes(original)

    encrypt_workspace_file(f, dek)
    raw = f.read_bytes()

    # nonce(12) + tag(16) + ciphertext
    assert len(raw) == 12 + 16 + len(original)  # SM4-GCM doesn't pad for 16-byte alignment
    # Actually SM4-GCM ciphertext is same length as plaintext
    assert len(raw) >= 12 + 16  # At minimum nonce + tag


def test_large_file_encrypted(dek, workspace):
    """1MB file encrypted/decrypted roundtrip."""
    f = workspace / "input" / "large.bin"
    original = os.urandom(1024 * 1024)
    f.write_bytes(original)

    encrypt_workspace_file(f, dek)
    decrypted = decrypt_workspace_file(f, dek)
    assert decrypted == original


def test_binary_file_encrypted(dek, workspace):
    """Binary file with all byte values encrypted correctly."""
    f = workspace / "input" / "binary.bin"
    original = bytes(range(256)) * 100
    f.write_bytes(original)

    encrypt_workspace_file(f, dek)
    decrypted = decrypt_workspace_file(f, dek)
    assert decrypted == original


def test_multiple_encrypted_files_independent(dek, workspace):
    """Each file is encrypted independently with its own nonce."""
    f1 = workspace / "input" / "a.bin"
    f2 = workspace / "input" / "b.bin"
    # Same content
    f1.write_bytes(b"identical content")
    f2.write_bytes(b"identical content")

    encrypt_workspace_file(f1, dek)
    encrypt_workspace_file(f2, dek)

    # Different nonces → different ciphertext
    assert f1.read_bytes() != f2.read_bytes()

    # Both decrypt to same plaintext
    assert decrypt_workspace_file(f1, dek) == b"identical content"
    assert decrypt_workspace_file(f2, dek) == b"identical content"
