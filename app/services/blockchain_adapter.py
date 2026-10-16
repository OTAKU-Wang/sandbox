"""Blockchain Adapter Abstraction — multi-backend audit anchoring.

Supports:
1. FISCO BCOS — Chinese consortium chain (default)
2. AntChain —蚂蚁链 (Alibaba Cloud)
3. PostgreSQL append-only — tamper-evident log table (no chain dependency)

All backends implement the same BlockchainAdapter interface.
The adapter is selected via configuration; the rest of the system is backend-agnostic.
"""
from app.services.crypto_service import crypto_service
import logging
import os
import uuid

# Imported at module level so the model is registered on Base.metadata
# (create_all / parity) as soon as any API imports this adapter.
from app.models.chain_anchoring import ChainAnchoringRecord  # noqa: F401
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class ChainBackend(str, Enum):
    FISCO_BCOS = "fisco_bcos"
    ANT_CHAIN = "ant_chain"
    PG_APPEND_ONLY = "pg_append_only"


@dataclass
class AnchorRecord:
    """A record anchored to the blockchain."""
    anchor_id: str
    data_hash: str  # SM3 hash of the anchored data
    metadata: dict
    backend: ChainBackend
    tx_hash: str | None = None  # Blockchain transaction hash
    block_number: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confirmed: bool = False


@dataclass
class AnchorResult:
    """Result of anchoring data."""
    success: bool
    anchor: AnchorRecord | None = None
    error: str | None = None


