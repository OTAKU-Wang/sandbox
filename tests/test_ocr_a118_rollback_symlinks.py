"""ocr A118 — symlinks inside the workspace during snapshot/rollback.

The workspace tree is shared between the host and the guest, so code running
inside the sandbox can create links in it. HEAD's delete loop only had
``is_dir()`` / ``is_file()`` branches — and ``Path.is_dir()`` *follows* links:
a link to a non-empty directory matched neither branch and survived the
rollback (leaving a host path the files API can be lured into writing to), a
link to an empty directory hit ``rmdir()`` and raised ``NotADirectoryError``,
and a dangling link survived too. POSIX-only (symlink semantics).
"""
import io
import os
import tarfile

import pytest

from app.services import session_snapshots as ss

pytestmark = pytest.mark.skipif(os.name != "posix", reason="symlink semantics are POSIX-only")


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "bwrap-link1234"
    (w / "files").mkdir(parents=True)
    (w / "input").mkdir()
    (w / "files" / "a.txt").write_bytes(b"AAAA")
    (w / "input" / "data.enc").write_bytes(b"\x00\x01CIPHER")
    return w


@pytest.fixture
def outside(tmp_path):
    """Anything outside the sandbox bind: must never be touched, never copied."""
    d = tmp_path / "outside"
    d.mkdir()
    (d / "host-secret.txt").write_bytes(b"HOST-SECRET")
    (d / "nonempty").mkdir()
    (d / "nonempty" / "inner.txt").write_bytes(b"INNER")
    (d / "empty").mkdir()
    return d


def _snapshot_then_link(ws, link_factory):
    snap = ss.create_snapshot(ws, session_id="s")
    (ws / "files" / "a.txt").write_bytes(b"CURRENT")
    link_factory(ws)
    return snap


def test_rollback_removes_link_to_nonempty_dir(ws, outside):
    snap = _snapshot_then_link(ws, lambda w: (w / "link_out").symlink_to(outside / "nonempty", True))

    ss.rollback(ws, snap["snapshot_id"])

    assert not (ws / "link_out").is_symlink(), "rollback left a workspace link pointing outside the sandbox"
    assert not (ws / "link_out").exists()
    assert (ws / "files" / "a.txt").read_bytes() == b"AAAA"
    assert (outside / "nonempty" / "inner.txt").read_bytes() == b"INNER"


def test_rollback_survives_link_to_empty_dir(ws, outside):
    snap = _snapshot_then_link(ws, lambda w: (w / "link_empty").symlink_to(outside / "empty", True))

    ss.rollback(ws, snap["snapshot_id"])

    assert not (ws / "link_empty").is_symlink()
    assert (ws / "files" / "a.txt").read_bytes() == b"AAAA"
    assert (outside / "empty").is_dir()


def test_rollback_removes_dangling_link(ws, tmp_path):
    snap = _snapshot_then_link(ws, lambda w: (w / "link_dangling").symlink_to(tmp_path / "no-such-target"))

    ss.rollback(ws, snap["snapshot_id"])

    assert not (ws / "link_dangling").is_symlink()
    assert (ws / "files" / "a.txt").read_bytes() == b"AAAA"


def test_rollback_removes_link_to_file_without_touching_target(ws, outside):
    snap = _snapshot_then_link(ws, lambda w: (w / "link_file").symlink_to(outside / "host-secret.txt"))

    ss.rollback(ws, snap["snapshot_id"])

    assert not (ws / "link_file").is_symlink()
    assert (outside / "host-secret.txt").read_bytes() == b"HOST-SECRET"


def test_snapshot_never_copies_link_target_into_the_archive(ws, outside):
    """A link the guest created must not turn into a copy of host data inside
    the snapshot (the archive is readable by the tenant via rollback)."""
    (ws / "link_secret").symlink_to(outside / "host-secret.txt")
    (ws / "link_dir").symlink_to(outside / "nonempty", True)

    manifest = ss.create_snapshot(ws, session_id="s")
    blob = (ss.snapshots_dir(ws) / f"{manifest['snapshot_id']}.tar.gz").read_bytes()
    names = {m.name for m in tarfile.open(fileobj=io.BytesIO(blob)).getmembers()}

    assert "link_secret" not in names
    assert "link_dir" not in names
    assert manifest["file_count"] == 2

    ss.rollback(ws, manifest["snapshot_id"])
    assert not (ws / "link_secret").exists()
    assert b"HOST-SECRET" not in b"".join(
        p.read_bytes() for p in ws.rglob("*") if p.is_file()
    ), "host file content came back into the workspace through the snapshot"
