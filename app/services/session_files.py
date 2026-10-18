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
# 成果采集范围：files/ 递归，但 input/（合约供数原件）与 tmp/（中间产物）不可能是交付物。
HARVEST_EXCLUDE_DIRS = ("input", "tmp")


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


def _safe_rel(root: Path, target: Path) -> str | None:
    """target 相对 root 的 POSIX 路径；越界（含软链跳出）返回 None。"""
    try:
        return "/".join(target.resolve().relative_to(root.resolve()).parts)
    except ValueError:
        return None


def list_output_files(workspace: Path) -> list[dict]:
    """成果清单：递归 files/，排除 input/ tmp/ 与索引/隐藏文件（SS-19 D-e2e-11）。

    开发环里笔记本的当前目录就是 files/，面板又建议产物放 output/ —— 旧 list_files()
    只看 files/ 顶层，写在 files/output/ 里的东西进不了包，闭环走到交付物那一步就变
    NO_OUTPUT_FILES。name 是相对 files/ 的 POSIX 路径。

    手写栈遍历（不用 rglob）：不跟软链/目录链接，否则沙箱代码建一个指向宿主外的
    软链就能把外部文件递进入包；同时避开循环链接让遍历不收敛。
    """
    root = files_dir(workspace)
    if not root.exists():
        return []
    index = _load_index(workspace)
    root_resolved = root.resolve()
    entries = []
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as it:
            siblings = sorted(it, key=lambda e: e.name)
        for de in siblings:
            path = Path(de.path)
            if de.name == INDEX_FILENAME or de.name.startswith(".") or de.is_symlink():
                continue
            if de.is_dir(follow_symlinks=False):
                if current == root and de.name in HARVEST_EXCLUDE_DIRS:
                    continue
                try:
                    path.resolve().relative_to(root_resolved)
                except ValueError:
                    continue
                stack.append(path)
                continue
            if not de.is_file(follow_symlinks=False):
                continue
            rel = _safe_rel(root, path)
            if rel is None or rel.split("/", 1)[0] in HARVEST_EXCLUDE_DIRS:
                continue
            # CR MAJOR-5：只按**相对路径**查索引——旧版兼查 `index.get(de.name)`，
            # 嵌套文件撞上同名扁平件时会被错标 encrypted/sha256，而读回侧
            # （read_output_file）只认 rel：审计过的清单与实读内容口径不一致。
            meta = index.get(rel) or {}
            stat = path.stat()
            entries.append(
                {
                    "name": rel,
                    "filename": rel,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "encrypted": bool(meta.get("encrypted", False)),
                    "sha256": meta.get("sha256"),
                }
            )
    return sorted(entries, key=lambda e: e["name"])


def read_output_file(workspace: Path, rel: str, dek: bytes | None = None) -> tuple[bytes, dict]:
    """按 list_output_files 给的相对路径读回内容（加密件需 DEK，布局与 read_file 一致）。"""
    root = files_dir(workspace)
    raw = (rel or "").strip()
    parts = [seg for seg in raw.replace("\\", "/").split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts) or raw.startswith(("/", "\\")) or "\x00" in raw:
        raise SessionFileError(f"invalid file name: {rel!r}")
    if parts[0] in HARVEST_EXCLUDE_DIRS:
        raise SessionFileError(f"{parts[0]}/ 不是交付物目录: {rel!r}")
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise SessionFileError(f"invalid file name: {rel!r}") from None
    if not target.is_file():
        raise FileNotFoundError(f"no such output file: {rel!r}")
    meta = _load_index(workspace).get(rel) or {}
    if meta.get("encrypted"):
        if dek is None:
            raise SessionFileError(f"file {rel!r} is encrypted at rest but no DEK provided")
        # decrypt_workspace_file 收的是 Path（它自己 read_bytes），这里不能先把字节读出来再传。
        data = decrypt_workspace_file(target, dek)
    else:
        data = target.read_bytes()
    return data, meta


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
