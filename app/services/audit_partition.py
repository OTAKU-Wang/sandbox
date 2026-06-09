"""Audit Table Partitioning + Bloom Filter Service.

Provides:
1. Time-based partitioning for audit_logs (monthly partitions)
2. Bloom filter for fast existence checks (did event X happen?)
3. Partition management (create, drop, archive old partitions)

PostgreSQL: Uses native declarative partitioning
SQLite: Uses filtered queries (partition-aware queries still work)
"""
from app.services.crypto_service import crypto_service
import logging
import math
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

from sqlalchemy import text, select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


@dataclass
class BloomFilter:
    """Simple Bloom filter for fast existence checks.

    Uses multiple hash functions to check if an element might be in a set.
    False positives are possible; false negatives are not.
    """
    size: int  # Number of bits
    hash_count: int  # Number of hash functions
    _bits: list[bool] = field(default_factory=list)

    def __post_init__(self):
        if not self._bits:
            self._bits = [False] * self.size

    def _hashes(self, item: str) -> list[int]:
        """Generate k hash positions for an item."""
        positions = []
        for i in range(self.hash_count):
            # Use SM3 with different seeds
            h = crypto_service.sm3_hash(f"{i}:{item}".encode())
            pos = int(h, 16) % self.size
            positions.append(pos)
        return positions

    def add(self, item: str) -> None:
        """Add an item to the bloom filter."""
        for pos in self._hashes(item):
            self._bits[pos] = True

    def might_contain(self, item: str) -> bool:
        """Check if an item might be in the set.

        Returns True if the item MIGHT be in the set (could be false positive).
        Returns False if the item is DEFINITELY NOT in the set.
        """
        return all(self._bits[pos] for pos in self._hashes(item))

    @property
    def fill_ratio(self) -> float:
        """Percentage of bits set to True."""
        return sum(self._bits) / self.size if self.size > 0 else 0.0

    @classmethod
    def optimal(cls, expected_items: int, fp_rate: float = 0.01) -> "BloomFilter":
        """Create an optimal bloom filter for expected item count and false positive rate.

        Args:
            expected_items: Expected number of items
            fp_rate: Desired false positive rate (default 1%)

        Returns:
            BloomFilter with optimal size and hash count
        """
        if expected_items <= 0:
            return cls(size=64, hash_count=3)

        # Optimal size: m = -(n * ln(p)) / (ln(2)^2)
        size = int(-(expected_items * math.log(fp_rate)) / (math.log(2) ** 2))
        size = max(64, size)  # Minimum 64 bits

        # Optimal hash count: k = (m/n) * ln(2)
        hash_count = int((size / expected_items) * math.log(2))
        hash_count = max(1, min(hash_count, 10))  # Between 1 and 10

        return cls(size=size, hash_count=hash_count)

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "size": self.size,
            "hash_count": self.hash_count,
            "bits": self._bits,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BloomFilter":
        """Deserialize from stored dict."""
        return cls(
            size=data["size"],
            hash_count=data["hash_count"],
            _bits=data["bits"],
        )


@dataclass
class PartitionInfo:
    """Information about an audit log partition."""
    name: str
    start_date: datetime
    end_date: datetime
    row_count: int | None = None
    size_bytes: int | None = None


