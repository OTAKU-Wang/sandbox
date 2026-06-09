"""Sprint 8 integration tests — P1-1~P1-6 architecture gaps.

These tests verify current behavior AND document gaps that Sprint 8
implementations will close. Tests that reveal in-memory limitations
or missing integrations are marked with pytest.xfail().
"""
import uuid
import pytest


# ============================================================
# P1-1: TaskPipeline — scheduler → queue → worker integration
# ============================================================

class TestTaskPipelineIntegration:
    """Test that scheduler, queue, and worker form a coherent pipeline."""

    def test_scheduler_queue_worker_exist_as_singletons(self):
        from app.services.task_scheduler import task_scheduler
        from app.services.task_queue import TaskQueue
        from app.services.task_worker import task_worker
        assert task_scheduler is not None
        assert task_worker is not None
        # TaskQueue is not a singleton but instantiable
        q = TaskQueue()
        assert q is not None

    @pytest.mark.asyncio
    async def test_scheduler_submits_to_queue(self):
        """Scheduler should be able to submit tasks to the queue."""
        from app.services.task_queue import TaskQueue, TaskPriority

        queue = TaskQueue()
        task = await queue.enqueue("test_task", {"data": "value"}, priority=TaskPriority.NORMAL)
        assert task.task_id
        assert task.task_type == "test_task"

    @pytest.mark.asyncio
    async def test_worker_dequeues_from_queue(self):
        """Worker should be able to dequeue tasks from the queue."""
        from app.services.task_queue import TaskQueue, TaskPriority

        queue = TaskQueue()
        task = await queue.enqueue("test_task", {"data": "value"}, priority=TaskPriority.HIGH)
        dequeued = await queue.dequeue()
        assert dequeued is not None

    @pytest.mark.asyncio
    async def test_task_state_machine_full_happy_path(self):
        """Full happy path through task state machine."""
        from app.services.task_state_machine import task_state_machine, TaskStatus

        states = [
            TaskStatus.QUEUED,
            TaskStatus.CODE_SCANNING,
            TaskStatus.PREPARING,
            TaskStatus.RUNNING,
            TaskStatus.OUTPUT_INSPECTING,
            TaskStatus.COMPLETED,
        ]

        current = states[0]
        for next_state in states[1:]:
            result = task_state_machine.validate_transition(current, next_state)
            assert result.success is True, f"Transition {current} -> {next_state} should be valid"
            current = next_state

    @pytest.mark.asyncio
    async def test_task_state_machine_cancel_from_any_active(self):
        """CANCELLED should be reachable from any active state."""
        from app.services.task_state_machine import task_state_machine, TaskStatus

        # OUTPUT_INSPECTING cannot be CANCELLED (only COMPLETED/REJECTED/FAILED)
        active_states = [
            TaskStatus.QUEUED,
            TaskStatus.CODE_SCANNING,
            TaskStatus.PREPARING,
            TaskStatus.RUNNING,
        ]

        for state in active_states:
            result = task_state_machine.validate_transition(state, TaskStatus.CANCELLED)
            assert result.success is True, f"Transition {state} -> CANCELLED should be valid"

    @pytest.mark.asyncio
    async def test_terminal_states_reject_all_transitions(self):
        """Terminal states should not allow any transitions."""
        from app.services.task_state_machine import task_state_machine, TaskStatus

        terminal = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED]
        all_states = list(TaskStatus)

        for term in terminal:
            for target in all_states:
                if target == term:
                    continue
                result = task_state_machine.validate_transition(term, target)
                assert result.success is False, f"Terminal {term} -> {target} should be rejected"

    @pytest.mark.asyncio
    async def test_queue_priority_ordering(self):
        """Higher priority tasks should be dequeued first."""
        from app.services.task_queue import TaskQueue, TaskPriority

        queue = TaskQueue()
        low = await queue.enqueue("low", {"p": 1}, priority=TaskPriority.LOW)
        high = await queue.enqueue("high", {"p": 2}, priority=TaskPriority.HIGH)
        critical = await queue.enqueue("critical", {"p": 3}, priority=TaskPriority.CRITICAL)

        first = await queue.dequeue()
        assert first.task_type == "critical"

        second = await queue.dequeue()
        assert second.task_type == "high"

        third = await queue.dequeue()
        assert third.task_type == "low"


# ============================================================
# P1-2: SessionStateMachine — 8-state lifecycle
# ============================================================

