"""ocr A119 — rollback must never fall back to a filter-less tar extraction.

HEAD wrapped ``extractall(..., filter="data")`` in ``except TypeError:`` and
re-ran the extraction WITHOUT the filter, i.e. on the error path it switched off
the ``../`` / absolute-path / symlink protection while the tree was already
half-unpacked. The guard now comes from a startup probe plus explicit member
validation, and a failed guarded extraction fails the rollback instead.
"""
import hashlib
import io
import json
import tarfile

import pytest

from app.services import session_snapshots as ss


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "bwrap-filt1234"
    (w / "files").mkdir(parents=True)
    (w / "files" / "a.txt").write_bytes(b"AAAA")
    return w


def _add_escape(tar):
    payload = b"ESCAPED-HOST-WRITE"
    for name in ("good.txt", "../escaped.txt"):
        info = tarfile.TarInfo(name)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))


def _add_escape_and_link(tar):
    _add_escape(tar)
    info = tarfile.TarInfo("evil_link")
    info.type = tarfile.SYMTYPE
    info.linkname = "../../outside-secret.txt"
    tar.addfile(info)


def _plant(ws, snapshot_id, add):
    """Write an archive + manifest straight into the snapshot store (checksum valid)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        add(tar)
    blob = buf.getvalue()
    sdir = ss.snapshots_dir(ws)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{snapshot_id}.tar.gz").write_bytes(blob)
    (sdir / f"{snapshot_id}.json").write_text(
        json.dumps({"snapshot_id": snapshot_id, "session_id": "s", "bytes": len(blob),
                    "file_count": 1, "created_at": 0.0,
                    "sha256": hashlib.sha256(blob).hexdigest()}),
        encoding="utf-8",
    )
    return snapshot_id


def _attempt_rollback(ws, snapshot_id):
    err = None
    try:
        ss.rollback(ws, snapshot_id)
    except BaseException as exc:  # noqa: BLE001 - the failure IS the subject
        err = exc
    return err


@pytest.fixture
def py312_default_filter(monkeypatch):
    """Pin the production interpreter's behaviour (py3.12): there, an
    ``extractall`` call without ``filter`` defaults to ``fully_trusted`` and
    really does write ``../`` members out — unlike this 3.14 dev box, where the
    stdlib default already guards. Without this the test would pass on the
    stdlib instead of on the module under test."""
    real_extractall = tarfile.TarFile.extractall

    def emulated(self, path=".", members=None, *, numeric_owner=False, filter=None, **kw):  # noqa: ANN001
        return real_extractall(
            self, path, members, numeric_owner=numeric_owner,
            filter="fully_trusted" if filter is None else filter,
        )

    monkeypatch.setattr(tarfile.TarFile, "extractall", emulated)


def test_data_filter_probe_available_on_this_interpreter():
    assert ss._data_filter_supported() is True


def test_typeerror_fallback_never_extracts_unfiltered(ws, monkeypatch, py312_default_filter):
    """Interpreter without the ``filter`` kwarg: HEAD caught the TypeError and
    unpacked the archive unguarded, writing the ``../`` member onto the host."""
    snapshot_id = _plant(ws, "snap-evil1", _add_escape)
    (ws / "files" / "a.txt").write_bytes(b"CURRENT")
    real_extractall = tarfile.TarFile.extractall

    def old_python(self, path=".", members=None, *, numeric_owner=False, **kw):  # noqa: ANN001
        if "filter" in kw:
            raise TypeError("extractall() got an unexpected keyword argument 'filter'")
        return real_extractall(self, path, members, numeric_owner=numeric_owner, filter="fully_trusted")

    monkeypatch.setattr(tarfile.TarFile, "extractall", old_python)
    err = _attempt_rollback(ws, snapshot_id)

    assert err is not None, "a snapshot that cannot be extracted safely must fail loudly"
    assert isinstance(err, ss.SnapshotError), f"expected SnapshotError, got {type(err).__name__}: {err}"
    assert not (ws.parent / "escaped.txt").exists(), "unfiltered extraction wrote outside the workspace"
    assert (ws / "files" / "a.txt").read_bytes() == b"CURRENT"


def test_manual_validation_rejects_escape_before_any_write(ws, monkeypatch, py312_default_filter):
    """No data filter available: members are validated up-front, so nothing at
    all is written and the workspace keeps its content."""
    snapshot_id = _plant(ws, "snap-evil2", _add_escape_and_link)
    (ws / "files" / "a.txt").write_bytes(b"CURRENT")
    monkeypatch.setattr(ss, "_data_filter_supported", lambda: False, raising=False)
    err = _attempt_rollback(ws, snapshot_id)

    assert isinstance(err, ss.SnapshotError), f"expected SnapshotError, got {type(err).__name__}: {err}"
    assert not (ws.parent / "escaped.txt").exists()
    assert not (ws / "evil_link").is_symlink()
    assert (ws / "files" / "a.txt").read_bytes() == b"CURRENT"


def test_no_filter_interpreter_still_restores(ws, monkeypatch, py312_default_filter):
    """Over-correction guard: a legitimate archive still rolls back on an
    interpreter that has no data filter."""
    manifest = ss.create_snapshot(ws, session_id="s")
    (ws / "files" / "a.txt").write_bytes(b"CURRENT")
    (ws / "files" / "extra.txt").write_bytes(b"EXTRA")
    monkeypatch.setattr(ss, "_data_filter_supported", lambda: False, raising=False)

    ss.rollback(ws, manifest["snapshot_id"])

    assert (ws / "files" / "a.txt").read_bytes() == b"AAAA"
    assert not (ws / "files" / "extra.txt").exists()
