import asyncio
import logging
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
# SS-10 CB-01: the legacy L2 (firecracker) + k8s adapters are deprecated and
# gated off by default in production (CDS_LEGACY_L2_ENABLED=false). The existing
# test baseline exercises those paths, so the suite pins the flag true —
# enforcement (flag off → L2/k8s unregistered) is covered by dedicated tests.
os.environ.setdefault("CDS_LEGACY_L2_ENABLED", "true")
# SS-13 (single-path cutover): the default session level is now the Cube
# MicroVM level (l2c) and the legacy levels (L0/L1/L2/L3/k8s) sit behind a
# new gate. Same precedent as the CDS_LEGACY_L2_ENABLED line above: the
# suite pins both flags true so pre-SS-13 L3-centric tests keep exercising
# their levels, while the SS-13 tests toggle the gates off per-test via
# monkeypatch. The TestCubeNodeClient stub + seeded cube node below keep
# default-level l2c creates green without real cube nodes.
os.environ.setdefault("CDS_CUBE_DATAPLANE_ENABLED", "true")
os.environ.setdefault("CDS_SANDBOX_LEGACY_ENABLED", "true")
# D-e2e-01: the admin bootstrap escape hatch is production-refused by
# validate_security_config; the suite needs it because fixture helpers create
# admin accounts via /register after other users exist. Enforcement
# (flag off -> 403 for non-first admin claims) is covered by dedicated tests
# in tests/test_auth_extended.py.
os.environ.setdefault("CDS_ALLOW_ADMIN_SELF_REGISTER", "true")
# SS-13 D3 follow-up: with the dataplane pinned true, the SS-10 prod gate
# (config.validate_security_config) requires node mTLS material + a template
# signing key in production-mode settings. Production-mode matrix tests
# (test_security_config_matrix) construct such settings, so the suite env
# supplies TEST-ONLY material (dummy CA path + the same test signing key the
# cube template tests already monkeypatch). Real material is deployment-only.
os.environ.setdefault("CDS_CUBE_NODE_TLS_CA", "/etc/cds/cube-node/test-node-ca.pem")
os.environ.setdefault("CDS_CUBE_TEMPLATE_SIGNING_KEY", "test-signing-key")
# SS-14 RT-07: provision pre-creates the per-session results dir (cubelet
# bind-mount contract) under CUBE_RESULTS_DIR — the default /var/lib/cds is
# not writable for the test user, so the suite pins a /tmp location.
os.environ.setdefault("CDS_CUBE_RESULTS_DIR", "/tmp/cds-test-sandbox-results")

# PZ-01 (MR-A2): real SFT training is fail-closed without a resolvable base
# model, so the suite default keeps the deterministic simulation escape
# hatch (spec config matrix: dev/test true, prod false). Fail-closed
# behavior is covered in tests/test_pz01_sft_real.py.
os.environ.setdefault("CDS_TRAINING_ALLOW_SIMULATION", "true")

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

# A192x (P4-1): harness self-heal. A lifespan-running test's shutdown cancels
# task_pipeline pollers mid-query; SQLAlchemy treats the CancelledError as
# connection-fatal and TERMINATES the DBAPI connection — fatal for this
# harness, because the whole suite shares ONE StaticPool connection (the
# pipeline's poller must see the fixtures' uncommitted flushes, so sessions
# cannot be isolated on their own connection). We neutralise ONLY the
# terminate step for this engine: the invalidated pool record still hands new
# checkouts a fresh in-memory connection, while pre-cancel sessions keep their
# (still open) old connection, so teardown rollbacks/drops of the test that
# cancelled succeed instead of dying with "no active connection". The orphaned
# old in-memory DB dies with the test. Loud warning per fork: if a full-suite
# reading ever shows forks OUTSIDE lifespan-stop windows, that is a new
# mid-test connection killer to chase.
_pool_logger = logging.getLogger("tests.conftest.a192x")

