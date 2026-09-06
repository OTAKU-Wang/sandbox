"""RAG_QUERY task API tests (gap T11).

task_type=rag_query flows through the existing task lifecycle: create (with a
natural-language query), purpose gate, and the get_task_result T5 verdict gate.
"""
import uuid

import pytest
from sqlalchemy import select

from app.models.contract import Contract, ContractStatus
from app.models.data_product import DataProduct
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.sandbox_task import SandboxTask, TaskStatus


async def _mk_session(db_session, user_id, *, contract_id=None, status=SessionStatus.RUNNING.value):
    product = DataProduct(provider_id=uuid.UUID(user_id), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    session = SandboxSession(
        user_id=uuid.UUID(user_id),
        data_product_id=product.id,
        contract_id=contract_id,
        status=status,
        container_id="test-container",
        timeout_seconds=3600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


@pytest.mark.asyncio
async def test_create_rag_query_task(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "rag_query",
            "rag_query": "什么样的房子适合一家四口？",
            "timeout_seconds": 600,
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["task_type"] == "rag_query"
    assert body["status"] == "pending"

    # task row exists with generated runner code stored encrypted
    from app.services.task_code_security import decrypt_task_code

    row = await db_session.execute(select(SandboxTask).where(SandboxTask.task_id == body["task_id"]))
    task = row.scalar_one()
    code = decrypt_task_code(task.code_content)
    assert "_RAG_QUERY" in code


@pytest.mark.asyncio
async def test_create_rag_query_task_requires_query(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={"session_id": str(session.id), "task_type": "rag_query"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_rag_query_task_rejects_code(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "rag_query",
            "code": "print(1)",
            "rag_query": "问个问题",
        },
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_rag_query_task_purpose_gate(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    _, buyer = await make_user("buyer", "buy")
    product = DataProduct(provider_id=uuid.UUID(user_id), name="p", status="published")
    db_session.add(product)
    await db_session.flush()
    contract = Contract(
        contract_no=f"C-{uuid.uuid4().hex[:8]}",
        contract_type="data_query",
        provider_id=uuid.UUID(user_id),
        buyer_id=uuid.UUID(buyer),
        title="purpose-gate",
        product_ids=[str(product.id)],
        status=ContractStatus.ACTIVE.value,
        purpose="model_training",
        purpose_scope=["model_training"],
    )
    db_session.add(contract)
    await db_session.flush()
    await db_session.refresh(contract)
    session = await _mk_session(db_session, user_id, contract_id=str(contract.id))

    # no purpose declared → rejected by the A4 purpose gate
    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "rag_query",
            "rag_query": "训练用问题",
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "purpose" in resp.json()["detail"]

    # matching purpose → accepted
    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "rag_query",
            "rag_query": "训练用问题",
            "purpose": "model_training",
        },
        headers=headers,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_rag_query_task_query_too_long(client, db_session, make_user):
    from app.core.config import get_settings

    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)
    long_query = "问" * (get_settings().RAG_QUERY_MAX_CHARS + 10)

    resp = await client.post(
        "/api/v1/sandbox-tasks",
        params={
            "session_id": str(session.id),
            "task_type": "rag_query",
            "rag_query": long_query,
        },
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rag_task_result_verdict_gate(client, db_session, make_user):
    headers, user_id = await make_user("data_provider", "prov")
    session = await _mk_session(db_session, user_id)

    no_verdict = SandboxTask(
        task_id=f"t-{uuid.uuid4().hex[:8]}",
        session_id=session.id,
        user_id=uuid.UUID(user_id),
        task_type="rag_query",
        status=TaskStatus.COMPLETED.value,
        resource_usage={},
    )
    db_session.add(no_verdict)
    await db_session.flush()

    resp = await client.get(
        f"/api/v1/sandbox-tasks/{no_verdict.task_id}/result",
        params={"session_id": str(session.id)},
        headers=headers,
    )
    assert resp.status_code == 409

    passed = SandboxTask(
        task_id=f"t-{uuid.uuid4().hex[:8]}",
        session_id=session.id,
        user_id=uuid.UUID(user_id),
        task_type="rag_query",
        status=TaskStatus.COMPLETED.value,
        resource_usage={
            "output_security": {
                "inspection_report": {"passed": True, "findings_count": 0},
                "redacted_output": '{"answer":"..."}',
            },
        },
    )
    db_session.add(passed)
    await db_session.flush()

    resp = await client.get(
        f"/api/v1/sandbox-tasks/{passed.task_id}/result",
        params={"session_id": str(session.id)},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["output_inspection"]["redacted_output"] == '{"answer":"..."}'
    assert body["output_inspection"]["passed"] is True
