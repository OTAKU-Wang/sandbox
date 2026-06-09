"""Tests for Audit Partition Manager — Bloom Filter + Partition Management."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.audit_partition import (
    BloomFilter, AuditPartitionManager, PartitionInfo,
    audit_partition_manager,
)


# ── BloomFilter ────────────────────────────────────────────────────

class TestBloomFilterBasic:
    def test_create_default(self):
        bf = BloomFilter(size=128, hash_count=3)
        assert bf.size == 128
        assert bf.hash_count == 3
        assert bf.fill_ratio == 0.0

    def test_add_and_might_contain(self):
        bf = BloomFilter(size=256, hash_count=3)
        bf.add("hello")
        assert bf.might_contain("hello") is True

    def test_false_negative_impossible(self):
        """Items added are always detected."""
        bf = BloomFilter(size=1024, hash_count=5)
        items = [f"item-{i}" for i in range(100)]
        for item in items:
            bf.add(item)
        for item in items:
            assert bf.might_contain(item) is True

    def test_not_added_might_not_contain(self):
        """Items not added should mostly not be detected (low false positive)."""
        bf = BloomFilter(size=10000, hash_count=5)
        bf.add("exists")
        # Check many items that weren't added — most should return False
        false_positives = sum(1 for i in range(1000) if bf.might_contain(f"nope-{i}"))
        # With 10000 bits and 5 hashes, FP rate should be very low
        assert false_positives < 50  # generous bound

    def test_fill_ratio_increases(self):
        bf = BloomFilter(size=256, hash_count=3)
        assert bf.fill_ratio == 0.0
        bf.add("a")
        assert bf.fill_ratio > 0.0
        ratio_after_1 = bf.fill_ratio
        bf.add("b")
        bf.add("c")
        assert bf.fill_ratio > ratio_after_1


class TestBloomFilterOptimal:
    def test_optimal_default(self):
        bf = BloomFilter.optimal(expected_items=10000)
        assert bf.size >= 64
        assert 1 <= bf.hash_count <= 10

    def test_optimal_small_items(self):
        bf = BloomFilter.optimal(expected_items=100)
        assert bf.size >= 64
        assert bf.hash_count >= 1

    def test_optimal_zero_items(self):
        bf = BloomFilter.optimal(expected_items=0)
        assert bf.size == 64
        assert bf.hash_count == 3

    def test_optimal_large_items(self):
        bf = BloomFilter.optimal(expected_items=1000000, fp_rate=0.001)
        assert bf.size > 10000
        assert bf.hash_count >= 1

    def test_optimal_fp_rate_affects_size(self):
        bf_loose = BloomFilter.optimal(expected_items=10000, fp_rate=0.1)
        bf_tight = BloomFilter.optimal(expected_items=10000, fp_rate=0.001)
        assert bf_tight.size > bf_loose.size


class TestBloomFilterSerialization:
    def test_to_dict(self):
        bf = BloomFilter(size=128, hash_count=3)
        bf.add("test")
        d = bf.to_dict()
        assert d["size"] == 128
        assert d["hash_count"] == 3
        assert len(d["bits"]) == 128
        assert True in d["bits"]

    def test_from_dict(self):
        bf = BloomFilter(size=128, hash_count=3)
        bf.add("test")
        d = bf.to_dict()
        bf2 = BloomFilter.from_dict(d)
        assert bf2.size == bf.size
        assert bf2.hash_count == bf.hash_count
        assert bf2.might_contain("test") is True

    def test_roundtrip_preserves_data(self):
        bf = BloomFilter.optimal(expected_items=1000)
        items = [f"key-{i}" for i in range(50)]
        for item in items:
            bf.add(item)
        bf2 = BloomFilter.from_dict(bf.to_dict())
        for item in items:
            assert bf2.might_contain(item) is True


# ── AuditPartitionManager ─────────────────────────────────────────

class TestPartitionNaming:
    def test_get_partition_for_date(self):
        mgr = AuditPartitionManager()
        dt = datetime(2026, 6, 15, tzinfo=timezone.utc)
        import asyncio
        name = asyncio.get_event_loop().run_until_complete(mgr.get_partition_for_date(dt))
        assert name == "audit_logs_2026_06"

    def test_get_partition_january(self):
        mgr = AuditPartitionManager()
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        import asyncio
        name = asyncio.get_event_loop().run_until_complete(mgr.get_partition_for_date(dt))
        assert name == "audit_logs_2026_01"

    def test_get_partition_december(self):
        mgr = AuditPartitionManager()
        dt = datetime(2026, 12, 31, tzinfo=timezone.utc)
        import asyncio
        name = asyncio.get_event_loop().run_until_complete(mgr.get_partition_for_date(dt))
        assert name == "audit_logs_2026_12"


class TestPartitionCreation:
    @pytest.mark.asyncio
    async def test_create_partition(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        info = await mgr.create_partition(db_session, 2026, 6)
        assert info.name == "audit_logs_2026_06"
        assert info.start_date == datetime(2026, 6, 1, tzinfo=timezone.utc)
        assert info.end_date == datetime(2026, 7, 1, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_create_december_partition(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        info = await mgr.create_partition(db_session, 2026, 12)
        assert info.name == "audit_logs_2026_12"
        assert info.end_date == datetime(2027, 1, 1, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_create_partition_initializes_bloom(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        await mgr.create_partition(db_session, 2026, 3)
        stats = mgr.get_bloom_stats("audit_logs_2026_03")
        assert stats is not None
        assert stats["size"] >= 64
        assert stats["hash_count"] >= 1
        assert stats["fill_ratio"] == 0.0

    @pytest.mark.asyncio
    async def test_ensure_current_partition(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        info = await mgr.ensure_current_partition(db_session)
        now = datetime.now(timezone.utc)
        expected = f"audit_logs_{now.year:04d}_{now.month:02d}"
        assert info.name == expected


class TestBloomOperations:
    def test_add_to_bloom_creates_filter(self):
        mgr = AuditPartitionManager()
        mgr.add_to_bloom("audit_logs_2026_06", "user:123:action:login")
        assert mgr.might_exist("audit_logs_2026_06", "user:123:action:login") is True

    def test_might_exist_missing_partition(self):
        mgr = AuditPartitionManager()
        assert mgr.might_exist("nonexistent", "item") is False

    def test_add_multiple_items(self):
        mgr = AuditPartitionManager()
        items = [f"user:{i}:action:login" for i in range(100)]
        for item in items:
            mgr.add_to_bloom("p1", item)
        for item in items:
            assert mgr.might_exist("p1", item) is True

    def test_bloom_stats_none_for_missing(self):
        mgr = AuditPartitionManager()
        assert mgr.get_bloom_stats("nonexistent") is None

    def test_bloom_stats_fill_ratio(self):
        mgr = AuditPartitionManager()
        mgr.add_to_bloom("p1", "item1")
        stats = mgr.get_bloom_stats("p1")
        assert stats is not None
        assert stats["fill_ratio"] > 0.0


class TestPartitionListAndDrop:
    @pytest.mark.asyncio
    async def test_list_partitions_empty(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        partitions = await mgr.list_partitions(db_session)
        assert len(partitions) == 0

    @pytest.mark.asyncio
    async def test_list_partitions_after_create(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        await mgr.create_partition(db_session, 2026, 1)
        await mgr.create_partition(db_session, 2026, 2)
        partitions = await mgr.list_partitions(db_session)
        by_name = {p.name: p for p in partitions}
        names = set(by_name)
        assert "audit_logs_2026_01" in names
        assert "audit_logs_2026_02" in names
        assert by_name["audit_logs_2026_01"].start_date == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert by_name["audit_logs_2026_01"].end_date == datetime(2026, 2, 1, tzinfo=timezone.utc)
        assert by_name["audit_logs_2026_02"].start_date == datetime(2026, 2, 1, tzinfo=timezone.utc)
        assert by_name["audit_logs_2026_02"].end_date == datetime(2026, 3, 1, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_drop_partition(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        await mgr.create_partition(db_session, 2026, 6)
        assert mgr.get_bloom_stats("audit_logs_2026_06") is not None
        result = await mgr.drop_partition(db_session, "audit_logs_2026_06")
        assert result is True
        assert mgr.get_bloom_stats("audit_logs_2026_06") is None

    @pytest.mark.asyncio
    async def test_drop_nonexistent_partition(self, db_session: AsyncSession):
        mgr = AuditPartitionManager()
        result = await mgr.drop_partition(db_session, "nonexistent")
        assert result is True  # drop is idempotent


class TestPartitionInfo:
    def test_partition_info_fields(self):
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 1, tzinfo=timezone.utc)
        info = PartitionInfo(name="audit_logs_2026_06", start_date=start, end_date=end)
        assert info.name == "audit_logs_2026_06"
        assert info.row_count is None
        assert info.size_bytes is None
