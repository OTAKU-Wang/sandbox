"""OCR A87（服务端半边）— create_task 接受 JSON body 承载脚本原文，保留 query 兼容形态。

根因：app/api/sandbox_tasks.py::create_task 把 code/language/timeout_seconds/purpose/
rag_query 全声明成带默认值的标量形参 ⇒ FastAPI 按查询参数解析，脚本原文只能走请求行，
进访问日志/反代日志/URL 缓存。本文件锁服务端的双形态契约：
- body 形态：这些字段可从 JSON body 读取（新 SDK 的默认形态）；
- 兼容形态：现有 query-string 请求必须继续被接受（旧 SDK 不破坏）；
- 任何 422 不得把请求体原文回显进响应（echo 即第二条泄漏路径）。
SDK 侧的请求行断言见 tests/test_ocr_a87_script_request_line.py（sdk-04 轨道）。
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.sandbox_task import SandboxTask

SENTINEL_SCRIPT = "import pandas as pd  # SENTINEL-A87-LEASE-TEXT\ndf = pd.read_parquet('/sandbox/data/x.parquet')\n"


async def _mk_session(db_session, user_id, *, status=SessionStatus.RUNNING.value):
    product = DataProduct(provider_id=uuid.UUID(user_id), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    session = SandboxSession(
        user_id=uuid.UUID(user_id),
        data_product_id=product.id,
        contract_id=None,
        status=status,
        container_id="test-container",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


async def _decrypt(db_session, task_id):
    from app.services.task_code_security import decrypt_task_code

    row = await db_session.execute(select(SandboxTask).where(SandboxTask.task_id == task_id))
    task = row.scalar_one()
    return task, decrypt_task_code(task.code_content)


@pytest.mark.asyncio
async def test_body_form_creates_task_with_code_in_json_body(client, db_session, make_user):
    """A87 核心：code 走 body、请求行只带标识字段 ⇒ 201 且落库原文一致。"""
    headers, user_id = await make_user("data_provider", "a87")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "query"},
        json={
            "code": SENTINEL_SCRIPT,
            "language": "python",
            "timeout_seconds": 600,
            "purpose": "analysis",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["purpose"] == "analysis"

    task, code = await _decrypt(db_session, body["task_id"])
    assert code == SENTINEL_SCRIPT
    assert task.language == "python"
    assert task.timeout_seconds == 600


@pytest.mark.asyncio
async def test_body_form_rag_query_never_needs_query_string(client, db_session, make_user):
    """rag_query（密态问句）同样可从 body 读取，服务端仍走 T11 生成 runner 路径。"""
    headers, user_id = await make_user("data_provider", "a87")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "rag_query"},
        json={"rag_query": "这批患者的平均血压是多少", "timeout_seconds": 600},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    task, code = await _decrypt(db_session, resp.json()["task_id"])
    assert task.task_type == "rag_query"
    assert "_RAG_QUERY" in code


@pytest.mark.asyncio
async def test_legacy_query_string_form_still_accepted(client, db_session, make_user):
    """兼容锁（旧 SDK 不得破坏）：纯 query-string 请求形态必须继续创建成功。

    与 tests/test_sdk_client.py 发出的线上形态一致：POST + params、无请求体。
    """
    headers, user_id = await make_user("data_provider", "a87")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "query",
            "code": "print(1)",
            "language": "python",
            "timeout_seconds": 600,
            "purpose": "analysis",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    _, code = await _decrypt(db_session, resp.json()["task_id"])
    assert code == "print(1)"


@pytest.mark.asyncio
async def test_body_wins_over_query_for_the_same_field(client, db_session, make_user):
    """双形态并存时 body 优先：body 是 A87 之后的规范形态。"""
    headers, user_id = await make_user("data_provider", "a87")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "code": "print('from-query')"},
        json={"code": SENTINEL_SCRIPT},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    _, code = await _decrypt(db_session, resp.json()["task_id"])
    assert code == SENTINEL_SCRIPT


@pytest.mark.asyncio
async def test_malformed_json_body_rejected_without_echoing_payload(client, db_session, make_user):
    """422 不得回显请求体：错误响应泄漏脚本原文 = 第二条泄漏路径（A87 禁回显）。"""
    headers, user_id = await make_user("data_provider", "a87")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "query"},
        content=b'{"code": "print(1)  # LEAK-CANARY-8f31\n truncated on purpose',
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert "LEAK-CANARY-8f31" not in resp.text


@pytest.mark.asyncio
async def test_non_object_json_body_rejected(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "a87")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "query"},
        json=["print('LEAK-CANARY-9d42')"],
        headers=headers,
    )
    assert resp.status_code == 422
    assert "LEAK-CANARY-9d42" not in resp.text


@pytest.mark.asyncio
async def test_body_type_errors_rejected_without_echoing_value(client, db_session, make_user):
    """字段类型错 ⇒ 422，且错误 detail 只点名不携带取值。"""
    headers, user_id = await make_user("data_provider", "a87")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "query"},
        json={"code": 12345},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "12345" not in resp.text

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "query"},
        json={"code": "print(1)", "timeout_seconds": "not-a-number"},
        headers=headers,
    )
    assert resp.status_code == 422