# Captured by _quiesce_background_singletons at every test boundary; re-pinned
# as the thread's "current" loop after every test (see pytest_runtest_teardown).
_pinned_loop: asyncio.AbstractEventLoop | None = None


def _a192x_noop_terminate(dbapi_connection):  # noqa: ARG001 — signature fixed by SQLAlchemy
    _pool_logger.warning(
        "[A192x] connection terminate neutralised (suite harness); new checkouts "
        "fork to a fresh in-memory DB until the next setup_db"
    )


test_engine.sync_engine.dialect.do_terminate = _a192x_noop_terminate  # type: ignore[method-assign]

# Patch the module-level engine so lifespan uses the test DB
original_engine = db_module.engine
db_module.engine = test_engine
# W11: background services open their own session via app.core.database.async_session;
# point it at the test factory so async operation runners work in tests.
db_module.async_session = test_session_factory

# Disable lifespan to avoid double table creation
app.router.lifespan_context = None

# SS-13: suite-wide stand-in for the cubelet gRPC client (default level l2c).
# The sandbox manager is a process-wide singleton built lazily, and its
# CubeRuntimeAdapter binds `cube_node_client.get_cube_node_client` at
# construction time — so the swap happens at conftest import, before any lazy
# manager init. Only the gRPC methods the session flows touch
# (create/exec/destroy/list) are stubbed; anything else fails loudly so a
# test can never silently fake a capability it did not intend to (honest
# degradation). Same stubbing pattern as tests/test_policy_compile_cube.py.
import itertools

from app._stubs.cube.cubebox.v1 import cubebox_pb2
from app._stubs.cube.errorcode.v1 import errorcode_pb2
from app.services import cube_node_client


class TestCubeNodeClient:
    """Fake cubelet client: Success for create/exec/destroy/list."""

    def __init__(self, node=None, settings=None):
        self._seq = itertools.count(1)

    def create(self, request):
        resp = cubebox_pb2.RunCubeSandboxResponse()
        resp.requestID = request.requestID
        # SS-14 RT-07 (D-e2e-19): 真实 cubelet sandbox_id = 32 位 hex;
        # container_id 右切解析（l2c-<node>-<32hex>）按此契约。
        resp.sandboxID = f"{next(self._seq):032x}"
        resp.ret.ret_code = errorcode_pb2.Success
        return resp

    def exec(self, request):
        resp = cubebox_pb2.ExecCubeSandboxResponse()
        resp.ret.ret_code = errorcode_pb2.Success
        return resp

    def update(self, request):
        resp = cubebox_pb2.UpdateCubeSandboxResponse()
        resp.ret.ret_code = errorcode_pb2.Success
        return resp

    def destroy(self, request):
        resp = cubebox_pb2.DestroyCubeSandboxResponse()
        resp.ret.ret_code = errorcode_pb2.Success
        return resp

    def list(self, request):
        # 上游契约: ListCubeSandboxResponse 只有 items 字段（无 ret）。
        resp = cubebox_pb2.ListCubeSandboxResponse()
        return resp

    def get_storage_metrics(self, request):
        # RT-07: 节点注册初始探针 = List + GetStorageMetrics（sandbox_nodes.py）。
        resp = cubebox_pb2.GetStorageMetricsResponse()
        resp.ret.ret_code = errorcode_pb2.Success
        # metrics 是 map 字段（上游 StorageMetrics proto）—— 探针只验证可达性。
        return resp

    def __getattr__(self, name):
        def _unstubbed(*args, **kwargs):
            raise AssertionError(
                f"TestCubeNodeClient: unstubbed cubelet gRPC {name!r} — "
                "extend the fake deliberately if the flow needs it"
            )

        return _unstubbed


def _install_test_cube_client() -> None:
    if getattr(cube_node_client, "_ss13_test_client", False):
        return
    cube_node_client.get_cube_node_client = lambda node, settings=None: TestCubeNodeClient(node)
    cube_node_client._ss13_test_client = True


_install_test_cube_client()


