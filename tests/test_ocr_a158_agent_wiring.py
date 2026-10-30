"""A158 wiring leg: role/budget must reach validate_run from BOTH call sites.

backend-25a turned `ToolRegistry.validate_run(tool_ids, *, role, budget)` into
the fail-closed single entry point, but the two real callers never passed the
context it needs: `app/api/agent.py` only relied on `_enforce_tool_roles`, and
`agent_service.build_agent_runner` validated tools structurally only. These
tests bypass the API first gate and hit the service layer directly — the exact
hole the ledger describes ("任何新增的工具选择入口如果忘了自己调
_enforce_tool_roles，就没有任何兜底").
"""
import uuid

import pytest

from app.services.agent_service import build_agent_runner
from app.services.agent_tools import tool_registry


def test_build_rejects_tool_selection_over_step_budget():
    # llm_reason budget_cost=2 > step_budget=1 — must be refused at build time
    with pytest.raises(ValueError, match="budget"):
        build_agent_runner("q", ["llm_reason"], step_budget=1)


def test_build_zero_step_budget_keeps_step_budget_message():
    with pytest.raises(ValueError, match="step_budget"):
        build_agent_runner("q", ["compute"], step_budget=0)


def test_build_within_budget_still_generates():
    src = build_agent_runner("q", ["compute", "llm_reason"], step_budget=3)
    assert "_AGENT_TOOLS = ['compute', 'llm_reason']" in src


def test_build_role_gate_bypassing_api():
    # inference roles exclude buyer; the API first gate is _enforce_tool_roles —
    # a direct service-layer call with a role must still fail closed (A158).
    with pytest.raises(ValueError, match="inference"):
        build_agent_runner("q", ["inference"], role="buyer", inference_model_id="m-1")
    build_agent_runner("q", ["inference"], role="operator", inference_model_id="m-1")


async def _mk_session(db_session, user_id):
    from app.models.sandbox_session import SandboxSession, SessionStatus

    session = SandboxSession(
        user_id=uuid.UUID(user_id), data_product_id=uuid.uuid4(), sandbox_level="L3",
        status=SessionStatus.READY.value, container_id=f"bwrap-a158-{uuid.uuid4().hex[:8]}",
        timeout_seconds=600,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


@pytest.mark.asyncio
async def test_api_budget_reaches_validate_run(client, db_session, make_user, monkeypatch):
    from app.services.task_pipeline import task_pipeline

    async def _fake_submit(**kwargs):
        pass

    monkeypatch.setattr(task_pipeline, "submit", _fake_submit)
    headers, uid = await make_user("data_provider", "a158_prov")
    session = await _mk_session(db_session, uid)

    resp = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x",
              "tool_ids": ["llm_reason"], "step_budget": 1},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "budget" in resp.json()["detail"]

    resp2 = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x",
              "tool_ids": ["llm_reason"], "step_budget": 2},
        headers=headers,
    )
    assert resp2.status_code == 201, resp2.text


@pytest.mark.asyncio
async def test_api_role_violation_status_contract_unchanged(client, db_session, make_user):
    """Regression guard: the wiring must not demote the 403 role denial to 422."""
    headers, uid = await make_user("buyer", "a158_buy")
    session = await _mk_session(db_session, uid)
    resp = await client.post(
        "/api/v1/agent/tasks",
        json={"session_id": str(session.id), "prompt": "x",
              "tool_ids": ["inference"], "inference_model_id": "model-1"},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert "not allowed" in resp.json()["detail"]
