"""Session snapshot store (Round 39 usability) — workspace snapshot/rollback.

A snapshot is a deterministic tar.gz of the session workspace, stored in a
sibling directory OUTSIDE the sandbox bind (``<workspace.parent>/_cds_snapshots/
<workspace.name>/``) so sandbox code can neither read nor tamper with it, and
so rollback can safely replace the workspace in place.

Design notes:
- Snapshot content is the at-rest workspace: every file is already
  DEK-encrypted, so archives are safe at rest without extra crypto.
- Integrity: each archive ships a manifest (file count, byte size, sha256 of
  the tar); rollback refuses archives whose checksum does not match.
- Retention: oldest snapshots are evicted beyond CDS_SESSION_MAX_SNAPSHOTS.
- Lifecycle: archives survive an accidental terminate (sibling dir), which
  makes "restore after terminate" possible for operators; the cleanup loop
  does not manage them in this round.

overlayfs layering remains the production-scale optimisation path (P2); the
copy-based snapshot is portable (works on Windows dev boxes and plain Linux)
and preserves the exact "删除即可回退 / restore-on-demand" semantics.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import tarfile
import time
import uuid
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SNAPSHOTS_DIRNAME = "_cds_snapshots"
_SNAPSHOT_SUFFIX = ".tar.gz"
_MANIFEST_SUFFIX = ".json"


class SnapshotError(ValueError):
    """Raised for snapshot limit violations / unknown or corrupt snapshots."""


def snapshots_dir(workspace: Path) -> Path:
    workspace = Path(workspace)
    return workspace.parent / SNAPSHOTS_DIRNAME / workspace.name


def _tar_bytes(workspace: Path) -> bytes:
    """Build a deterministic tar.gz of the workspace tree in memory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(workspace.rglob("*")):
            if p.is_file():
                tar.add(str(p), arcname=str(p.relative_to(workspace)), recursive=False)
    return buf.getvalue()


def create_snapshot(workspace: Path, session_id: str) -> dict:
    """Snapshot the workspace; returns the manifest dict."""
    settings = get_settings()
    workspace = Path(workspace)
    if not workspace.exists():
        raise SnapshotError("workspace does not exist")

    sdir = snapshots_dir(workspace)
    sdir.mkdir(parents=True, exist_ok=True)

    # Retention: evict oldest beyond the cap (before adding a new one).
    existing = sorted(sdir.glob(f"*{_SNAPSHOT_SUFFIX}"), key=lambda p: p.stat().st_mtime)
    max_n = settings.SESSION_MAX_SNAPSHOTS
    while len(existing) >= max_n:
        oldest = existing.pop(0)
        oldest.unlink(missing_ok=True)
        manifest = oldest.with_name(oldest.name[: -len(_SNAPSHOT_SUFFIX)] + _MANIFEST_SUFFIX)
        manifest.unlink(missing_ok=True)

    blob = _tar_bytes(workspace)
    snapshot_id = f"snap-{uuid.uuid4().hex[:12]}"
    tar_path = sdir / f"{snapshot_id}{_SNAPSHOT_SUFFIX}"
    tmp_path = sdir / f".{snapshot_id}.tmp-{os.getpid()}"
    tmp_path.write_bytes(blob)
    os.replace(tmp_path, tar_path)

    manifest = {
        "snapshot_id": snapshot_id,
        "session_id": session_id,
        "created_at": time.time(),
        "bytes": len(blob),
        "file_count": sum(1 for m in tarfile.open(fileobj=io.BytesIO(blob)).getmembers() if m.isfile()),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }
    (sdir / f"{snapshot_id}{_MANIFEST_SUFFIX}").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    logger.info("[SessionSnapshots] created %s for session %s (%d bytes)", snapshot_id, session_id, len(blob))
    return manifest


def list_snapshots(workspace: Path) -> list[dict]:
    sdir = snapshots_dir(workspace)
    if not sdir.exists():
        return []
    out = []
    for mf in sorted(sdir.glob(f"*{_MANIFEST_SUFFIX}"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(mf.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _verify_integrity(tar_path: Path, manifest: dict) -> None:
    blob = tar_path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != manifest.get("sha256"):
        raise SnapshotError(f"snapshot {manifest.get('snapshot_id')} failed integrity check (checksum mismatch)")


def rollback(workspace: Path, snapshot_id: str) -> dict:
    """Restore the workspace from a snapshot (current content is discarded)."""
    workspace = Path(workspace)
    snapshot_id = str(snapshot_id or "")
    if "/" in snapshot_id or "\\" in snapshot_id or ".." in snapshot_id or not snapshot_id:
        raise SnapshotError(f"invalid snapshot id: {snapshot_id!r}")
    sdir = snapshots_dir(workspace)
    tar_path = sdir / f"{snapshot_id}{_SNAPSHOT_SUFFIX}"
    manifest_path = sdir / f"{snapshot_id}{_MANIFEST_SUFFIX}"
    if not tar_path.is_file() or not manifest_path.is_file():
        raise SnapshotError(f"snapshot not found: {snapshot_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _verify_integrity(tar_path, manifest)

    # Discard current workspace content (snapshots live outside the bind).
    discarded = 0
    for p in workspace.rglob("*"):
        if p.is_file():
            discarded += 1
    for p in sorted(workspace.rglob("*"), reverse=True):
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
        elif p.is_file():
            p.unlink()

    with tarfile.open(tar_path, "r:gz") as tar:
        try:
            tar.extractall(path=str(workspace), filter="data")  # noqa: S202 - data filter + self-produced archives
        except TypeError:  # Python < 3.12 without data filter backport
            tar.extractall(path=str(workspace))

    logger.info(
        "[SessionSnapshots] rolled back %s for session %s (discarded %d files)",
        snapshot_id, manifest.get("session_id"), discarded,
    )
    return {**manifest, "discarded_files": discarded}


def delete_snapshot(workspace: Path, snapshot_id: str) -> bool:
    snapshot_id = str(snapshot_id or "")
    if "/" in snapshot_id or "\\" in snapshot_id or ".." in snapshot_id or not snapshot_id:
        raise SnapshotError(f"invalid snapshot id: {snapshot_id!r}")
    sdir = snapshots_dir(workspace)
    tar_path = sdir / f"{snapshot_id}{_SNAPSHOT_SUFFIX}"
    manifest_path = sdir / f"{snapshot_id}{_MANIFEST_SUFFIX}"
    if not tar_path.is_file():
        return False
    tar_path.unlink()
    manifest_path.unlink(missing_ok=True)
    return True
