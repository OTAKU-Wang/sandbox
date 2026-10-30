"""A136: 会话模板 JSON 一处形状不对就把会话创建打成 500（high）。

`app/services/session_templates.py::_merged_registry()` 只捕 `(ValueError, TypeError)`，
但它自己写的是 `(tpl.get("files") or {}).items()` —— 运维把 `CDS_SESSION_TEMPLATES_JSON`
写成 `{"bad": {"files": ["a.py"]}}`（files 给了数组）时抛的是 `AttributeError`，直接穿透
`list_templates()` / `get_template()`；这两个函数在 `POST /sandbox-sessions`（
`session_lifecycle` 取模板 → `seed_workspace`）的调用链上，于是一个配置手误让所有用户的
会话创建 500。模板是可用性便利功能，任何一条坏模板只能让它自己不下发。
"""
import pytest

from app.core.config import get_settings
from app.services import session_templates as st


def _set_payload(monkeypatch, payload: str) -> None:
    monkeypatch.setattr(get_settings(), "SESSION_TEMPLATES_JSON", payload, raising=False)


@pytest.fixture(autouse=True)
def _restore_builtin():
    builtins = {k: dict(v) for k, v in st.BUILTIN_TEMPLATES.items()}
    yield
    st.BUILTIN_TEMPLATES.clear()
    st.BUILTIN_TEMPLATES.update(builtins)


def test_files_as_list_does_not_break_the_registry(monkeypatch):
    _set_payload(monkeypatch, '{"bad": {"description": "d", "files": ["a.py"], "env": {"A": "1"}}}')

    names = [t["name"] for t in st.list_templates()]

    assert "python-analysis" in names, "内置模板被一条坏配置带走了"
    assert "bad" not in names
    assert st.get_template("bad") is None
    assert st.get_template("duckdb-query") is not None


def test_env_as_string_and_nested_file_value_are_skipped(monkeypatch):
    _set_payload(monkeypatch, (
        '{"e": {"env": "FOO=1"}, '
        '"f": {"files": {"a.py": {"nested": 1}, "b.py": "print(1)"}}, '
        '"good": {"description": "g", "files": {"c.py": "x"}, "env": {"K": "V"}}}'
    ))

    names = [t["name"] for t in st.list_templates()]

    assert "e" not in names
    assert st.get_template("f")["files"] == {"b.py": "print(1)"}, "一条里坏的条目应逐条跳过，好的保留"
    assert st.get_template("good")["files"] == {"c.py": "x"}
    assert st.get_template("good")["env"] == {"K": "V"}


def test_malformed_and_non_json_payloads_are_ignored(monkeypatch):
    for payload in ("not json at all", "[1, 2, 3]", '"a string"', '{"n": 3}', "{}"):
        _set_payload(monkeypatch, payload)
        names = [t["name"] for t in st.list_templates()]
        assert "python-analysis" in names, payload
        assert st.get_template("python-analysis") is not None, payload


def test_valid_override_still_wins(monkeypatch):
    _set_payload(monkeypatch, (
        '{"python-analysis": {"description": "mine", "files": {"m.py": "1"}, '
        '"env": {"Z": "1"}, "cube_template_id": "ct-1"}}'
    ))

    tpl = st.get_template("python-analysis")

    assert tpl["description"] == "mine"
    assert tpl["files"] == {"m.py": "1"}
    assert tpl["cube_template_id"] == "ct-1"


def test_seed_workspace_still_writes_builtins(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "files").mkdir(parents=True)
    workspace.chmod(0o777)

    summary = st.seed_workspace(str(workspace), st.get_template("empty"))

    assert summary == {"files": [], "env": {}}
