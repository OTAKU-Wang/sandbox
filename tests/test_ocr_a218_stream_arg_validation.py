"""A218（OCR-10-18 队列）— ``stream_exec`` 其余入参的发火前校验。

第一波已钉了 ``timeout_seconds``（``test_sdk_stream_truncation.py``），本文件补
其余三个：``session_id`` / ``language`` / ``recv_timeout``。旧实现完全不校验，
坏值要烧完 ws-ticket 往返 + websocket 握手才以裸 ``ValueError``/``TypeError``
（或 session_id 里带保留字符时的路由 404）砸回调用方。修后契约与
``timeout_seconds`` 一致：**参数形状错 = 调用方 bug = 立刻 ValueError，一个
网络往返都不烧**；而空 ``code`` 是数据错误，保持既有 ``CDSError(400)`` 语义
（本文件同时钉住这条不许被"顺手加强"）。
"""
import math
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSError, CDSClient  # noqa: E402
from cds_sdk import stream as sdk_stream  # noqa: E402

BASE = "http://test"


class _CountingClient:
    """stream_exec 只用到 request_ws_ticket 与 _client.base_url（拼 WS URL 用）。"""

    _client = httpx.Client(base_url=BASE)

    def __init__(self):
        self.calls: list[str] = []

    def request_ws_ticket(self):
        self.calls.append("ws-ticket")
        return {"ticket": "t"}


def _collect(client, session_id="sess-1", code="print(1)", **kw):
    return list(sdk_stream.stream_exec(client, session_id, code, **kw))


# ── session_id ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "   ", 123, None, ["s"], 3.5])
def test_bad_session_id_raises_value_error_before_any_io(bad):
    client = _CountingClient()
    with pytest.raises(ValueError, match="session_id"):
        _collect(client, session_id=bad)
    assert client.calls == [], "坏参数不许烧 ws-ticket"


# ── recv_timeout ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [0, -1, "30", True, False, math.nan, [15], None])
def test_bad_recv_timeout_raises_value_error_before_any_io(bad):
    client = _CountingClient()
    with pytest.raises(ValueError, match="recv_timeout"):
        _collect(client, recv_timeout=bad)
    assert client.calls == []


# ── language ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "  ", None, 7, ["python"]])
def test_bad_language_raises_value_error_before_any_io(bad):
    client = _CountingClient()
    with pytest.raises(ValueError, match="language"):
        _collect(client, language=bad)
    assert client.calls == []


# ── 正面：合法值照常走完整流程；空 code 语义不动 ────────────────────────────


def test_valid_args_reach_the_stream():
    class _WS:
        def __init__(self):
            self.frames = ['{"type":"stdout","data":"hi\\n"}',
                           '{"type":"exit","code":0}']

        def send(self, _): pass

        def recv(self):
            return self.frames.pop(0)

        def close(self): pass

    frames = _collect(_CountingClient(), recv_timeout=0.5, timeout_seconds=1.5,
                      _ws_open=lambda url, t: _WS())
    assert [f["type"] for f in frames] == ["stdout", "exit"]


def test_empty_code_keeps_cdserror_400_contract():
    """code 是数据不是参数形状：空 code 仍走 CDSError(400)，不许改成 ValueError。"""
    client = _CountingClient()
    with pytest.raises(CDSError) as exc:
        _collect(client, code="   ")
    assert exc.value.status_code == 400


# ── CDSClient.stream_exec：校验必须先于 get_session（零 HTTP） ───────────────


def test_client_facade_validates_before_first_http_call():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json={"sandbox_level": "l0-x",
                                         "status": "running"})

    with CDSClient(BASE, token="tok",
                   transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(ValueError, match="session_id"):
            c.stream_exec("", "print(1)")
        with pytest.raises(ValueError, match="recv_timeout"):
            c.stream_exec("sess-1", "print(1)", recv_timeout=-3)
    assert requests == [], "坏 stream 参数一个 HTTP 请求都不许发"
