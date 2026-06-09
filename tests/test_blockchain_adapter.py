"""Tests for Blockchain Adapter — multi-backend audit anchoring."""
import pytest
import pytest_asyncio
from app.services.crypto_service import crypto_service

from app.services.blockchain_adapter import (
    BlockchainAdapterFactory, ChainBackend,
    FISCOBCOSAdapter, AntChainAdapter, PGAppendOnlyAdapter,
    AnchorRecord, AnchorResult,
)


@pytest.fixture
def fisco():
    return FISCOBCOSAdapter()


@pytest.fixture
def antchain():
    return AntChainAdapter()


@pytest.fixture
def pg():
    return PGAppendOnlyAdapter()


# ── Factory ───────────────────────────────────────────────────────

class TestFactory:
    def test_create_fisco(self):
        adapter = BlockchainAdapterFactory.create(ChainBackend.FISCO_BCOS)
        assert isinstance(adapter, FISCOBCOSAdapter)

    def test_create_antchain(self):
        adapter = BlockchainAdapterFactory.create(ChainBackend.ANT_CHAIN)
        assert isinstance(adapter, AntChainAdapter)

    def test_create_pg(self):
        adapter = BlockchainAdapterFactory.create(ChainBackend.PG_APPEND_ONLY)
        assert isinstance(adapter, PGAppendOnlyAdapter)

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError):
            BlockchainAdapterFactory.create("unknown_backend")


# ── FISCO BCOS ────────────────────────────────────────────────────

class TestFISCOBCOS:
    @pytest.mark.asyncio
    async def test_anchor(self, fisco):
        result = await fisco.anchor(b"test data")
        assert result.success is True
        assert result.anchor is not None
        assert result.anchor.tx_hash.startswith("0x")
        assert result.anchor.backend == ChainBackend.FISCO_BCOS

    @pytest.mark.asyncio
    async def test_verify_valid(self, fisco):
        data = b"verify me"
        result = await fisco.anchor(data)
        assert await fisco.verify(result.anchor.anchor_id, data) is True

    @pytest.mark.asyncio
    async def test_verify_invalid(self, fisco):
        result = await fisco.anchor(b"original")
        assert await fisco.verify(result.anchor.anchor_id, b"tampered") is False

    @pytest.mark.asyncio
    async def test_verify_nonexistent(self, fisco):
        assert await fisco.verify("nonexistent", b"data") is False

    @pytest.mark.asyncio
    async def test_get_anchor(self, fisco):
        result = await fisco.anchor(b"data")
        record = await fisco.get_anchor(result.anchor.anchor_id)
        assert record is not None
        assert record.anchor_id == result.anchor.anchor_id

    @pytest.mark.asyncio
    async def test_get_anchor_nonexistent(self, fisco):
        assert await fisco.get_anchor("nonexistent") is None

    def test_backend_type(self, fisco):
        assert fisco.get_backend_type() == ChainBackend.FISCO_BCOS

    @pytest.mark.asyncio
    async def test_metadata(self, fisco):
        result = await fisco.anchor(b"data", metadata={"key": "value"})
        assert result.anchor.metadata["key"] == "value"

    @pytest.mark.asyncio
    async def test_data_hash_is_sm3(self, fisco):
        data = b"hash check"
        result = await fisco.anchor(data)
        expected = crypto_service.sm3_hash(data)
        assert result.anchor.data_hash == expected


# ── AntChain ──────────────────────────────────────────────────────

class TestAntChain:
    @pytest.mark.asyncio
    async def test_anchor(self, antchain):
        result = await antchain.anchor(b"test data")
        assert result.success is True
        assert result.anchor.tx_hash.startswith("ANT-")

    @pytest.mark.asyncio
    async def test_verify(self, antchain):
        data = b"verify me"
        result = await antchain.anchor(data)
        assert await antchain.verify(result.anchor.anchor_id, data) is True

    @pytest.mark.asyncio
    async def test_verify_tampered(self, antchain):
        result = await antchain.anchor(b"original")
        assert await antchain.verify(result.anchor.anchor_id, b"tampered") is False

    def test_backend_type(self, antchain):
        assert antchain.get_backend_type() == ChainBackend.ANT_CHAIN


# ── PG Append-Only ────────────────────────────────────────────────

class TestPGAppendOnly:
    @pytest.mark.asyncio
    async def test_anchor(self, pg):
        result = await pg.anchor(b"test data")
        assert result.success is True
        assert result.anchor.confirmed is True  # PG is immediately confirmed

    @pytest.mark.asyncio
    async def test_verify(self, pg):
        data = b"verify me"
        result = await pg.anchor(data)
        assert await pg.verify(result.anchor.anchor_id, data) is True

    @pytest.mark.asyncio
    async def test_chain_integrity(self, pg):
        for i in range(5):
            await pg.anchor(f"data-{i}".encode())
        assert await pg.verify_chain_integrity() is True

    @pytest.mark.asyncio
    async def test_chain_length(self, pg):
        for i in range(3):
            await pg.anchor(f"data-{i}".encode())
        assert pg.chain_length == 3

    @pytest.mark.asyncio
    async def test_chain_hash_chaining(self, pg):
        r1 = await pg.anchor(b"first")
        r2 = await pg.anchor(b"second")
        # Second entry should reference first's hash
        assert r2.anchor.metadata["prev_hash"] == r1.anchor.tx_hash

    @pytest.mark.asyncio
    async def test_empty_chain_integrity(self, pg):
        assert await pg.verify_chain_integrity() is True

    def test_backend_type(self, pg):
        assert pg.get_backend_type() == ChainBackend.PG_APPEND_ONLY


# ── AnchorRecord ──────────────────────────────────────────────────

class TestAnchorRecord:
    @pytest.mark.asyncio
    async def test_record_fields(self, fisco):
        result = await fisco.anchor(b"data", metadata={"k": "v"})
        record = result.anchor
        assert isinstance(record, AnchorRecord)
        assert record.data_hash == crypto_service.sm3_hash(b"data")
        assert record.backend == ChainBackend.FISCO_BCOS
        assert record.metadata["k"] == "v"
