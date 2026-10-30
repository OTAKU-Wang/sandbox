"""OCR A216（服务端 + 默认翻转）— 大 payload（>65536B）任务体经 SDK 默认形态到得了 handler。

httpx 0.28.1 对 query 组件硬限 65536 字符（sdk-04 实测：`code` 参数最长 65531），
query 形态下 >64KB 的 notebook 在发出前就抛 InvalidURL。本文件把 SDK 默认形态翻成
body 之后，用真实 ASGI 服务端做端到端回环：SDK 原样产出的请求（path+query+body）
逐字节 replay 到 create_task，任务落库后解密原文必须与输入逐字节相等。
"""
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from cds_sdk import CDSClient  # noqa: E402

from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.sandbox_task import SandboxTask

BIG_CODE = "# A216 大 notebook 回环探针\n" + "".join(
    f"row_{i} = df.filter(pl.col('c{i}') >= {i}).select('id')\n" for i in range(3600)
)


async def _mk_session(db_session, user_id):
    product = DataProduct(provider_id=uuid.UUID(user_id), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    session = SandboxSession(
        user_id=uuid.UUID(user_id),
        data_product_id=product.id,
        contract_id=None,
        status=SessionStatus.RUNNING.value,
        container_id="test-container",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


def test_probe_payload_exceeds_the_query_component_cap():
    """前提锁：探针必须大于 httpx 0.28.1 的 query 组件上限（65536）。"""
    assert len(BIG_CODE.encode("utf-8")) > 65_536


def test_default_sdk_sends_large_code_without_url_cap():
    """默认形态（翻转后）：100KB 级 code 走 body，请求行不随脚本增长。

    旧默认（query 串）下同一调用在 httpx 0.28.1 里根本发不出去
    （InvalidURL: URL component 'query' too long）——这正是本条 RED。
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-big"})

    with CDSClient(
        "http://test", token="tok", transport=httpx.MockTransport(handler)
    ) as c:
        out = c.create_task("11111111-1111-1111-1111-111111111111", "query", code=BIG_CODE)

    assert out["task_id"] == "task-big"
    req = captured[0]
    assert len(str(req.url)) < 1_000
    assert "code" not in req.url.params
    import json as _json

    assert _json.loads(req.content)["code"] == BIG_CODE


@pytest.mark.asyncio
async def test_large_payload_bytes_round_trip_through_the_route(client, db_session, make_user):
    """>64KB body 原样 POST 到路由 ⇒ 201，落库解密后与输入逐字节相等。"""
    headers, user_id = await make_user("data_provider", "a216")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "query"},
        json={"code": BIG_CODE, "timeout_seconds": 600},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text[:300]

    from app.services.task_code_security import decrypt_task_code

    row = await db_session.execute(
        select(SandboxTask).where(SandboxTask.task_id == resp.json()["task_id"])
    )
    task = row.scalar_one()
    stored = decrypt_task_code(task.code_content)
    assert stored == BIG_CODE
    assert len(stored.encode("utf-8")) > 65_536


@pytest.mark.asyncio
async def test_sdk_default_request_replays_into_the_route_intact(
    client, db_session, make_user
):
    """SDK↔服务端接缝：默认 CDSClient 真实产出的请求（method/path/query/body/头）
    逐字节 replay 进 ASGI 路由 ⇒ 201 且服务端落库原文与 SDK 输入逐字节相等。"""
    headers, user_id = await make_user("data_provider", "a216")
    session = await _mk_session(db_session, user_id)
    jwt = headers.get("Authorization", headers.get("authorization", "")).removeprefix("Bearer ")
    assert jwt

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-replay", "status": "pending"})

    with CDSClient("http://test", token=jwt, transport=httpx.MockTransport(handler)) as c:
        c.create_task(str(session.id), "query", code=BIG_CODE, purpose="analysis")

    req = captured[0]
    replay_headers = {
        "authorization": req.headers["authorization"],
        "content-type": req.headers["content-type"],
    }
    resp = await client.post(
        req.url.raw_path.decode("ascii"), content=req.content, headers=replay_headers
    )
    assert resp.status_code == 201, resp.text[:300]

    from app.services.task_code_security import decrypt_task_code

    row = await db_session.execute(
        select(SandboxTask).where(SandboxTask.task_id == resp.json()["task_id"])
    )
    task = row.scalar_one()
    assert decrypt_task_code(task.code_content) == BIG_CODE
    assert task.purpose == "analysis"


@pytest.mark.asyncio
async def test_legacy_query_form_still_creates_task_after_default_flip(client, db_session, make_user):
    """旧客户端逐字段现网形态（SDK 显式 task_payload_in_body=False 时发出的请求）
    replay 进路由仍必须 201 —— 默认翻转不破坏旧查询串客户端。"""
    headers, user_id = await make_user("data_provider", "a216")
    session = await _mk_session(db_session, user_id)
    jwt = headers.get("Authorization", headers.get("authorization", "")).removeprefix("Bearer ")

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, json={"task_id": "task-legacy"})

    with CDSClient(
        "http://test", token=jwt, transport=httpx.MockTransport(handler),
        task_payload_in_body=False,
    ) as c:
        c.create_task(str(session.id), "query", code="print('legacy-query-form')")

    req = captured[0]
    assert req.url.params["code"] == "print('legacy-query-form')"
    assert req.content == b""

    resp = await client.post(
        req.url.raw_path.decode("ascii"), content=req.content,
        headers={"authorization": req.headers["authorization"]},
    )
    assert resp.status_code == 201, resp.text[:300]

    from app.services.task_code_security import decrypt_task_code

    row = await db_session.execute(
        select(SandboxTask).where(SandboxTask.task_id == resp.json()["task_id"])
    )
    assert decrypt_task_code(row.scalar_one().code_content) == "print('legacy-query-form')"
