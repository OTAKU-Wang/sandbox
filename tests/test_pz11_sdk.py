"""PZ-11 (SS-11 SDK 二期) tests — disclosure parsing, buyer-journey domain
methods, app-credential auth, ws-ticket streaming flow (mocked websocket).
httpx.MockTransport style, no server needed.
"""
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

# Make the sdk package importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient, CDSResponse, CDSError  # noqa: E402
import cds_sdk.stream as sdk_stream  # noqa: E402


def _client(handler, **kw) -> CDSClient:
    kwargs = {"token": "tok", "transport": httpx.MockTransport(handler)}
    kwargs.update(kw)
    return CDSClient("http://test", **kwargs)


# ── PZ-11④: disclosure parsing ─────────────────────────────────────────
def test_disclosure_extracted_from_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "s-1",
            "output": "ok",
            "is_simulation": True,
            "tee_mode": "software_confidential",
            "backend": "bwrap",
            "engine": "t5",
            "trust_root": "none",
            "signing_backend": "software",
            "aead_algorithm": "aes-256-gcm",
            "dp_applied": True,
        })

    with _client(handler) as c:
        r = c.get_session("s-1")
    assert r.disclosure == {
        "is_simulation": True,
        "tee_mode": "software_confidential",
        "backend": "bwrap",
        "engine": "t5",
        "trust_root": "none",
        "signing_backend": "software",
        "aead_algorithm": "aes-256-gcm",
        "dp_applied": True,
    }


def test_disclosure_empty_when_absent():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "s-1", "status": "running"})

    with _client(handler) as c:
        r = c.get_session("s-1")
    assert isinstance(r, CDSResponse)
    assert r.disclosure == {}


def test_disclosure_nested_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "s-1",
            "disclosure": {"is_simulation": True, "tee_mode": "tee_simulator"},
        })

    with _client(handler) as c:
        r = c.get_session("s-1")
    assert r.disclosure["is_simulation"] is True
    assert r.disclosure["tee_mode"] == "tee_simulator"


def test_disclosure_additive_plain_dict_behavior():
    """Existing dict users are unaffected: CDSResponse *is* a dict."""
    payload = {"id": "s-1", "is_simulation": True}
    r = CDSResponse(payload)
    assert r["id"] == "s-1"
    assert dict(r) == payload
    assert r.get("is_simulation") is True
    assert json.loads(json.dumps(r)) == payload  # json-serializable as before


# ── PZ-11①: high-frequency domain methods (buyer journey) ─────────────
def test_domain_method_data_products():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/api/v1/data-products":
            return httpx.Response(200, json={"items": [{"id": "dp-1"}], "total": 1})
        return httpx.Response(200, json={"id": "dp-1", "name": "credit-records"})

    with _client(handler) as c:
        listing = c.list_data_products(status="published", q="credit", limit=5)
        product = c.get_data_product("dp-1")
    assert seen[0][0] == "/api/v1/data-products"
    assert seen[0][1] == {"skip": "0", "limit": "5", "status": "published", "q": "credit"}
    assert listing["total"] == 1
    assert seen[1][0] == "/api/v1/data-products/dp-1"
    assert product["name"] == "credit-records"


def test_domain_methods_paths_and_bodies():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = (request.method, json.loads(request.content or b"{}"))
        if request.url.path == "/api/v1/catalog":
            return httpx.Response(200, json={"items": [], "total": 0})
        if request.url.path == "/api/v1/training/jobs":
            return httpx.Response(200, json={"items": [{"job_id": "tj-1", "status": "running"}], "total": 1})
        if request.url.path.endswith("/jobs/tj-1"):
            return httpx.Response(200, json={"job_id": "tj-1", "status": "running"})
        if request.url.path == "/api/v1/federation/trusts":
            return httpx.Response(200, json=[{"trust_id": "tr-1"}])
        if request.url.path == "/api/v1/monitoring/stats":
            return httpx.Response(200, json={"active_sessions": 3})
        if request.url.path == "/api/v1/audit/compliance-report":
            return httpx.Response(200, json={"total_events": 10})
        if request.url.path.endswith("/audit"):
            return httpx.Response(200, json={"events": [], "count": 0})
        if request.url.path.endswith("/network-policy"):
            if request.method == "GET":
                return httpx.Response(200, json={"mode": "deny_all"})
            return httpx.Response(200, json={"mode": "allowlist"})
        return httpx.Response(200, json={"cleaned": 2})

    with _client(handler) as c:
        assert c.search_catalog("credit", industry="finance")["total"] == 0
        assert c.list_training_jobs(status="running")["total"] == 1
        assert c.training_job_status("tj-1") == "running"
        assert c.list_federation_spaces()[0]["trust_id"] == "tr-1"
        assert c.get_monitoring_summary()["active_sessions"] == 3
        assert c.get_compliance_report()["total_events"] == 10
        assert c.get_session_audit("s-1", category="network")["count"] == 0
        assert c.cleanup_expired_sessions()["cleaned"] == 2
        assert c.get_session_network_policy("s-1")["mode"] == "deny_all"
        assert c.update_session_network_policy(
            "s-1", {"mode": "allowlist", "allowed_domains": ["api.example.com"]}
        )["mode"] == "allowlist"

    assert seen["/api/v1/catalog"] == ("GET", {})
    assert seen["/api/v1/sandbox-sessions/s-1/network-policy"] == (
        "PUT",
        {"mode": "allowlist", "allowed_domains": ["api.example.com"]},
    )


