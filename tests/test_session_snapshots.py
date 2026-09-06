"""Session snapshot/rollback service tests (Round 39 usability).

Pure service-level tests against a tmp workspace — portable (no bwrap, runs
on Windows and Linux alike).
"""
import json

import pytest

from app.services import session_snapshots as ss


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "bwrap-abcd1234"
    (w / "files").mkdir(parents=True)
    (w / "input").mkdir()
    (w / "files" / "a.txt").write_bytes(b"AAAA")
    (w / "input" / "data.enc").write_bytes(b"\x00\x01\x02ENCRYPTED")
    (w / ".security_config.json").write_text(json.dumps({"workspace_key_id": "k1"}))
    return w


def test_create_and_list_snapshot(ws):
    m = ss.create_snapshot(ws, session_id="sess-1")
    assert m["session_id"] == "sess-1"
    assert m["file_count"] == 3
    assert m["bytes"] > 0
    assert len(m["sha256"]) == 64

    listed = ss.list_snapshots(ws)
    assert [x["snapshot_id"] for x in listed] == [m["snapshot_id"]]

    # Snapshots live OUTSIDE the sandbox bind.
    sdir = ss.snapshots_dir(ws)
    assert not str(sdir).startswith(str(ws))


def test_rollback_restores_content(ws):
    m1 = ss.create_snapshot(ws, session_id="s")

    # Mutate: delete a file, overwrite another, add a new one.
    (ws / "files" / "a.txt").write_bytes(b"MUTATED")
    (ws / "input" / "data.enc").unlink()
    (ws / "files" / "new.txt").write_bytes(b"NEW")

    result = ss.rollback(ws, m1["snapshot_id"])
    assert result["discarded_files"] == 3
    assert (ws / "files" / "a.txt").read_bytes() == b"AAAA"
    assert (ws / "input" / "data.enc").read_bytes() == b"\x00\x01\x02ENCRYPTED"
    assert not (ws / "files" / "new.txt").exists()
    assert json.loads((ws / ".security_config.json").read_text())["workspace_key_id"] == "k1"


def test_rollback_rejects_tampered_archive(ws):
    m = ss.create_snapshot(ws, session_id="s")
    sdir = ss.snapshots_dir(ws)
    tar_path = sdir / f"{m['snapshot_id']}.tar.gz"
    tar_path.write_bytes(b"tampered-garbage")  # corrupt the archive
    with pytest.raises(ss.SnapshotError, match="integrity"):
        ss.rollback(ws, m["snapshot_id"])


def test_rollback_unknown_or_invalid_id(ws):
    ss.create_snapshot(ws, session_id="s")
    with pytest.raises(ss.SnapshotError, match="not found"):
        ss.rollback(ws, "snap-doesnotexist")
    with pytest.raises(ss.SnapshotError, match="invalid snapshot id"):
        ss.rollback(ws, "../../escape")
    with pytest.raises(ss.SnapshotError, match="invalid snapshot id"):
        ss.rollback(ws, "")


def test_delete_snapshot(ws):
    m = ss.create_snapshot(ws, session_id="s")
    assert ss.delete_snapshot(ws, m["snapshot_id"]) is True
    assert ss.list_snapshots(ws) == []
    assert ss.delete_snapshot(ws, m["snapshot_id"]) is False


def test_retention_evicts_oldest(ws, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "SESSION_MAX_SNAPSHOTS", 2)
    ids = []
    for i in range(3):
        (ws / "files" / f"v{i}.txt").write_bytes(f"v{i}".encode())
        m = ss.create_snapshot(ws, session_id="s")
        ids.append(m["snapshot_id"])
    listed = ss.list_snapshots(ws)
    remaining = [x["snapshot_id"] for x in listed]
    assert len(remaining) == 2
    assert ids[0] not in remaining  # oldest evicted
    assert ids[-1] in remaining
