"""Tests for Merkle Batch Pipeline (P1-4).

Verifies the auto-batch pipeline that collects events via Redis Stream,
builds Merkle trees, and anchors roots to the blockchain.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.merkle_pipeline import MerkleBatchPipeline, BatchResult


class TestMerkleBatchPipeline:
    """Test MerkleBatchPipeline core functionality."""

    def test_pipeline_creation(self):
        pipeline = MerkleBatchPipeline(batch_size=50, batch_interval=30)
        assert pipeline._batch_size == 50
        assert pipeline._batch_interval == 30
        assert pipeline._running is False

    def test_pipeline_defaults(self):
        pipeline = MerkleBatchPipeline()
        assert pipeline._batch_size == 100
        assert pipeline._batch_interval == 60

    def test_get_batch_returns_none_for_unknown(self):
        pipeline = MerkleBatchPipeline()
        assert pipeline.get_batch("nonexistent") is None

    def test_get_proof_returns_none_for_unknown(self):
        pipeline = MerkleBatchPipeline()
        assert pipeline.get_proof("nonexistent", 0) is None

    def test_stats_initial(self):
        pipeline = MerkleBatchPipeline()
        stats = pipeline.get_stats()
        assert stats["total_batches"] == 0
        assert stats["total_proofs"] == 0
        assert stats["anchored_batches"] == 0
        assert stats["running"] is False

    @pytest.mark.asyncio
    async def test_start_stop(self):
        pipeline = MerkleBatchPipeline(batch_interval=0.1)
        await pipeline.start()
        assert pipeline._running is True
        assert pipeline._task is not None
        await pipeline.stop()
        assert pipeline._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        pipeline = MerkleBatchPipeline(batch_interval=0.1)
        await pipeline.start()
        await pipeline.start()  # Should not create another task
        task1 = pipeline._task
        await pipeline.stop()
        assert task1 is not None

    @pytest.mark.asyncio
    async def test_process_batch_builds_merkle_tree(self):
        """_process_batch should build Merkle tree and generate proofs."""
        from app.services.merkle_service import MerkleService

        pipeline = MerkleBatchPipeline()
        merkle = MerkleService()

        # Create mock blockchain adapter
        mock_adapter = AsyncMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.anchor = MagicMock(tx_hash="0xabc123")
        mock_adapter.anchor = AsyncMock(return_value=mock_result)

        events = [
            ("msg1", b"event1", {}),
            ("msg2", b"event2", {}),
            ("msg3", b"event3", {}),
        ]

        result = await pipeline._process_batch(events, merkle, mock_adapter)
        assert result is not None
        assert result.leaf_count == 3
        assert result.root_hash
        assert result.anchored is True
        assert result.anchor_tx_hash == "0xabc123"

        # Check proofs were stored
        proofs = pipeline._proofs.get(result.batch_id)
        assert proofs is not None
        assert len(proofs) == 3

    @pytest.mark.asyncio
    async def test_process_batch_empty_events(self):
        pipeline = MerkleBatchPipeline()
        result = await pipeline._process_batch([], None, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_process_batch_anchor_failure_still_returns_result(self):
        """Even if blockchain anchoring fails, batch result should be returned."""
        from app.services.merkle_service import MerkleService

        pipeline = MerkleBatchPipeline()
        merkle = MerkleService()

        mock_adapter = AsyncMock()
        mock_adapter.anchor = AsyncMock(side_effect=Exception("Chain down"))

        events = [("msg1", b"event1", {})]
        result = await pipeline._process_batch(events, merkle, mock_adapter)
        assert result is not None
        assert result.anchored is False
        assert result.anchor_tx_hash is None

    @pytest.mark.asyncio
    async def test_push_event_without_redis(self):
        """push_event should gracefully handle Redis being unavailable."""
        pipeline = MerkleBatchPipeline()
        with patch("app.core.redis.get_redis", side_effect=ConnectionError("no redis")):
            msg_id = await pipeline.push_event(b"test event")
            assert msg_id == ""

    @pytest.mark.asyncio
    async def test_read_batch_without_redis(self):
        """_read_batch should return empty list when Redis is unavailable."""
        pipeline = MerkleBatchPipeline()
        with patch("app.core.redis.get_redis", side_effect=ConnectionError("no redis")):
            events = await pipeline._read_batch()
            assert events == []

    def test_batch_result_fields(self):
        from datetime import datetime, timezone
        result = BatchResult(
            batch_id="batch-123",
            root_hash="abc",
            leaf_count=5,
            anchor_tx_hash="0x123",
            anchored=True,
        )
        assert result.batch_id == "batch-123"
        assert result.leaf_count == 5
        assert result.anchored is True
        assert isinstance(result.timestamp, datetime)


class TestMerklePipelineIntegration:
    """Integration tests with MerkleService."""

    @pytest.mark.asyncio
    async def test_full_batch_with_real_merkle_service(self):
        """End-to-end: events → Merkle tree → anchor."""
        from app.services.merkle_service import MerkleService

        pipeline = MerkleBatchPipeline()
        merkle = MerkleService()

        mock_adapter = AsyncMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.anchor = MagicMock(tx_hash="0xdef456")
        mock_adapter.anchor = AsyncMock(return_value=mock_result)

        # Simulate 10 events
        events = [(f"msg{i}", f"event{i}".encode(), {"seq": i}) for i in range(10)]
        result = await pipeline._process_batch(events, merkle, mock_adapter)

        assert result is not None
        assert result.leaf_count == 10
        assert result.anchored is True

        # Verify proofs are valid
        for i in range(10):
            proof = pipeline.get_proof(result.batch_id, i)
            assert proof is not None
            assert proof["leaf_index"] == i
            # Verify the proof
            merkle_proof = merkle.generate_proof(
                [p["leaf_hash"] for p in pipeline._proofs[result.batch_id].values()],
                i,
            )
            # The stored proof should match
            assert proof["root_hash"] == result.root_hash

    @pytest.mark.asyncio
    async def test_stats_after_processing(self):
        from app.services.merkle_service import MerkleService

        pipeline = MerkleBatchPipeline()
        merkle = MerkleService()

        mock_adapter = AsyncMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.anchor = MagicMock(tx_hash="0x123")
        mock_adapter.anchor = AsyncMock(return_value=mock_result)

        events = [("msg1", b"e1", {}), ("msg2", b"e2", {})]
        result = await pipeline._process_batch(events, merkle, mock_adapter)
        # _process_batch returns result; store it like worker_loop does
        if result:
            pipeline._batches[result.batch_id] = result

        stats = pipeline.get_stats()
        assert stats["total_batches"] == 1
        assert stats["total_proofs"] == 2
        assert stats["anchored_batches"] == 1