# SS-14 RT-07: the l2c session-create flow pre-creates the per-session
# cds-encrypted volume on the node (CubeRuntimeAdapter.prepare_session_volume:
# master EnsureVolume + plugin KMS registration + PutVolumeData over the unix
# socket) — hardware-bound, fail-closed by design. In the suite the node
# dataplane is the TestCubeNodeClient stand-in above, so the volume-precreate
# half of the flow gets its own stand-in — P1a-6 (cr §4/tests MAJOR-1): the
# stand-in used to be an IMPORT-TIME PERMANENT class-attribute overwrite,
# which made the real method unreachable for the whole session with no
# restore path. It is now a per-test monkeypatch (autouse, so default
# behavior is byte-for-byte the same for every existing test) that pytest
# unwinds after each test; tests asking for the `real_session_volume`
# fixture get the untouched real method instead. The session-data contract
# (product must carry an uploaded data resource) stays enforced for real:
# fixtures upload a resource instead of bypassing the check.
from app.services.cube_runtime_adapter import CubeRuntimeAdapter

# Captured BEFORE any test-layer patching — this is the product method.
_REAL_PREPARE_SESSION_VOLUME = CubeRuntimeAdapter.prepare_session_volume


async def _test_prepare_session_volume(self, session_id: str, data: bytes) -> str:
    return f"vault:v1:test-{session_id}"


@pytest.fixture(autouse=True)
def _cube_volume_precreate_stub(request, monkeypatch):
    """Per-test volume-precreate stand-in (P1a-6). Skipped for tests that
    opt into `real_session_volume`; monkeypatch restores the class default
    (= the real method) at teardown, so nothing is permanently patched."""
    if "real_session_volume" in request.fixturenames:
        return
    monkeypatch.setattr(CubeRuntimeAdapter, "prepare_session_volume", _test_prepare_session_volume)


@pytest.fixture
def real_session_volume(monkeypatch):
    """P1a-6 opt-in: run the REAL CubeRuntimeAdapter.prepare_session_volume.

    The two hardware-bound edge RPCs (_master_ensure_volume → cube master
    HTTP, _put_volume_data → node plugin unix socket) still need per-test
    fakes; the orchestration itself (fail-closed gates, DEK re-check against
    the DB, return of the plugin key ref) runs for real. See
    tests/test_p1a6_volume_precreate_real.py.
    """
    monkeypatch.setattr(CubeRuntimeAdapter, "prepare_session_volume", _REAL_PREPARE_SESSION_VOLUME)
    return _REAL_PREPARE_SESSION_VOLUME


@pytest_asyncio.fixture(autouse=True)
async def _quiesce_background_singletons():
    """A192x (P4-1): drain process-wide background singletons at the END of each
    test, after every DB fixture has already released the shared connection.

    Why this exists: an API test that submits a pipeline task leaves the
    task_pipeline poller/dispatcher running on the session loop. The next
    lifespan-running test's shutdown calls ``task_pipeline.stop()``, whose
    cancellation lands mid-SELECT; SQLAlchemy treats the CancelledError as
    connection-fatal and TERMINATES the DBAPI connection — with the suite's
    single shared StaticPool connection that kills the whole harness
    (ci/known-failures.md A192x: "1 ERROR after test_agent_api"). Stopping the
    pipeline here instead means (a) lifespan shutdowns see an already-stopped
    no-op, and (b) any terminate lands after the teardown's drop_all, so the
    invalidated record simply reconnects empty for the next test — which is
    exactly the state every test starts from anyway.

    Ordering is enforced by setup_db depending on this fixture (setup first ⇒
    teardown last). We also await the fire-and-forget cancel of in-flight
    execution tasks so no cancellation can straggle into the NEXT test's
    create_all. Same spirit as the autouse process-global resets elsewhere
    (``_clean_ss12_state``).
    """
    yield
    import asyncio as _asyncio

    global _pinned_loop
    _pinned_loop = _asyncio.get_running_loop()

    from app.services import egress_audit
    from app.services.task_pipeline import task_pipeline

    running = list(task_pipeline._running.values())
    try:
        await task_pipeline.stop()
    except Exception as exc:  # quiescing must never itself fail a test
        logging.getLogger(__name__).warning("task_pipeline quiesce failed: %s", exc)
    if running:
        await _asyncio.gather(*running, return_exceptions=True)
    # A192x (P4-1), same quarantine for the egress-audit daemon writer: any
    # test that touches log_egress_event lazily respawns _writer_loop on the
    # SESSION loop (egress_audit.py:124) — without this it outlives its test
    # forever (test_egress_audit.py:83 even installs an intentionally stuck
    # writer) and poisons any global all_tasks() consumer on the pinned loop
    # (the exact 55%-position full-suite hang behind A192x). shutdown is
    # no-op when never started, sentinel+5s-bounded join otherwise.
    try:
        await egress_audit.shutdown_egress_audit()
    except Exception as exc:
        logging.getLogger(__name__).warning("egress_audit quiesce failed: %s", exc)


