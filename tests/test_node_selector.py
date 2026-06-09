"""Tests for Node Selector — scoring-based node selection."""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sandbox_node import SandboxNode, NodeStatus
from app.services.node_selector import NodeSelector, DEFAULT_WEIGHTS


@pytest.fixture
def selector():
    return NodeSelector()


async def _create_node(
    db: AsyncSession,
    node_id: str = "node-1",
    status: str = NodeStatus.ONLINE.value,
    capabilities: list[str] | None = None,
    region: str = "default",
    cpu_usage: float = 0.3,
    memory_usage: float = 0.3,
    gpu_usage: float = 0.0,
    active_tasks: int = 0,
    max_tasks: int = 10,
    error_rate: float = 0.0,
    cpu_cores: int = 8,
    memory_mb: int = 16384,
    gpu_count: int = 0,
    last_heartbeat: datetime | None = None,
) -> SandboxNode:
    """Helper to create a sandbox node."""
    if last_heartbeat is None:
        last_heartbeat = datetime.now(timezone.utc)

    node = SandboxNode(
        node_id=node_id,
        hostname=f"{node_id}.local",
        ip_address="192.168.1.1",
        status=status,
        capabilities=capabilities or ["cpu"],
        region=region,
        cpu_cores=cpu_cores,
        memory_mb=memory_mb,
        gpu_count=gpu_count,
        cpu_usage=cpu_usage,
        memory_usage=memory_usage,
        gpu_usage=gpu_usage,
        active_tasks=active_tasks,
        max_tasks=max_tasks,
        error_rate=error_rate,
        last_heartbeat=last_heartbeat,
    )
    db.add(node)
    await db.flush()
    return node


# ── Registration ──────────────────────────────────────────────────

class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_new_node(self, db_session: AsyncSession, selector: NodeSelector):
        node = await selector.register_node(
            db_session,
            node_id="reg-1",
            hostname="reg-1.local",
            ip_address="10.0.0.1",
            capabilities=["cpu", "gpu_t4"],
            region="us-east",
            cpu_cores=16,
            memory_mb=32768,
            gpu_count=1,
            max_tasks=5,
        )
        assert node.node_id == "reg-1"
        assert node.status == NodeStatus.ONLINE.value
        assert node.capabilities == ["cpu", "gpu_t4"]
        assert node.region == "us-east"

    @pytest.mark.asyncio
    async def test_register_existing_updates(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="reg-2", capabilities=["cpu"])
        node = await selector.register_node(
            db_session,
            node_id="reg-2",
            hostname="reg-2-new.local",
            ip_address="10.0.0.2",
            capabilities=["cpu", "gpu_a10"],
        )
        assert node.capabilities == ["cpu", "gpu_a10"]
        assert node.hostname == "reg-2-new.local"


# ── Selection ─────────────────────────────────────────────────────

class TestSelection:
    @pytest.mark.asyncio
    async def test_select_single_node(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="sel-1")
        result = await selector.select(db_session)
        assert result.selected is not None
        assert result.selected.node_id == "sel-1"

    @pytest.mark.asyncio
    async def test_select_no_nodes(self, db_session: AsyncSession, selector: NodeSelector):
        result = await selector.select(db_session)
        assert result.selected is None
        assert "No online nodes" in result.reason

    @pytest.mark.asyncio
    async def test_select_offline_excluded(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="off-1", status=NodeStatus.OFFLINE.value)
        result = await selector.select(db_session)
        assert result.selected is None

    @pytest.mark.asyncio
    async def test_select_prefers_lower_load(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="busy", cpu_usage=0.9, memory_usage=0.9)
        await _create_node(db_session, node_id="idle", cpu_usage=0.1, memory_usage=0.1)
        # With weighted-random jitter, "idle" should win most of the time
        selections = []
        for _ in range(100):
            result = await selector.select(db_session)
            assert result.selected is not None
            selections.append(result.selected.node_id)
        idle_count = selections.count("idle")
        assert idle_count > 40, f"idle node selected {idle_count}/100 times, expected >40"

    @pytest.mark.asyncio
    async def test_select_prefers_lower_error_rate(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="unreliable", error_rate=0.5)
        await _create_node(db_session, node_id="reliable", error_rate=0.0)
        selections = []
        for _ in range(100):
            result = await selector.select(db_session)
            assert result.selected is not None
            selections.append(result.selected.node_id)
        reliable_count = selections.count("reliable")
        assert reliable_count > 40, f"reliable node selected {reliable_count}/100 times, expected >40"

    @pytest.mark.asyncio
    async def test_select_prefers_more_capacity(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="full", active_tasks=9, max_tasks=10)
        await _create_node(db_session, node_id="empty", active_tasks=0, max_tasks=10)
        selections = []
        for _ in range(100):
            result = await selector.select(db_session)
            assert result.selected is not None
            selections.append(result.selected.node_id)
        empty_count = selections.count("empty")
        assert empty_count > 40, f"empty node selected {empty_count}/100 times, expected >40"


# ── Capability Filtering ──────────────────────────────────────────

