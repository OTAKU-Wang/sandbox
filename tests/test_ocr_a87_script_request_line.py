"""OCR A87 — 任务创建把脚本原文放进 query string：大脚本撞请求行长度上限，且脚本原文进访问日志。

本条根因在服务端签名（app/api/sandbox_tasks.py::create_task 把 code 声明成查询参数），
SDK 侧先提供不落请求行的通道：`CDSClient(task_payload_in_body=True)` 时整段脚本走 POST body，
请求行长度与脚本大小解耦。httpx.MockTransport style, no server needed.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient  # noqa: E402

BASE = "http://test"
SESSION = "11111111-1111-1111-1111-111111111111"
BIG_SCRIPT = "import pandas as pd\n" + "".join(
    f"df = df.filter(pl.col('c{i}') > {i})\n" for i in range(12000)
)


def _client(handler, **kw) -> CDSClient:
    kwargs = {"token": "tok", "transport": httpx.MockTransport(handler)}
    kwargs.update(kw)
    return CDSClient(BASE, **kwargs)


def test_script_size_is_decoupled_from_the_request_line():
    """200KB 级脚本在 body 形态下不得出现在 URL 里（h11/uvicorn 单行上限）。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-1"})

    assert len(BIG_SCRIPT) > 200_000

    with _client(handler, task_payload_in_body=True) as c:
        c.create_task(SESSION, "query", code=BIG_SCRIPT)

    req = captured[0]
    request_line = str(req.url)
    assert len(request_line) < 1_000, f"request line grew with the script: {len(request_line)}"
    assert "code" not in req.url.params
    assert json.loads(req.content)["code"] == BIG_SCRIPT


def test_query_string_form_cannot_carry_a_large_script():
    """实测边界（legacy 形态）：httpx 0.28.1 把 query 组件硬限在 65536，
    显式 task_payload_in_body=False 时连请求都发不出去（默认自 A87 落地起为 body）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"task_id": "task-1"})

    with _client(handler, task_payload_in_body=False) as c, pytest.raises(httpx.InvalidURL):
        c.create_task(SESSION, "query", code=BIG_SCRIPT)


def test_default_is_now_the_body_form_after_the_route_landed():
    """A87 落地后的默认锁：不传旗标时脚本走 body，请求行与脚本大小解耦。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-2"})

    with _client(handler) as c:
        c.create_task(SESSION, "query", code=BIG_SCRIPT)

    req = captured[0]
    assert len(str(req.url)) < 1_000
    assert "code" not in req.url.params
    assert json.loads(req.content)["code"] == BIG_SCRIPT
