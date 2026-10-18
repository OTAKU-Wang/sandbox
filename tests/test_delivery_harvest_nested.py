"""SS-19 D-e2e-11：成果包采集必须递归 files/（排除 input/ tmp/）。

开发环里笔记本的当前目录就是 ``files/``，面板建议产物放 ``output/``。旧采集只看
``files/`` 顶层，于是写在 ``files/output/`` 的产物进不了包，闭环走到「生成最终
交付物」那一步只会得到 ``NO_OUTPUT_FILES`` —— 用户看到的是「沙箱里没有产出」，
而产出其实就在盘上。这两条把采集范围钉住，不依赖时序。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

try:
    from app.services import session_files as sf
except ModuleNotFoundError as _exc:  # POSIX-only 依赖（resource）在 Windows 上不存在
    pytest.skip(f"session_files 需 POSIX 依赖: {_exc}", allow_module_level=True)


def _mk_workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "files" / "output" / "nested").mkdir(parents=True)
    (ws / "files" / "input").mkdir(parents=True)
    (ws / "files" / "tmp").mkdir(parents=True)
    (ws / "files" / "note.txt").write_text("flat\n", encoding="utf-8")
    (ws / "files" / "output" / "汇总.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (ws / "files" / "output" / "nested" / "deep.json").write_text('{"ok": true}', encoding="utf-8")
    (ws / "files" / "input" / "secret.csv").write_text("raw\n", encoding="utf-8")
    (ws / "files" / "tmp" / "scratch.bin").write_bytes(b"\x00\x01")
    (ws / "files" / ".hidden").write_text("x", encoding="utf-8")
    return ws


class TestListOutputFiles:
    def test_nested_outputs_included_input_tmp_hidden_excluded(self, tmp_path):
        names = [e["name"] for e in sf.list_output_files(_mk_workspace(tmp_path))]
        assert names == ["note.txt", "output/nested/deep.json", "output/汇总.csv"]
        assert ".hidden" not in names and sf.INDEX_FILENAME not in names

    def test_entries_carry_both_name_and_filename_keys(self, tmp_path):
        entries = sf.list_output_files(_mk_workspace(tmp_path))
        assert {e["name"] for e in entries} == {e["filename"] for e in entries}

    def test_missing_files_dir_is_empty_not_error(self, tmp_path):
        assert sf.list_output_files(tmp_path / "nope") == []

    def test_hidden_directory_at_depth_is_not_walked(self, tmp_path):
        deep = tmp_path / "ws" / "files" / "output" / ".cache"
        deep.mkdir(parents=True)
        (tmp_path / "ws" / "files" / "output").mkdir(parents=True, exist_ok=True)
        (deep / "blob.bin").write_bytes(b"x")
        (tmp_path / "ws" / "files" / "output" / "ok.txt").write_text("ok", encoding="utf-8")
        names = [e["name"] for e in sf.list_output_files(tmp_path / "ws")]
        assert names == ["output/ok.txt"]

    def test_harvest_does_not_create_the_workspace_it_reads(self, tmp_path):
        absent = tmp_path / "never-created"
        sf.list_output_files(absent)
        assert not (absent / "files").exists()

    def test_symlink_outside_files_dir_is_not_collected(self, tmp_path):
        secret = tmp_path / "outside-secret.csv"
        secret.write_text("leak\n", encoding="utf-8")
        ws = _mk_workspace(tmp_path)
        try:
            (ws / "files" / "output" / "link.csv").symlink_to(secret)
            (ws / "files" / "output" / "loop").symlink_to(ws / "files")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"host does not allow creating symlinks: {exc}")
        names = [e["name"] for e in sf.list_output_files(ws)]
        assert "output/link.csv" not in names and "output/loop" not in " ".join(names)
        assert "output/汇总.csv" in names
        with pytest.raises(sf.SessionFileError):
            sf.read_output_file(ws, "output/link.csv")


class TestReadOutputFile:
    def test_roundtrip_nested_plain_file(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        data, meta = sf.read_output_file(ws, "output/汇总.csv")
        assert data == "a,b\n1,2\n".encode("utf-8")
        assert meta == {}

    def test_encrypted_flat_upload_is_decrypted_on_harvest(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        dek = bytes(range(16))
        sf.write_file(ws, "uploaded.csv", b"x,y\n3,4\n", dek=dek)
        entries = {e["name"]: e for e in sf.list_output_files(ws)}
        assert entries["uploaded.csv"]["encrypted"] is True
        data, _meta = sf.read_output_file(ws, "uploaded.csv", dek=dek)
        assert data == b"x,y\n3,4\n"

    def test_encrypted_file_without_dek_is_refused(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        sf.write_file(ws, "uploaded.csv", b"x,y\n3,4\n", dek=bytes(range(16)))
        with pytest.raises(sf.SessionFileError, match="DEK"):
            sf.read_output_file(ws, "uploaded.csv")

    @pytest.mark.parametrize("bad", ["../secrets.env", "output/../../x", "/etc/passwd", ""])
    def test_traversal_and_empty_rejected(self, tmp_path, bad):
        ws = _mk_workspace(tmp_path)
        with pytest.raises(sf.SessionFileError):
            sf.read_output_file(ws, bad)

    def test_missing_file_raises_not_found(self, tmp_path):
        ws = _mk_workspace(tmp_path)
        with pytest.raises(FileNotFoundError):
            sf.read_output_file(ws, "output/ghost.csv")

    @pytest.mark.parametrize("rel", ["input/secret.csv", "tmp/scratch.bin"])
    def test_excluded_dirs_are_not_readable_as_outputs(self, tmp_path, rel):
        ws = _mk_workspace(tmp_path)
        with pytest.raises(sf.SessionFileError):
            sf.read_output_file(ws, rel)


class TestCollectSessionFiles:
    async def test_collect_includes_nested_output(self, tmp_path, monkeypatch):
        from app.api import deliveries as dv
        from app.api import sandbox_sessions as ss

        ws = _mk_workspace(tmp_path)
        files = {e["name"]: e for e in sf.list_output_files(ws)}
        assert "output/汇总.csv" in files and "input/secret.csv" not in files

        async def _fake_dek(_session):
            return bytes(range(16))

        monkeypatch.setattr(ss, "_session_workspace_or_400", lambda _session: ws)
        monkeypatch.setattr(ss, "_session_dek", _fake_dek)
        session = SimpleNamespace(id=uuid.uuid4(), sandbox_level="L3", container_id="l3-x")

        entries = await dv._collect_session_files(None, session)
        got = {e["name"]: e["data"] for e in entries}
        assert got["output/汇总.csv"] == "a,b\n1,2\n".encode("utf-8")
        assert got["note.txt"] == b"flat\n"
        assert "input/secret.csv" not in got and "tmp/scratch.bin" not in got