class BlockchainAdapter(ABC):
    """Abstract interface for blockchain backends.

    PZ-03: every backend persists successful anchors to the
    chain_anchoring_records table (via set_db_session_factory) and can
    re-hydrate its in-memory anchor cache after a restart via
    hydrate_from_db, so verify() survives process restarts.
    """

    def set_db_session_factory(self, factory):
        """Set the async session factory for DB persistence."""
        self._db_session_factory = factory
        self._hydrated = False

    def _chain_record_rows(self, record: AnchorRecord) -> list[dict]:
        """Build chain_anchoring_records row values for an anchor.

        One row per anchored audit record (metadata['record_ids']); anchors
        without explicit record ids write a single row keyed by the anchor
        id. The full AnchorRecord payload travels in `receipt` so a fresh
        instance can re-hydrate and verify without the legacy table.
        """
        receipt = {
            "anchor_id": record.anchor_id,
            "data_hash": record.data_hash,
            "metadata": record.metadata,
            "timestamp": record.timestamp.isoformat(),
        }
        audit_ids = []
        for rid in record.metadata.get("record_ids") or []:
            try:
                audit_ids.append(uuid.UUID(str(rid)))
            except (ValueError, AttributeError, TypeError):
                continue
        if not audit_ids:
            audit_ids = [uuid.UUID(record.anchor_id)]
        return [
            {
                "id": uuid.uuid4(),
                "audit_record_id": aid,
                "backend": record.backend.value,
                "tx_hash": record.tx_hash,
                "block_height": record.block_number,
                "confirmed": record.confirmed,
                "receipt": receipt,
            }
            for aid in audit_ids
        ]

    async def _persist_chain_record(self, record: AnchorRecord) -> None:
        """Persist an anchor to chain_anchoring_records (PZ-03). Never raises."""
        factory = getattr(self, "_db_session_factory", None)
        if not factory:
            return
        try:
            from sqlalchemy import insert
            from app.models.chain_anchoring import ChainAnchoringRecord
            async with factory() as session:
                for values in self._chain_record_rows(record):
                    await session.execute(insert(ChainAnchoringRecord).values(**values))
                await session.commit()
        except Exception as e:
            logger.error(f"[Blockchain] Failed to persist chain record {record.anchor_id}: {e}")

    async def _load_chain_records(self) -> list[AnchorRecord]:
        """Load this backend's anchor records from chain_anchoring_records."""
        factory = getattr(self, "_db_session_factory", None)
        if not factory:
            return []
        try:
            from sqlalchemy import select
            from app.models.chain_anchoring import ChainAnchoringRecord
            async with factory() as session:
                result = await session.execute(
                    select(ChainAnchoringRecord)
                    .where(ChainAnchoringRecord.backend == self.get_backend_type().value)
                    .order_by(ChainAnchoringRecord.created_at, ChainAnchoringRecord.id)
                )
                rows = list(result.scalars().all())
        except Exception as e:
            logger.warning(f"[Blockchain] Failed to load chain records from DB: {e}")
            return []
        records: list[AnchorRecord] = []
        seen: set[str] = set()
        for row in rows:
            receipt = row.receipt
            if not receipt or not receipt.get("anchor_id"):
                continue
            anchor_id = receipt["anchor_id"]
            if anchor_id in seen:
                continue
            seen.add(anchor_id)
            try:
                timestamp = datetime.fromisoformat(receipt["timestamp"])
            except (KeyError, TypeError, ValueError):
                timestamp = row.created_at or datetime.now(timezone.utc)
            records.append(AnchorRecord(
                anchor_id=anchor_id,
                data_hash=receipt.get("data_hash", ""),
                metadata=receipt.get("metadata") or {},
                backend=ChainBackend(self.get_backend_type().value),
                tx_hash=row.tx_hash,
                block_number=row.block_height,
                confirmed=row.confirmed,
                timestamp=timestamp,
            ))
        records.sort(key=lambda r: r.timestamp)
        return records

    async def hydrate_from_db(self) -> int:
        """Re-hydrate the in-memory anchor cache from the DB (idempotent).

        Called lazily on anchor/verify/get_anchor and explicitly by the audit
        API, so verify() succeeds on a fresh instance (simulated restart).
        """
        factory = getattr(self, "_db_session_factory", None)
        if getattr(self, "_hydrated", False) or not factory:
            return len(getattr(self, "_anchors", {}) or {})
        self._hydrated = True
        loaded = await self._load_chain_records()
        anchors = getattr(self, "_anchors", None)
        if anchors is not None:
            for record in loaded:
                anchors.setdefault(record.anchor_id, record)
        if loaded:
            self._after_hydrate(loaded)
        return len(loaded)

    def _after_hydrate(self, loaded: list[AnchorRecord]) -> None:
        """Restore hash-chain bookkeeping (order + continuation hash) from the loaded records."""
        existing = set(getattr(self, "_chain", []))
        if hasattr(self, "_chain"):
            self._chain = list(self._chain) + [r.anchor_id for r in loaded if r.anchor_id not in existing]
        if not existing:
            last = loaded[-1]
            chain_hash = (last.metadata or {}).get("chain_hash") or last.tx_hash
            if chain_hash:
                self._last_hash = chain_hash

    @abstractmethod
    async def anchor(self, data: bytes, metadata: dict | None = None) -> AnchorResult:
        """Anchor data hash to the blockchain.

        Args:
            data: Raw data to hash and anchor
            metadata: Optional metadata to store alongside

        Returns:
            AnchorResult with transaction details
        """
        ...

    @abstractmethod
    async def verify(self, anchor_id: str, data: bytes) -> bool:
        """Verify that data matches a previously anchored hash.

        Args:
            anchor_id: The anchor record ID
            data: Data to verify against the anchored hash

        Returns:
            True if data hash matches the anchored hash
        """
        ...

    @abstractmethod
    async def get_anchor(self, anchor_id: str) -> AnchorRecord | None:
        """Retrieve an anchor record by ID."""
        ...

    @abstractmethod
    def get_backend_type(self) -> ChainBackend:
        """Return the backend type."""
        ...


