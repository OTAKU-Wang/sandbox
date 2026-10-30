"""A173（OCR-10-18 队列，W2）— SDK ``_parse_error`` 的"非 2xx 必抛 CDSApiError"契约。

A76 已把**非 dict 的合法 JSON**（``[{...}]`` / ``"x"`` / ``null`` / ``42``）落进
CDSApiError；A173 补的是同一契约的剩余逃逸面：错误体提取只接了 ``ValueError``，
任何别的解析异常（如声明 ``Content-Encoding: gzip`` 的坏压缩响应抛的
``httpx.DecodingError``——不是 ValueError 的子类）都会绕过 ``_parse_error``，
把裸传输异常甩给调用方。修后所有提取路径逐层设防：json 失败 → 文本 →
原始字节 repr，出口只会是 ``CDSApiError``。
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSApiError, CDSClient  # noqa: E402
from cds_sdk.client import _parse_error  # noqa: E402

BASE = "http://test"


def _client(handler) -> CDSClient:
    return CDSClient(BASE, token="tok", transport=httpx.MockTransport(handler))


def _resp(status: int, content: bytes, **headers) -> httpx.Response:
    return httpx.Response(
        status, content=content,
        headers={"content-type": "application/json", **headers},
        request=httpx.Request("GET", f"{BASE}/x"),
    )


# ── A173 本尊：非 ValueError 的解析异常不许逃逸 ─────────────────────────────


class _JsonExplodes(httpx.Response):
    """模拟真实坏压缩响应：``.json()`` 抛 DecodingError（非 ValueError）。"""

    def json(self, **kwargs):
        raise httpx.DecodingError("Failed to decode gzip body")


class _JsonAndTextExplode(_JsonExplodes):
    """最坏面：``.text`` 也抛 —— 回退必须落到原始字节，不许裸抛。"""

    @property
    def text(self):  # noqa: D102
        raise httpx.DecodingError("Failed to decode gzip body")


def test_decoding_error_from_json_still_raises_cdsapi_error():
    """提取层抛 DecodingError（非 ValueError）时也必须落成 CDSApiError——文本回退。"""
    resp = _JsonExplodes(502, content=b"gateway said no")
    err = _parse_error(resp, "/x")
    assert isinstance(err, CDSApiError)
    assert err.status_code == 502
    assert err.code == "HTTP_502"
    assert err.detail == "gateway said no"  # .json() 炸了也要保住可读文本


def test_json_and_text_both_failing_falls_back_to_raw_bytes():
    """最坏面：``.text`` 也抛 —— 回退必须落到原始字节 repr，不许裸抛。"""
    resp = _JsonAndTextExplode(503, content=b"\x1f\x8b\x08\x00junk")
    err = _parse_error(resp, "/x")
    assert isinstance(err, CDSApiError)
    assert err.status_code == 503
    assert "junk" in err.detail  # repr(bytes) 兜底，detail 仍可观测


def test_binary_garbage_body_round_trip_is_api_error():
    """整链路（MockTransport）：502 + 非 UTF-8 二进制体也只冒 CDSApiError。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"\x00\xff\xfe binary",
                              headers={"content-type": "application/json"})

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.get_session("s-1")
    assert exc.value.status_code == 502


# ── A76 半边不回归：非 dict 的合法 JSON 保持原语义 ─────────────────────────


@pytest.mark.parametrize("body,expected_detail", [
    (b'[{"detail": "x"}]', [{"detail": "x"}]),
    (b'"error"', "error"),
    (b'42', 42),
])
def test_non_dict_json_becomes_detail(body, expected_detail):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=body,
                              headers={"content-type": "application/json"})

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.get_session("s-1")
    assert exc.value.detail == expected_detail
    # 列表里的 code 不许被误当成顶层 code（顶层不是 dict 就没有 W2 code）
    assert exc.value.code == "HTTP_502"


def test_null_json_falls_back_to_text_detail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"null",
                              headers={"content-type": "application/json"})

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.get_session("s-1")
    assert exc.value.detail == "null"


def test_w2_envelope_still_parsed():
    """钉住正向：修坏体解析不许弄坏 W2 字段提取。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={
            "code": "RATE_LIMITED", "message": "slow", "detail": {"k": 1},
            "request_id": "req-9",
        })

    with _client(handler) as c, pytest.raises(CDSApiError) as exc:
        c.get_session("s-1")
    assert exc.value.code == "RATE_LIMITED"
    assert exc.value.request_id == "req-9"
    assert exc.value.detail == {"k": 1}
