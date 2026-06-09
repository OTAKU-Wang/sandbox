"""Node Selector — scoring-based node selection for sandbox task scheduling.

Selects the best available node for a task based on:
1. Required capabilities (GPU, TEE, etc.)
2. Current load (CPU, memory, GPU usage)
3. Error rate (penalize unreliable nodes)
4. Task capacity (avoid overloaded nodes)
5. Region affinity (prefer same region)

Scoring formula:
    score = capability_match * w1
          + (1 - avg_load) * w2
          + (1 - error_rate) * w3
          + capacity_ratio * w4
          + region_match * w5
"""
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sandbox_node import SandboxNode, NodeStatus

logger = logging.getLogger(__name__)


@dataclass
class NodeScore:
    """Scored node candidate."""
    node: SandboxNode
    score: float
    capability_match: bool
    load_score: float
    reliability_score: float
    capacity_score: float


@dataclass
class SelectionResult:
    """Result of node selection."""
    selected: SandboxNode | None
    candidates: list[NodeScore]
    reason: str = ""


# Weights for scoring components
DEFAULT_WEIGHTS = {
    "capability": 0.35,    # Must-have capabilities
    "load": 0.25,          # Current resource utilization
    "reliability": 0.20,   # Error rate
    "capacity": 0.15,      # Task capacity headroom
    "region": 0.05,        # Region affinity
}

# Heartbeat timeout: nodes without heartbeat in this period are considered offline
HEARTBEAT_TIMEOUT = timedelta(minutes=5)