class FISCOBCOSAdapter(BlockchainAdapter):
    """FISCO BCOS consortium chain adapter with local verifiable fallback.

    In production this adapter can be replaced by a Web3SDK-backed
    implementation. Without a node, it records a FISCO-shaped local hash chain
    with block numbers and transaction hashes so anchoring remains verifiable.
    """

    def __init__(self, chain_id: str = "1", group_id: int = 1):
        self._chain_id = chain_id
        self._group_id = group_id
        self._anchors: dict[str, AnchorRecord] = {}
        self._chain: list[str] = []
        self._last_hash = "fisco-genesis"
        self._db_session_factory = None
        self._hydrated = False

    async def anchor(self, data: bytes, metadata: dict | None = None) -> AnchorResult:
        await self.hydrate_from_db()
        data_hash = crypto_service.sm3_hash(data)
        anchor_id = str(uuid.uuid4())

        prev_hash = self._last_hash
        chain_hash = crypto_service.sm3_hash(
            f"fisco:{self._chain_id}:{self._group_id}:{prev_hash}:{data_hash}:{anchor_id}".encode()
        )
        tx_hash = f"0x{chain_hash}"

        record = AnchorRecord(
            anchor_id=anchor_id,
            data_hash=data_hash,
            metadata={**(metadata or {}), "prev_hash": prev_hash, "chain_hash": chain_hash},
            backend=ChainBackend.FISCO_BCOS,
            tx_hash=tx_hash,
            block_number=len(self._chain) + 1,
            confirmed=True,
        )
        self._anchors[anchor_id] = record
        self._chain.append(anchor_id)
        self._last_hash = chain_hash

        await self._persist_chain_record(record)

        logger.info(f"FISCO BCOS local anchor: {anchor_id} tx={tx_hash}")
        return AnchorResult(success=True, anchor=record)

    async def verify(self, anchor_id: str, data: bytes) -> bool:
        record = self._anchors.get(anchor_id)
        if not record:
            await self.hydrate_from_db()
            record = self._anchors.get(anchor_id)
        if not record:
            return False
        return crypto_service.sm3_hash(data) == record.data_hash

    async def get_anchor(self, anchor_id: str) -> AnchorRecord | None:
        record = self._anchors.get(anchor_id)
        if not record:
            await self.hydrate_from_db()
            record = self._anchors.get(anchor_id)
        return record

    def get_backend_type(self) -> ChainBackend:
        return ChainBackend.FISCO_BCOS

    async def verify_chain_integrity(self) -> bool:
        await self.hydrate_from_db()
        prev_hash = "fisco-genesis"
        for anchor_id in self._chain:
            record = self._anchors.get(anchor_id)
            if not record:
                return False
            expected = crypto_service.sm3_hash(
                f"fisco:{self._chain_id}:{self._group_id}:{prev_hash}:{record.data_hash}:{anchor_id}".encode()
            )
            if record.metadata.get("chain_hash") != expected or record.tx_hash != f"0x{expected}":
                return False
            prev_hash = expected
        return True


