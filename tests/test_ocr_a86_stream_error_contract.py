"""A86 — WS 会话的两处错误契约缺口（medium）。

(a) ``_default_ws_open`` 的 ``ws.connect(url)`` 不带 timeout，连完才
``settimeout`` ⇒ TCP / 握手阶段可无限阻塞（``recv_timeout`` 只覆盖 recv）。
(b) ``ws.send`` 只有外层 ``try/finally``，websocket-client 的原始异常直接
穿透到调用方，破坏 SDK「只抛 CDSError」的契约（A76 同族）。
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient, CDSError  # noqa: E402
import cds_sdk.stream as sdk_stream  # noqa: E402


class _Recorder:
    def __init__(self):
        self.connect_url = None
        self.connect_options = None
        self.settimeout_value = "unset"

    def connect(self, url, **options):
        self.connect_url = url
        self.connect_options = options

    def settimeout(self, value):
        self.settimeout_value = value


class _RecorderModule:
    def __init__(self):
        self.ws = _Recorder()

    def WebSocket(self):  # noqa: N802 - mirrors websocket-client's name
        return self.ws


def test_default_ws_open_bounds_the_connect_and_handshake_phase(monkeypatch):
    """websocket-client 1.9.2: ``connect(timeout=N)`` 写进 ``sock_opt.timeout``，
    即 TCP 连接与握手阶段的截止；只调 ``settimeout`` 覆盖不到这一段。"""
    mod = _RecorderModule()
    monkeypatch.setattr(sdk_stream, "_import_websocket", lambda: mod)

    ws = sdk_stream._default_ws_open("ws://host:8000/api/v1/x", 7.5)

    assert mod.ws.connect_url == "ws://host:8000/api/v1/x"
    assert mod.ws.connect_options is not None
    assert mod.ws.connect_options.get("timeout") == 7.5, (
        "connect 未带 timeout ⇒ 握手阶段可无限阻塞，recv_timeout 管不到它"
    )
    assert mod.ws.settimeout_value == 7.5
    assert ws is mod.ws


class _SendBoomWS:
    def __init__(self, exc):
        self.exc = exc
        self.closed = False

    def settimeout(self, _t):
        pass

    def send(self, _data):
        raise self.exc

    def recv(self):
        raise AssertionError("send 失败后不应继续 recv")

    def close(self):
        self.closed = True


def _client(monkeypatch, ws):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"sandbox_level": "L0", "status": "running"}
            if request.url.path.endswith("/api/v1/sandbox-sessions/s-1")
            else {"ticket": "t-1", "expires_in": 30},
        )

    c = CDSClient("http://test", token="tok", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(sdk_stream, "_default_ws_open", lambda url, timeout: ws)
    return c


@pytest.mark.parametrize("raw_exc", [
    RuntimeError("Broken pipe"),
    OSError(32, "Broken pipe"),
    ValueError("frame too long"),
])
def test_send_failure_surfaces_as_cdserror_not_a_raw_third_party_error(monkeypatch, raw_exc):
    ws = _SendBoomWS(raw_exc)
    c = _client(monkeypatch, ws)
    with c:
        with pytest.raises(CDSError) as exc:
            list(c.stream_exec("s-1", "echo hi", language="bash"))
    assert exc.value.status_code == 502
    assert "Broken pipe" in str(exc.value.detail) or "frame too long" in str(exc.value.detail)
    assert ws.closed is True, "失败路径仍要关掉 socket"


def test_send_failure_is_a_cdserror_even_for_unexpected_types(monkeypatch):
    """反向钉：不是"什么都不抛"，而是"只抛 CDSError"——原始异常保留在 detail 链上。"""
    ws = _SendBoomWS(KeyError("handshake"))
    c = _client(monkeypatch, ws)
    with c:
        with pytest.raises(CDSError) as exc:
            list(c.stream_exec("s-1", "echo hi", language="bash"))
    assert isinstance(exc.value.__cause__, KeyError)


def test_recv_failure_that_is_not_close_or_timeout_stays_a_cdserror(monkeypatch):
    class _Boom:
        def settimeout(self, _t):
            pass

        def send(self, _data):
            return None

        def recv(self):
            raise RuntimeError("protocol boom")

        def close(self):
            return None

    c = _client(monkeypatch, _Boom())
    with c:
        with pytest.raises(CDSError) as exc:
            list(c.stream_exec("s-1", "echo hi", language="bash"))
    assert exc.value.status_code == 502
    assert "protocol boom" in str(exc.value.detail)


def test_start_frame_is_still_sent_verbatim(monkeypatch):
    """成功路径回归钉：修复不得改变发给服务端的 start 帧。"""
    sent = []

    class _Ok:
        def settimeout(self, _t):
            pass

        def send(self, data):
            sent.append(json.loads(data))
            return None

        def recv(self):
            return json.dumps({"type": "exit", "code": 0})

        def close(self):
            return None

    c = _client(monkeypatch, _Ok())
    with c:
        got = list(c.stream_exec("s-1", "echo hi", language="bash", timeout_seconds=9))
    assert sent == [{"type": "start", "command": "echo hi", "timeout": 9}]
    assert [f["type"] for f in got] == ["exit"]