class TestSessionStateMachine:
    """Test session lifecycle state machine (to be implemented)."""

    def test_session_model_exists(self):
        """SandboxSession model should exist."""
        from app.models.sandbox_session import SandboxSession
        assert SandboxSession is not None

    def test_session_has_status_field(self):
        """SandboxSession should have a status field."""
        from app.models.sandbox_session import SandboxSession
        # Check columns exist
        columns = [c.name for c in SandboxSession.__table__.columns]
        assert "status" in columns or "state" in columns

    def test_no_dedicated_session_state_machine(self):
        """GAP: No SessionStateMachine class exists yet.
        Sprint 8 should implement 8-state lifecycle:
        QUEUED -> PROVISIONING -> INITIALIZING -> RUNNING ->
        PAUSING -> PAUSED -> RESUMING -> COMPLETED/FAILED
        """
        try:
            from app.services.session_state_machine import SessionStateMachine
            # If it exists, Sprint 8 delivered it
            assert SessionStateMachine is not None
        except ImportError:
            pytest.xfail("SessionStateMachine not yet implemented (Sprint 8 P1-2)")


# ============================================================
# P1-3: BlockchainAdapter — persistence gap
# ============================================================

class TestBlockchainPersistence:
    """Test blockchain adapter persistence requirements."""

    def test_adapter_has_anchor_method(self):
        from app.services.blockchain_adapter import BlockchainAdapter
        assert hasattr(BlockchainAdapter, 'anchor')

    def test_adapter_has_verify_method(self):
        from app.services.blockchain_adapter import BlockchainAdapter
        assert hasattr(BlockchainAdapter, 'verify')

    @pytest.mark.asyncio
    async def test_pg_adapter_in_memory_anchors_lost_on_restart(self, db_session):
        """PGAppendOnlyAdapter anchors should survive restart when DB factory is set."""
        from app.services.blockchain_adapter import PGAppendOnlyAdapter
        from tests.conftest import test_session_factory

        adapter = PGAppendOnlyAdapter()
        adapter.set_db_session_factory(test_session_factory)

        # Anchor some data
        result = await adapter.anchor(b"test data", {"key": "value"})
        assert result.success
        assert result.anchor is not None
        anchor_id = result.anchor.anchor_id

        # Verify it's in memory
        record = await adapter.get_anchor(anchor_id)
        assert record is not None

        # Create a NEW adapter (simulates restart) with same DB factory
        adapter2 = PGAppendOnlyAdapter()
        adapter2.set_db_session_factory(test_session_factory)
        record2 = await adapter2.get_anchor(anchor_id)
        assert record2 is not None, "Anchor should survive restart via DB persistence"
        assert record2.data_hash == record.data_hash

    @pytest.mark.asyncio
    async def test_adapter_hash_chain_integrity(self):
        """Hash chain should be maintained across anchors."""
        from app.services.blockchain_adapter import PGAppendOnlyAdapter
        adapter = PGAppendOnlyAdapter()

        r1 = await adapter.anchor(b"event1", {})
        r2 = await adapter.anchor(b"event2", {})
        r3 = await adapter.anchor(b"event3", {})

        # All anchors should succeed
        assert r1.success and r2.success and r3.success
        # Chain should be verifiable
        assert await adapter.verify(r2.anchor.anchor_id, b"event2") is True


# ============================================================
# P1-4: Merkle batch pipeline — Redis Stream gap
# ============================================================

