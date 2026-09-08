import asyncio
import os
import uuid
from typing import AsyncGenerator

os.environ["TESTING"] = "1"
# P0 fail-closed switches (plan docs/ai-sandbox-gap-remediation-plan.md §5.1):
# the existing test baseline runs with the relaxed values that dev uses.
# Enforcement behaviour is covered by dedicated tests that monkeypatch the
# cached Settings object.
os.environ.setdefault("CDS_KMS_REQUIRE_ATTESTATION", "false")
os.environ.setdefault("CDS_DEV_SANDBOX_REQUIRE_CONTRACT", "false")
# N7: test suite exercises the kubectl fallback path (no cluster in CI); the
# python-client path is covered by dedicated mocked tests that flip it on.
os.environ.setdefault("CDS_K8S_USE_PYTHON_CLIENT", "false")
# Training runs in deterministic-proxy mode in the test environment (torch is
# not installed); the fail-closed switch is covered by dedicated tests.
os.environ.setdefault("CDS_TRAINING_REQUIRE_TORCH", "false")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
import app.core.database as db_module
from app.main import app
from app.core.config import Settings

# In-memory SQLite with StaticPool so all connections share the same DB
test_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

# Patch the module-level engine so lifespan uses the test DB
original_engine = db_module.engine
db_module.engine = test_engine
# W11: background services open their own session via app.core.database.async_session;
# point it at the test factory so async operation runners work in tests.
db_module.async_session = test_session_factory

# Disable lifespan to avoid double table creation
app.router.lifespan_context = None


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Register a test user and return auth headers."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"testuser_{unique}",
        "email": f"test_{unique}@example.com",
        "password": "testpass123",
        "role": "data_provider",
    })
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_user(db_session: AsyncSession):
    """Fixture that returns a factory for creating users with arbitrary roles."""
    async def _make(role: str = "data_provider", prefix: str = "test") -> tuple[dict, str]:
        return await create_user_with_role(db_session, role, prefix)
    return _make


@pytest.fixture
def publish_product(db_session: AsyncSession):
    """Mark a product as published for tests that are not exercising lifecycle."""
    async def _publish(product_id: str):
        from app.models.data_product import DataProduct, DataProductStatus

        product = await db_session.get(DataProduct, uuid.UUID(str(product_id)))
        assert product is not None
        product.status = DataProductStatus.PUBLISHED.value
        await db_session.flush()
        return product

    return _publish


@pytest_asyncio.fixture
async def operator_headers(client: AsyncClient, db_session: AsyncGenerator[AsyncSession, None]) -> dict:
    """Create an operator user directly in DB and return auth headers."""
    from app.services.auth_service import hash_password, create_access_token
    from app.models.user import User
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"operator_{unique}",
        email=f"operator_{unique}@example.com",
        hashed_password=hash_password("testpass123"),
        role="operator",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, db_session: AsyncGenerator[AsyncSession, None]) -> dict:
    """Create an admin user directly in DB and return auth headers."""
    from app.services.auth_service import hash_password, create_access_token
    from app.models.user import User
    unique = uuid.uuid4().hex[:8]
    user = User(
        username=f"admin_{unique}",
        email=f"admin_{unique}@example.com",
        hashed_password=hash_password("testpass123"),
        role="admin",
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}


async def create_user_with_role(db_session: AsyncSession, role: str, prefix: str = "test") -> tuple[dict, str]:
    """Create a user with arbitrary role directly in DB. Returns (headers, user_id)."""
    from app.services.auth_service import hash_password, create_access_token
    from app.models.user import User
    unique = uuid.uuid4().hex[:8]
    username = f"{prefix}_{unique}".replace("-", "_")
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("testpass123"),
        role=role,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user)
    token = create_access_token(user.id, user.role)
    headers = {"Authorization": f"Bearer {token}"}
    return headers, str(user.id)
