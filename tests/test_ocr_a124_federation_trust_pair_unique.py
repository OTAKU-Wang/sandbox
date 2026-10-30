"""A124 — one live federation trust per space pair, enforced by the schema.

`load_trusts_from_db()` reads `status IN ('active','suspended')` with no
`order_by` and overwrites its `space_id -> trust` map per row, so duplicate
rows for one pair make the effective `trust_level` / `allowed_operations`
after a restart depend on the row-return order. SQLite enforces a partial
unique index the same way PostgreSQL does, so the constraint is proven
behaviourally here rather than only in metadata.
"""
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateIndex

from app.models.federation_trust import FederationTrustRecord

INDEX_NAME = "uq_federation_trusts_active_pair"


def _pair_index():
    return next((i for i in FederationTrustRecord.__table__.indexes if i.name == INDEX_NAME), None)


def _trust(remote: str, status: str) -> FederationTrustRecord:
    return FederationTrustRecord(
        local_space_id="cds-local",
        local_space_name="Local Space",
        local_endpoint="https://local.example.com",
        remote_space_id=remote,
        remote_space_name="Remote Space",
        remote_endpoint="https://remote.example.com",
        trust_level="basic",
        status=status,
        allowed_operations_json='["read_catalog"]',
        policy_sync_enabled=False,
    )


def test_unique_partial_index_exists_on_the_space_pair():
    index = _pair_index()
    assert index is not None, (
        f"federation_trusts has no {INDEX_NAME}: two active rows for one space pair "
        "make the restart-time authorization surface depend on row-return order"
    )
    assert index.unique is True
    assert [c.name for c in index.columns] == ["local_space_id", "remote_space_id"]


def test_index_is_partial_for_both_dialects():
    index = _pair_index()
    assert index is not None
    for dialect in ("postgresql", "sqlite"):
        where = index.dialect_options[dialect].get("where")
        assert where is not None, f"{INDEX_NAME} must carry {dialect}_where, not a table-wide unique"
        assert "active" in str(where)


def test_pg_ddl_renders_partial_unique_index():
    index = _pair_index()
    assert index is not None
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert "CREATE UNIQUE INDEX" in ddl
    assert "ON federation_trusts" in ddl
    assert "(local_space_id, remote_space_id)" in ddl
    assert "WHERE status = 'active'" in ddl


def test_index_lands_on_a_fresh_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'trusts.db'}")
    try:
        FederationTrustRecord.__table__.create(engine)
        with engine.connect() as conn:
            stored = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"),
                {"n": INDEX_NAME},
            ).scalar()
        assert stored, f"{INDEX_NAME} was not created by create_all"
        assert "WHERE" in stored.upper()
    finally:
        engine.dispose()


async def test_second_active_row_for_the_same_pair_is_rejected(db_session: AsyncSession):
    db_session.add(_trust("space-b", "active"))
    await db_session.flush()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(_trust("space-b", "active"))
            await db_session.flush()


async def test_revoked_or_pending_rows_may_coexist_with_the_active_one(db_session: AsyncSession):
    db_session.add(_trust("space-c", "active"))
    db_session.add(_trust("space-c", "revoked"))
    db_session.add(_trust("space-c", "pending"))
    await db_session.flush()

    live = await db_session.execute(
        select(FederationTrustRecord).where(FederationTrustRecord.status == "active")
    )
    for row in live.scalars():
        row.status = "revoked"
    await db_session.flush()

    revived = _trust("space-c", "active")
    db_session.add(revived)
    await db_session.flush()
    assert revived.id is not None


async def test_suspended_rows_are_not_covered_by_the_index(db_session: AsyncSession):
    """suspend → re-establish must stay persistable: `establish_trust` always INSERTs."""
    db_session.add(_trust("space-e", "suspended"))
    db_session.add(_trust("space-e", "suspended"))
    await db_session.flush()
    db_session.add(_trust("space-e", "active"))
    await db_session.flush()


def test_loader_lookup_index_is_kept():
    plain = [i for i in FederationTrustRecord.__table__.indexes if [c.name for c in i.columns] == ["remote_space_id"]]
    assert plain, "the per-remote-space lookup index must not be dropped by this change"
