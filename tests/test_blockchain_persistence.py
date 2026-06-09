"""Tests for blockchain adapter DB persistence (P1-3).

Verifies that anchor records persist to the database and survive
simulated restarts. Uses the wired singleton blockchain_adapter.
"""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.blockchain_adapter import (
    PGAppendOnlyAdapter, ChainBackend, blockchain_adapter,
)


class TestPGAppendOnlyDBPersistence:
    """Test DB persistence for PGAppendOnlyAdapter."""

    @pytest.mark.asyncio
    async def test_adapter_has_db_session_factory_attr(self):
        adapter = PGAppendOnlyAdapter()
        assert hasattr(adapter, 'set_db_session_factory')
        assert hasattr(adapter, '_persist_anchor')
        assert hasattr(adapter, '_load_anchor_from_db')

    @pytest.mark.asyncio
    async def test_anchor_persists_when_factory_set(self):
        """When DB factory is set, anchor() should call _persist_anchor."""
        adapter = PGAppendOnlyAdapter()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        factory = MagicMock(return_value=mock_session)
        adapter.set_db_session_factory(factory)

        result = await adapter.anchor(b"test data", {"key": "value"})
        assert result.success
        # Verify persist was called (session.add should have been called)
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_anchor_works_without_factory(self):
        """Without DB factory, anchor should still work (in-memory only)."""
        adapter = PGAppendOnlyAdapter()
        result = await adapter.anchor(b"test data")
        assert result.success
        assert result.anchor is not None

    @pytest.mark.asyncio
    async def test_get_anchor_loads_from_db_when_not_in_cache(self):
        """get_anchor should fall back to DB when not in memory cache."""
        adapter = PGAppendOnlyAdapter()
        anchor_id = str(uuid.uuid4())

        # Mock DB row
        mock_row = MagicMock()
        mock_row.id = uuid.UUID(anchor_id)
        mock_row.data_hash = "abc123"
        mock_row.backend = "pg_append_only"
        mock_row.tx_hash = "chain_hash_123"
        mock_row.block_number = None
        mock_row.confirmed = True
        mock_row.metadata_json = '{"key": "value"}'
        mock_row.created_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)

        factory = MagicMock(return_value=mock_session)
        adapter.set_db_session_factory(factory)

        record = await adapter.get_anchor(anchor_id)
        assert record is not None
        assert record.anchor_id == anchor_id
        assert record.data_hash == "abc123"

    @pytest.mark.asyncio
    async def test_get_anchor_returns_none_when_not_found(self):
        """get_anchor should return None for nonexistent anchors."""
        adapter = PGAppendOnlyAdapter()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)

        factory = MagicMock(return_value=mock_session)
        adapter.set_db_session_factory(factory)

        record = await adapter.get_anchor(str(uuid.uuid4()))
        assert record is None

    @pytest.mark.asyncio
    async def test_verify_loads_from_db(self):
        """verify() should check DB when anchor not in memory."""
        adapter = PGAppendOnlyAdapter()
        anchor_id = str(uuid.uuid4())

        from app.services.crypto_service import crypto_service
        data = b"test data"
        expected_hash = crypto_service.sm3_hash(data)

        mock_row = MagicMock()
        mock_row.id = uuid.UUID(anchor_id)
        mock_row.data_hash = expected_hash
        mock_row.backend = "pg_append_only"
        mock_row.tx_hash = "chain_hash"
        mock_row.block_number = None
        mock_row.confirmed = True
        mock_row.metadata_json = None
        mock_row.created_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)

        factory = MagicMock(return_value=mock_session)
        adapter.set_db_session_factory(factory)

        assert await adapter.verify(anchor_id, data) is True

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_break_anchor(self):
        """If DB persist fails, anchor should still succeed in-memory."""
        adapter = PGAppendOnlyAdapter()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock(side_effect=Exception("DB down"))

        factory = MagicMock(return_value=mock_session)
        adapter.set_db_session_factory(factory)

        result = await adapter.anchor(b"test data")
        assert result.success
        assert result.anchor is not None

    @pytest.mark.asyncio
    async def test_singleton_has_db_factory(self):
        """The global singleton should have DB factory wired."""
        # The singleton is created at module import time
        # It should have _db_session_factory set (may be None if DB not available)
        assert hasattr(blockchain_adapter, '_db_session_factory')
        assert isinstance(blockchain_adapter, PGAppendOnlyAdapter)

    @pytest.mark.asyncio
    async def test_chain_integrity_with_db(self):
        """verify_chain_integrity should work with DB-backed adapter."""
        adapter = PGAppendOnlyAdapter()

        # Mock empty DB result (no prior anchors)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        factory = MagicMock(return_value=mock_session)
        adapter.set_db_session_factory(factory)

        # Anchor 3 events
        await adapter.anchor(b"event1", {})
        await adapter.anchor(b"event2", {})
        await adapter.anchor(b"event3", {})

        # In-memory chain integrity should still work
        assert await adapter.verify_chain_integrity() is True
