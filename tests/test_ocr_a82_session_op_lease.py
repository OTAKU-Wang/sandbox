"""A82 — a wedged `running` operation must not lock a rollback forever, and two
concurrent rollbacks of one session must not both pass the exclusivity gate.

What the local lane can and cannot prove (boundary, stated up front):

* It CAN prove the schema half: the lease columns exist, and the partial UNIQUE
  index makes a second `pending`/`running` rollback for the same session a
  database-level `IntegrityError` on SQLite too (partial indexes exist there
  since 3.8.0), while non-exclusive op types and terminal-state rows stay
  unaffected.
* It CANNOT prove the runtime half: reclaiming a row left in `running` by a
  crashed process needs `app/services/session_operations.py` to write
  `started_at` and sweep stale rows at boot. That half is NEEDS-OWNER in the
  evidence file; this test pins only the schema it needs.
"""
import uuid
from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateIndex

from app.models.session_operation import SessionOperation

EXCLUSIVE_INDEX = "uq_session_ops_rollback_inflight"
TABLE = cast(sa.Table, SessionOperation.__table__)


def _exclusive_index() -> sa.Index | None:
    return next((ix for ix in TABLE.indexes if ix.name == EXCLUSIVE_INDEX), None)


def test_lease_columns_exist():
    missing = [name for name in ("started_at", "finished_at") if name not in TABLE.c]
    assert not missing, (
        f"session_operations lacks {missing}: with no lease column, a row left in "
        "`running` by a restarted process can never be reclaimed, and the rollback "
        "exclusivity gate refuses that session forever"
    )
    for name in ("started_at", "finished_at"):
        assert TABLE.c[name].nullable, f"{name} must be nullable (set when the op starts)"


def test_exclusive_partial_unique_index_exists():
    ix = _exclusive_index()
    assert ix is not None, (
        f"session_operations has no {EXCLUSIVE_INDEX}: the rollback gate is "
        "SELECT-then-INSERT, so two concurrent submissions both pass"
    )
    assert ix.unique, f"{EXCLUSIVE_INDEX} must be UNIQUE"
    assert [c.name for c in ix.columns] == ["session_id", "op_type"]


def test_index_is_partial_over_the_inflight_states():
    ix = _exclusive_index()
    assert ix is not None, EXCLUSIVE_INDEX
    clause = ix.dialect_options["sqlite"].get("where")
    predicate = str(clause) if clause is not None else ""
    assert "rollback" in predicate, "predicate must stay scoped to the exclusive op type"
    assert "pending" in predicate and "running" in predicate, (
        "predicate must cover only the in-flight statuses, otherwise a completed "
        "rollback would permanently block the next one"
    )


def test_pg_ddl_is_a_partial_unique_index():
    from sqlalchemy.dialects import postgresql

    ix = _exclusive_index()
    ddl = str(CreateIndex(ix).compile(dialect=postgresql.dialect())) if ix else ""
    assert ddl.startswith("CREATE UNIQUE INDEX"), ddl
    assert "WHERE" in ddl.upper(), ddl


@pytest.mark.asyncio
async def test_second_inflight_rollback_is_refused_by_the_db(db_session):
    session_id = uuid.uuid4()
    db_session.add(
        SessionOperation(op_id=uuid.uuid4(), session_id=session_id, op_type="rollback",
                         status="running", progress=0.5)
    )
    await db_session.flush()

    db_session.add(
        SessionOperation(op_id=uuid.uuid4(), session_id=session_id, op_type="rollback",
                         status="pending", progress=0.0)
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_nonexclusive_ops_and_terminal_rows_stay_unconstrained(db_session):
    session_id = uuid.uuid4()
    for status in ("pending", "running"):
        db_session.add(
            SessionOperation(op_id=uuid.uuid4(), session_id=session_id, op_type="snapshot",
                             status=status, progress=0.0)
        )
    db_session.add(
        SessionOperation(op_id=uuid.uuid4(), session_id=session_id, op_type="rollback",
                         status="succeeded", progress=1.0)
    )
    db_session.add(
        SessionOperation(op_id=uuid.uuid4(), session_id=session_id, op_type="rollback",
                         status="failed", progress=0.3)
    )
    await db_session.flush()
    await db_session.rollback()