def pytest_runtest_teardown(item, nextitem):
    """A192x (P4-1): keep the pinned session loop visible as the thread's
    CURRENT loop after every test.

    The legacy session ``event_loop`` fixture used to leave the loop set in
    the event-loop policy, and a number of SYNC tests rely on that
    (``asyncio.get_event_loop().run_until_complete(...)`` in
    test_audit_partition / test_p05_code_scanning / test_rate_limiting).
    pytest-asyncio 1.4 with loop-scope=session no longer publishes the loop
    that way, and any sync test calling ``asyncio.run()`` on Python 3.14
    leaves the policy current-loop as None (3.12 and earlier tolerated it) —
    poisoning every later get_event_loop() consumer. This hook runs after
    fixture finalizers, so the re-pin cannot be undone by quiesce itself.

    Retirement clause (lead ruling 2026-10-30): this hook REPLACES the
    side-effect semantics of the legacy fixture, not the fixture itself.
    If anyone ever resurrects a session ``event_loop`` fixture the pin in
    tests/test_a192x_harness_discipline.py goes red — retire this hook in
    the same change; never run both (double publication of the current
    loop is how the next A192x archaeology round starts).
    """
    if _pinned_loop is not None and not _pinned_loop.is_closed():
        asyncio.set_event_loop(_pinned_loop)


@pytest_asyncio.fixture
async def _task_generation():
    """A192x (P4-1): generation watermark for tests that fire-and-forget.

    On the session-pinned loop, ``asyncio.all_tasks()`` shows the WHOLE
    suite's task population — tests must only ever await tasks their own body
    spawned. Request this fixture and pass it to your drain helper; it
    snapshots every pending task at setup (BEFORE your body runs). Ordering
    contract: snapshot precedes the test body, the drain only waits for
    post-snapshot newcomers — whoever swaps this order owns the deadlock.
    Consumers are pinned by tests/test_a192x_harness_discipline.py.
    """
    return {id(t) for t in asyncio.all_tasks()}


@pytest_asyncio.fixture(autouse=True)
async def setup_db(_quiesce_background_singletons):
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    # A192x 收口（P4-1, 2026-10-30）：本套件此前每用例一个新循环，而 StaticPool
    # 只有一条连接——它生在哪个循环上就只能在那个循环上用，跨循环复用在上一个
    # lifespan dispose 之后的 teardown 里炸（现场：test_agent_api 之后的启动披露
    # 用例）。修法 = pyproject 把 test/fixture 的 loop scope 钉到 session（全席
    # 一个循环，正是下方曾存在的 session 级 event_loop 遗留 fixture 的设计意图，
    # pytest-asyncio 1.4 已不采纳该写法，此处删除）。这里不再做任何 dispose：
    # 台账实测 teardown dispose 会让 StaticPool 上其他存活会话同失连接（净负）。


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


SS13_TEST_CUBE_NODE_ID = "cdstestcubenode"