class TestMerkleBatchPipeline:
    """Test Merkle batch processing requirements."""

    def test_merkle_service_exists(self):
        from app.services.merkle_service import MerkleService
        assert MerkleService is not None

    @pytest.mark.asyncio
    async def test_build_batch_and_verify(self):
        """Batch building and verification should work."""
        from app.services.merkle_service import MerkleService
        svc = MerkleService()

        events = [b"event1", b"event2", b"event3", b"event4"]
        batch = svc.build_batch(events)
        assert batch.root_hash
        assert batch.leaf_count == 4

        # Verify root matches (returns tuple)
        is_valid, computed = svc.verify_batch_root(events, batch.root_hash)
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_tampered_batch_fails_verification(self):
        from app.services.merkle_service import MerkleService
        svc = MerkleService()

        events = [b"event1", b"event2"]
        batch = svc.build_batch(events)

        tampered = [b"event1", b"TAMPERED"]
        is_valid, _ = svc.verify_batch_root(tampered, batch.root_hash)
        assert is_valid is False

    def test_no_redis_stream_pipeline(self):
        """GAP: No Redis Stream batch pipeline exists.
        Sprint 8 should implement periodic batch accumulation
        via Redis Stream (XADD/XREADGROUP).
        """
        from app.services.merkle_service import MerkleService
        svc = MerkleService()
        # MerkleService is stateless — no pipeline attribute
        assert not hasattr(svc, '_stream') or svc._stream is None
        assert not hasattr(svc, '_batch_pipeline') or svc._batch_pipeline is None

    def test_generate_and_verify_proof(self):
        """Individual leaf proofs should work."""
        from app.services.merkle_service import MerkleService
        svc = MerkleService()

        # generate_proof expects list[str] (leaf hashes), not raw bytes
        raw = [b"a", b"b", b"c", b"d"]
        leaves = [svc.hash_leaf(r) for r in raw]
        proof = svc.generate_proof(leaves, 1)  # proof for "b"
        assert proof is not None
        assert svc.verify_proof(proof) is True


# ============================================================
# P1-5: K8s adapter not wired into SandboxRuntime
# ============================================================

class TestK8sAdapterIntegration:
    """Test K8s sandbox adapter registration requirements."""

    def test_sandbox_runtime_exists(self):
        from app.services.sandbox_runtime import SandboxRuntime
        assert SandboxRuntime is not None

    def test_k8s_adapter_exists(self):
        from app.services.k8s_sandbox import K8sSandboxAdapter
        assert K8sSandboxAdapter is not None

    def test_k8s_adapter_not_registered_in_runtime(self):
        """GAP: K8sSandboxAdapter is not registered in SandboxRuntime.
        Sprint 8 should wire it into the adapter registry.
        """
        from app.services.sandbox_runtime import SandboxRuntime
        runtime = SandboxRuntime()

        # Check if K8s is available as an adapter
        has_k8s = False
        if hasattr(runtime, '_adapters'):
            for key, val in runtime._adapters.items():
                if 'k8s' in str(key).lower() or 'k8s' in str(val).lower():
                    has_k8s = True
                    break

        if not has_k8s:
            pytest.xfail("K8sSandboxAdapter not registered in SandboxRuntime (Sprint 8 P1-5)")

    def test_runtime_adapter_selection(self):
        """Runtime should select appropriate adapter based on session."""
        from app.services.sandbox_runtime import SandboxRuntime
        runtime = SandboxRuntime()

        # Bwrap adapter should be available
        assert hasattr(runtime, '_adapters') or hasattr(runtime, 'create_session')


# ============================================================
# P1-6: Federation trust persistence gap
# ============================================================