class AntChainAdapter(BlockchainAdapter):
    """AntChain adapter with local verifiable fallback.

    In production this can be replaced by an AntChain SDK adapter. The local
    implementation preserves transaction semantics with a signed hash chain.
    """

    def __init__(self, access_key: str = "", region: str = "cn-hangzhou"):
        self._access_key = access_key
        self._region = region
        self._anchors: dict[str, AnchorRecord] = {}
        self._chain: list[str] = []
        self._last_hash = "antchain-genesis"
        self._db_session_factory = None
        self._hydrated = False

    async def anchor(self, data: bytes, metadata: dict | None = None) -> AnchorResult:
        await self.hydrate_from_db()
        data_hash = crypto_service.sm3_hash(data)
        anchor_id = str(uuid.uuid4())

        prev_hash = self._last_hash
        chain_hash = crypto_service.sm3_hash(
            f"antchain:{self._region}:{prev_hash}:{data_hash}:{anchor_id}".encode()
        )
        tx_hash = f"ANT-{chain_hash[:32]}"

        record = AnchorRecord(
            anchor_id=anchor_id,
            data_hash=data_hash,
            metadata={**(metadata or {}), "prev_hash": prev_hash, "chain_hash": chain_hash},
            backend=ChainBackend.ANT_CHAIN,
            tx_hash=tx_hash,
            block_number=len(self._chain) + 1,
            confirmed=True,
        )
        self._anchors[anchor_id] = record
        self._chain.append(anchor_id)
        self._last_hash = chain_hash

        await self._persist_chain_record(record)

        logger.info(f"AntChain local anchor: {anchor_id} tx={tx_hash}")
        return AnchorResult(success=True, anchor=record)

    async def verify(self, anchor_id: str, data: bytes) -> bool:
        record = self._anchors.get(anchor_id)
        if not record:
            await self.hydrate_from_db()
            record = self._anchors.get(anchor_id)
        if not record:
            return False
        return crypto_service.sm3_hash(data) == record.data_hash

    async def get_anchor(self, anchor_id: str) -> AnchorRecord | None:
        record = self._anchors.get(anchor_id)
        if not record:
            await self.hydrate_from_db()
            record = self._anchors.get(anchor_id)
        return record

    def get_backend_type(self) -> ChainBackend:
        return ChainBackend.ANT_CHAIN

    async def verify_chain_integrity(self) -> bool:
        await self.hydrate_from_db()
        prev_hash = "antchain-genesis"
        for anchor_id in self._chain:
            record = self._anchors.get(anchor_id)
            if not record:
                return False
            expected = crypto_service.sm3_hash(
                f"antchain:{self._region}:{prev_hash}:{record.data_hash}:{anchor_id}".encode()
            )
            if record.metadata.get("chain_hash") != expected or record.tx_hash != f"ANT-{expected[:32]}":
                return False
            prev_hash = expected
        return True


