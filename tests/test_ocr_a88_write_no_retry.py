"""OCR A88 — 非幂等 POST 参与 429/503 重试：写操作在网关已转发后被重复执行。

重试集合必须是只读方法（GET/HEAD/OPTIONS）；POST/PUT/DELETE 把可重试错误原样抛给
调用方，由调用方决定是否重试。httpx.MockTransport style, no server needed.
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
def _no_real_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


WRITE_CALLS = {
    "POST create_session": lambda c: c.create_session("product-1"),
    "POST terminate_session": lambda c: c.terminate_session("s-1"),
    "POST create_snapshot": lambda c: c.create_snapshot("s-1", async_mode=True),
    "POST rollback_snapshot": lambda c: c.rollback_snapshot("s-1", "snap-1"),
    "PUT update_session_network_policy": lambda c: c.update_session_network_policy(
        "s-1", {"mode": "allowlist"}
    ),
    "DELETE delete_file": lambda c: c.delete_file("s-1", "out.csv"),
    "DELETE delete_snapshot": lambda c: c.delete_snapshot("s-1", "snap-1"),
}


@pytest.mark.parametrize("status_code,code", [(429, "RATE_LIMITED"), (503, "OPERATION_LOCKED")])
@pytest.mark.parametrize("label", sorted(WRITE_CALLS))
def test_write_methods_are_never_auto_retried(status_code, code, label, _no_real_sleep):
    """每个写方法在 429/503 下都只允许出网一次（双开/重复快照的物理防线）。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            status_code,
            json={"code": code, "message": "transient"},
            headers={"Retry-After": "0"},
        )

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        WRITE_CALLS[label](c)
    assert len(calls) == 1, f"{label} was resent {len(calls)} times: {calls}"
    assert exc.value.code == code
    assert _no_real_sleep == [], "a non-retryable write must not sleep on Retry-After"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_readonly_methods_stay_retried(method, _no_real_sleep):
    """只读集合逐个锁住：幂等重放没有副作用，重试收益保留。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(
                503, json={"code": "OPERATION_LOCKED"}, headers={"Retry-After": "0"}
            )
        return httpx.Response(200, json={"ok": True})

    with _client(handler) as c:
        c._request(method, "/api/v1/sandbox-sessions/s-1")
    assert calls == [method, method]
    assert _no_real_sleep == [0.0]