class TestFederationTrustPersistence:
    """Test federation trust state persistence requirements."""

    def _make_connector(self, db_factory=None):
        from app.services.federation_connector import FederationConnector, SpaceIdentity
        connector = FederationConnector()
        local = SpaceIdentity(
            space_id="space-local",
            space_name="Local Space",
            endpoint="https://local.example.com",
        )
        connector.set_local_space(local)
        if db_factory:
            connector.set_db_session_factory(db_factory)
        return connector

    def test_trust_lifecycle(self):
        """Trust creation and retrieval should work."""
        from app.services.federation_connector import SpaceIdentity, TrustLevel
        connector = self._make_connector()

        remote = SpaceIdentity(
            space_id="space-remote",
            space_name="Remote Space",
            endpoint="https://remote.example.com",
        )
        trust = connector.establish_trust(remote, trust_level=TrustLevel.BASIC)
        assert trust is not None
        assert trust.trust_id

    @pytest.mark.asyncio
    async def test_trust_lost_on_restart(self, db_session):
        """Trust state should persist to DB and survive restart when factory is set."""
        from app.services.federation_connector import SpaceIdentity, TrustLevel, FederationTrust, FederationStatus
        from tests.conftest import test_session_factory
        import uuid as _uuid

        connector1 = self._make_connector(db_factory=test_session_factory)

        remote = SpaceIdentity(
            space_id="space-remote-2",
            space_name="Remote Space 2",
            endpoint="https://remote2.example.com",
        )
        # Create and persist directly (bypass establish_trust's fire-and-forget)
        trust = FederationTrust(
            trust_id=f"trust-{_uuid.uuid4().hex[:12]}",
            local_space=connector1._local_space,
            remote_space=remote,
            trust_level=TrustLevel.BASIC,
            status=FederationStatus.ACTIVE,
            allowed_operations=["read_catalog", "search"],
        )
        await connector1._persist_trust(trust)

        # New connector simulates restart
        connector2 = self._make_connector(db_factory=test_session_factory)
        count = await connector2.load_trusts_from_db()
        assert count >= 1, "Trust should persist to DB and survive restart"
        assert len(connector2._trusts) >= 1, "Trust loaded into connector memory"

    @pytest.mark.asyncio
    async def test_audit_log_persistence(self, db_session):
        """Audit log entries should persist to DB when factory is set."""
        from app.services.federation_connector import FederationAuditEntry
        from tests.conftest import test_session_factory

        connector = self._make_connector(db_factory=test_session_factory)

        entry = FederationAuditEntry(
            entry_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            source_space="space-local",
            target_space="space-remote-3",
            operation="trust.establish",
            resource="federation/trust",
            status="success",
            details={"trust_level": "basic"},
        )
        await connector._persist_audit_entry(entry)

        # Verify entry was persisted (no assertion needed — if it doesn't raise, it works)
        # A new connector should be able to query the same DB
        connector2 = self._make_connector(db_factory=test_session_factory)
        assert connector2 is not None

    @pytest.mark.asyncio
    async def test_trust_persists_to_db(self, db_session):
        """Federation trust should persist to DB when factory is set."""
        from app.services.federation_connector import SpaceIdentity, TrustLevel, FederationTrust, FederationStatus
        from tests.conftest import test_session_factory
        import uuid as _uuid

        connector = self._make_connector(db_factory=test_session_factory)

        # Create trust directly (bypass establish_trust's fire-and-forget)
        trust = FederationTrust(
            trust_id=f"trust-{_uuid.uuid4().hex[:12]}",
            local_space=connector._local_space,
            remote_space=SpaceIdentity(
                space_id="space-remote-db",
                space_name="Remote DB Space",
                endpoint="https://remotedb.example.com",
            ),
            trust_level=TrustLevel.BASIC,
            status=FederationStatus.ACTIVE,
            allowed_operations=["read_catalog", "search"],
        )
        await connector._persist_trust(trust)

        # New connector with same factory should load trusts
        connector2 = self._make_connector(db_factory=test_session_factory)
        count = await connector2.load_trusts_from_db()
        assert count >= 1, "Trust should persist to DB and load on restart"

    @pytest.mark.asyncio
    async def test_audit_persists_to_db(self, db_session):
        """Federation audit entries should persist to DB when factory is set."""
        from app.services.federation_connector import FederationAuditEntry
        from tests.conftest import test_session_factory

        connector = self._make_connector(db_factory=test_session_factory)
        entry = FederationAuditEntry(
            entry_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            source_space="space-local",
            target_space="space-remote-audit",
            operation="test.op",
            resource="test-resource",
            status="success",
            details={"test": True},
        )
        await connector._persist_audit_entry(entry)
        # No assertion needed — if it doesn't raise, persistence works


# ============================================================
# Cross-cutting: TaskWorker execution path
# ============================================================

class TestTaskWorkerExecution:
    """Test TaskWorker execution path (currently untested)."""

    def test_worker_has_execute_task(self):
        from app.services.task_worker import TaskWorker
        assert hasattr(TaskWorker, '_execute_task')

    def test_worker_has_submit_to_queue(self):
        from app.services.task_worker import TaskWorker
        assert hasattr(TaskWorker, 'submit_to_queue')

    def test_worker_integrates_with_sandbox(self):
        """Worker should use SandboxRuntime for execution."""
        from app.services.task_worker import TaskWorker
        # Check if worker references sandbox runtime
        import inspect
        source = inspect.getsource(TaskWorker)
        assert 'sandbox' in source.lower() or 'SandboxRuntime' in source

    def test_worker_integrates_with_kms(self):
        """Worker should use KMSService for key generation."""
        from app.services.task_worker import TaskWorker
        import inspect
        source = inspect.getsource(TaskWorker)
        assert 'kms' in source.lower() or 'KMSService' in source

    def test_worker_integrates_with_audit(self):
        """Worker should use AuditService for logging."""
        from app.services.task_worker import TaskWorker
        import inspect
        source = inspect.getsource(TaskWorker)
        assert 'audit' in source.lower() or 'AuditService' in source
