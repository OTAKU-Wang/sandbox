"""PZ-03: chain-anchoring DB persistence (SS-11 §7.2, FG-005 software side).

- migration 0011 builds chain_anchoring_records on a fresh database
- anchor() on every backend writes chain_anchoring_records rows
- a fresh adapter instance re-hydrates from the DB so verify() survives
  process restarts (including FISCO hash-chain continuation)
- chain_anchoring_state() derives the honest disclosure state
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy import create_engine

REPO_ROOT = Path(__file__).resolve().parents[1]

from tests.conftest import test_session_factory

from app.models.chain_anchoring import ChainAnchoringRecord
from app.services.blockchain_adapter import (
    AntChainAdapter,
    FISCOBCOSAdapter,
    PGAppendOnlyAdapter,
    chain_anchoring_state,
)


def _run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "CDS_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


@pytest.mark.slow
def test_migration_0011_creates_chain_anchoring_records(tmp_path):
    db = tmp_path / "pz03.db"
    result = _run_alembic(db, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    inspector = inspect(create_engine(f"sqlite:///{db}"))
    assert "chain_anchoring_records" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("chain_anchoring_records")}
    assert columns == {
        "id", "audit_record_id", "backend", "tx_hash", "block_height",
        "confirmed", "receipt", "created_at",
    }


@pytest.mark.slow
def test_model_matches_migration_columns():
    """The ORM model must expose exactly the migrated columns (parity input)."""
    model_columns = set(ChainAnchoringRecord.__table__.columns.keys())
    assert model_columns == {
        "id", "audit_record_id", "backend", "tx_hash", "block_height",
        "confirmed", "receipt", "created_at",
    }


class TestFISCOMPersistence:
    @pytest.mark.asyncio
    async def test_anchor_persists_row(self):
        adapter = FISCOBCOSAdapter()
        adapter.set_db_session_factory(test_session_factory)

        record_id = str(uuid.uuid4())
        result = await adapter.anchor(b"fisco-payload", {"record_ids": [record_id]})
        assert result.success

        async with test_session_factory() as session:
            rows = (await session.execute(
                select(ChainAnchoringRecord).where(
                    ChainAnchoringRecord.backend == "fisco_bcos"
                )
            )).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.audit_record_id == uuid.UUID(record_id)
        assert row.tx_hash == result.anchor.tx_hash
        assert row.block_height == result.anchor.block_number
        assert row.receipt["anchor_id"] == result.anchor.anchor_id
        assert row.receipt["data_hash"] == result.anchor.data_hash

    @pytest.mark.asyncio
    async def test_verify_survives_restart(self):
        adapter = FISCOBCOSAdapter()
        adapter.set_db_session_factory(test_session_factory)
        data = b"restart-payload"
        result = await adapter.anchor(data, {"record_ids": [str(uuid.uuid4())]})
        anchor_id = result.anchor.anchor_id
        first_block = result.anchor.block_number

        # Simulated restart: fresh instance, empty in-memory cache.
        fresh = FISCOBCOSAdapter()
        fresh.set_db_session_factory(test_session_factory)
        assert fresh._anchors == {}
        assert await fresh.verify(anchor_id, data) is True
        assert await fresh.get_anchor(anchor_id) is not None

        # Chain continuation after restart + integrity holds.
        second = await fresh.anchor(b"after-restart", {})
        assert second.success
        assert second.anchor.block_number == first_block + 1
        assert await fresh.verify_chain_integrity() is True

    @pytest.mark.asyncio
    async def test_no_factory_still_works_in_memory(self):
        adapter = FISCOBCOSAdapter()
        result = await adapter.anchor(b"no-db")
        assert result.success
        assert await adapter.verify(result.anchor.anchor_id, b"no-db") is True


class TestAntChainPersistence:
    @pytest.mark.asyncio
    async def test_anchor_persists_and_rehydrates(self):
        adapter = AntChainAdapter()
        adapter.set_db_session_factory(test_session_factory)
        data = b"ant-payload"
        result = await adapter.anchor(data, {"record_ids": [str(uuid.uuid4())]})
        assert result.success

        async with test_session_factory() as session:
            rows = (await session.execute(
                select(ChainAnchoringRecord).where(
                    ChainAnchoringRecord.backend == "ant_chain"
                )
            )).scalars().all()
        assert len(rows) == 1

        fresh = AntChainAdapter()
        fresh.set_db_session_factory(test_session_factory)
        assert await fresh.verify(result.anchor.anchor_id, data) is True


class TestPGAppendOnlyPersistence:
    @pytest.mark.asyncio
    async def test_anchor_writes_both_tables(self):
        from app.models.blockchain_anchor import BlockchainAnchor

        adapter = PGAppendOnlyAdapter()
        adapter.set_db_session_factory(test_session_factory)
        result = await adapter.anchor(b"pg-payload", {"record_ids": [str(uuid.uuid4())]})
        assert result.success

        async with test_session_factory() as session:
            legacy = (await session.execute(
                select(BlockchainAnchor)
            )).scalars().all()
            new_rows = (await session.execute(
                select(ChainAnchoringRecord).where(
                    ChainAnchoringRecord.backend == "pg_append_only"
                )
            )).scalars().all()
        assert len(legacy) == 1
        assert len(new_rows) == 1
        assert new_rows[0].receipt["anchor_id"] == result.anchor.anchor_id

    @pytest.mark.asyncio
    async def test_chain_continuation_after_restart(self):
        adapter = PGAppendOnlyAdapter()
        adapter.set_db_session_factory(test_session_factory)
        first = await adapter.anchor(b"one", {})
        assert first.success

        fresh = PGAppendOnlyAdapter()
        fresh.set_db_session_factory(test_session_factory)
        loaded = await fresh.hydrate_from_db()
        assert loaded == 1
        assert fresh._last_hash == first.anchor.tx_hash

        second = await fresh.anchor(b"two", {})
        assert second.success
        assert second.anchor.metadata["prev_hash"] == first.anchor.tx_hash
        assert await fresh.verify(first.anchor.anchor_id, b"one") is True


class TestApiVerifyAfterRestart:
    @pytest.mark.asyncio
    async def test_verify_endpoint_survives_restart(self, client, operator_headers, db_session, monkeypatch, tmp_path):
        from datetime import datetime, timezone
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
        from app.core.database import Base
        from app.models.audit_log import AuditLog
        import app.api.audit as audit_api

        # The adapter persists to its own file-backed engine: the in-memory
        # test DB shares one StaticPool connection, where a second session's
        # commit would clobber the request session's open transaction (a test
        # artifact — in production the adapter uses its own pool).
        adapter_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fisco.db'}")
        adapter_factory = async_sessionmaker(adapter_engine, class_=AsyncSession, expire_on_commit=False)
        async with adapter_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        try:
            first = FISCOBCOSAdapter()
            first.set_db_session_factory(adapter_factory)
            monkeypatch.setattr(audit_api, "blockchain_adapter", first)

            log = AuditLog(
                user_id=None,
                action="sandbox.provision",
                resource_type="sandbox_session",
                resource_id=str(uuid.uuid4()),
                detail={},
                created_at=datetime.now(timezone.utc),
            )
            db_session.add(log)
            await db_session.flush()
            await db_session.refresh(log)

            resp = await client.post(
                "/api/v1/audit/anchor", json={"record_ids": [str(log.id)]}, headers=operator_headers,
            )
            assert resp.status_code == 200
            assert resp.json()["backend"]["chain_anchoring"] == "fisco-sim"

            fresh = FISCOBCOSAdapter()
            fresh.set_db_session_factory(adapter_factory)
            monkeypatch.setattr(audit_api, "blockchain_adapter", fresh)

            resp = await client.get(f"/api/v1/audit/verify/{log.id}", headers=operator_headers)
            assert resp.status_code == 200
            body = resp.json()
            assert body["verified"] is True
            assert body["backend"]["backend"] == "fisco_bcos"
        finally:
            await adapter_engine.dispose()


class TestChainAnchoringState:
    def test_default_is_pg_log(self, monkeypatch):
        monkeypatch.delenv("CDS_BLOCKCHAIN_BACKEND", raising=False)
        assert chain_anchoring_state() == "pg-log"

    def test_fisco_sim(self, monkeypatch):
        monkeypatch.setenv("CDS_BLOCKCHAIN_BACKEND", "fisco_bcos")
        assert chain_anchoring_state(adapter=FISCOBCOSAdapter()) == "fisco-sim"
        # active adapter is PG while fisco was requested → not wired
        assert chain_anchoring_state(adapter=PGAppendOnlyAdapter()) == "not_configured"

    def test_ant_sim(self, monkeypatch):
        monkeypatch.setenv("CDS_BLOCKCHAIN_BACKEND", "ant_chain")
        assert chain_anchoring_state(adapter=AntChainAdapter()) == "ant-sim"

    def test_unknown_backend_value(self, monkeypatch):
        monkeypatch.setenv("CDS_BLOCKCHAIN_BACKEND", "ethereum")
        assert chain_anchoring_state() == "not_configured"
