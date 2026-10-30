"""OCR A213 — SDK 重试不区分幂等性：429/503 自动重发也打在 POST 上。

网关可能在**后端已落库之后**才回 429/503，此时重发 POST 就是重复创建资源
（双开会话 / 重复快照 / 重复 API key）。重试集合必须只对只读方法生效。
httpx.MockTransport style, no server needed.
"""
import sys
from pathlib import Path

import httpx
import pytest

# Make the sdk package importable without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSApiError, CDSClient  # noqa: E402
import cds_sdk.client as client_mod  # noqa: E402

BASE = "http://test"


def _client(handler, **kw) -> CDSClient:
    kwargs = {"token": "tok", "transport": httpx.MockTransport(handler)}
    kwargs.update(kw)
    return CDSClient(BASE, **kwargs)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _transient(status: int, code: str):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            status,
            json={"code": code, "message": "transient"},
            headers={"Retry-After": "0"},
        )

    return handler, calls


def test_post_429_is_never_resent():
    """POST /sandbox-sessions 收到 429 ⇒ 只发一次，把可重试错误交给调用方。"""
    handler, calls = _transient(429, "RATE_LIMITED")
    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.create_session("product-1")
    assert calls == ["POST"], f"POST was resent: {calls}"
    assert exc.value.code == "RATE_LIMITED"


def test_post_503_is_never_resent():
    handler, calls = _transient(503, "SERVICE_UNAVAILABLE")
    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.create_session("product-1")
    assert calls == ["POST"], f"POST was resent: {calls}"
    assert exc.value.code == "SERVICE_UNAVAILABLE"


def test_get_429_is_still_retried():
    """只读方法的重试行为不变（既有契约：429/503 + Retry-After）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) < 3:
            return httpx.Response(
                429, json={"code": "RATE_LIMITED"}, headers={"Retry-After": "0"}
            )
        return httpx.Response(200, json={"ok": True})

    with _client(handler) as c:
        r = c.get_session("s-1")
    assert calls == ["GET", "GET", "GET"]
    assert r["ok"] is True


def test_retry_error_still_carries_retry_after_for_the_caller():
    """不自动重发不等于丢信息：调用方要能拿到 Retry-After 自己决定何时重试。"""

    def handler503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"code": "OPERATION_LOCKED", "message": "rollback in progress"},
            headers={"Retry-After": "7"},
        )

    with _client(handler503) as c, pytest.raises(CDSApiError) as exc:
        c.create_session("product-1")
    assert exc.value.retry_after == 7
