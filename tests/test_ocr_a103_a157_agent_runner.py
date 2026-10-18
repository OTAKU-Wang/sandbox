"""A103 / A157 — 生成的 agent runner：CI 语法门 + `http` 工具出网面收敛。

* **A103** `build_agent_runner` 用 `f"...{dict(http_allow or {"domains": ...})!r}"`
  —— 同一对双引号嵌套只在 PEP 701（py3.12+）合法，而 CI 的
  `python -m compileall -q app tests alembic` 跑在 py3.11（.github/workflows/ci.yml）
  ⇒ 门必红。不变量：整棵 app/ 树与生成的 runner 都不含「f-string 替换域里复用
  同一种引号」的写法（用 tokenize 判定，不依赖本机解释器版本）。
* **A157** runner 里 `http` 工具的白名单只管 hostname：`https://allowed-host:6379/`
  直接通过（域名白名单换成了到内网任意端口的 TCP 出口）；`method` 原样透传
  （描述写 GET/POST，实际 DELETE 照发）；`timeout` 不收敛（10**9 照用）；条目写
  成 FQDN 尾点形式永不命中且无报错；CIDR 写错被 `except ValueError: continue`
  静默丢弃。不变量：scheme/端口收敛、方法白名单、超时上限、两侧尾点归一化，
  以及**生成期**就拒畸形 allowlist。
"""
import io
import tokenize
import urllib.request
from pathlib import Path

import pytest

from app.services import agent_service
from app.services.agent_service import build_agent_runner, validate_agent_runner

REPO = Path(__file__).resolve().parents[1]


# ── A103: py3.11 lexical gate ──────────────────────────────────────────

def _same_quote_fstrings(src: str, name: str = "<memory>"):
    """Positions of f-strings whose replacement field reuses the outer quote.

    PEP 701 (py3.12) made `f"{d {"k"}}"` legal; on the py3.11 CI interpreter
    that `compileall` runs, it is a SyntaxError. Tokenizer-level so it does not
    depend on which interpreter is executing this test.
    """
    if not hasattr(tokenize, "FSTRING_START"):  # pragma: no cover - py<3.12
        pytest.skip("PEP 701 detection needs the 3.12 tokenizer")
    hits = []
    stack: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.FSTRING_START:
            stack.append(tok.string.lstrip("fFbBrRuU"))
        elif tok.type == tokenize.FSTRING_END:
            if stack:
                stack.pop()
        elif tok.type == tokenize.STRING and stack:
            quote = tok.string.lstrip("fFbBrRuU")[:1]
            if quote and quote in stack[-1]:
                hits.append((name, tok.start))
    return hits


def test_app_tree_compiles_under_py311_lexical_rules():
    offenders = []
    for path in sorted((REPO / "app").rglob("*.py")):
        offenders += _same_quote_fstrings(path.read_text(encoding="utf-8"), path.name)
    assert offenders == [], f"PEP 701 nesting (py3.11 compileall will fail): {offenders}"


def test_generated_runner_has_no_pep701_nesting():
    for http_allow in (None, {"domains": ["*.example.com"], "ips": ["10.0.0.0/8"]}):
        runner = build_agent_runner("t", tool_ids=["compute", "http"], http_allow=http_allow)
        assert _same_quote_fstrings(runner, "runner") == []


def test_generated_runner_still_round_trips_byte_exact():
    """The A103 rewrite must not perturb the trusted-template comparison."""
    for http_allow in (None, {"domains": ["*.example.com"], "ips": []}):
        runner = build_agent_runner("统计", tool_ids=["compute", "http", "llm_reason"], http_allow=http_allow)
        ok, reason = validate_agent_runner(runner, ["compute", "http", "llm_reason"])
        assert ok, reason
        assert "_AGENT_HTTP_ALLOW = " in runner


# ── A157: the http tool's egress surface ───────────────────────────────

def _ns(http_allow):
    """Exec the generated runner inertly (__name__ != __main__) and hand back
    its namespace so the in-sandbox matcher can be called for real."""
    src = build_agent_runner("拉数据", tool_ids=["compute", "http"], step_budget=6, http_allow=http_allow)
    ns = {"__name__": "cds_agent_runner_under_test"}
    exec(compile(src, "<agent-runner>", "exec"), ns)
    return ns


