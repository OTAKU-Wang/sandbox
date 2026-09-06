"""Session lifecycle tests (gap A2/D1) — termination primitive + expiry sweep.

These tests exercise the service directly against an in-memory DB, without
provisioning a real sandbox (container_id is left unset), so they run on any
platform (the sandbox runtime's ``resource`` module is Unix-only).
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.models.sandbox_session import SandboxSession, SessionStatus
from app.models.data_product import DataProduct
from app.models.kms import KeyMetadata, KeyStatus, KeyType
from app.models.network_policy import NetworkPolicy
from app.models.sandbox_task import SandboxTask, TaskStatus


async def _mk_user_product(db_session, make_user, prefix="u") -> tuple[str, uuid.UUID]:
    _, user_id = await make_user("data_provider", prefix)
    product = DataProduct(provider_id=uuid.UUID(user_id), name=f"p-{prefix}", status="published")
    db_session.add(product)
    await db_session.flush()
    await db_session.refresh(product)
    return user_id, product.id


async def _mk_session(db_session, user_id, product_id, *, status=SessionStatus.RUNNING.value, timeout=3600, contract_id=None):
    session = SandboxSession(
        user_id=uuid.UUID(user_id),
        data_product_id=product_id,
        sandbox_level="L3",
        status=status,
        timeout_seconds=timeout,
        contract_id=contract_id,
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.refresh(session)
    return session


@pytest.mark.asyncio
async def test_terminate_session_marks_terminated(db_session, make_user):
    from app.services.session_lifecycle import terminate_session

    user_id, product_id = await _mk_user_product(db_session, make_user)
    session = await _mk_session(db_session, user_id, product_id)

    ok = await terminate_session(session, db_session, reason="test")
    assert ok is True
    assert session.status == SessionStatus.TERMINATED.value
    assert session.ended_at is not None
    assert session.error_message == "test"


@pytest.mark.asyncio
async def test_terminate_session_destroys_key_cancels_task_deactivates_policy(db_session, make_user):
    from app.services.session_lifecycle import terminate_session

    user_id, product_id = await _mk_user_product(db_session, make_user)
    session = await _mk_session(db_session, user_id, product_id)
    session.session_key_id = "session-key-lifecycle-1"

    key = KeyMetadata(
        key_id="session-key-lifecycle-1",
        key_type=KeyType.SESSION.value,
        status=KeyStatus.ACTIVE.value,
        session_id=session.id,
        wrapped_payload=b"wrapped-blob",
        sm2_encrypted_payload=b"sm2-blob",
    )
    db_session.add(key)
    task = SandboxTask(
        task_id=f"t-{uuid.uuid4().hex[:8]}",
        session_id=session.id,
        user_id=uuid.UUID(user_id),
        task_type="query",
        status=TaskStatus.RUNNING.value,
    )
    db_session.add(task)
    policy = NetworkPolicy(session_id=str(session.id), user_id=user_id, mode="deny_all", active=True)
    db_session.add(policy)
    await db_session.flush()

    ok = await terminate_session(session, db_session, reason="test")
    assert ok is True
    await db_session.flush()

    await db_session.refresh(key)
    await db_session.refresh(task)
    await db_session.refresh(policy)
    assert key.status == KeyStatus.DESTROYED.value
    assert key.destroy_reason == "test"
    assert key.wrapped_payload is None
    assert key.sm2_encrypted_payload is None
    assert task.status == TaskStatus.CANCELLED.value
    assert policy.active is False


@pytest.mark.asyncio
async def test_terminate_session_rejects_terminal_state(db_session, make_user):
    from app.services.session_lifecycle import terminate_session

    user_id, product_id = await _mk_user_product(db_session, make_user)
    session = await _mk_session(db_session, user_id, product_id, status=SessionStatus.TERMINATED.value)

    ok = await terminate_session(session, db_session, reason="test")
    assert ok is False


@pytest.mark.asyncio
async def test_cleanup_expired_only_reclaims_expired_sessions(db_session, make_user):
    from app.services.session_lifecycle import cleanup_expired_sessions

    user_id, product_id = await _mk_user_product(db_session, make_user)
    expired = await _mk_session(db_session, user_id, product_id, timeout=1)
    expired.created_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    fresh = await _mk_session(db_session, user_id, product_id, timeout=3600)
    await db_session.flush()

    cleaned = await cleanup_expired_sessions(db_session)
    assert cleaned == 1
    await db_session.flush()

    assert expired.status == SessionStatus.TERMINATED.value
    assert fresh.status == SessionStatus.RUNNING.value


@pytest.mark.asyncio
async def test_cleanup_expired_handles_ready_state(db_session, make_user):
    """READY sessions (provisioned, never run) must also be reclaimed — the
    old admin endpoint only swept pending/provisioning/running, leaking READY
    orphan sandboxes."""
    from app.services.session_lifecycle import cleanup_expired_sessions

    user_id, product_id = await _mk_user_product(db_session, make_user)
    ready = await _mk_session(db_session, user_id, product_id, status=SessionStatus.READY.value, timeout=1)
    ready.created_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    await db_session.flush()

    cleaned = await cleanup_expired_sessions(db_session)
    assert cleaned == 1
    assert ready.status == SessionStatus.TERMINATED.value