# Tests that assert a PRISTINE (empty) cluster. The suite-wide seeded cube
# node is skipped for them — they unit-test empty-fleet / no-online-node
# behaviour that the SS-13 default (non-empty cube fleet) intentionally
# replaces for every other test. test_cube_template_dist asserts EXACT node
# sets returned by service.distribute() (it adds its own nodes), so a
# seeded node would add a third, breaking the assertions.
_SS13_PRISTINE_CLUSTER_FILES = {"test_node_selector.py", "test_node_operations.py", "test_cube_template_dist.py", "test_cube_node_client.py"}
_SS13_PRISTINE_CLUSTER_TEST_NAMES = {
    "test_gate_no_eligible_node_returns_400",
    "test_node_selection_excludes_runtime_version_mismatch",
}

# Tests that assert the PRE-SS-13 gate defaults (cube dataplane disabled,
# legacy levels off). The suite pins CDS_CUBE_DATAPLANE_ENABLED=true and
# CDS_SANDBOX_LEGACY_ENABLED=true (conftest env); this fixture restores the
# old defaults on the cached Settings for exactly those tests — the explicit
# monkeypatch the parent design mandates, applied conftest-side because
# SS-13 scope is limited to conftest + the two new test files.
_SS13_LEGACY_DEFAULT_TEST_NAMES = {
    "test_provision_rejects_when_dataplane_disabled",
    # test_admin_auth.py admin-pages prod scenario builds the cached
    # production config the same way the matrix tests do; the SS-13 legacy
    # prod gate would fire first on the suite-pinned flag.
    "test_validate_security_config_rejects_admin_pages_in_production",
    # test_security_config_matrix.py prod-pass/warn scenarios build the full
    # production config from the cached get_settings(); the suite env pins
    # would trip the SS-10 CB-01 node-mTLS gate and the SS-13 legacy gate
    # before the gate under test.
    "test_validate_security_config_warns_on_hardware_gated_fallbacks",
    "test_prod_federation_enabled_with_mtls_material_passes",
    "test_prod_attestation_measurements_empty_raises",
    "test_prod_attestation_measurements_set_passes",
    "test_prod_mfa_secret_key_set_passes",
}


@pytest.fixture(autouse=True)
def _restore_legacy_gate_defaults(request, monkeypatch):
    if request.node.name not in _SS13_LEGACY_DEFAULT_TEST_NAMES:
        return
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "CUBE_DATAPLANE_ENABLED", False)
    monkeypatch.setattr(settings, "SANDBOX_LEGACY_ENABLED", False)


@pytest_asyncio.fixture(autouse=True)
async def seed_default_cube_node(request, db_session, setup_db):
    """SS-13: the suite-wide cube fleet — one READY, TEE-capable node.

    The default session level is l2c, so API-level session creates need an
    eligible cube node (gate: online + pinned runtime_version + cubelet
    endpoint; field shape per tests/test_e2e_flows.py seed +
    _eligible_cube_nodes). capabilities.tee="tdx" makes the node TEE-capable
    for the tee_policy=tee_required flow (per-session trust_root disclosure).

    The setup_db parameter is a deliberate dependency: this pytest-asyncio
    setup schedules async fixture bodies in instantiation order, and the
    seed must not run before setup_db's create_all.
    """
    basename = request.node.fspath.basename
    if basename in _SS13_PRISTINE_CLUSTER_FILES:
        return
    if basename == "test_cube_runtime_adapter.py" and request.node.name in _SS13_PRISTINE_CLUSTER_TEST_NAMES:
        return

    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models.sandbox_node import NodeStatus, SandboxNode
    from app.services.cube_runtime_adapter import pinned_cube_version

    existing = (await db_session.execute(
        select(SandboxNode).where(SandboxNode.node_id == SS13_TEST_CUBE_NODE_ID)
    )).scalar_one_or_none()
    if existing is None:
        db_session.add(SandboxNode(
            node_id=SS13_TEST_CUBE_NODE_ID,
            hostname=f"{SS13_TEST_CUBE_NODE_ID}.local",
            ip_address="10.99.0.1",
            status=NodeStatus.ONLINE.value,
            capabilities={
                "cubelet_endpoint": "10.99.0.1:9443",
                "runtime_version": pinned_cube_version(),
                "tee": "tdx",
            },
            region="default",
            cpu_cores=16,
            memory_mb=32768,
            gpu_count=0,
            cpu_usage=0.05,
            memory_usage=0.05,
            gpu_usage=0.0,
            active_tasks=0,
            max_tasks=100,
            error_rate=0.0,
            last_heartbeat=datetime.now(timezone.utc),
        ))
        await db_session.flush()


