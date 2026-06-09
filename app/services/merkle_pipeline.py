"""Merkle Batch Pipeline — automatic batch anchoring via Redis Stream.

Collects audit events from a Redis Stream, batches them into Merkle trees,
and anchors the batch roots to the blockchain adapter.

Flow:
  audit_event → XADD to Redis Stream → Pipeline worker XREAD →
  batch by size/time → build Merkle tree → anchor root → store proofs

Configuration:
  - MERKLE_BATCH_SIZE: max events per batch (default 100)
  - MERKLE_BATCH_INTERVAL: max seconds between batches (default 60)
"""
import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STREAM_KEY = "cds:merkle:events"
BATCH_SIZE = 100
BATCH_INTERVAL = 60  # seconds
CONSUMER_GROUP = "merkle_pipeline"
CONSUMER_NAME = "merkle_worker_1"


@dataclass
class BatchResult:
    """Result of processing a Merkle batch."""
    batch_id: str
    root_hash: str
    leaf_count: int
    anchor_tx_hash: str | None
    anchored: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MerkleBatchPipeline:
    """Automatic Merkle batch pipeline backed by Redis Stream.

    Collects individual audit events, batches them, builds a Merkle tree,
    and anchors the root to the blockchain. Proof data is stored for
    third-party verification.
    """

    def __init__(self, batch_size: int = BATCH_SIZE,
                 batch_interval: float = BATCH_INTERVAL):
        self._batch_size = batch_size
        self._batch_interval = batch_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._batches: dict[str, BatchResult] = {}
        self._proofs: dict[str, dict] = {}  # batch_id → {leaf_index: proof}

    async def push_event(self, event_data: bytes, metadata: dict | None = None) -> str:
        """Push an audit event to the Redis Stream for batch processing.

        Returns the Redis Stream message ID.
        """
        try:
            from app.core.redis import get_redis
            redis = await get_redis()
            entry = {
                "data": event_data.hex(),
                "metadata": json.dumps(metadata or {}),
            }
            msg_id = await redis.xadd(STREAM_KEY, entry, maxlen=10000)
            logger.debug("[MerklePipeline] Pushed event to stream: %s", msg_id)
            return msg_id
        except Exception as e:
            logger.warning("[MerklePipeline] Redis push failed, event lost: %s", e)
            return ""

    async def start(self):
        """Start the pipeline worker."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._worker_loop())
        logger.info("[MerklePipeline] Started (batch_size=%d, interval=%ds)",
                     self._batch_size, self._batch_interval)

    async def stop(self):
        """Stop the pipeline worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[MerklePipeline] Stopped")

    async def _worker_loop(self):
        """Main worker loop: read from Redis Stream, batch, anchor."""
        from app.services.merkle_service import merkle_service
        from app.services.blockchain_adapter import blockchain_adapter

        while self._running:
            try:
                events = await self._read_batch()
                if not events:
                    await asyncio.sleep(self._batch_interval)
                    continue

                batch_result = await self._process_batch(events, merkle_service, blockchain_adapter)
                if batch_result:
                    self._batches[batch_result.batch_id] = batch_result
                    logger.info("[MerklePipeline] Batch %s: %d leaves, root=%s, anchored=%s",
                                batch_result.batch_id, batch_result.leaf_count,
                                batch_result.root_hash[:16], batch_result.anchored)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[MerklePipeline] Worker error: %s", e)
                await asyncio.sleep(self._batch_interval)

    async def _read_batch(self) -> list[tuple[str, bytes, dict]]:
        """Read a batch of events from Redis Stream.

        Returns list of (msg_id, data_bytes, metadata).
        """
        try:
            from app.core.redis import get_redis
            redis = await get_redis()

            # Try to create consumer group (ignore if exists)
            try:
                await redis.xgroup_create(STREAM_KEY, CONSUMER_GROUP, id="0", mkstream=True)
            except Exception:
                pass  # Group already exists

            # Read with count and block
            results = await redis.xreadgroup(
                CONSUMER_GROUP, CONSUMER_NAME,
                {STREAM_KEY: ">"},
                count=self._batch_size,
                block=int(self._batch_interval * 1000),
            )

            events = []
            for stream, messages in results:
                for msg_id, fields in messages:
                    data = bytes.fromhex(fields.get("data", ""))
                    metadata = json.loads(fields.get("metadata", "{}"))
                    events.append((msg_id, data, metadata))
                    # Acknowledge the message
                    await redis.xack(STREAM_KEY, CONSUMER_GROUP, msg_id)

            return events
        except Exception as e:
            logger.warning("[MerklePipeline] Redis read failed: %s", e)
            return []

    async def _process_batch(self, events: list[tuple[str, bytes, dict]],
                              merkle_service, blockchain_adapter) -> BatchResult | None:
        """Process a batch: build Merkle tree, anchor root, store proofs."""
        if not events:
            return None

        batch_id = f"batch-{uuid.uuid4().hex[:12]}"
        event_data = [data for _, data, _ in events]

        # Build Merkle batch
        batch = merkle_service.build_batch(event_data)
        leaves = batch.leaves

        # Generate and store proofs for each leaf
        proofs = {}
        for i in range(len(leaves)):
            proof = merkle_service.generate_proof(leaves, i)
            if proof:
                proofs[i] = {
                    "leaf_index": proof.leaf_index,
                    "leaf_hash": proof.leaf_hash,
                    "proof_path": proof.proof_path,
                    "root_hash": proof.root_hash,
                }
        self._proofs[batch_id] = proofs

        # Anchor root to blockchain
        anchor_tx_hash = None
        anchored = False
        try:
            result = await blockchain_adapter.anchor(
                batch.root_hash.encode(),
                {"batch_id": batch_id, "leaf_count": len(leaves)},
            )
            if result.success and result.anchor:
                anchor_tx_hash = result.anchor.tx_hash
                anchored = True
        except Exception as e:
            logger.error("[MerklePipeline] Anchor failed for batch %s: %s", batch_id, e)

        return BatchResult(
            batch_id=batch_id,
            root_hash=batch.root_hash,
            leaf_count=len(leaves),
            anchor_tx_hash=anchor_tx_hash,
            anchored=anchored,
        )

    def get_batch(self, batch_id: str) -> BatchResult | None:
        """Get a batch result by ID."""
        return self._batches.get(batch_id)

    def get_proof(self, batch_id: str, leaf_index: int) -> dict | None:
        """Get a Merkle proof for a specific leaf in a batch."""
        proofs = self._proofs.get(batch_id)
        if not proofs:
            return None
        return proofs.get(leaf_index)

    def get_stats(self) -> dict:
        """Get pipeline statistics."""
        return {
            "total_batches": len(self._batches),
            "total_proofs": sum(len(p) for p in self._proofs.values()),
            "anchored_batches": sum(1 for b in self._batches.values() if b.anchored),
            "running": self._running,
            "batch_size": self._batch_size,
            "batch_interval": self._batch_interval,
        }


# Singleton
merkle_pipeline = MerkleBatchPipeline()
