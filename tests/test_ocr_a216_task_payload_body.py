"""OCR A216 — SDK 把整段任务代码与 RAG 问句放进 POST 的 query string。

请求行会进访问日志/反代日志（本仓已有 logs/cds-egress/access.jsonl 先例），密态租户
数据因此落到审计面之外；SDK 必须能把这些字段放进 POST body。
服务端 `POST /api/v1/sandbox-tasks` 目前把 code/rag_query 声明成查询参数
（app/api/sandbox_tasks.py::create_task），所以请求体形态默认关闭 —— 见
.gate-scratch/evidence/sdk-04.md 的 NEEDS-OWNER。
httpx.MockTransport style, no server needed.
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


def _client(handler, **kw) -> CDSClient:
    kwargs = {"token": "tok", "transport": httpx.MockTransport(handler)}
    kwargs.update(kw)
    return CDSClient(BASE, **kwargs)


def test_code_never_appears_in_query_string_in_body_mode():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-1", "status": "pending"})

    with _client(handler, task_payload_in_body=True) as c:
        c.create_task(SESSION, "query", code="SELECT secret_col FROM t", purpose="analysis")

    req = captured[0]
    assert req.method == "POST"
    assert "code" not in req.url.params
    assert "SELECT secret_col FROM t" not in str(req.url)
    assert json.loads(req.content) == {
        "code": "SELECT secret_col FROM t",
        "language": "python",
        "timeout_seconds": 3600,
        "purpose": "analysis",
    }


def test_rag_query_never_appears_in_query_string_in_body_mode():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-2", "status": "pending"})

    with _client(handler, task_payload_in_body=True) as c:
        c.create_task(SESSION, "rag_query", rag_query="这批患者的平均血压是多少")

    req = captured[0]
    assert "rag_query" not in req.url.params
    assert "平均血压" not in str(req.url)
    assert json.loads(req.content)["rag_query"] == "这批患者的平均血压是多少"


def test_body_mode_query_string_carries_only_identifiers():
    """请求行只允许留路由/标识字段：服务端现有的必填校验与审计 detail 不受影响。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-3"})

    with _client(handler, task_payload_in_body=True) as c:
        c.create_task(SESSION, "query", code="1+1")

    assert set(captured[0].url.params.keys()) == {"session_id", "task_type"}
    assert captured[0].url.params["session_id"] == SESSION
    assert captured[0].headers["content-type"].startswith("application/json")


def test_default_wire_is_the_body_form():
    """默认锁（A87 落地后）：默认即 body 形态，code 不再上请求行。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-4"})

    with _client(handler) as c:
        c.create_task(SESSION, "query", code="1+1")

    req = captured[0]
    assert "code" not in req.url.params
    assert json.loads(req.content)["code"] == "1+1"


def test_legacy_query_wire_still_available_for_old_servers():
    """兼容锁：task_payload_in_body=False 逐字段恢复现网旧形态（纯查询串、无请求体）。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-5"})

    with _client(handler, task_payload_in_body=False) as c:
        c.create_task(SESSION, "query", code="1+1")

    assert captured[0].url.params["code"] == "1+1"
    assert captured[0].content == b""
