"""OCR A76 — `_parse_error` 对合法但非 dict 的 JSON（数组/字符串/null）抛 AttributeError。

调用方契约是「非 2xx 一定抛 CDSApiError」；中间的网关返 `[{...}]` / `"error"` / `null`
时，`payload.get` 抛的 AttributeError 无人接。httpx.MockTransport style, no server.
"""
import sys
from json import dumps as _dumps
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


@pytest.mark.parametrize(
    "body",
    [
        [{"error": "upstream exploded"}],
        "error",
        123,
        None,
    ],
    ids=["list", "string", "number", "null"],
)
def test_non_dict_json_still_raises_api_error(body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            headers={"content-type": "application/json"},
            content=_dumps(body).encode(),
        )

    with _client(handler) as c:
        with pytest.raises(CDSApiError) as exc:
            c.get_session("s-1")
    assert exc.value.status_code == 502
    assert exc.value.code == "HTTP_502"


def test_json_array_detail_is_carried_verbatim():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json=[{"loc": ["body", "code"], "msg": "field required"}])

    with _client(handler) as c:
        with pytest.raises(CDSApiError) as exc:
            c.get_session("s-1")
    assert exc.value.detail == [{"loc": ["body", "code"], "msg": "field required"}]


def test_non_json_body_still_falls_back_to_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>502 Bad Gateway</html>")

    with _client(handler) as c:
        with pytest.raises(CDSApiError) as exc:
            c.get_session("s-1")
    assert exc.value.detail == "<html>502 Bad Gateway</html>"
    assert exc.value.code == "HTTP_502"