class TestCapabilityFiltering:
    @pytest.mark.asyncio
    async def test_select_with_required_capability(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="cpu-only", capabilities=["cpu"])
        await _create_node(db_session, node_id="gpu-node", capabilities=["cpu", "gpu_t4"])
        result = await selector.select(db_session, required_capabilities=["gpu_t4"])
        assert result.selected is not None
        assert result.selected.node_id == "gpu-node"

    @pytest.mark.asyncio
    async def test_select_no_matching_capability(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="cpu-only", capabilities=["cpu"])
        result = await selector.select(db_session, required_capabilities=["gpu_a100"])
        assert result.selected is None
        assert "capabilities" in result.reason

    @pytest.mark.asyncio
    async def test_select_multiple_capabilities(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="full", capabilities=["cpu", "gpu_a10", "tee"])
        await _create_node(db_session, node_id="partial", capabilities=["cpu", "gpu_a10"])
        result = await selector.select(db_session, required_capabilities=["gpu_a10", "tee"])
        assert result.selected is not None
        assert result.selected.node_id == "full"


# ── Region Affinity ───────────────────────────────────────────────

class TestRegionAffinity:
    @pytest.mark.asyncio
    async def test_select_preferred_region(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="local", region="us-east")
        await _create_node(db_session, node_id="remote", region="eu-west")
        selections = []
        for _ in range(100):
            result = await selector.select(db_session, preferred_region="us-east")
            assert result.selected is not None
            selections.append(result.selected.node_id)
        local_count = selections.count("local")
        assert local_count > 40, f"local node selected {local_count}/100 times, expected >40"

    @pytest.mark.asyncio
    async def test_select_no_preference(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="any", region="eu-west")
        result = await selector.select(db_session)
        assert result.selected is not None


# ── Heartbeat ─────────────────────────────────────────────────────

class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_stale_heartbeat_excluded(self, db_session: AsyncSession, selector: NodeSelector):
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        await _create_node(db_session, node_id="stale", last_heartbeat=stale_time)
        result = await selector.select(db_session)
        assert result.selected is None

    @pytest.mark.asyncio
    async def test_update_heartbeat(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="hb-1", cpu_usage=0.5)
        node = await selector.update_heartbeat(
            db_session, "hb-1",
            cpu_usage=0.8,
            memory_usage=0.6,
            active_tasks=3,
        )
        assert node is not None
        assert node.cpu_usage == 0.8
        assert node.memory_usage == 0.6
        assert node.active_tasks == 3

    @pytest.mark.asyncio
    async def test_update_heartbeat_nonexistent(self, db_session: AsyncSession, selector: NodeSelector):
        node = await selector.update_heartbeat(db_session, "nonexistent")
        assert node is None


# ── Status Management ─────────────────────────────────────────────

class TestStatusManagement:
    @pytest.mark.asyncio
    async def test_set_node_status(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="status-1")
        node = await selector.set_node_status(db_session, "status-1", NodeStatus.MAINTENANCE)
        assert node is not None
        assert node.status == NodeStatus.MAINTENANCE.value

    @pytest.mark.asyncio
    async def test_set_status_nonexistent(self, db_session: AsyncSession, selector: NodeSelector):
        node = await selector.set_node_status(db_session, "nonexistent", NodeStatus.OFFLINE)
        assert node is None

    @pytest.mark.asyncio
    async def test_draining_node_excluded(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="draining", status=NodeStatus.DRAINING.value)
        result = await selector.select(db_session)
        assert result.selected is None


# ── Exclude Nodes ─────────────────────────────────────────────────

class TestExcludeNodes:
    @pytest.mark.asyncio
    async def test_exclude_specific_nodes(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="ex-1")
        await _create_node(db_session, node_id="ex-2")
        result = await selector.select(db_session, exclude_nodes=["ex-1"])
        assert result.selected is not None
        assert result.selected.node_id == "ex-2"

    @pytest.mark.asyncio
    async def test_exclude_all_nodes(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="only")
        result = await selector.select(db_session, exclude_nodes=["only"])
        assert result.selected is None


# ── Scoring ───────────────────────────────────────────────────────

class TestScoring:
    @pytest.mark.asyncio
    async def test_candidates_ordered_by_score(self, db_session: AsyncSession, selector: NodeSelector):
        await _create_node(db_session, node_id="low", cpu_usage=0.8, memory_usage=0.8)
        await _create_node(db_session, node_id="mid", cpu_usage=0.5, memory_usage=0.5)
        await _create_node(db_session, node_id="high", cpu_usage=0.1, memory_usage=0.1)
        result = await selector.select(db_session)
        assert len(result.candidates) == 3
        scores = [c.score for c in result.candidates]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_custom_weights(self, db_session: AsyncSession):
        # Heavily weight reliability
        custom_weights = {
            "capability": 0.1,
            "load": 0.1,
            "reliability": 0.7,
            "capacity": 0.05,
            "region": 0.05,
        }
        selector = NodeSelector(weights=custom_weights)
        await _create_node(db_session, node_id="reliable", error_rate=0.0, cpu_usage=0.9)
        await _create_node(db_session, node_id="unreliable", error_rate=0.5, cpu_usage=0.1)
        selections = []
        for _ in range(100):
            result = await selector.select(db_session)
            assert result.selected is not None
            selections.append(result.selected.node_id)
        reliable_count = selections.count("reliable")
        assert reliable_count > 40, f"reliable node selected {reliable_count}/100 times, expected >40"