@pytest.fixture
def l2c_api_env(monkeypatch):
    """SS-13: force a fresh sandbox manager for API-level l2c tests.

    The manager is a process-wide singleton; resetting it makes the
    CubeRuntimeAdapter bind the conftest TestCubeNodeClient (installed at
    import time) instead of whatever factory a previous test's manager kept.
    """
    import app.services.sandbox_manager as sm

    monkeypatch.setattr(sm, "sandbox_manager", None)


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, db_session: AsyncSession) -> dict:
    """Register a test user and return auth headers."""
    unique = uuid.uuid4().hex[:8]
    resp = await client.post("/api/v1/auth/register", json={
        "username": f"testuser_{unique}",
        "email": f"test_{unique}@example.com",
        "password": "testpass123",
        # PZ-10: only admin is self-selectable at registration — flip the role
        # to data_provider in the DB below (same pattern as operator fixtures).
        "role": "admin",
    })
    assert resp.status_code == 201
    # PZ-10 tightened role self-selection (only admin bootstrap); register as
    # admin then flip the user to data_provider directly (same as operator/admin fixtures).
    from app.models.user import User

    uid = resp.json()["user"]["id"]
    user = await db_session.get(User, uuid.UUID(uid))
    user.role = "data_provider"
    await db_session.flush()
    await db_session.refresh(user)
    from app.services.auth_service import create_access_token

    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_user(db_session: AsyncSession):
    """Fixture that returns a factory for creating users with arbitrary roles."""
    async def _make(role: str = "data_provider", prefix: str = "test") -> tuple[dict, str]:
        return await create_user_with_role(db_session, role, prefix)
    return _make


@pytest.fixture
def publish_product(db_session: AsyncSession):
    """Mark a product as published for tests that are not exercising lifecycle.

    SS-14 RT-07: l2c 会话创建是 fail-closed 的（产品必须携带已上传的数据资源，
    会话数据来自资源下载）。此 fixture 在发布的同时补一个小 CSV 资源并链接到
    产品（storage_service 测试环境自动落本地目录，KMS 软件 HSM）—— 让默认级别
    l2c 的会话创建在 stub 节点环境走真实的数据面契约，而不是绕过检查。
    产品自带 resource_id 或调用方自行上传时，不重复建资源（幂等）。
    """
    async def _ensure_resource(product):
        if getattr(product, "resource_id", None) is not None:
            return
        from app.models.data_resource import DataResource, ResourceStatus
        from app.services.storage_service import storage_service

        csv_data = b"city,amount\nShanghai,100\nBeijing,200\n"
        meta = storage_service.upload(csv_data, f"test-resources/{uuid.uuid4().hex}.csv", "text/csv")
        resource = DataResource(
            provider_id=product.provider_id,
            name=f"publish-fixture-resource-{uuid.uuid4().hex[:8]}",
            storage_path=meta["path"],
            encryption_key_id=meta.get("key_id"),
            status=ResourceStatus.READY.value,
            file_size_bytes=meta.get("size"),
            sm3_checksum=meta.get("checksum"),
        )
        db_session.add(resource)
        await db_session.flush()
        product.resource_id = resource.id

    async def _publish(product_id: str):
        from app.models.data_product import DataProduct, DataProductStatus

        product = await db_session.get(DataProduct, uuid.UUID(str(product_id)))
        assert product is not None
        product.status = DataProductStatus.PUBLISHED.value
        await _ensure_resource(product)
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
