"""A215（OCR-10-18 队列）— SDK 标识符百分号编码，拼路径不再能被路由劫持。

原实现把 ``session_id`` / ``filename`` / ``contract_id`` 等直接 f-string 拼进
URL 模板，httpx 只编码 ``params=``/headers，**不碰**已拼进 path 的文本。于是
一个含 ``/`` 或 ``..`` 的 id（``"../../admin"``、``"x/execute"``）能把请求悄悄
改写到另一个路由，``#``/``?`` 能截断路径或注入 query。修后所有这类点走
``_encoding.path_segment``（``quote(safe="")``）：服务端每个这类参数都是普通
（非 ``:path``）FastAPI 段，故常规 UUID/hex/slug（字母数字与 ``._-~``）逐字
不变、行为零感知，只有保留字符被挡住。本文件钉正反两面。
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient  # noqa: E402
from cds_sdk._encoding import path_segment  # noqa: E402

BASE = "http://test"


def _capturing_client(captured: list) -> CDSClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"ok": True})

    return CDSClient(BASE, token="tok", transport=httpx.MockTransport(handler))


# ── 反面：越界字符不许改写路由 ──────────────────────────────────────────────


def test_session_id_with_traversal_is_encoded_not_rerouted():
    """"../../admin" 必须整体成为一个段，不许真的把请求打到别的路径上。"""
    seen: list = []
    with _capturing_client(seen) as c:
        c.get_session("../../admin")
    url = seen[0]
    # 关键：上线字节（raw_path）里斜杠已编码 → id 只能待在自己的段里。
    assert url.raw_path == b"/api/v1/sandbox-sessions/..%2F..%2Fadmin"


def test_slash_in_id_cannot_add_a_new_route_segment():
    """含斜杠的 id 不许把单段路由撑成两段（否则匹配到别的端点）。"""
    seen: list = []
    with _capturing_client(seen) as c:
        c.get_session("abc/def/ghi")
    # /api/v1/sandbox-sessions/{id} 是两段固定前缀 + 一个 id 段；编码后仍是单段。
    assert seen[0].raw_path == b"/api/v1/sandbox-sessions/abc%2Fdef%2Fghi"


def test_query_and_fragment_metachars_are_neutralized():
    seen: list = []
    with _capturing_client(seen) as c:
        c.get_session("x?y=1#frag")
    assert seen[0].query == b""  # "?" 没被当查询分隔符
    assert seen[0].fragment == ""  # "#" 没被当片段分隔符
    assert b"%3F" in seen[0].raw_path and b"%23" in seen[0].raw_path


def test_contract_id_and_task_id_same_policy():
    seen: list = []
    with _capturing_client(seen) as c:
        c.get_contract("c/../x")
        c.get_task("s", "t/../y")
    assert seen[0].raw_path == b"/api/v1/contracts/c%2F..%2Fx"
    assert seen[1].raw_path.startswith(b"/api/v1/sandbox-tasks/t%2F..%2Fy?")
    # session_id 走 params=，httpx 自己编码，值同样越不了界。
    assert b"session_id=s" in seen[1].raw_path


def test_filename_segment_is_encoded():
    seen: list = []
    with _capturing_client(seen) as c:
        c.download_file("sess-1", "a/b.txt")
    # session 段与 filename 段各自独立编码，中间真斜杠仍在。
    assert seen[0].raw_path == b"/api/v1/sandbox-sessions/sess-1/files/a%2Fb.txt"


# ── 正面：常规 id 逐字不变（行为零感知） ────────────────────────────────────


@pytest.mark.parametrize("plain_id", [
    "sess-2026-10-30-abcd",
    "9b75d339",
    "l2c-node-01",
    "~user.cfg-x_1",
])
def test_ordinary_ids_pass_through_byte_identical(plain_id):
    assert path_segment(plain_id) == plain_id


def test_plain_session_id_unchanged_on_wire():
    seen: list = []
    with _capturing_client(seen) as c:
        c.get_session("sess-abc123")
    assert seen[0].raw_path == b"/api/v1/sandbox-sessions/sess-abc123"


# ── stream：session 段 + ticket query 值都编码（A215 同源点） ───────────────


def test_stream_path_and_ticket_are_encoded():
    from cds_sdk import stream as s

    class _TicketClient:
        _client = httpx.Client(base_url=BASE)

        def request_ws_ticket(self):
            return {"ticket": "t/ick+et=v"}

    seen_url: dict = {}

    class _FakeWS:
        def send(self, _): pass
        def recv(self):
            return '{"type":"exit","code":0}'
        def close(self): pass

    def _open(url, _timeout):
        seen_url["url"] = url
        return _FakeWS()

    list(s.stream_exec(_TicketClient(), "sess/../x", "print(1)", _ws_open=_open))
    url = seen_url["url"]
    assert "%2F" in url  # session 段编码
    assert "t%2Fick" in url  # ticket 值里的 / 被编码，不会撑破 ?ticket= 边界
    assert url.endswith("ticket=t%2Fick%2Bet%3Dv")
