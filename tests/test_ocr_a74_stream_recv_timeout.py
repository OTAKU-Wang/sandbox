"""A74 — SDK 把 WS recv 超时当"正常结束"（high，违反 honest-degradation）。

沙箱内长时间静默（编译 / 拉数据）超过 ``recv_timeout`` 时，``stream_exec``
过去直接把 ``WebSocketTimeoutException`` / ``TimeoutError`` 当成"连接已关"
并 ``return``：调用方拿到被截断的输出却没有任何标记，看起来像命令跑完了。

本文件钉住修复后的契约：
* 超时 = 降级，必须显式披露 —— 抛 :class:`CDSError`（504）并说明输出被截断；
* 真正的"服务端已关闭连接"仍然是正常结束（不许过度修成一律报错）；
* 超时前已到达的帧必须已经交付给调用方（截断可见，而不是凭空消失）。
"""
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient, CDSError  # noqa: E402
import cds_sdk.stream as sdk_stream  # noqa: E402


class WebSocketTimeoutException(Exception):
    """与 websocket-client 同名：``stream.py`` 的旧实现是按**类名**识别的。"""


class WebSocketConnectionClosedException(Exception):
    """websocket-client 真正的"连接已关"异常。"""


class _FakeWS:
    """recv() 逐项消费 ``effects``：str → 帧，BaseException 实例 → 抛出。"""

    def __init__(self, effects):
        self.effects = list(effects)
        self.sent = []
        self.closed = False

    def settimeout(self, _t):
        pass

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        if not self.effects:
            raise WebSocketConnectionClosedException("stream end")
        effect = self.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    def close(self):
        self.closed = True


def _client(monkeypatch, effects):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"sandbox_level": "L0", "status": "running"}
            if request.url.path.endswith("/api/v1/sandbox-sessions/s-1")
            else {"ticket": "t-1", "expires_in": 30},
        )

    c = CDSClient("http://test", token="tok", transport=httpx.MockTransport(handler))
    fake = _FakeWS(effects)
    monkeypatch.setattr(sdk_stream, "_default_ws_open", lambda url, timeout: fake)
    return c, fake


def _drain(c, **kw):
    """收集超时前已交付的帧，返回 (frames, raised-CDSError)。"""
    got = []
    try:
        for frame in c.stream_exec("s-1", "make -j", language="bash", **kw):
            got.append(frame)
    except CDSError as exc:
        return got, exc
    return got, None


def test_recv_timeout_raises_instead_of_ending_the_stream_cleanly(monkeypatch):
    frames = [
        json.dumps({"type": "stdout", "data": "gcc -c a.c\n"}),
        WebSocketTimeoutException("timed out"),
    ]
    c, fake = _client(monkeypatch, frames)
    with c:
        got, exc = _drain(c, recv_timeout=0.5)

    assert exc is not None, (
        "recv 超时被当成正常结束：调用方只拿到截断输出，没有任何降级标记"
    )
    assert exc.status_code == 504
    msg = f"{exc.status_code} {exc.detail}"
    assert "truncat" in msg.lower(), "降级必须披露截断，而不是静默少给输出"
    assert "0.5" in msg, "错误里要带上生效的 recv_timeout 阈值"
    assert [f["type"] for f in got] == ["stdout"]
    assert fake.closed is True


def test_builtin_timeout_error_is_not_silent_either(monkeypatch):
    c, _ = _client(monkeypatch, [TimeoutError("recv timeout")])
    with c:
        got, exc = _drain(c, recv_timeout=1.25)
    assert exc is not None and exc.status_code == 504
    assert "truncat" in str(exc.detail).lower()
    assert got == []


@pytest.mark.skipif(
    importlib.util.find_spec("websocket") is None,
    reason="optional dependency websocket-client not installed",
)
def test_real_websocket_client_timeout_class_is_also_not_silent(monkeypatch):
    from websocket import WebSocketTimeoutException as RealTimeout

    c, _ = _client(monkeypatch, [RealTimeout("read timed out")])
    with c:
        _, exc = _drain(c, recv_timeout=2.0)
    assert exc is not None and exc.status_code == 504


def test_connection_closed_still_ends_the_iteration_cleanly(monkeypatch):
    """过度修的反向钉：真正的"服务端关掉了连接"是合法终点，不该抛错。"""
    frames = [
        json.dumps({"type": "stdout", "data": "done\n"}),
        WebSocketConnectionClosedException("Connection is already closed."),
    ]
    c, fake = _client(monkeypatch, frames)
    with c:
        got, exc = _drain(c)
    assert exc is None
    assert [f["type"] for f in got] == ["stdout"]
    assert fake.closed is True


def test_closed_classifier_does_not_swallow_timeouts():
    """A74 的机制钉：旧实现把超时类名混进了 closed 集合（按类名匹配）。"""
    assert sdk_stream._is_closed_error(WebSocketConnectionClosedException()) is True
    assert sdk_stream._is_closed_error(ConnectionError("reset")) is True
    assert sdk_stream._is_closed_error(WebSocketTimeoutException("x")) is False
    assert sdk_stream._is_closed_error(TimeoutError("x")) is False