class NodeSelector:
    """Selects the best sandbox node for task execution.

    Uses a weighted scoring algorithm to rank available nodes.
    """

    def __init__(self, weights: dict[str, float] | None = None):
        self.weights = weights or DEFAULT_WEIGHTS

    async def select(
        self,
        db: AsyncSession,
        required_capabilities: list[str] | None = None,
        preferred_region: str | None = None,
        exclude_nodes: list[str] | None = None,
    ) -> SelectionResult:
        """Select the best available node for a task.

        Args:
            db: Database session
            required_capabilities: Capabilities the node must have (e.g., ["gpu_a10", "tee"])
            preferred_region: Preferred region for locality
            exclude_nodes: Node IDs to exclude

        Returns:
            SelectionResult with the selected node and scored candidates
        """
        # Query online nodes
        query = select(SandboxNode).where(
            SandboxNode.status == NodeStatus.ONLINE.value
        )

        if exclude_nodes:
            query = query.where(SandboxNode.node_id.notin_(exclude_nodes))

        result = await db.execute(query)
        nodes = list(result.scalars().all())

        if not nodes:
            return SelectionResult(
                selected=None,
                candidates=[],
                reason="No online nodes available",
            )

        # Filter by heartbeat timeout
        now = datetime.now(timezone.utc)
        alive_nodes = []
        for n in nodes:
            if not n.last_heartbeat:
                continue
            # Handle both naive and aware datetimes (SQLite returns naive)
            hb = n.last_heartbeat
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            if (now - hb) < HEARTBEAT_TIMEOUT:
                alive_nodes.append(n)

        if not alive_nodes:
            return SelectionResult(
                selected=None,
                candidates=[],
                reason="All nodes have stale heartbeats",
            )

        # Score each node
        scored: list[NodeScore] = []
        for node in alive_nodes:
            score_result = self._score_node(
                node,
                required_capabilities or [],
                preferred_region,
            )
            if score_result.capability_match:
                scored.append(score_result)

        if not scored:
            return SelectionResult(
                selected=None,
                candidates=[],
                reason=f"No nodes with required capabilities: {required_capabilities}",
            )

        # Sort by score (descending)
        scored.sort(key=lambda s: s.score, reverse=True)

        # Weighted random selection from top-K to prevent hotspots (P1-6)
        # Add small random jitter to break ties and distribute load
        top_k = scored[:min(3, len(scored))]
        weights = [s.score + random.uniform(0, 0.1) for s in top_k]
        selected_idx = 0
        total = sum(weights)
        if total > 0:
            r = random.uniform(0, total)
            cumulative = 0.0
            for i, w in enumerate(weights):
                cumulative += w
                if r <= cumulative:
                    selected_idx = i
                    break

        chosen = top_k[selected_idx]
        return SelectionResult(
            selected=chosen.node,
            candidates=scored,
            reason=f"Selected {chosen.node.node_id} (score={chosen.score:.3f}, top-{len(top_k)} weighted random)",
        )

    def _score_node(
        self,
        node: SandboxNode,
        required_capabilities: list[str],
        preferred_region: str | None,
    ) -> NodeScore:
        """Score a single node against requirements."""
        # Check capability match — handle both list and JSON string
        caps = node.capabilities
        if isinstance(caps, str):
            import json
            try:
                caps = json.loads(caps)
            except (json.JSONDecodeError, TypeError):
                caps = []
        node_caps = set(caps or [])
        required_set = set(required_capabilities)
        capability_match = required_set.issubset(node_caps)

        # Load score: average of CPU, memory, GPU utilization (lower is better)
        load_score = 1.0 - ((node.cpu_usage + node.memory_usage + node.gpu_usage) / 3.0)

        # Reliability score: based on error rate (lower error rate is better)
        reliability_score = 1.0 - node.error_rate

        # Capacity score: how much headroom for new tasks
        if node.max_tasks > 0:
            capacity_score = 1.0 - (node.active_tasks / node.max_tasks)
        else:
            capacity_score = 0.0

        # Region match
        region_match = 1.0 if (not preferred_region or node.region == preferred_region) else 0.0

        # Weighted score
        score = (
            (1.0 if capability_match else 0.0) * self.weights["capability"]
            + max(0.0, load_score) * self.weights["load"]
            + reliability_score * self.weights["reliability"]
            + max(0.0, capacity_score) * self.weights["capacity"]
            + region_match * self.weights["region"]
        )

        return NodeScore(
            node=node,
            score=score,
            capability_match=capability_match,
            load_score=load_score,
            reliability_score=reliability_score,
            capacity_score=capacity_score,
        )

    async def update_heartbeat(
        self,
        db: AsyncSession,
        node_id: str,
        cpu_usage: float | None = None,
        memory_usage: float | None = None,
        gpu_usage: float | None = None,
        active_tasks: int | None = None,
    ) -> SandboxNode | None:
        """Update node heartbeat and metrics."""
        result = await db.execute(
            select(SandboxNode).where(SandboxNode.node_id == node_id)
        )
        node = result.scalar_one_or_none()
        if not node:
            return None

        node.last_heartbeat = datetime.now(timezone.utc)
        if cpu_usage is not None:
            node.cpu_usage = max(0.0, min(1.0, cpu_usage))
        if memory_usage is not None:
            node.memory_usage = max(0.0, min(1.0, memory_usage))
        if gpu_usage is not None:
            node.gpu_usage = max(0.0, min(1.0, gpu_usage))
        if active_tasks is not None:
            node.active_tasks = max(0, active_tasks)

        await db.flush()
        return node

    async def register_node(
        self,
        db: AsyncSession,
        node_id: str,
        hostname: str,
        ip_address: str,
        capabilities: list[str],
        region: str = "default",
        cpu_cores: int = 0,
        memory_mb: int = 0,
        gpu_count: int = 0,
        gpu_memory_mb: int = 0,
        max_tasks: int = 10,
    ) -> SandboxNode:
        """Register a new sandbox node."""
        # Check if node already exists
        result = await db.execute(
            select(SandboxNode).where(SandboxNode.node_id == node_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Update existing node
            existing.hostname = hostname
            existing.ip_address = ip_address
            existing.capabilities = capabilities
            existing.region = region
            existing.cpu_cores = cpu_cores
            existing.memory_mb = memory_mb
            existing.gpu_count = gpu_count
            existing.gpu_memory_mb = gpu_memory_mb
            existing.max_tasks = max_tasks
            existing.status = NodeStatus.ONLINE.value
            existing.last_heartbeat = datetime.now(timezone.utc)
            await db.flush()
            await db.refresh(existing)
            return existing

        # Create new node
        node = SandboxNode(
            node_id=node_id,
            hostname=hostname,
            ip_address=ip_address,
            capabilities=capabilities,
            region=region,
            cpu_cores=cpu_cores,
            memory_mb=memory_mb,
            gpu_count=gpu_count,
            gpu_memory_mb=gpu_memory_mb,
            max_tasks=max_tasks,
            status=NodeStatus.ONLINE.value,
            last_heartbeat=datetime.now(timezone.utc),
        )
        db.add(node)
        await db.flush()
        await db.refresh(node)
        return node

    async def set_node_status(
        self,
        db: AsyncSession,
        node_id: str,
        status: NodeStatus,
    ) -> SandboxNode | None:
        """Update node status."""
        result = await db.execute(
            select(SandboxNode).where(SandboxNode.node_id == node_id)
        )
        node = result.scalar_one_or_none()
        if not node:
            return None

        node.status = status.value
        await db.flush()
        return node


# Singleton
node_selector = NodeSelector()
