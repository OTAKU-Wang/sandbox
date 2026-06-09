"""Cross-Space Catalog Sync — mirror data product catalogs between trusted spaces.

Synchronizes catalog entries from remote spaces into a local mirror table,
enabling cross-space data discovery without exposing raw data.

Sync modes:
- full: Replace all remote entries with fresh pull
- incremental: Only fetch entries updated since last sync

Conflict resolution:
- Remote entries are namespaced by space_id to avoid collisions
- Last-write-wins based on remote updated_at timestamp
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_product import DataProduct
from app.models.user import User, UserRole
from app.services.federation_connector import (
    federation_connector, FederationConnector, FederationTrust,
    FederationStatus, SpaceIdentity,
)

logger = logging.getLogger(__name__)
_REMOTE_PROVIDER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "cds:federated-remote-provider")


class SyncMode(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class SyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SyncResult:
    sync_id: str
    space_id: str
    mode: SyncMode
    status: SyncStatus
    entries_synced: int = 0
    entries_added: int = 0
    entries_updated: int = 0
    entries_removed: int = 0
    errors: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


@dataclass
class CatalogEntry:
    """Remote catalog entry for sync."""
    remote_id: str
    space_id: str
    name: str
    description: str
    product_type: str
    industry: str
    security_level: str
    row_count: int | None = None
    provider_name: str = ""
    remote_created_at: datetime | None = None
    remote_updated_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


class CatalogSyncService:
    """Cross-space catalog synchronization engine.

    Pulls catalog entries from remote spaces via federation gateway
    and mirrors them in the local catalog for cross-space discovery.
    """

    def __init__(self, connector: FederationConnector | None = None):
        self._connector = connector or federation_connector
        self._sync_history: list[SyncResult] = []

    @staticmethod
    def _remote_provider_id(space_id: str, provider_name: str = "") -> uuid.UUID:
        provider_key = provider_name.strip() or space_id
        return uuid.uuid5(_REMOTE_PROVIDER_NAMESPACE, f"{space_id}:{provider_key}")

    @staticmethod
    def _remote_provider_username(space_id: str, provider_name: str, provider_id: uuid.UUID) -> str:
        source = f"{space_id}-{provider_name or 'provider'}"
        safe = re.sub(r"[^a-zA-Z0-9_]+", "_", source).strip("_").lower()
        safe = safe[:43] or "remote_provider"
        return f"remote_{safe}_{provider_id.hex[:12]}"

    async def _ensure_remote_provider(
        self,
        db: AsyncSession,
        space_id: str,
        provider_name: str = "",
    ) -> uuid.UUID:
        """Create or reuse a stable local mirror identity for a remote provider."""
        provider_id = self._remote_provider_id(space_id, provider_name)
        result = await db.execute(select(User).where(User.id == provider_id))
        existing = result.scalar_one_or_none()
        if existing:
            if provider_name and existing.organization != provider_name:
                existing.organization = provider_name
            return provider_id

        username = self._remote_provider_username(space_id, provider_name, provider_id)
        user = User(
            id=provider_id,
            username=username,
            email=f"{provider_id.hex[:20]}@remote-provider.cds.local",
            hashed_password="!federated-remote-provider-no-login!",
            role=UserRole.DATA_PROVIDER,
            organization=provider_name or f"Remote space {space_id}",
            is_active=False,
        )
        db.add(user)
        return provider_id

    async def _build_remote_product(
        self,
        db: AsyncSession,
        space_id: str,
        entry: CatalogEntry,
    ) -> DataProduct:
        provider_id = await self._ensure_remote_provider(db, space_id, entry.provider_name)
        return DataProduct(
            name=f"[remote:{space_id}] {entry.name}",
            description=entry.description,
            product_type=entry.product_type,
            industry=entry.industry,
            security_level=entry.security_level,
            row_count=entry.row_count,
            status="published",
            provider_id=provider_id,
            data_schema={
                "remote": {
                    "space_id": space_id,
                    "remote_id": entry.remote_id,
                    "provider_name": entry.provider_name,
                    "metadata": entry.metadata,
                }
            },
        )

    async def sync_space(
        self,
        db: AsyncSession,
        space_id: str,
        mode: SyncMode = SyncMode.INCREMENTAL,
    ) -> SyncResult:
        """Sync catalog entries from a remote space.

        Args:
            db: Database session.
            space_id: Remote space ID to sync from.
            mode: full (replace all) or incremental (since last sync).

        Returns:
            SyncResult with sync statistics.
        """
        sync_id = f"sync-{uuid.uuid4().hex[:12]}"
        result = SyncResult(sync_id=sync_id, space_id=space_id, mode=mode, status=SyncStatus.RUNNING)

        trust = self._connector.get_trust_by_space(space_id)
        if not trust or trust.status != FederationStatus.ACTIVE:
            result.status = SyncStatus.FAILED
            result.errors.append(f"No active trust with space {space_id}")
            self._sync_history.append(result)
            return result

        try:
            # Fetch remote catalog via federation gateway
            remote_entries = await self._fetch_remote_catalog(trust, mode)
            result.entries_synced = len(remote_entries)

            if mode == SyncMode.FULL:
                added, updated, removed = await self._full_sync(db, space_id, remote_entries)
            else:
                added, updated = await self._incremental_sync(db, space_id, remote_entries)
                removed = 0

            result.entries_added = added
            result.entries_updated = updated
            result.entries_removed = removed
            result.status = SyncStatus.COMPLETED

            logger.info(
                f"Catalog sync {sync_id}: {space_id} mode={mode.value} "
                f"added={added} updated={updated} removed={removed}"
            )

        except Exception as e:
            result.status = SyncStatus.FAILED
            result.errors.append(str(e))
            logger.error(f"Catalog sync {sync_id} failed: {e}")

        result.completed_at = datetime.now(timezone.utc)
        self._sync_history.append(result)
        return result

    async def _fetch_remote_catalog(
        self, trust: FederationTrust, mode: SyncMode,
    ) -> list[CatalogEntry]:
        """Fetch catalog entries from remote space via federation gateway."""
        response = self._connector.send_request(
            trust, "read_catalog", "/catalog",
            payload={"sync_mode": mode.value},
        )

        if response.status_code != 200:
            raise RuntimeError(f"Remote catalog fetch failed: {response.error}")

        entries = []
        raw_items = response.data.get("products", []) if response.data else []
        for item in raw_items:
            entries.append(CatalogEntry(
                remote_id=item.get("id", ""),
                space_id=trust.remote_space.space_id,
                name=item.get("name", ""),
                description=item.get("description", ""),
                product_type=item.get("product_type", "unknown"),
                industry=item.get("industry", ""),
                security_level=item.get("security_level", "L1"),
                row_count=item.get("row_count"),
                provider_name=item.get("provider_name", ""),
                metadata=item.get("metadata", {}),
            ))

        return entries

    async def _full_sync(
        self, db: AsyncSession, space_id: str, entries: list[CatalogEntry],
    ) -> tuple[int, int, int]:
        """Full sync: replace all remote entries for this space."""
        # Remove existing entries from this space
        existing = await db.execute(
            select(DataProduct).where(
                DataProduct.name.like(f"[remote:{space_id}]%")
            )
        )
        existing_count = len(list(existing.scalars().all()))

        await db.execute(
            delete(DataProduct).where(
                DataProduct.name.like(f"[remote:{space_id}]%")
            )
        )

        # Insert fresh entries
        added = 0
        for entry in entries:
            product = await self._build_remote_product(db, space_id, entry)
            db.add(product)
            added += 1

        await db.flush()
        return added, 0, existing_count

    async def _incremental_sync(
        self, db: AsyncSession, space_id: str, entries: list[CatalogEntry],
    ) -> tuple[int, int]:
        """Incremental sync: only add/update changed entries."""
        # Get existing remote entries for this space
        existing_result = await db.execute(
            select(DataProduct).where(
                DataProduct.name.like(f"[remote:{space_id}]%")
            )
        )
        existing_map: dict[str, DataProduct] = {}
        for p in existing_result.scalars().all():
            # Extract original name from "[remote:space_id] name"
            original_name = p.name.split("] ", 1)[1] if "] " in p.name else p.name
            existing_map[original_name] = p

        added = 0
        updated = 0

        for entry in entries:
            if entry.name in existing_map:
                # Update existing
                product = existing_map[entry.name]
                product.description = entry.description
                product.product_type = entry.product_type
                product.industry = entry.industry
                product.security_level = entry.security_level
                product.row_count = entry.row_count
                product.provider_id = await self._ensure_remote_provider(
                    db, space_id, entry.provider_name
                )
                product.data_schema = {
                    "remote": {
                        "space_id": space_id,
                        "remote_id": entry.remote_id,
                        "provider_name": entry.provider_name,
                        "metadata": entry.metadata,
                    }
                }
                updated += 1
            else:
                # Add new
                product = await self._build_remote_product(db, space_id, entry)
                db.add(product)
                added += 1

        await db.flush()
        return added, updated

    async def sync_all_active(self, db: AsyncSession) -> list[SyncResult]:
        """Sync catalogs from all active trusted spaces."""
        trusts = self._connector.list_trusts(status=FederationStatus.ACTIVE)
        results = []
        for trust in trusts:
            if "read_catalog" in trust.allowed_operations:
                result = await self.sync_space(db, trust.remote_space.space_id)
                results.append(result)
        return results

    def get_sync_history(self, space_id: str | None = None) -> list[SyncResult]:
        """Get sync history, optionally filtered by space."""
        if space_id:
            return [r for r in self._sync_history if r.space_id == space_id]
        return list(self._sync_history)


# Singleton
catalog_sync = CatalogSyncService()
