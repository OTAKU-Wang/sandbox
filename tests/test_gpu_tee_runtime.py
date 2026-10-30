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


# ── A132 GPU-TEE 双胞胎：披露、拒绝、标签改实（2026-10-30）─────────────────
# ①模拟器全部产物自带 is_simulation 并透传 API；②auto 无真硬件响亮拒绝
# RUNTIME_UNAVAILABLE（SS-13），与显式开关互不越界；③32 字节密钥不再标 SM4。

import ast
from pathlib import Path

from app.core.errors import CDSError

SIM_SOURCE = Path(__file__).resolve().parent.parent / "app" / "services" / "gpu_tee_simulator.py"


class TestA132SimulatorDisclosure:
    """模拟器证明报告不许走默认值冒充真 GPU-TEE（禁伪造铁律）。"""

    @pytest.mark.asyncio
    async def test_simulator_attestation_discloses_simulation(self):
        from app.services.gpu_tee_simulator import GPUTeeSimulator

        report = await GPUTeeSimulator().attest()
        assert report.is_simulation is True
        assert report.tee_backend == "cpu_simulation"

    @pytest.mark.asyncio
    async def test_initialize_gpu_cc_attestation_is_disclosed(self):
        """initialize_gpu_cc 内部使用的报告同样必须带披露。"""
        from app.services.gpu_tee_simulator import GPUTeeSimulator

        sim = GPUTeeSimulator()
        await sim.initialize_gpu_cc("a132-session")
        assert sim._attestation_report is not None
        assert sim._attestation_report.is_simulation is True
        assert sim._attestation_report.tee_backend == "cpu_simulation"

    @pytest.mark.asyncio
    async def test_all_simulator_artifacts_disclose(self):
        from app.services.gpu_tee_simulator import GPUTeeSimulator

        sim = GPUTeeSimulator()
        alloc = await sim.allocate_secure_memory(64)
        job = await sim.submit_compute(ComputeMode.TRAINING, b"x")
        metrics = await sim.get_metrics()
        handle = await sim.initialize_gpu_cc("a132")
        epoch = await sim.run_training_epoch(handle, [b"a"])
        assert alloc.is_simulation is True
        assert job.is_simulation is True
        assert metrics.is_simulation is True
        assert epoch.is_simulation is True
        assert sim.execute_training(config={"epochs": 1}).is_simulation is True

    def test_channel_key_label_is_truthful(self):
        """③：SM4 真密钥只有 128-bit——32 字节 AES 密钥绝不许再标 SM4-GCM。"""
        from app.services.gpu_tee_simulator import GPUTeeSimulator

        ch = GPUTeeSimulator().create_secure_channel()
        assert len(ch.key) == 32  # 256-bit
        assert ch.algorithm.startswith("AES-256-GCM")
        assert "SM4 substitute" in ch.algorithm  # 只能以「SM4 替身」身份出现
        assert ch.algorithm != "SM4-GCM"
        assert ch.is_simulation is True

    def test_structural_pin_simulator_never_builds_undisclosed_report(self):
        """结构针：模拟器源文件里每一处共享产物 dataclass 构造都必须显式
        is_simulation=True——这些 dataclass 默认值是 False（真硬件语义），
        漏传一个关键字就复活 A132 双胞胎缺陷。AST 解析，重排/重构也躲不掉。"""
        tree = ast.parse(SIM_SOURCE.read_text(encoding="utf-8"))
        risky = {"GPUAttestationReport", "SecureAllocation", "ComputeJob", "GPUMetrics", "EpochMetrics"}
        found = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in risky:
                kw = {k.arg for k in node.keywords}
                assert "is_simulation" in kw, (
                    f"gpu_tee_simulator.py line {node.lineno}: {node.func.id}(...) 构造缺 is_simulation=True —— 模拟报告必带 is_simulation"
                )
                sim_kw = next(k for k in node.keywords if k.arg == "is_simulation")
                assert isinstance(sim_kw.value, ast.Constant) and sim_kw.value.value is True
                found += 1
        assert found >= 5, f"结构针期望至少 5 处产物构造，只找到 {found} —— 构造被改名/移除须同步更新本针"

    def test_simulator_owned_dataclasses_default_to_disclosed(self):
        """Simulator 专属 dataclass（SecureChannel/TrainingResult）默认必须 True：
        它们只由模拟器产出，默认 False 就是下一个双胞胎的温床。"""
        from app.services.gpu_tee_simulator import SecureChannel, TrainingResult

        import datetime as _dt
        ch = SecureChannel(key=b"0" * 32, created_at=_dt.datetime.now(_dt.timezone.utc))
        tr = TrainingResult(loss=1.0, epochs=1, duration_ms=1)
        assert ch.is_simulation is True
        assert tr.is_simulation is True


