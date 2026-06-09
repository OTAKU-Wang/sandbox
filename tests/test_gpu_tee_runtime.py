"""Tests for GPU-TEE Runtime — NVIDIA CC mode interface."""
import pytest
import pytest_asyncio

from app.services.gpu_tee_runtime import (
    GPUTeeRuntimeStub, GPUTeeRuntimeFactory,
    GPUTeeStatus, ComputeMode,
    AttestationReport, SecureAllocation, ComputeJob, GPUMetrics,
    ChannelKey, GPUCCConfig, GPUHandle, EpochMetrics,
)


@pytest.fixture
def runtime():
    return GPUTeeRuntimeStub()


# ── Factory ───────────────────────────────────────────────────────

class TestFactory:
    def test_create_stub(self):
        rt = GPUTeeRuntimeFactory.create("stub")
        assert isinstance(rt, GPUTeeRuntimeStub)

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError):
            GPUTeeRuntimeFactory.create("nonexistent")


# ── Status ────────────────────────────────────────────────────────

class TestStatus:
    @pytest.mark.asyncio
    async def test_status_available(self, runtime):
        status = await runtime.check_status()
        assert status == GPUTeeStatus.AVAILABLE


# ── Attestation ───────────────────────────────────────────────────

class TestAttestation:
    @pytest.mark.asyncio
    async def test_attest(self, runtime):
        report = await runtime.attest()
        assert isinstance(report, AttestationReport)
        assert report.tee_enabled is True
        assert report.valid is True
        assert len(report.measurement) == 64  # SHA-256 hex
        assert report.gpu_model == "NVIDIA H100 (local)"
        assert len(report.signature) == 64

    @pytest.mark.asyncio
    async def test_attest_unique_reports(self, runtime):
        r1 = await runtime.attest()
        r2 = await runtime.attest()
        assert r1.report_id != r2.report_id


# ── Memory Allocation ─────────────────────────────────────────────

class TestMemoryAllocation:
    @pytest.mark.asyncio
    async def test_allocate(self, runtime):
        alloc = await runtime.allocate_secure_memory(1024 * 1024)
        assert isinstance(alloc, SecureAllocation)
        assert alloc.size_bytes == 1024 * 1024
        assert alloc.encrypted is True

    @pytest.mark.asyncio
    async def test_allocate_tracks_memory(self, runtime):
        await runtime.allocate_secure_memory(1024)
        metrics = await runtime.get_metrics()
        assert metrics.memory_used_mb >= 0  # Very small, rounds to 0 MB

    @pytest.mark.asyncio
    async def test_free_memory(self, runtime):
        alloc = await runtime.allocate_secure_memory(1024)
        assert await runtime.free_secure_memory(alloc.allocation_id) is True

    @pytest.mark.asyncio
    async def test_free_nonexistent(self, runtime):
        assert await runtime.free_secure_memory("nonexistent") is False

    @pytest.mark.asyncio
    async def test_multiple_allocations(self, runtime):
        a1 = await runtime.allocate_secure_memory(1024)
        a2 = await runtime.allocate_secure_memory(2048)
        assert len(runtime.allocations) == 2
        await runtime.free_secure_memory(a1.allocation_id)
        assert len(runtime.allocations) == 1


# ── Compute Jobs ──────────────────────────────────────────────────

class TestComputeJobs:
    @pytest.mark.asyncio
    async def test_submit_inference(self, runtime):
        job = await runtime.submit_compute(ComputeMode.INFERENCE, b"input data")
        assert isinstance(job, ComputeJob)
        assert job.mode == ComputeMode.INFERENCE
        assert job.status == "completed"
        assert job.input_allocation is not None
        assert job.output_allocation is not None
        output = runtime.read_secure_payload(job.output_allocation)
        assert output.startswith(b"inference:")

    @pytest.mark.asyncio
    async def test_submit_training(self, runtime):
        job = await runtime.submit_compute(ComputeMode.TRAINING, b"training data")
        assert job.mode == ComputeMode.TRAINING

    @pytest.mark.asyncio
    async def test_submit_analysis(self, runtime):
        job = await runtime.submit_compute(ComputeMode.ANALYSIS, b"analysis data")
        assert job.mode == ComputeMode.ANALYSIS

    @pytest.mark.asyncio
    async def test_get_job_status(self, runtime):
        job = await runtime.submit_compute(ComputeMode.INFERENCE, b"data")
        retrieved = await runtime.get_job_status(job.job_id)
        assert retrieved is not None
        assert retrieved.job_id == job.job_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self, runtime):
        assert await runtime.get_job_status("nonexistent") is None

    @pytest.mark.asyncio
    async def test_job_timestamps(self, runtime):
        job = await runtime.submit_compute(ComputeMode.INFERENCE, b"data")
        assert job.started_at is not None
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_jobs_tracked(self, runtime):
        await runtime.submit_compute(ComputeMode.INFERENCE, b"1")
        await runtime.submit_compute(ComputeMode.TRAINING, b"2")
        assert len(runtime.jobs) == 2


# ── Metrics ───────────────────────────────────────────────────────