class PGAppendOnlyAdapter(BlockchainAdapter):
    """PostgreSQL append-only log adapter.

    Uses a tamper-evident log table with hash chaining.
    Each entry's hash includes the previous entry's hash, making tampering detectable.
    No external blockchain dependency — suitable for development or environments
    without blockchain infrastructure.

    Anchor records are persisted to the blockchain_anchors table so they
    survive application restarts.
    """

    def __init__(self):
        self._anchors: dict[str, AnchorRecord] = {}  # In-memory cache
        self._chain: list[str] = []  # Ordered list of anchor_ids for hash chaining
        self._last_hash: str = "genesis"
        self._db_session_factory = None
        self._hydrated = False

    async def _get_last_hash_from_db(self) -> str:
        """Load the last chain hash from DB on startup."""
        if not self._db_session_factory:
            return self._last_hash
        try:
            from sqlalchemy import select, desc
            from app.models.blockchain_anchor import BlockchainAnchor
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(BlockchainAnchor)
                    .where(BlockchainAnchor.backend == ChainBackend.PG_APPEND_ONLY.value)
                    .order_by(desc(BlockchainAnchor.created_at))
                    .limit(1)
                )
                row = result.scalar_one_or_none()
                if row and row.tx_hash:
                    return row.tx_hash
        except Exception as e:
            logger.warning(f"[Blockchain] Failed to load last hash from DB: {e}")
        return self._last_hash

    async def _persist_anchor(self, record: AnchorRecord) -> None:
        """Persist an anchor to the legacy blockchain_anchors table and the
        PZ-03 chain_anchoring_records table in a single commit."""
        if not self._db_session_factory:
            return
        try:
            import json
            from sqlalchemy import insert
            from app.models.blockchain_anchor import BlockchainAnchor
            from app.models.chain_anchoring import ChainAnchoringRecord
            async with self._db_session_factory() as session:
                anchor_row = BlockchainAnchor(
                    id=uuid.UUID(record.anchor_id),
                    data_hash=record.data_hash,
                    backend=record.backend.value,
                    tx_hash=record.tx_hash,
                    block_number=record.block_number,
                    confirmed=record.confirmed,
                    metadata_json=json.dumps(record.metadata),
                )
                session.add(anchor_row)
                for values in self._chain_record_rows(record):
                    await session.execute(insert(ChainAnchoringRecord).values(**values))
                await session.commit()
        except Exception as e:
            logger.error(f"[Blockchain] Failed to persist anchor {record.anchor_id}: {e}")

    async def _load_anchor_from_db(self, anchor_id: str) -> AnchorRecord | None:
        """Load an anchor record from the database."""
        if not self._db_session_factory:
            return None
        try:
            import json
            from sqlalchemy import select
            from app.models.blockchain_anchor import BlockchainAnchor
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(BlockchainAnchor).where(BlockchainAnchor.id == uuid.UUID(anchor_id))
                )
                row = result.scalar_one_or_none()
                if not row:
                    return None
                metadata = json.loads(row.metadata_json) if row.metadata_json else {}
                return AnchorRecord(
                    anchor_id=str(row.id),
                    data_hash=row.data_hash,
                    metadata=metadata,
                    backend=ChainBackend(row.backend),
                    tx_hash=row.tx_hash,
                    block_number=row.block_number,
                    confirmed=row.confirmed,
                    timestamp=row.created_at,
                )
        except Exception as e:
            logger.warning(f"[Blockchain] Failed to load anchor {anchor_id} from DB: {e}")
            return None

    async def anchor(self, data: bytes, metadata: dict | None = None) -> AnchorResult:
        data_hash = crypto_service.sm3_hash(data)
        anchor_id = str(uuid.uuid4())

        # Load last hash from DB if chain is empty (first call after restart)
        if not self._chain and self._last_hash == "genesis":
            self._last_hash = await self._get_last_hash_from_db()

        # Hash chain: include previous hash
        chain_input = f"{self._last_hash}:{data_hash}:{anchor_id}"
        chain_hash = crypto_service.sm3_hash(chain_input.encode())

        record = AnchorRecord(
            anchor_id=anchor_id,
            data_hash=data_hash,
            metadata={**(metadata or {}), "chain_hash": chain_hash, "prev_hash": self._last_hash},
            backend=ChainBackend.PG_APPEND_ONLY,
            tx_hash=chain_hash,
            confirmed=True,  # PG entries are immediately confirmed
        )
        self._anchors[anchor_id] = record
        self._chain.append(anchor_id)
        self._last_hash = chain_hash

        # Persist to database
        await self._persist_anchor(record)

        logger.info(f"PG append-only anchor: {anchor_id} chain_hash={chain_hash[:16]}...")
        return AnchorResult(success=True, anchor=record)

    async def verify(self, anchor_id: str, data: bytes) -> bool:
        record = self._anchors.get(anchor_id)
        if not record:
            record = await self._load_anchor_from_db(anchor_id)
        if not record:
            return False
        return crypto_service.sm3_hash(data) == record.data_hash

    async def verify_chain_integrity(self) -> bool:
        """Verify the entire hash chain is intact.

        Loads all anchors from DB and verifies the hash chain.
        Returns True if chain has not been tampered with.
        """
        if not self._db_session_factory:
            # Fallback to in-memory check
            prev_hash = "genesis"
            for aid in self._chain:
                record = self._anchors.get(aid)
                if not record:
                    return False
                expected = crypto_service.sm3_hash(f"{prev_hash}:{record.data_hash}:{aid}".encode())
                if record.metadata.get("chain_hash") != expected:
                    return False
                prev_hash = expected
            return True

        try:
            from sqlalchemy import select, asc
            from app.models.blockchain_anchor import BlockchainAnchor
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(BlockchainAnchor)
                    .where(BlockchainAnchor.backend == ChainBackend.PG_APPEND_ONLY.value)
                    .order_by(asc(BlockchainAnchor.created_at))
                )
                rows = result.scalars().all()

            prev_hash = "genesis"
            for row in rows:
                aid = str(row.id)
                expected = crypto_service.sm3_hash(f"{prev_hash}:{row.data_hash}:{aid}".encode())
                if row.tx_hash != expected:
                    return False
                prev_hash = expected
            return True
        except Exception as e:
            logger.error(f"[Blockchain] Chain integrity verification failed: {e}")
            return False

    async def get_anchor(self, anchor_id: str) -> AnchorRecord | None:
        record = self._anchors.get(anchor_id)
        if record:
            return record
        return await self._load_anchor_from_db(anchor_id)

    def get_backend_type(self) -> ChainBackend:
        return ChainBackend.PG_APPEND_ONLY

    @property
    def chain_length(self) -> int:
        return len(self._chain)


