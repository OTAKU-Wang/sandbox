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
  Semantics (ocr A116): unset keeps the default (10); ``0`` or a negative
  limit is an explicit operator decision to DISABLE snapshot creation —
  create_snapshot refuses with SnapshotError (the API maps it to HTTP 400),
  it never 500s and never evicts everything in a loop.
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
import shutil
import stat
import tarfile
import time
import uuid
from pathlib import Path, PurePosixPath

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
    """Build a deterministic tar.gz of the workspace tree in memory.

    Directories are included (parent-before-child under lexicographic order)
    so empty runtime dirs (e.g. ``tmp/`` — the adapter's exec staging area)
    survive a rollback.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(workspace.rglob("*")):
            # A118: the workspace tree is shared with the guest, so sandbox code can
            # drop links in it. Never dereference one into the archive — that would
            # copy data from outside the sandbox into a snapshot the tenant reads back.
            if p.is_symlink():
                continue
            if p.is_file() or p.is_dir():
                tar.add(str(p), arcname=str(p.relative_to(workspace)), recursive=False)
    return buf.getvalue()


def create_snapshot(workspace: Path, session_id: str) -> dict:
    """Snapshot the workspace; returns the manifest dict."""
    settings = get_settings()
    workspace = Path(workspace)
    if not workspace.exists():
        raise SnapshotError("workspace does not exist")

    # A116: a non-positive limit means snapshots are disabled by configuration
    # (unset keeps the field default of 10). Refuse honestly instead of running
    # the eviction loop with max_n <= 0, which popped from an empty list and
    # turned POST .../snapshots into a 500.
    max_n = int(settings.SESSION_MAX_SNAPSHOTS)
    if max_n <= 0:
        raise SnapshotError(
            f"snapshots are disabled by configuration (CDS_SESSION_MAX_SNAPSHOTS={settings.SESSION_MAX_SNAPSHOTS}); "
            "set a positive limit to enable snapshot creation"
        )

    sdir = snapshots_dir(workspace)
    sdir.mkdir(parents=True, exist_ok=True)

    # Retention: evict oldest beyond the cap (before adding a new one).
    existing = sorted(sdir.glob(f"*{_SNAPSHOT_SUFFIX}"), key=lambda p: p.stat().st_mtime)
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


_filter_support: bool | None = None


def _data_filter_supported() -> bool:
    """Whether this interpreter's tarfile offers the safe ``filter="data"`` (3.12+).

    A119: probed once instead of discovered by catching ``TypeError`` around the
    extraction — the catch is what used to justify a second, unguarded unpack.
    """
    global _filter_support
    if _filter_support is None:
        _filter_support = callable(getattr(tarfile, "data_filter", None))
    return _filter_support


def _check_member(member: tarfile.TarInfo) -> None:
    """Reject any member that could write outside the destination (no-filter path)."""
    name = member.name.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if not name or name.startswith("/") or ".." in parts:
        raise SnapshotError(f"snapshot member would escape the workspace: {member.name!r}")
    if member.issym() or member.islnk():
        raise SnapshotError(f"snapshot member is a link (rejected): {member.name!r} -> {member.linkname!r}")
    if not (member.isfile() or member.isdir()):
        raise SnapshotError(f"unsupported snapshot member type: {member.name!r}")


def _extract_snapshot(tar_path: Path, dest: Path) -> None:
    """Unpack a snapshot into ``dest`` — guarded on every path.

    With the data filter available it is used; without it, every member is
    validated before a single byte is written. A guarded extraction that fails
    part-way is a hard error, never a reason to retry unguarded.
    """
    with tarfile.open(tar_path, "r:gz") as tar:
        if _data_filter_supported():
            try:
                tar.extractall(path=str(dest), filter="data")  # noqa: S202 - data filter + self-produced archives
                return
            except TypeError as exc:
                raise SnapshotError(
                    f"snapshot {tar_path.name}: tarfile data filter unusable ({exc}); "
                    "refusing to extract without path protection"
                ) from exc

    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            _check_member(member)
        tar.extractall(path=str(dest))  # noqa: S202 - every member validated just above


def _sibling_tmp(workspace: Path, purpose: str) -> Path:
    """Hidden scratch dir beside the workspace — outside the sandbox bind, same filesystem."""
    return workspace.parent / f".{workspace.name}.cds-{purpose}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _discard_tree(path: Path) -> None:
    """Delete a tree without ever following a link out of it (rmtree unlinks links)."""
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        logger.error(
            "[SessionSnapshots] could not fully discard %s — it sits outside the sandbox "
            "bind and needs operator cleanup",
            path,
        )


def rollback(workspace: Path, snapshot_id: str) -> dict:
    """Restore the workspace from a snapshot (current content is discarded).

    A117: unpack-then-swap, never delete-then-unpack. The archive is expanded
    into a sibling staging directory first; only once that is complete does the
    current workspace move aside and the staged tree take its place. A corrupt
    member, ENOSPC or a rename that cannot happen (e.g. the workspace is a mount
    point) therefore leaves the workspace exactly as it was and the attempt
    stays retryable.
    """
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

    discarded = sum(1 for p in workspace.rglob("*") if p.is_file())

    staging = _sibling_tmp(workspace, "stage")
    backup: Path | None = None
    workspace_mode: int | None = None
    try:
        staging.mkdir(parents=True, exist_ok=False)
        _extract_snapshot(tar_path, staging)
        # Pre-guard snapshots taken before directory entries were archived:
        # the runtime expects tmp/ (exec staging) and the files API keeps its
        # store under files/ — both must exist after a rollback.
        (staging / "tmp").mkdir(parents=True, exist_ok=True)
        (staging / "files").mkdir(parents=True, exist_ok=True)

        if workspace.exists():
            if not workspace.is_dir():
                raise SnapshotError(f"workspace path is not a directory: {workspace}")
            workspace_mode = stat.S_IMODE(workspace.stat().st_mode)
            backup = _sibling_tmp(workspace, "prev")
            os.replace(workspace, backup)
        try:
            os.replace(staging, workspace)
        except OSError:
            if backup is not None:
                os.replace(backup, workspace)
                backup = None
            raise
        if workspace_mode is not None:
            try:
                os.chmod(workspace, workspace_mode)
            except OSError as exc:
                logger.warning(
                    "[SessionSnapshots] could not restore workspace mode %o on %s: %s",
                    workspace_mode, workspace, exc,
                )
        if backup is not None:
            _discard_tree(backup)
    except Exception:
        if backup is not None and not workspace.exists():
            try:
                os.replace(backup, workspace)
                backup = None
            except OSError:
                logger.error(
                    "[SessionSnapshots] rollback of %s failed and the previous workspace could not be "
                    "moved back into place; it is preserved at %s",
                    snapshot_id, backup,
                )
        _discard_tree(staging)
        raise

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
