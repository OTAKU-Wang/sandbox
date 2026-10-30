"""OCR A212 — `_request_with_headers` 缺 `>= 400` 抛错：错误体被当成功数据返回。

走这条通道的调用（keyset 分页 list_sessions）在 4xx/5xx 时拿不到 CDSApiError，
只能拿到一个错误体 + 缺键的正常形状返回值。httpx.MockTransport style, no server.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSApiError, CDSClient  # noqa: E402

BASE = "http://test"


def _client(handler, **kw) -> CDSClient:
    kwargs = {"token": "tok", "transport": httpx.MockTransport(handler)}
    kwargs.update(kw)
    return CDSClient(BASE, **kwargs)


def test_keyset_list_403_raises_api_error():
    """403 必须抛 CDSApiError，而不是把错误体当分页结果返回。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"code": "FORBIDDEN", "message": "not the owner",
                  "detail": None, "request_id": "abcd1234efgh5678"},
        )

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.list_sessions(cursor="")
    assert exc.value.status_code == 403
    assert exc.value.code == "FORBIDDEN"
    assert exc.value.request_id == "abcd1234efgh5678"


def test_keyset_list_500_legacy_detail_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal boom"})

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.list_sessions(cursor="c1", limit=5)
    assert exc.value.code == "HTTP_500"
    assert exc.value.detail == "internal boom"


def test_keyset_list_success_path_unchanged():
    """成功路径仍返回 body + x-next-cursor（A212 的修复不得动分页头）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"id": "s-1"}]},
            headers={"x-next-cursor": "c2"},
        )

    with _client(handler) as c:
        body = c.list_sessions(cursor="")
    assert body["items"] == [{"id": "s-1"}]
    assert body["next_cursor"] == "c2"