class TestA157Allowlist:
    def test_malformed_allowlist_is_rejected_at_generation_time(self):
        for bad in (
            {"domains": ["not a host"]},
            {"domains": [""]},
            {"domains": ["*."]},
            {"domains": ["api.example.com:443"]},
            {"domains": ["http://api.example.com"]},
            {"ips": ["not/a/cidr"]},
            {"ips": ["10.0.0.0/33"]},
            {"ips": ["10.0.0.1/255.255.0.0.1"]},
            {"domains": ["api.example.com"], "cidrs": ["10.0.0.0/8"]},
            {"domains": "api.example.com"},
            {"domains": [None]},
        ):
            with pytest.raises(ValueError, match="http_allow"):
                build_agent_runner("t", tool_ids=["compute", "http"], http_allow=bad)

    def test_normal_allowlists_are_accepted(self):
        for ok in (
            None,
            {},
            {"domains": ["*.example.com"]},
            {"domains": ["api.example.com."]},
            {"domains": [], "ips": ["10.0.0.0/8", "192.168.1.7"]},
        ):
            build_agent_runner("t", tool_ids=["compute", "http"], http_allow=ok)

    def test_port_and_scheme_are_converged(self):
        ns = _ns({"domains": ["api.example.com"], "ips": ["10.0.0.0/8"]})
        net = ns["_net_allowed"]
        assert net("https://api.example.com/v1/data") is True
        assert net("http://api.example.com/v1/data") is True
        assert net("https://api.example.com/") is True
        # the ledger's hole: a whitelisted host on an arbitrary port
        assert net("https://api.example.com:6379/") is False
        assert net("http://api.example.com:8200/") is False
        assert net("https://api.example.com:8443/") is False
        assert net("ftp://api.example.com/x") is False
        assert net("file:///etc/passwd") is False
        assert net("https://api.example.com.evil.test/") is False

    def test_ip_allowlist_still_works_and_denies_by_default(self):
        ns = _ns({"domains": [], "ips": ["10.0.0.0/8"]})
        net = ns["_net_allowed"]
        assert net("https://10.1.2.3/api") is True
        assert net("https://10.1.2.3:6379/api") is False
        assert net("https://11.1.2.3/api") is False
        assert net("https://example.com/") is False

    def test_trailing_dot_normalises_on_both_sides(self):
        assert _ns({"domains": ["api.example.com."]})["_net_allowed"]("https://api.example.com/x") is True
        assert _ns({"domains": ["api.example.com"]})["_net_allowed"]("https://api.example.com./x") is True

    def test_wildcard_pattern_still_matches_subdomains(self):
        ns = _ns({"domains": ["*.example.com"]})
        assert ns["_net_allowed"]("https://a.example.com/x") is True
        assert ns["_net_allowed"]("https://example.com/x") is False


class TestA157ToolHttp:
    def test_method_whitelist_refuses_before_any_socket(self, monkeypatch):
        dialed = []
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **k: dialed.append(a) or (_ for _ in ()).throw(AssertionError("dialed out")),
        )
        ns = _ns({"domains": ["api.example.com"]})
        for method in ("DELETE", "PUT", "PATCH", "HEAD", "OPTIONS", "POST "):
            with pytest.raises(ValueError, match="http tool"):
                ns["_tool_http"]({"url": "https://api.example.com/x", "method": method})
        assert dialed == []

    def test_timeout_is_clamped_and_method_forwarded(self, monkeypatch):
        captured = {}

        class _Resp:
            status = 200

            def read(self):
                return b'{"ok": true}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            captured["timeout"] = timeout
            captured["method"] = req.get_method()
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        ns = _ns({"domains": ["api.example.com"]})

        ns["_tool_http"]({"url": "https://api.example.com/x", "method": "POST",
                          "body": "{}", "timeout": 10 ** 9})
        assert captured["timeout"] == ns["_AGENT_HTTP_MAX_TIMEOUT"]
        assert captured["method"] == "POST"

        ns["_tool_http"]({"url": "https://api.example.com/x", "timeout": -5})
        assert captured["timeout"] >= 1

        ns["_tool_http"]({"url": "https://api.example.com/x", "timeout": "abc"})
        assert captured["timeout"] == 10

    def test_denied_url_never_reaches_urlopen(self, monkeypatch):
        dialed = []
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda *a, **k: dialed.append(a),
        )
        ns = _ns({"domains": ["api.example.com"]})
        with pytest.raises(ValueError):
            ns["_tool_http"]({"url": "https://api.example.com:6379/"})
        assert dialed == []