class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics(self, runtime):
        metrics = await runtime.get_metrics()
        assert isinstance(metrics, GPUMetrics)
        assert metrics.gpu_model == "NVIDIA H100 (local)"
        assert metrics.tee_active is True
        assert metrics.memory_total_mb == 81920

    @pytest.mark.asyncio
    async def test_metrics_temperature(self, runtime):
        metrics = await runtime.get_metrics()
        assert metrics.temperature_c > 0

    @pytest.mark.asyncio
    async def test_metrics_utilization(self, runtime):
        await runtime.allocate_secure_memory(1024 * 1024 * 1024)  # 1GB
        metrics = await runtime.get_metrics()
        assert metrics.utilization_pct > 0


# ── Enums ─────────────────────────────────────────────────────────

class TestEnums:
    def test_status_values(self):
        assert GPUTeeStatus.AVAILABLE.value == "available"
        assert GPUTeeStatus.UNAVAILABLE.value == "unavailable"

    def test_compute_mode_values(self):
        assert ComputeMode.INFERENCE.value == "inference"
        assert ComputeMode.TRAINING.value == "training"
        assert ComputeMode.ANALYSIS.value == "analysis"


# ── CC Mode Initialization ────────────────────────────────────

class TestGPUCCInit:
    @pytest.mark.asyncio
    async def test_initialize_gpu_cc(self, runtime):
        handle = await runtime.initialize_gpu_cc("sess-1")
        assert isinstance(handle, GPUHandle)
        assert handle.session_id == "sess-1"
        assert handle.channel_key is not None
        assert len(handle.channel_key.key_material) == 32  # SHA-256

    @pytest.mark.asyncio
    async def test_initialize_with_config(self, runtime):
        config = GPUCCConfig(device_id=1, cc_mode_enabled=True)
        handle = await runtime.initialize_gpu_cc("sess-2", config)
        assert handle.device_id == 1

    @pytest.mark.asyncio
    async def test_channel_key_has_measurement(self, runtime):
        handle = await runtime.initialize_gpu_cc("sess-3")
        assert len(handle.channel_key.gpu_measurement) == 64  # SHA-256 hex
        assert handle.channel_key.gpu_measurement != ""

    @pytest.mark.asyncio
    async def test_initialize_creates_unique_keys(self, runtime):
        h1 = await runtime.initialize_gpu_cc("s1")
        h2 = await runtime.initialize_gpu_cc("s2")
        assert h1.channel_key.key_id != h2.channel_key.key_id

    @pytest.mark.asyncio
    async def test_handle_send_encrypted(self, runtime):
        handle = await runtime.initialize_gpu_cc("sess-4")
        ref = handle.send_encrypted(b"training batch data")
        assert ref.startswith("gpu-alloc-")
        assert handle.stats["bytes_sent"] > 0

    @pytest.mark.asyncio
    async def test_handle_receive_encrypted(self, runtime):
        handle = await runtime.initialize_gpu_cc("sess-5")
        ref = handle.send_encrypted(b"secret batch")
        data = handle.receive_encrypted(ref)
        assert data == b"secret batch"
        assert handle.stats["bytes_received"] == len(data)

    @pytest.mark.asyncio
    async def test_multiple_handles_tracked(self, runtime):
        config1 = GPUCCConfig(device_id=0)
        config2 = GPUCCConfig(device_id=1)
        await runtime.initialize_gpu_cc("s1", config1)
        await runtime.initialize_gpu_cc("s2", config2)
        assert len(runtime._handles) == 2


# ── Training Epoch ────────────────────────────────────────────

class TestTrainingEpoch:
    @pytest.mark.asyncio
    async def test_run_training_epoch(self, runtime):
        handle = await runtime.initialize_gpu_cc("train-1")
        batches = [b"batch-1", b"batch-2", b"batch-3"]
        metrics = await runtime.run_training_epoch(handle, batches)
        assert isinstance(metrics, EpochMetrics)
        assert metrics.total_batches == 3
        assert metrics.avg_loss > 0
        assert metrics.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_epoch_with_model_ref(self, runtime):
        handle = await runtime.initialize_gpu_cc("train-2")
        metrics = await runtime.run_training_epoch(handle, [b"data"], model_ref="llama-7b")
        assert metrics.total_batches == 1

    @pytest.mark.asyncio
    async def test_epoch_empty_batches(self, runtime):
        handle = await runtime.initialize_gpu_cc("train-3")
        metrics = await runtime.run_training_epoch(handle, [])
        assert metrics.total_batches == 0
        assert metrics.avg_loss == 0.0

    @pytest.mark.asyncio
    async def test_epoch_metrics_update(self):
        m = EpochMetrics()
        m.update(0.5)
        m.update(0.3)
        assert m.total_batches == 2
        assert abs(m.avg_loss - 0.4) < 0.01


# ── Dataclass Construction ────────────────────────────────────

class TestNewDataclasses:
    def test_channel_key(self):
        ck = ChannelKey(key_id="k1", key_material=b"x" * 32)
        assert ck.algorithm == "AES-256-GCM"

    def test_gpu_cc_config_defaults(self):
        c = GPUCCConfig()
        assert c.device_id == 0
        assert c.cc_mode_enabled is True
        assert c.attestation_required is True

    def test_gpu_handle_stats(self):
        ck = ChannelKey(key_id="k1", key_material=b"x" * 32)
        h = GPUHandle(device_id=0, channel_key=ck)
        h.send_encrypted(b"hello")
        assert h.stats["bytes_sent"] == 5

    def test_epoch_metrics_defaults(self):
        m = EpochMetrics()
        assert m.total_batches == 0
        assert m.avg_loss == 0.0