class AuditPartitionManager:
    """Manages audit log partitioning and bloom filter indexing.

    Partitioning Strategy:
    - Monthly partitions: audit_logs_2026_01, audit_logs_2026_02, etc.
    - Each partition has its own bloom filter for fast existence checks
    - Old partitions can be archived or dropped

    Bloom Filter:
    - Used for fast checks like "did user X perform action Y?"
    - Stored per-partition for efficient lookups
    - False positive rate: 1% by default
    """

    def __init__(self):
        self._bloom_filters: dict[str, BloomFilter] = {}  # partition_name → bloom filter

    @staticmethod
    def _partition_range_from_name(partition_name: str) -> tuple[datetime, datetime] | None:
        """Parse audit_logs_YYYY_MM into an exact monthly UTC range."""
        parts = partition_name.split("_")
        if len(parts) < 4:
            return None
        try:
            year = int(parts[-2])
            month = int(parts[-1])
            start_date = datetime(year, month, 1, tzinfo=timezone.utc)
            if month == 12:
                end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)
            return start_date, end_date
        except ValueError:
            return None

    async def create_partition(
        self,
        db: AsyncSession,
        year: int,
        month: int,
    ) -> PartitionInfo:
        """Create a monthly partition for audit logs.

        Args:
            db: Database session
            year: Partition year
            month: Partition month (1-12)

        Returns:
            PartitionInfo for the created partition
        """
        partition_name = f"audit_logs_{year:04d}_{month:02d}"

        # Calculate date range
        start_date = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        # Check if using PostgreSQL (partitioning) or SQLite (no-op)
        is_postgres = await self._is_postgres(db)

        if is_postgres:
            # Create partition table
            await db.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {partition_name}
                PARTITION OF audit_logs
                FOR VALUES FROM ('{start_date.isoformat()}') TO ('{end_date.isoformat()}')
            """))
            logger.info(f"Created PostgreSQL partition: {partition_name}")
        else:
            # SQLite: no native partitioning, but we track partition metadata
            logger.info(f"SQLite mode: partition {partition_name} tracked in metadata")

        # Initialize bloom filter for this partition
        self._bloom_filters[partition_name] = BloomFilter.optimal(expected_items=100000)

        return PartitionInfo(
            name=partition_name,
            start_date=start_date,
            end_date=end_date,
        )

    async def ensure_current_partition(self, db: AsyncSession) -> PartitionInfo:
        """Ensure the current month's partition exists.

        Args:
            db: Database session

        Returns:
            PartitionInfo for the current month's partition
        """
        now = datetime.now(timezone.utc)
        return await self.create_partition(db, now.year, now.month)

    async def get_partition_for_date(self, date: datetime) -> str:
        """Get the partition name for a given date.

        Args:
            date: Date to get partition for

        Returns:
            Partition name (e.g., "audit_logs_2026_06")
        """
        return f"audit_logs_{date.year:04d}_{date.month:02d}"

    async def list_partitions(self, db: AsyncSession) -> list[PartitionInfo]:
        """List all existing partitions.

        Args:
            db: Database session

        Returns:
            List of PartitionInfo for existing partitions
        """
        is_postgres = await self._is_postgres(db)

        if is_postgres:
            result = await db.execute(text("""
                SELECT tablename FROM pg_tables
                WHERE tablename LIKE 'audit_logs_%'
                ORDER BY tablename
            """))
            partitions = []
            for row in result:
                name = row[0]
                date_range = self._partition_range_from_name(name)
                if not date_range:
                    continue
                start_date, end_date = date_range
                partitions.append(PartitionInfo(name=name, start_date=start_date, end_date=end_date))
            return partitions
        else:
            # SQLite: return tracked partitions
            partitions = []
            for name in sorted(self._bloom_filters.keys()):
                date_range = self._partition_range_from_name(name)
                if date_range:
                    start_date, end_date = date_range
                else:
                    start_date = datetime.min.replace(tzinfo=timezone.utc)
                    end_date = datetime.max.replace(tzinfo=timezone.utc)
                partitions.append(PartitionInfo(name=name, start_date=start_date, end_date=end_date))
            return partitions

    def add_to_bloom(self, partition_name: str, item: str) -> None:
        """Add an item to a partition's bloom filter.

        Args:
            partition_name: Partition name
            item: Item to add (e.g., "user:123:action:login")
        """
        if partition_name not in self._bloom_filters:
            self._bloom_filters[partition_name] = BloomFilter.optimal(expected_items=100000)
        self._bloom_filters[partition_name].add(item)

    def might_exist(self, partition_name: str, item: str) -> bool:
        """Check if an item might exist in a partition.

        Args:
            partition_name: Partition name
            item: Item to check

        Returns:
            True if the item MIGHT exist (could be false positive)
            False if the item is DEFINITELY NOT in the partition
        """
        bf = self._bloom_filters.get(partition_name)
        if not bf:
            return False
        return bf.might_contain(item)

    def get_bloom_stats(self, partition_name: str) -> dict | None:
        """Get bloom filter statistics for a partition.

        Args:
            partition_name: Partition name

        Returns:
            Dict with bloom filter stats, or None if partition not found
        """
        bf = self._bloom_filters.get(partition_name)
        if not bf:
            return None
        return {
            "size": bf.size,
            "hash_count": bf.hash_count,
            "fill_ratio": bf.fill_ratio,
        }

    async def drop_partition(self, db: AsyncSession, partition_name: str) -> bool:
        """Drop an old partition.

        Args:
            db: Database session
            partition_name: Partition name to drop

        Returns:
            True if partition was dropped, False if not found
        """
        is_postgres = await self._is_postgres(db)

        if is_postgres:
            await db.execute(text(f"DROP TABLE IF EXISTS {partition_name}"))
            logger.info(f"Dropped PostgreSQL partition: {partition_name}")

        # Remove bloom filter
        self._bloom_filters.pop(partition_name, None)
        return True

    async def _is_postgres(self, db: AsyncSession) -> bool:
        """Check if the database is PostgreSQL."""
        try:
            result = await db.execute(text("SELECT version()"))
            version = result.scalar()
            return version is not None and "PostgreSQL" in str(version)
        except Exception:
            return False


# Singleton
audit_partition_manager = AuditPartitionManager()
