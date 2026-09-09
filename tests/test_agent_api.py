"""N10 T5: agent task API — create/validate/role-gate."""
import uuid

import pytest


async def _mk_session(client, db_session, user_id, role="data_provider"):
    from app.models.sandbox_session import SandboxSession, SessionStatus

    session = SandboxSession(
        user_id=uuid.UUID(user_id), data_product_id=uuid.uuid4(), sandbox_level="L3",
        status=SessionStatus.READY.value, container_id=f"bwrap-agent-{uuid.uuid4().hex[:8]}",
        timeout_seconds=600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


@pytest.mark.asyncio
async def test_create_agent_task_ok(client, db_session, make_user, monkeypatch):
    from app.services.task_pipeline import PipelineTask, task_pipeline
    from app.services.task_state_machine import TaskStatus

    submitted = {}

    async def _fake_submit(**kwargs):
        submitted.update(kwargs)

    monkeypatch.setattr(task_pipeline, "submit", _fake_submit)

    headers, uid = await make_user("data_provider", "prov")
    session = await _mk_session(client, db_session, uid)
    resp = await client.post(
        "/api/v1/agent/tasks",
        json={
            "session_id": str(session.id),
            "prompt": "统计销售额并检索相关语料",
            "tool_ids": ["compute", "rag_retrieve", "llm_reason"],
            "step_budget": 8,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["task_type"] == "agent"
    assert body["tool_ids"] == ["compute", "rag_retrieve", "llm_reason"]
    assert submitted.get("payload", {}).get("agent") is True
    assert submitted["payload"]["agent_tool_ids"] == ["compute", "rag_retrieve", "llm_reason"]
    # rag_retrieve selected -> corpus materialization flag present in runner code.
    assert "_RAG_INDEX_FILE = 'input/rag_index.json'" in submitted["payload"]["code"]
    # llm_reason without a generative model -> runner falls back to the
    # deterministic planner (honest, no N6 hook).
    assert "_RAG_GENERATIVE_MODEL_ID = None" in submitted["payload"]["code"]


@pytest.mark.asyncio
async def test_create_agent_task_rejects_unknown_tool(client, db_session, make_user):
    headers, uid = await make_user("data_provider", "prov")
    session = await _mk_session(client, db_session, uid)
    resp = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x", "tool_ids": ["compute", "not_a_tool"]},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "unknown tool" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_agent_task_inference_requires_model(client, db_session, make_user):
    headers, uid = await make_user("data_provider", "prov")
    session = await _mk_session(client, db_session, uid)
    resp = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x", "tool_ids": ["inference"]},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "inference_model_id" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_agent_task_role_gates_tools(client, db_session, make_user):
    headers, uid = await make_user("buyer", "buy")
    session = await _mk_session(client, db_session, uid)
    # inference tool is not allowed for buyer role.
    resp = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x", "tool_ids": ["inference"],
              "inference_model_id": "model-1"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "not allowed" in resp.json()["detail"]
    # compute + rag_retrieve are allowed for buyer.
    resp2 = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x", "tool_ids": ["compute", "rag_retrieve"]},
        headers=headers,
    )
    assert resp2.status_code == 201, resp2.text


@pytest.mark.asyncio
async def test_create_agent_task_requires_session_owner(client, db_session, make_user):
    from app.models.sandbox_session import SandboxSession, SessionStatus

    headers, uid = await make_user("data_provider", "prov")
    other_headers, other = await make_user("data_provider", "prov2")
    session = await _mk_session(client, db_session, other)  # owned by other
    resp = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x", "tool_ids": ["compute"]},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "Not your session" in resp.json()["detail"]