class TestA132AutoRefusesSilentFallback:
    """② SS-13 铁律：auto 在无真 GPU-TEE 且未显式开模拟时必须响亮拒绝。"""

    def test_auto_without_opt_in_raises_runtime_unavailable(self, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", False)
        with pytest.raises(CDSError) as ei:
            GPUTeeRuntimeFactory.create("auto")
        assert ei.value.code == "RUNTIME_UNAVAILABLE"
        assert ei.value.http_status == 400
        assert ei.value.detail["reason"] == "gpu_tee_real_runtime_missing"

    def test_auto_with_opt_in_selects_disclosed_simulator(self, monkeypatch):
        from app.core.config import get_settings
        from app.services.gpu_tee_simulator import GPUTeeSimulator

        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", True)
        assert isinstance(GPUTeeRuntimeFactory.create("auto"), GPUTeeSimulator)

    def test_flag_does_not_hijack_named_choice_and_vice_versa(self, monkeypatch):
        """互不越界：开关不改写显式点名，点名也不是 auto 的后门。"""
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", True)
        assert isinstance(GPUTeeRuntimeFactory.create("stub"), GPUTeeRuntimeStub)
        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", False)
        assert isinstance(GPUTeeRuntimeFactory.create("local"), GPUTeeRuntimeStub)

    def test_default_runtime_is_none_without_opt_in(self, monkeypatch):
        """import 即得模拟器同样被禁：未开模拟时默认单例为 None（诚实的没有），
        而不是一个伪装成硬件的替身。"""
        from app.core.config import get_settings
        from app.services import gpu_tee_runtime as gtr
        from app.services.gpu_tee_runtime import GPUTeeRuntime
        from app.services.gpu_tee_simulator import GPUTeeSimulator

        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", False)
        assert gtr._build_default_runtime() is None
        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", True)
        built = gtr._build_default_runtime()
        assert isinstance(built, GPUTeeRuntime)
        assert isinstance(built, GPUTeeSimulator)  # 开启模拟时默认单例=披露型模拟器

    def test_shared_artifact_defaults_stay_false_for_real_runtime(self):
        """新增披露字段默认 False（真硬件语义）——只强不弱：不得反向污染
        真实现语义（与 pz04 的 AttestationReport 默认值针同构）。"""
        alloc = SecureAllocation(allocation_id="a", size_bytes=1)
        job = ComputeJob(job_id="j", mode=ComputeMode.INFERENCE)
        met = GPUMetrics(gpu_device=0, gpu_model="real", memory_total_mb=1, memory_used_mb=0,
                         utilization_pct=0.0, temperature_c=1.0, tee_active=True)
        assert alloc.is_simulation is False
        assert job.is_simulation is False
        assert met.is_simulation is False
        assert EpochMetrics().is_simulation is False


class TestA132StubArtifactsDisclose:
    """stub 与模拟器同族：attest 之外的产物也要自带披露。"""

    @pytest.mark.asyncio
    async def test_stub_artifacts_disclose(self, runtime):
        alloc = await runtime.allocate_secure_memory(32)
        job = await runtime.submit_compute(ComputeMode.ANALYSIS, b"z")
        metrics = await runtime.get_metrics()
        handle = await runtime.initialize_gpu_cc("a132-stub")
        epoch = await runtime.run_training_epoch(handle, [b"b"])
        assert alloc.is_simulation is True
        assert job.is_simulation is True
        assert metrics.is_simulation is True
        assert epoch.is_simulation is True


class TestA132ApiPropagation:
    """披露沿链透传：security-posture 的 gpu_tee 卡两种状态下都携带 is_simulation 证据。"""

    @pytest.mark.asyncio
    async def test_posture_gpu_card_carries_is_simulation_evidence(self, client, operator_headers, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", True)
        resp = await client.get("/api/v1/monitoring/security-posture", headers=operator_headers)
        assert resp.status_code == 200, resp.text
        card = next(c for c in resp.json()["capabilities"] if c["id"] == "gpu_tee")
        assert card["status"] == "software"
        assert "is_simulation=true" in card["evidence"]
        assert "is_simulation=true" in card["summary"]

    @pytest.mark.asyncio
    async def test_posture_gpu_card_when_simulation_disabled_is_honest(self, client, operator_headers, monkeypatch):
        from app.core.config import get_settings

        monkeypatch.setattr(get_settings(), "GPU_TEE_SIMULATION", False)
        resp = await client.get("/api/v1/monitoring/security-posture", headers=operator_headers)
        assert resp.status_code == 200, resp.text
        card = next(c for c in resp.json()["capabilities"] if c["id"] == "gpu_tee")
        # 关闭模拟 ≠ 已配置真硬件：不再谎称 configured，按 SS-13 报 risk/block。
        assert card["status"] == "risk"
        assert card["release_gate"] == "block"
        assert "RUNTIME_UNAVAILABLE" in card["summary"]
        assert "is_simulation=false" in card["evidence"]
