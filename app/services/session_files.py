"""Session file store (Round 39 usability) — sandbox-visible per-session files.

Uploads land inside the session workspace ``files/`` directory (the same
directory the sandbox binds read-write), so code executing in the sandbox can
read uploaded inputs. Files are encrypted at rest with the session DEK using
the exact ``[nonce(12)][tag(16)][ciphertext]`` layout provision uses — sandbox
code decrypts via the injected ``CDS_DEK_HEX`` (same pattern as RAG corpora).

Downloads run through the T5 output review (DLP) in the API layer before any
byte reaches the caller; blocked content is never released.

A per-directory ``.files_index.json`` records which entries are encrypted and
their checksums; it is metadata-only (never content) and excluded from
listings.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from app.core.config import get_settings
from app.services.sandbox_security import decrypt_workspace_file, encrypt_workspace_file

logger = logging.getLogger(__name__)

FILES_DIRNAME = "files"
INDEX_FILENAME = ".files_index.json"
_FILENAME_FORBIDDEN = {"", ".", ".."}


class SessionFileError(ValueError):
    """Raised for invalid file names / limit violations (maps to 4xx)."""


def validate_filename(name: str) -> str:
    """Reject path traversal and unsafe names; return the cleaned name."""
    name = (name or "").strip()
    if name in _FILENAME_FORBIDDEN or len(name) > 255:
        raise SessionFileError(f"invalid file name: {name!r}")
    if "/" in name or "\\" in name or "\x00" in name:
        raise SessionFileError("file name must not contain path separators")
    if any(ord(c) < 32 for c in name):
        raise SessionFileError("file name must not contain control characters")
    return name


def files_dir(workspace: Path) -> Path:
    return Path(workspace) / FILES_DIRNAME


def _index_path(workspace: Path) -> Path:
    return files_dir(workspace) / INDEX_FILENAME


def _load_index(workspace: Path) -> dict:
    p = _index_path(workspace)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(workspace: Path, index: dict) -> None:
    files_dir(workspace).mkdir(parents=True, exist_ok=True)
    _index_path(workspace).write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def list_files(workspace: Path) -> list[dict]:
    """List uploaded files (flat, top-level only, index excluded)."""
    d = files_dir(workspace)
    if not d.exists():
        return []
    index = _load_index(workspace)
    entries = []
    for p in sorted(d.iterdir()):
        if not p.is_file() or p.name == INDEX_FILENAME:
            continue
        meta = index.get(p.name, {})
        stat = p.stat()
        entries.append(
            {
                "filename": p.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "encrypted": bool(meta.get("encrypted", False)),
                "sha256": meta.get("sha256"),
            }
        )
    return entries


def write_file(workspace: Path, filename: str, data: bytes, dek: bytes | None = None) -> dict:
    """Write (and at-rest-encrypt) one file into the session files dir.

    Enforces the per-file size cap and the per-session file count cap from
    settings. The write is atomic (tmp + rename).
    """
    settings = get_settings()
    name = validate_filename(filename)
    max_bytes = settings.SESSION_FILE_MAX_BYTES
    if len(data) > max_bytes:
        raise SessionFileError(
            f"file too large: {len(data)} bytes (limit {max_bytes})"
        )
    d = files_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)

    index = _load_index(workspace)
    existing = [p for p in d.iterdir() if p.is_file() and p.name != INDEX_FILENAME]
    if name not in index and len(existing) >= settings.SESSION_MAX_FILES:
        raise SessionFileError(
            f"session file count limit reached ({settings.SESSION_MAX_FILES})"
        )

    target = d / name
    tmp = d / f".{name}.tmp-{os.getpid()}-{time.time_ns()}"
    tmp.write_bytes(data)
    os.replace(tmp, target)

    encrypted = False
    if dek:
        try:
            encrypt_workspace_file(target, dek)
            encrypted = True
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("[SessionFiles] encrypt failed for %s: %s", name, e)
            target.unlink(missing_ok=True)
            raise SessionFileError(f"file encryption failed: {e}")

    meta = {
        "encrypted": encrypted,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "uploaded_at": time.time(),
    }
    index[name] = meta
    _save_index(workspace, index)
    return {"filename": name, "size": len(data), "encrypted": encrypted, "sha256": meta["sha256"]}


def read_file(workspace: Path, filename: str, dek: bytes | None = None) -> tuple[bytes, dict]:
    """Read one file, decrypting when the index marks it encrypted.

    Returns (content_bytes, meta). Raises SessionFileError for unknown names.
    """
    name = validate_filename(filename)
    target = files_dir(workspace) / name
    if not target.is_file():
        raise SessionFileError(f"file not found: {name}")
    index = _load_index(workspace)
    meta = index.get(name, {})
    if meta.get("encrypted"):
        if not dek:
            raise SessionFileError("file is encrypted but no session DEK is available")
        content = decrypt_workspace_file(target, dek)
    else:
        content = target.read_bytes()
    return content, meta


def delete_file(workspace: Path, filename: str) -> bool:
    name = validate_filename(filename)
    target = files_dir(workspace) / name
    if not target.is_file():
        return False
    target.unlink()
    index = _load_index(workspace)
    index.pop(name, None)
    _save_index(workspace, index)
    return True
