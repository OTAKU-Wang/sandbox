"""Session template registry + seeding tests (Round 40 usability).

Registry tests are pure and run everywhere; seeding imports session_files
(POSIX ``resource``) and is skipped on Windows like the other file-store tests.
"""
import sys

import pytest

_win_skip = pytest.mark.skipif(
    sys.platform == "win32",
    reason="session_files imports POSIX-only 'resource'; covered on Linux",
)


# ─── registry (pure, runs everywhere) ──────────────────────────

def test_registry_defaults():
    from app.services import session_templates as st

    names = {t["name"] for t in st.list_templates()}
    assert {"empty", "python-analysis", "duckdb-query"} <= names
    tpl = st.get_template("python-analysis")
    assert tpl is not None
    assert "analysis.py" in tpl["files"]
    assert isinstance(tpl["env"], dict)


def test_get_template_rejects_bad_names():
    from app.services import session_templates as st

    assert st.get_template("no-such-template") is None
    assert st.get_template("") is None
    assert st.get_template("../../etc/passwd") is None
    assert st.get_template("x" * 65) is None


def test_json_override_extends_registry(monkeypatch):
    from app.core.config import get_settings
    from app.services import session_templates as st

    monkeypatch.setattr(
        get_settings(),
        "SESSION_TEMPLATES_JSON",
        '{"custom": {"description": "mine", "files": {"hi.txt": "hello"}, "env": {"A": "1"}}}',
    )
    names = {t["name"] for t in st.list_templates()}
    assert "custom" in names and "python-analysis" in names  # merged, not replaced
    tpl = st.get_template("custom")
    assert tpl["files"] == {"hi.txt": "hello"}
    assert tpl["env"] == {"A": "1"}


def test_json_override_invalid_is_ignored(monkeypatch):
    from app.core.config import get_settings
    from app.services import session_templates as st

    monkeypatch.setattr(get_settings(), "SESSION_TEMPLATES_JSON", "{not json")
    names = {t["name"] for t in st.list_templates()}
    assert "python-analysis" in names  # built-ins intact


def test_register_template_programmatic():
    from app.services import session_templates as st

    st.register_template("prog-tpl", "test only", {"f.txt": "x"}, {"K": "V"})
    try:
        tpl = st.get_template("prog-tpl")
        assert tpl["files"] == {"f.txt": "x"} and tpl["env"] == {"K": "V"}
    finally:
        st.BUILTIN_TEMPLATES.pop("prog-tpl", None)


# ─── seeding (POSIX) ───────────────────────────────────────────

@_win_skip
def test_seed_workspace_writes_files(tmp_path):
    from app.services import session_files as sf
    from app.services import session_templates as st

    ws = tmp_path / "ws"
    ws.mkdir()
    tpl = st.get_template("python-analysis")
    summary = st.seed_workspace(ws, tpl)

    assert set(summary["files"]) == set(tpl["files"].keys())
    names = [e["filename"] for e in sf.list_files(ws)]
    assert "analysis.py" in names
    # Template boilerplate is intentionally plaintext (index-registered).
    assert (ws / "files" / "analysis.py").read_text(encoding="utf-8").startswith('"""Starter')
    meta = sf.read_file(ws, "analysis.py")[1]
    assert meta["encrypted"] is False


@_win_skip
def test_seed_respects_count_limit(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services import session_files as sf
    from app.services import session_templates as st

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(get_settings(), "SESSION_MAX_FILES", 1)
    tpl = {"description": "", "files": {"a.txt": "a", "b.txt": "b"}, "env": {}}
    with pytest.raises(sf.SessionFileError, match="count limit"):
        st.seed_workspace(ws, tpl)
