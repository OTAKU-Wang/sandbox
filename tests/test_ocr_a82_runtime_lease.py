"""A82 (runtime half) — the lease must be written, swept, and race-safe.

test_ocr_a82_session_op_lease.py pins only the schema half (columns + partial
UNIQUE index) and names this half NEEDS-OWNER. Here:

* every writer of `running` stamps `started_at` and every terminal transition
  stamps `finished_at` (otherwise a lease can never be evaluated);
* the boot sweep `reclaim_stale_operations` fails stale/legacy `running` rows
  so the rollback exclusivity gate stops refusing the session forever;
* a fresh `running` row (live lease) is never reclaimed;
* the INSERT racing the SELECT-then-INSERT gate surfaces as 423
  OperationLocked, never a raw IntegrityError/500;
* `operation_payload` exposes the lease timestamps.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.session_operation import SessionOperation
from app.services import session_operations as so


def _mk_op(db, session_id, *, status="running", started_at=None, op_type="rollback"):
    op = SessionOperation(
        session_id=session_id, op_type=op_type, status=status, progress=0.5,
        started_at=started_at,
    )
    db.add(op)
    return op


@pytest.mark.asyncio
async def test_sync_execute_stamps_lease_timestamps(db_session):
    """`running` without started_at is unjudgeable; terminal without finished_at
    loses the audit trail of when the lease actually released."""
    async def executor(op):
        return {"ok": True}

    sid = uuid.uuid4()
    op = await so.submit_operation(
        db_session, sid, "rollback", run_async=False, executor=executor,
    )
    assert op.status == "succeeded"
    assert op.started_at is not None, "A82: the writer of `running` must stamp started_at"
    assert op.finished_at is not None, "A82: terminal transitions must stamp finished_at"
    assert op.finished_at >= op.started_at


@pytest.mark.asyncio
async def test_sync_execute_failure_stamps_finished_at(db_session):
    async def executor(op):
        raise RuntimeError("boom")

    sid = uuid.uuid4()
    op = await so.submit_operation(
        db_session, sid, "rollback", run_async=False, executor=executor,
    )
    assert op.status == "failed"
    assert op.finished_at is not None, "A82: the failure path must release the lease too"


@pytest.mark.asyncio
async def test_reclaim_fails_stale_and_legacy_running_rows(db_session):
    stale_sid = uuid.uuid4()
    legacy_sid = uuid.uuid4()
    fresh_sid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    _mk_op(db_session, stale_sid, started_at=now - timedelta(seconds=so.OPERATION_LEASE_SECONDS + 60))
    _mk_op(db_session, legacy_sid, started_at=None)  # pre-A82 row: no heartbeat at all
    _mk_op(db_session, fresh_sid, started_at=now - timedelta(seconds=10))  # live lease
    await db_session.flush()

    reclaimed = await so.reclaim_stale_operations(db_session)
    assert reclaimed == 2, "stale + legacy (NULL started_at) rows must be reclaimed"

    for sid, expect_status in ((stale_sid, "failed"), (legacy_sid, "failed"), (fresh_sid, "running")):
        rows = (await db_session.execute(
            SessionOperation.__table__.select().where(
                SessionOperation.__table__.c.session_id == sid,
                SessionOperation.__table__.c.op_type == "rollback",
            )
        )).all()
        assert len(rows) == 1
        assert rows[0].status == expect_status, f"session {sid} unexpectedly {rows[0].status}"
        if expect_status == "failed":
            assert "reclaimed" in (rows[0].error or "")
            assert rows[0].finished_at is not None


@pytest.mark.asyncio
async def test_reclaimed_session_can_rollback_again(db_session, monkeypatch):
    """The actual product symptom: after a restart the old `running` row keeps
    OperationLocked=true for every future rollback of that session."""
    sid = uuid.uuid4()
    _mk_op(db_session, sid, started_at=datetime.now(timezone.utc) - timedelta(hours=2))
    await db_session.flush()

    from app.core.errors import OperationLocked

    async def executor(op):
        return {"ok": True}

    with pytest.raises(OperationLocked):
        await so.submit_operation(db_session, sid, "rollback", run_async=False, executor=executor)

    reclaimed = await so.reclaim_stale_operations(db_session)
    await db_session.commit()
    assert reclaimed == 1

    op = await so.submit_operation(db_session, sid, "rollback", run_async=False, executor=executor)
    assert op.status == "succeeded", "A82: after the boot sweep the session must be usable again"


@pytest.mark.asyncio
async def test_integrity_error_racing_the_gate_maps_to_operation_locked(db_session, monkeypatch):
    """Simulate the race window: the SELECT gate reads "none", then another
    writer commits a conflicting rollback row before our INSERT flushes. The
    partial UNIQUE index fires — that must become 423, not a 500."""
    from app.core.errors import OperationLocked

    sid = uuid.uuid4()
    _mk_op(db_session, sid, started_at=datetime.now(timezone.utc))
    await db_session.commit()  # conflicting row is committed and visible

    # Race window: pretend the pre-check ran before the other commit landed.
    async def gate_miss(db, session_id, op_type):
        return False

    monkeypatch.setattr(so, "_has_running_exclusive", gate_miss)

    async def executor(op):
        return {"ok": True}

    with pytest.raises(OperationLocked):
        await so.submit_operation(db_session, sid, "rollback", run_async=False, executor=executor)
    await db_session.rollback()


def test_operation_payload_exposes_lease_timestamps():
    now = datetime.now(timezone.utc)
    op = SessionOperation(
        session_id=uuid.uuid4(), op_type="rollback", status="succeeded",
        progress=1.0, started_at=now, finished_at=now,
    )
    payload = so.operation_payload(op)
    assert payload["started_at"] == now.isoformat()
    assert payload["finished_at"] == now.isoformat()

    legacy = SessionOperation(session_id=uuid.uuid4(), op_type="rollback", status="running")
    legacy_payload = so.operation_payload(legacy)
    assert legacy_payload["started_at"] is None
    assert legacy_payload["finished_at"] is None
