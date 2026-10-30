"""SDK MINOR (cr-2026-10-15 §4.7) — Retry-After 退避被 5s 硬截断，与文档相悖。

``sdk/README.md`` 承诺"尊重 ``Retry-After``"，而 ``client.py`` 在两个重试点
都 ``min(..., 5.0)``——服务端要求等 30s 也只会等 5s 就打回去。修后：按
Retry-After 真实退避，防御性上限 60s，缺头/坏头回退 1s。既有的重试语义
（仅幂等读、仅 429/503；410 SESSION_TERMINATED 终态不重试）不动，由本文件
与既有 test_ocr_a213/a88 一起钉住。httpx.MockTransport，无服务端。
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSApiError, CDSClient  # noqa: E402
import cds_sdk.client as client_mod  # noqa: E402

BASE = "http://test"


def _client(handler, **kw) -> CDSClient:
    kwargs = {"token": "tok", "transport": httpx.MockTransport(handler)}
    kwargs.update(kw)
    return CDSClient(BASE, **kwargs)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Autouse: swallow real sleeping, expose the recorded delays."""
    recorded: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: recorded.append(s))
    return recorded


def _flaky(status: int = 429, retry_after: str | None = "30"):
    """第一次 429/503（带 Retry-After），第二次 200。返回 (handler, calls)。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            headers = {"Retry-After": retry_after} if retry_after is not None else {}
            return httpx.Response(
                status, json={"code": "RATE_LIMITED", "message": "slow down"},
                headers=headers,
            )
        return httpx.Response(200, json={"ok": True})

    return handler, calls


# ── a) 尊重 Retry-After（含超过旧 5s 截断的值） ─────────────────────────────


def test_retry_after_30s_is_honoured_not_clipped_to_5(sleeps):
    """README 承诺的语义：30s 就真等 30s（旧代码 min(...,5.0) 会截成 5）。"""
    handler, calls = _flaky(429, "30")
    with _client(handler) as c:
        assert c.health()["ok"] is True
    assert calls == ["GET", "GET"]
    assert sleeps == [30.0]


def test_retry_after_capped_at_60s(sleeps):
    """防御性上限：同步客户端不会为一次重试睡过 60s。"""
    handler, _ = _flaky(503, "600")
    with _client(handler) as c:
        assert c.health()["ok"] is True
    assert sleeps == [60.0]


def test_missing_retry_after_falls_back_to_1s(sleeps):
    handler, _ = _flaky(429, retry_after=None)
    with _client(handler) as c:
        assert c.health()["ok"] is True
    assert sleeps == [1.0]


def test_unparseable_retry_after_falls_back_to_1s(sleeps):
    handler, _ = _flaky(429, "soon")
    with _client(handler) as c:
        assert c.health()["ok"] is True
    assert sleeps == [1.0]


def test_zero_retry_after_is_honoured(sleeps):
    handler, _ = _flaky(429, "0")
    with _client(handler) as c:
        assert c.health()["ok"] is True
    assert sleeps == [0.0]


def test_headers_channel_shares_the_same_backoff(sleeps):
    """``_request_with_headers``（keyset 分页通道）与 ``_request`` 同规。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(
                429, json={"code": "RATE_LIMITED", "message": "slow down"},
                headers={"Retry-After": "42"},
            )
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    with _client(handler) as c:
        body = c.list_sessions(cursor="c-1")
    assert body["items"] == []
    assert sleeps == [42.0]


# ── 既有语义不动：410 终态、只重试幂等读 ────────────────────────────────────


def test_410_session_terminated_still_not_retried(sleeps):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            410, json={"code": "SESSION_TERMINATED", "message": "gone"},
        )

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.get_session("s-dead")
    assert exc.value.code == "SESSION_TERMINATED"
    assert calls == ["GET"], "410 是 W2 终态，一次都不许重发"
    assert sleeps == []


def test_cap_constant_is_60():
    """60s 上限是文档化契约（README/ctor docstring），不是散落的魔数。"""
    assert client_mod._RETRY_AFTER_CAP_SECONDS == 60.0
