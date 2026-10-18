"""W9: idle auto-pause + auto-resume with contract re-verification.

Covers docs/cubesandbox-parity-plan.md W9 test matrix:
- idle_policy="pause" session expiring → SUSPENDED (not TERMINATED) + audit
- default policy session expiring → still terminated (regression)
- auto_resume=True + ACTIVE contract → interaction wakes the session
- auto_resume=True + TERMINATED contract → 410 SessionTerminated, no wake
- auto_resume=True + NO contract (contract_id IS NULL) → wakes (A31)
- auto_resume=True + dangling contract_id (row gone) → 410, fail-closed (A31)
- expired SUSPENDED sessions are terminated even under pause policy
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.contract import Contract, ContractStatus
from app.models.sandbox_session import SandboxSession, SessionStatus
from app.services import session_lifecycle
from app.services.session_lifecycle import cleanup_expired_sessions


async def _mk_session(db_session, user, *, idle_policy=None, auto_resume=False,
                      status=SessionStatus.RUNNING.value, age_hours=25, contract_id=None,
                      pre_pause_status=None):
    session = SandboxSession(
        id=uuid.uuid4(),
        user_id=user.id,
        data_product_id=uuid.uuid4(),
        sandbox_level="L3",
        sandbox_mode="structured_query",
        status=status,
        idle_policy=idle_policy,
        auto_resume=auto_resume,
        contract_id=contract_id,
        pre_pause_status=pre_pause_status,
        timeout_seconds=3600,
        created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest.fixture
async def session_owner(db_session):
    from app.models.user import User

    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"idle_{unique}",
        email=f"idle_{unique}@example.com",
        hashed_password="x",
        role="data_provider",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_idle_pause_policy_suspends_instead_of_terminating(db_session, session_owner):
    session = await _mk_session(db_session, session_owner, idle_policy="pause")
    cleaned = await cleanup_expired_sessions(db_session)
    assert cleaned == 0
    await db_session.refresh(session)
    assert session.status == SessionStatus.SUSPENDED.value
    assert session.pre_pause_status == SessionStatus.RUNNING.value


@pytest.mark.asyncio
async def test_default_policy_still_terminates(db_session, session_owner):
    session = await _mk_session(db_session, session_owner)
    cleaned = await cleanup_expired_sessions(db_session)
    assert cleaned == 1
    await db_session.refresh(session)
    assert session.status == SessionStatus.TERMINATED.value


@pytest.mark.asyncio
async def test_expired_suspended_session_terminated_even_with_pause_policy(db_session, session_owner):
    session = await _mk_session(
        db_session, session_owner, idle_policy="pause",
        status=SessionStatus.SUSPENDED.value, pre_pause_status="running",
    )
    await cleanup_expired_sessions(db_session)
    await db_session.refresh(session)
    assert session.status == SessionStatus.TERMINATED.value


async def _mk_contract(db_session, provider, *, status=ContractStatus.ACTIVE.value):
    contract = Contract(
        id=uuid.uuid4(),
        contract_no=f"W9-{uuid.uuid4().hex[:10]}",
        contract_type="data_use",
        title="W9 auto-resume probe",
        provider_id=provider.id,
        buyer_id=provider.id,
        status=status,
        product_ids=[],
    )
    db_session.add(contract)
    await db_session.flush()
    return contract


@pytest.mark.asyncio
async def test_auto_resume_wakes_session_with_active_contract(
    client, db_session, session_owner
):
    contract = await _mk_contract(db_session, session_owner, status=ContractStatus.ACTIVE.value)
    session = await _mk_session(
        db_session, session_owner, idle_policy="pause", auto_resume=True,
        status=SessionStatus.SUSPENDED.value, pre_pause_status="running",
        age_hours=1, contract_id=str(contract.id),
    )

    from app.services.auth_service import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(session_owner.id, session_owner.role)}"}
    resp = await client.post(f"/api/v1/sandbox-sessions/{session.id}/refreshes", headers=headers)
    assert resp.status_code == 200
    await db_session.refresh(session)
    assert session.status == SessionStatus.RUNNING.value
    assert session.pre_pause_status is None


@pytest.mark.asyncio
async def test_auto_resume_rejected_when_contract_terminated(
    client, db_session, session_owner
):
    contract = await _mk_contract(db_session, session_owner, status=ContractStatus.TERMINATED.value)
    session = await _mk_session(
        db_session, session_owner, idle_policy="pause", auto_resume=True,
        status=SessionStatus.SUSPENDED.value, pre_pause_status="running",
        age_hours=1, contract_id=str(contract.id),
    )

    from app.services.auth_service import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(session_owner.id, session_owner.role)}"}
    resp = await client.post(f"/api/v1/sandbox-sessions/{session.id}/refreshes", headers=headers)
    assert resp.status_code == 410
    assert resp.json()["code"] == "SESSION_TERMINATED"
    await db_session.refresh(session)
    assert session.status == SessionStatus.SUSPENDED.value


@pytest.mark.asyncio
async def test_auto_resume_without_contract_wakes_session(client, db_session, session_owner):
    """A31: contract_id IS NULL 是「本就没有治理合约」，不是「合约已失效」。

    旧实现把 `contract_active = False` 当默认值，无合约会话一旦 SUSPENDED 就永久
    410 SESSION_TERMINATED（`_load_operable_session` 也走同一判据 ⇒ 手工 /resume
    同样堵死，三个隐式调用点全堵）。收紧不能过头：没有需要撤销的授权时，自动
    resume 必须照常唤醒会话，并且唤醒的是它自己的会话（反向用例）。
    """
    session = await _mk_session(
        db_session, session_owner, idle_policy="pause", auto_resume=True,
        status=SessionStatus.SUSPENDED.value, pre_pause_status="running", age_hours=1,
    )

    from app.services.auth_service import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(session_owner.id, session_owner.role)}"}
    resp = await client.post(f"/api/v1/sandbox-sessions/{session.id}/refreshes", headers=headers)
    assert resp.status_code == 200, resp.text
    await db_session.refresh(session)
    assert session.status == SessionStatus.RUNNING.value
    assert session.pre_pause_status is None


@pytest.mark.asyncio
async def test_auto_resume_rejected_when_contract_row_missing(client, db_session, session_owner):
    """A31 的 fail-closed 边界：挂着 contract_id 但行已不存在 ⇒ 仍然 410（不得放宽）。"""
    session = await _mk_session(
        db_session, session_owner, idle_policy="pause", auto_resume=True,
        status=SessionStatus.SUSPENDED.value, pre_pause_status="running", age_hours=1,
        contract_id=str(uuid.uuid4()),
    )

    from app.services.auth_service import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(session_owner.id, session_owner.role)}"}
    resp = await client.post(f"/api/v1/sandbox-sessions/{session.id}/refreshes", headers=headers)
    assert resp.status_code == 410
    assert resp.json()["code"] == "SESSION_TERMINATED"
    await db_session.refresh(session)
    assert session.status == SessionStatus.SUSPENDED.value


@pytest.mark.asyncio
async def test_auto_resume_rejected_when_contract_id_unparseable(client, db_session, session_owner):
    """A31 fail-closed 边界二：contract_id 不是合法 UUID ⇒ 无法证明授权仍在，仍 410。"""
    session = await _mk_session(
        db_session, session_owner, idle_policy="pause", auto_resume=True,
        status=SessionStatus.SUSPENDED.value, pre_pause_status="running", age_hours=1,
        contract_id="not-a-uuid",
    )

    from app.services.auth_service import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(session_owner.id, session_owner.role)}"}
    resp = await client.post(f"/api/v1/sandbox-sessions/{session.id}/refreshes", headers=headers)
    assert resp.status_code == 410
    assert resp.json()["code"] == "SESSION_TERMINATED"
    await db_session.refresh(session)
    assert session.status == SessionStatus.SUSPENDED.value


@pytest.mark.asyncio
async def test_create_session_accepts_idle_fields(client, db_session, session_owner):
    from app.services.auth_service import create_access_token

    headers = {"Authorization": f"Bearer {create_access_token(session_owner.id, session_owner.role)}"}
    resp = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": str(uuid.uuid4()),
        "sandbox_level": "L3",
        "idle_policy": "pause",
        "auto_resume": True,
        "timeout_seconds": 3600,
    }, headers=headers)
    # Schema-level acceptance: any non-422 outcome proves the fields parse
    # (creation itself may 4xx on missing product/provision environment).
    assert resp.status_code != 422
    if resp.status_code == 201:
        assert resp.json()["idle_policy"] == "pause"

    bad = await client.post("/api/v1/sandbox-sessions", json={
        "data_product_id": str(uuid.uuid4()),
        "sandbox_level": "L3",
        "idle_policy": "explode",
    }, headers=headers)
    assert bad.status_code == 422
