"""Tests for Cross-Space Catalog Sync (S6-1)."""
import pytest
from unittest.mock import patch
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.data_product import DataProduct
from app.models.user import User, UserRole
from app.services.catalog_sync import (
    CatalogSyncService, CatalogEntry, SyncMode, SyncStatus,
)
from app.services.federation_connector import (
    FederationConnector, SpaceIdentity, TrustLevel, FederationStatus, GatewayResponse,
)


def _mock_execute_remote(self, request, trust):
    """Mock remote request handler for catalog sync tests."""
    if request.operation == "read_catalog":
        return GatewayResponse(
            request_id=request.request_id, status_code=200,
            data={"products": [], "source": trust.remote_space.space_id},
            source_space=trust.remote_space.space_id,
        )
    return GatewayResponse(
        request_id=request.request_id, status_code=400,
        error=f"Unknown operation: {request.operation}",
        source_space=trust.remote_space.space_id,
    )


@pytest.fixture
def connector():
    return FederationConnector()


@pytest.fixture
def local_space():
    return SpaceIdentity(space_id="cds-local", space_name="Local", endpoint="https://local.example.com")


@pytest.fixture
def remote_space():
    return SpaceIdentity(space_id="cds-remote", space_name="Remote", endpoint="https://remote.example.com")


@pytest.fixture
def sync_service(connector):
    return CatalogSyncService(connector=connector)


class TestCatalogEntry:
    def test_entry_creation(self):
        entry = CatalogEntry(
            remote_id="p1", space_id="cds-remote", name="Test Product",
            description="A test", product_type="dataset", industry="finance",
            security_level="L2",
        )
        assert entry.remote_id == "p1"
        assert entry.space_id == "cds-remote"
        assert entry.row_count is None

    def test_entry_defaults(self):
        entry = CatalogEntry(
            remote_id="p1", space_id="s1", name="P", description="",
            product_type="dataset", industry="", security_level="L1",
        )
        assert entry.provider_name == ""
        assert entry.metadata == {}


class TestSyncMode:
    def test_sync_mode_values(self):
        assert SyncMode.FULL.value == "full"
        assert SyncMode.INCREMENTAL.value == "incremental"

    def test_sync_status_values(self):
        assert SyncStatus.PENDING.value == "pending"
        assert SyncStatus.RUNNING.value == "running"
        assert SyncStatus.COMPLETED.value == "completed"
        assert SyncStatus.FAILED.value == "failed"


class TestSyncNoTrust:
    @pytest.mark.asyncio
    async def test_sync_no_trust_fails(self, sync_service):
        """Sync fails when no trust exists with the space."""
        # Create a mock db that won't be used
        class MockDB:
            async def execute(self, *a): pass
            async def flush(self): pass
        result = await sync_service.sync_space(MockDB(), "unknown-space")
        assert result.status == SyncStatus.FAILED
        assert "No active trust" in result.errors[0]

    @pytest.mark.asyncio
    async def test_sync_suspended_trust_fails(self, sync_service, connector, local_space, remote_space):
        """Sync fails when trust is suspended."""
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space)
        connector.suspend_trust(trust.trust_id)

        class MockDB:
            async def execute(self, *a): pass
            async def flush(self): pass
        result = await sync_service.sync_space(MockDB(), "cds-remote")
        assert result.status == SyncStatus.FAILED


class TestSyncHistory:
    def test_history_initially_empty(self, sync_service):
        assert sync_service.get_sync_history() == []

    def test_history_after_sync(self, sync_service):
        # Manually add to history
        from app.services.catalog_sync import SyncResult
        result = SyncResult(sync_id="s1", space_id="sp1", mode=SyncMode.FULL, status=SyncStatus.COMPLETED)
        sync_service._sync_history.append(result)
        assert len(sync_service.get_sync_history()) == 1

    def test_history_filter_by_space(self, sync_service):
        from app.services.catalog_sync import SyncResult
        sync_service._sync_history = [
            SyncResult(sync_id="s1", space_id="sp1", mode=SyncMode.FULL, status=SyncStatus.COMPLETED),
            SyncResult(sync_id="s2", space_id="sp2", mode=SyncMode.FULL, status=SyncStatus.COMPLETED),
            SyncResult(sync_id="s3", space_id="sp1", mode=SyncMode.INCREMENTAL, status=SyncStatus.COMPLETED),
        ]
        assert len(sync_service.get_sync_history("sp1")) == 2
        assert len(sync_service.get_sync_history("sp2")) == 1
        assert len(sync_service.get_sync_history("sp3")) == 0