class BlockchainAdapterFactory:
    """Factory for creating blockchain adapters."""

    _adapters: dict[ChainBackend, type] = {
        ChainBackend.FISCO_BCOS: FISCOBCOSAdapter,
        ChainBackend.ANT_CHAIN: AntChainAdapter,
        ChainBackend.PG_APPEND_ONLY: PGAppendOnlyAdapter,
    }

    @classmethod
    def create(cls, backend: ChainBackend, **kwargs) -> BlockchainAdapter:
        """Create an adapter for the specified backend."""
        adapter_cls = cls._adapters.get(backend)
        if not adapter_cls:
            raise ValueError(f"Unknown blockchain backend: {backend}")
        return adapter_cls(**kwargs)

    @classmethod
    def register(cls, backend: ChainBackend, adapter_cls: type) -> None:
        """Register a custom adapter class."""
        cls._adapters[backend] = adapter_cls


# Default singleton (PG append-only for dev)
_pg_adapter = PGAppendOnlyAdapter()
try:
    from app.core.database import async_session
    _pg_adapter.set_db_session_factory(async_session)
except Exception:
    pass  # DB not configured yet (e.g. during testing)
blockchain_adapter: BlockchainAdapter = _pg_adapter


# PZ-03 honest chain-anchoring state (SS-11 §3.1) — values disclosed in
# audit anchor/verify, compliance report, security posture and proof bundle.
_CHAIN_ENV_ALIASES = {
    "pg_append_only": "pg_append_only",
    "local_hash_chain": "pg_append_only",
    "fisco_bcos": "fisco_bcos",
    "fisco": "fisco_bcos",
    "fisco-bcos": "fisco_bcos",
    "ant_chain": "ant_chain",
    "antchain": "ant_chain",
    "ant-chain": "ant_chain",
}
_CHAIN_STATE = {
    ChainBackend.PG_APPEND_ONLY: "pg-log",
    ChainBackend.FISCO_BCOS: "fisco-sim",
    ChainBackend.ANT_CHAIN: "ant-sim",
}


def chain_anchoring_state(
    backend: ChainBackend | None = None,
    adapter: BlockchainAdapter | None = None,
) -> str:
    """Derive the honest chain-anchoring state from the active backend.

    pg-log          — real PG append-only tamper-evident log (not a chain)
    fisco-sim       — FISCO BCOS adapter, in-process simulation (disclosed)
    ant-sim         — AntChain adapter, in-process simulation (disclosed)
    not_configured  — a chain backend was requested via CDS_BLOCKCHAIN_BACKEND
                      but the active adapter is not that backend (or the value
                      is unknown), so no real/wired chain anchoring exists.

    `backend` is the backend an anchor actually used (preferred when known);
    `adapter` / the module singleton are the fallback sources.
    """
    if backend is None:
        adapter = adapter or blockchain_adapter
        backend = adapter.get_backend_type()
    requested = (os.environ.get("CDS_BLOCKCHAIN_BACKEND") or "").strip().lower()
    if requested:
        mapped = _CHAIN_ENV_ALIASES.get(requested)
        if mapped is None or mapped != backend.value:
            return "not_configured"
    return _CHAIN_STATE.get(backend, "not_configured")
