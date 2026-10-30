"""SDK MINOR (cr-2026-10-15 §4.7) — 流式 recv 超时 vs 干净收尾必须可机判。

A74 已让 recv 超时抛 ``CDSError``(504) 而不是静默收尾，但截断标记只活在
错误文案里 —— 调用方要判别"输出被截断"只能匹配字符串。本钉把标记结构
化：超时抛的错误对象带 ``truncated = True``（``CDSError`` 类级默认 False，
干净收尾根本不抛错）。另钉 ``timeout_seconds`` 的入参校验发生在**任何网络
往返之前**（MockTransport 计数为 0），非正/非数直接 ``ValueError``。
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient, CDSError  # noqa: E402
import cds_sdk.stream as sdk_stream  # noqa: E402


class WebSocketTimeoutException(Exception):
    """Mirrors websocket-client's class name (duck-typed by _is_timeout_error)."""


class ConnectionClosed(Exception):
    pass


# websocket-client's closed exception is duck-typed by class name too.
WebSocketConnectionClosedException = type(
    "WebSocketConnectionClosedException", (Exception,), {}
)


class _FakeWS:
    def __init__(self, effects):
        self.effects = list(effects)
        self.sent: list[dict] = []
        self.closed = False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if not self.effects:
            raise WebSocketConnectionClosedException("drained")
        effect = self.effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    def close(self):
        self.closed = True


def _client(monkeypatch, effects, *, requests: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path.endswith("/api/v1/auth/ws-ticket"):
            return httpx.Response(200, json={"ticket": "t-1", "expires_in": 30})
        return httpx.Response(200, json={"sandbox_level": "L0", "status": "running"})

    c = CDSClient("http://test", token="tok", transport=httpx.MockTransport(handler))
    fake = _FakeWS(effects)
    monkeypatch.setattr(sdk_stream, "_default_ws_open", lambda url, timeout: fake)
    return c, fake


def _drain(c, **kw):
    got, exc = [], None
    try:
        for frame in c.stream_exec("s-1", "make -j", language="bash", **kw):
            got.append(frame)
    except CDSError as raised:
        exc = raised
    return got, exc


# ── b) 截断可判别（结构化标记） ─────────────────────────────────────────────


def test_recv_timeout_error_carries_structured_truncated_flag(monkeypatch):
    frames = [
        json.dumps({"type": "stdout", "data": "gcc -c a.c\n"}),
        WebSocketTimeoutException("timed out"),
    ]
    c, fake = _client(monkeypatch, frames)
    with c:
        got, exc = _drain(c, recv_timeout=0.5)

    assert exc is not None and exc.status_code == 504
    # the machine-readable marker — consumers branch on this, not on prose.
    assert exc.truncated is True
    # delivered frames stop at the stall; the end was NOT clean.
    assert [f["type"] for f in got] == ["stdout"]
    assert fake.closed is True


def test_clean_end_never_flags_truncation(monkeypatch):
    """exit 终帧 = 合法终点：不抛错，且 CDSError 的默认标记必须是 False。"""
    frames = [
        json.dumps({"type": "stdout", "data": "ok\n"}),
        json.dumps({"type": "exit", "code": 0}),
    ]
    c, _ = _client(monkeypatch, frames)
    with c:
        got, exc = _drain(c)
    assert exc is None
    assert [f["type"] for f in got] == ["stdout", "exit"]
    assert CDSError(502, "x").truncated is False  # 类级默认：未被标记 = 非截断


def test_server_close_is_clean_not_truncated(monkeypatch):
    frames = [
        json.dumps({"type": "stdout", "data": "partial?\n"}),
        WebSocketConnectionClosedException("closed by server"),
    ]
    c, _ = _client(monkeypatch, frames)
    with c:
        got, exc = _drain(c)
    assert exc is None, "服务端关闭是 A74 钉住的合法终点，不得抛错"
    assert [f["type"] for f in got] == ["stdout"]


# ── c) timeout_seconds 先校验（先于一切网络往返） ──────────────────────────


@pytest.mark.parametrize("bad", ["30", 0, -1, True, False, object(), [15]])
def test_bad_timeout_seconds_raises_valueerror_before_any_request(monkeypatch, bad):
    requests: list = []
    c, _ = _client(monkeypatch, [json.dumps({"type": "exit", "code": 0})],
                   requests=requests)
    with c:
        with pytest.raises(ValueError, match="timeout_seconds"):
            list(c.stream_exec("s-1", "echo hi", language="bash", timeout_seconds=bad))
    # 校验发生在 ws-ticket / get_session 之前：一个 HTTP 往返都没烧。
    assert requests == []


def test_valid_timeout_seconds_still_reaches_the_start_frame(monkeypatch):
    requests: list = []
    c, fake = _client(monkeypatch, [json.dumps({"type": "exit", "code": 0})],
                      requests=requests)
    with c:
        got, exc = _drain(c, timeout_seconds=15)
    assert exc is None and got[-1]["type"] == "exit"
    assert fake.sent[0]["timeout"] == 15


def test_helper_rejects_nan_but_accepts_positive_floats():
    with pytest.raises(ValueError, match="timeout_seconds"):
        sdk_stream._validate_timeout_seconds(float("nan"))
    sdk_stream._validate_timeout_seconds(None)   # 省略 = 合法
    sdk_stream._validate_timeout_seconds(1.5)    # 正 float = 合法