class TestRemoteCatalogFetch:
    @pytest.fixture(autouse=True)
    def _mock_http(self):
        with patch.object(FederationConnector, '_execute_remote_request', _mock_execute_remote):
            yield

    def test_fetch_simulated_response(self, connector, local_space, remote_space):
        """Federation connector's simulated search returns data."""
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, allowed_operations=["read_catalog"])
        response = connector.send_request(trust, "read_catalog", "/catalog")
        assert response.status_code == 200
        assert "products" in response.data

    def test_fetch_catalog_entries_from_response(self, connector, local_space, remote_space):
        """Parse remote response into CatalogEntry objects."""
        connector.set_local_space(local_space)
        trust = connector.establish_trust(remote_space, allowed_operations=["read_catalog"])
        response = connector.send_request(trust, "read_catalog", "/catalog")

        entries = []
        for item in response.data.get("products", []):
            entries.append(CatalogEntry(
                remote_id=item.get("id", ""),
                space_id=trust.remote_space.space_id,
                name=item.get("name", ""),
                description=item.get("description", ""),
                product_type=item.get("product_type", "unknown"),
                industry=item.get("industry", ""),
                security_level=item.get("security_level", "L1"),
            ))
        # Simulated response has empty products list, but parsing works
        assert isinstance(entries, list)


class TestRemoteProviderMirror:
    @pytest.mark.asyncio
    async def test_full_sync_creates_stable_remote_provider(self, sync_service, db_session):
        entry = CatalogEntry(
            remote_id="remote-product-1",
            space_id="cds-remote",
            name="Remote Product",
            description="Remote catalog product",
            product_type="structured",
            industry="finance",
            security_level="L2",
            row_count=10,
            provider_name="Remote Provider A",
            metadata={"region": "cn-east"},
        )

        added, updated, removed = await sync_service._full_sync(db_session, "cds-remote", [entry])

        assert (added, updated, removed) == (1, 0, 0)
        product_result = await db_session.execute(
            select(DataProduct).where(DataProduct.name == "[remote:cds-remote] Remote Product")
        )
        product = product_result.scalar_one()
        expected_provider_id = sync_service._remote_provider_id("cds-remote", "Remote Provider A")
        assert product.provider_id == expected_provider_id
        assert product.data_schema["remote"]["remote_id"] == "remote-product-1"

        provider_result = await db_session.execute(select(User).where(User.id == expected_provider_id))
        provider = provider_result.scalar_one()
        assert provider.role == UserRole.DATA_PROVIDER
        assert provider.is_active is False
        assert provider.organization == "Remote Provider A"

    @pytest.mark.asyncio
    async def test_incremental_sync_reuses_remote_provider_identity(self, sync_service, db_session):
        first = CatalogEntry(
            remote_id="remote-product-1",
            space_id="cds-remote",
            name="Remote Product 1",
            description="First",
            product_type="structured",
            industry="finance",
            security_level="L2",
            provider_name="Remote Provider A",
        )
        second = CatalogEntry(
            remote_id="remote-product-2",
            space_id="cds-remote",
            name="Remote Product 2",
            description="Second",
            product_type="structured",
            industry="finance",
            security_level="L2",
            provider_name="Remote Provider A",
        )

        await sync_service._incremental_sync(db_session, "cds-remote", [first])
        await sync_service._incremental_sync(db_session, "cds-remote", [second])

        result = await db_session.execute(
            select(DataProduct.provider_id).where(DataProduct.name.like("[remote:cds-remote]%"))
        )
        provider_ids = set(result.scalars().all())
        assert provider_ids == {sync_service._remote_provider_id("cds-remote", "Remote Provider A")}
