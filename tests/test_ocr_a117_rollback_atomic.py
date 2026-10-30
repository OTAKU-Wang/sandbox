"""ocr A117 — snapshot rollback must be atomic (session_snapshots.rollback).

HEAD rolled the workspace back by deleting every entry first and only then
unpacking the archive, so any failure (ENOSPC, a bad member, a kill, a rename
that cannot happen) left the user workspace half-deleted with no fallback copy.
These tests drive that failure injection at service level (portable tmp tree,
no sandbox host involved).
"""
import errno
import tarfile

import pytest

from app.services import session_snapshots as ss

_RESERVED = {"_cds_snapshots"}


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "bwrap-atom1234"
    (w / "files").mkdir(parents=True)
    (w / "input").mkdir()
    (w / "files" / "a.txt").write_bytes(b"AAAA")
    (w / "input" / "data.enc").write_bytes(b"\x00\x01CIPHER")
    (w / ".security_config.json").write_text("{}")
    return w


def _mutate(w):
    """Current (post-snapshot) content that a failed rollback must not lose."""
    (w / "files" / "a.txt").write_bytes(b"MUTATED-CURRENT")
    (w / "files" / "new.txt").write_bytes(b"NEW-CURRENT")


def _tree(w):
    return {str(p.relative_to(w)): p.read_bytes() for p in sorted(w.rglob("*")) if p.is_file()}


def _siblings(w):
    return sorted(p.name for p in w.parent.iterdir() if p.name not in _RESERVED | {w.name})


def test_extract_failure_leaves_workspace_intact(ws, monkeypatch):
    """磁盘满 / 归档成员抛 OSError：回滚整笔失败，当前工作区一个字节都不能少。"""
    snap = ss.create_snapshot(ws, session_id="s")
    _mutate(ws)
    before = _tree(ws)

    def exploding(self, path=".", members=None, **kw):  # noqa: ANN001 - stdlib patch
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(tarfile.TarFile, "extractall", exploding)
    with pytest.raises((ss.SnapshotError, OSError)):
        ss.rollback(ws, snap["snapshot_id"])

    assert _tree(ws) == before
    assert _siblings(ws) == []


def test_swap_failure_restores_previous_workspace(ws, monkeypatch):
    """The archive unpacked fine but the directory swap failed: the workspace
    that was moved aside has to be moved back, not left in a hidden copy."""
    snap = ss.create_snapshot(ws, session_id="s")
    _mutate(ws)
    before = _tree(ws)

    real_replace = ss.os.replace
    blocked = {"done": False}

    def flaky_replace(src, dst, *a, **kw):
        if str(dst) == str(ws) and not blocked["done"]:
            blocked["done"] = True
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(ss.os, "replace", flaky_replace)
    with pytest.raises((ss.SnapshotError, OSError)):
        ss.rollback(ws, snap["snapshot_id"])

    assert _tree(ws) == before
    assert _siblings(ws) == []


def test_swap_failure_keeps_previous_content_recoverable(ws, monkeypatch):
    """Even when putting the workspace back is impossible too (the device really
    is full), the discarded content must survive on disk and the caller must get
    an error — never a silently emptied workspace."""
    snap = ss.create_snapshot(ws, session_id="s")
    _mutate(ws)
    before = _tree(ws)

    real_replace = ss.os.replace

    def broken_replace(src, dst, *a, **kw):
        if str(dst) == str(ws):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(ss.os, "replace", broken_replace)
    with pytest.raises((ss.SnapshotError, OSError)):
        ss.rollback(ws, snap["snapshot_id"])

    preserved = [p for p in ws.parent.iterdir() if p.name not in _RESERVED | {ws.name}]
    assert len(preserved) == 1
    assert not ws.exists()
    kept = {str(p.relative_to(preserved[0])): p.read_bytes() for p in sorted(preserved[0].rglob("*")) if p.is_file()}
    assert kept == before


def test_successful_rollback_restores_and_leaves_no_temp_tree(ws):
    """Over-correction guard: the staged swap must keep the normal semantics
    (content restored, discarded_files counted, tmp//files/ present, no litter)."""
    snap = ss.create_snapshot(ws, session_id="s")
    _mutate(ws)

    result = ss.rollback(ws, snap["snapshot_id"])

    assert result["discarded_files"] == 4
    assert (ws / "files" / "a.txt").read_bytes() == b"AAAA"
    assert (ws / "input" / "data.enc").read_bytes() == b"\x00\x01CIPHER"
    assert not (ws / "files" / "new.txt").exists()
    assert (ws / "tmp").is_dir() and (ws / "files").is_dir()
    assert _siblings(ws) == []


def test_rollback_is_retryable_after_a_failed_attempt(ws, monkeypatch):
    """失败可重试：一次失败的回滚不能把快照或工作区烧成不可恢复状态。"""
    snap = ss.create_snapshot(ws, session_id="s")
    _mutate(ws)

    def exploding(self, path=".", members=None, **kw):  # noqa: ANN001 - stdlib patch
        raise OSError(errno.EIO, "read failed")

    monkeypatch.setattr(tarfile.TarFile, "extractall", exploding)
    with pytest.raises((ss.SnapshotError, OSError)):
        ss.rollback(ws, snap["snapshot_id"])
    monkeypatch.undo()

    ss.rollback(ws, snap["snapshot_id"])
    assert (ws / "files" / "a.txt").read_bytes() == b"AAAA"
    assert _siblings(ws) == []