# ── PZ-11⑥: app_id/app_secret gateway credential auth ─────────────────
def test_app_credential_client_sends_gateway_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"rows": [[1]], "rows_returned": 1})

    c = CDSClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        app_id="app-1", app_secret="sec-1",
    )
    r = c.gateway_query("c-1", "SELECT 1", "dp-1")
    assert r["rows_returned"] == 1
    assert seen["headers"]["x-app-id"] == "app-1"
    assert seen["headers"]["x-app-secret"] == "sec-1"
    assert "authorization" not in seen["headers"]
    assert seen["body"] == {"sql": "SELECT 1", "product_id": "dp-1", "format": "json"}
    c.close()


def test_client_requires_some_auth_path():
    with pytest.raises(CDSError):
        CDSClient("http://test", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(CDSError):
        CDSClient("http://test", app_id="app-1",
                  transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(CDSError):
        CDSClient("http://test", token="tok", app_id="app-1", app_secret="s",
                  transport=httpx.MockTransport(lambda r: httpx.Response(200)))


# ── PZ-11②: ws-ticket streaming flow (mocked websocket) ───────────────
class _FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.url = None
        self.closed = False

    def settimeout(self, _t):
        pass

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        if not self.frames:
            raise _ClosedError()
        return self.frames.pop(0)

    def close(self):
        self.closed = True


class _ClosedError(ConnectionError):
    pass


def _ws_client(monkeypatch, frames, **kw):
    handler = lambda request: httpx.Response(  # noqa: E731
        200,
        json={"sandbox_level": "L0", "status": "running"}
        if request.url.path.endswith("/api/v1/sandbox-sessions/s-1")
        else {"ticket": "t-1", "expires_in": 30},
    )
    c = _client(handler, **kw)
    fake = _FakeWS(frames)
    monkeypatch.setattr(
        sdk_stream, "_default_ws_open",
        lambda url, timeout: (fake.__setattr__("url", url) or fake),
    )
    return c, fake


def test_ws_ticket_flow_yields_frames(monkeypatch):
    frames = [
        json.dumps({"type": "stdout", "data": "hi\n"}),
        json.dumps({"type": "stdout", "data": "there\n"}),
        json.dumps({"type": "exit", "code": 0}),
    ]
    c, fake = _ws_client(monkeypatch, frames)
    with c:
        got = list(c.stream_exec("s-1", "print('hi there')"))
    assert [f["type"] for f in got] == ["stdout", "stdout", "exit"]
    assert got[0]["data"] == "hi\n"
    assert got[-1]["code"] == 0
    # ticket exchange → ws URL carries the one-time ticket
    assert "ticket=t-1" in fake.url
    assert fake.url.startswith("ws://test/api/v1/sandbox-sessions/s-1/exec/stream")
    # first frame is a start frame with the python code wrapped for bash
    assert fake.sent[0]["type"] == "start"
    assert fake.sent[0]["command"].startswith("python3 -c ")
    assert "hi there" in fake.sent[0]["command"]
    assert fake.closed is True


def test_ws_ticket_flow_bash_language_passthrough(monkeypatch):
    frames = [json.dumps({"type": "exit", "code": 0})]
    c, fake = _ws_client(monkeypatch, frames)
    with c:
        list(c.stream_exec("s-1", "echo ok", language="bash", timeout_seconds=15))
    assert fake.sent[0]["command"] == "echo ok"
    assert fake.sent[0]["timeout"] == 15


def test_stream_blocked_frame_ends_iteration(monkeypatch):
    frames = [
        json.dumps({"type": "stdout", "data": "partial\n"}),
        json.dumps({"type": "blocked", "code": "PII_CRITICAL"}),
        json.dumps({"type": "stdout", "data": "never\n"}),  # after close
    ]
    c, fake = _ws_client(monkeypatch, frames)
    with c:
        got = list(c.stream_exec("s-1", "print('x')"))
    assert [f["type"] for f in got] == ["stdout", "blocked"]
    assert got[1]["code"] == "PII_CRITICAL"


def test_stream_exec_unsupported_level_raises(monkeypatch):
    c, _ = _ws_client(monkeypatch, [], )
    monkeypatch.setattr(
        c, "get_session", lambda sid: {"sandbox_level": "L1", "status": "running"}
    )
    with pytest.raises(CDSError) as exc:
        list(c.stream_exec("s-1", "print(1)"))
    assert exc.value.code == "STREAM_UNSUPPORTED"
    assert "logs()" in exc.value.message  # actionable guidance on .message


def test_ws_open_failure_raises_cdserror(monkeypatch):
    c, _ = _ws_client(monkeypatch, [])
    monkeypatch.setattr(
        sdk_stream, "_default_ws_open",
        lambda url, timeout: (_ for _ in ()).throw(OSError("refused")),
    )
    with c:
        with pytest.raises(CDSError) as exc:
            list(c.stream_exec("s-1", "print(1)"))
    assert exc.value.status_code == 502
    assert "refused" in str(exc.value.detail)


def test_websocket_dependency_guard(monkeypatch):
    if importlib.util.find_spec("websocket") is None:
        c, _ = _ws_client(monkeypatch, [])
        with c:
            with pytest.raises(CDSError) as exc:
                list(c.stream_exec("s-1", "print(1)"))
        assert "websocket-client" in str(exc.value)
    else:
        # websocket-client installed: the default opener resolves the module
        mod = sdk_stream._import_websocket()
        assert hasattr(mod, "WebSocket")
