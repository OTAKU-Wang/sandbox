"""Tests for Firecracker microVM runtime adapter."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import shutil

from app.services.firecracker_runtime import (
    FirecrackerRuntime,
    VMConfig,
    VMInstance,
    VMState,
    ExecutionResult,
)


@pytest.fixture
def runtime(tmp_path):
    """Create a FirecrackerRuntime in simulation mode."""
    return FirecrackerRuntime(
        workspace_root=str(tmp_path / "fc-workspace"),
    )


@pytest.fixture
def config():
    return VMConfig(vcpu_count=2, mem_size_mb=512)


# ─── VM Lifecycle ──────────────────────────────

class TestVMLifecycle:
    def test_create_vm(self, runtime, config):
        vm = runtime.create_vm("sess-1", config)
        assert vm.state == VMState.RUNNING
        assert vm.session_id == "sess-1"
        assert vm.vm_id.startswith("fc-")
        assert Path(vm.workspace).exists()

    def test_create_vm_default_config(self, runtime):
        vm = runtime.create_vm("sess-2")
        assert vm.config.vcpu_count == 2
        assert vm.config.mem_size_mb == 512

    def test_list_vms(self, runtime):
        vm1 = runtime.create_vm("s1")
        vm2 = runtime.create_vm("s2")
        vms = runtime.list_vms()
        assert len(vms) == 2
        assert any(v.vm_id == vm1.vm_id for v in vms)
        assert any(v.vm_id == vm2.vm_id for v in vms)

    def test_get_vm(self, runtime):
        vm = runtime.create_vm("s1")
        found = runtime.get_vm(vm.vm_id)
        assert found is not None
        assert found.vm_id == vm.vm_id

    def test_get_vm_not_found(self, runtime):
        assert runtime.get_vm("nonexistent") is None

    def test_destroy_vm(self, runtime):
        vm = runtime.create_vm("s1")
        assert runtime.destroy_vm(vm) is True
        assert vm.state == VMState.STOPPED
        assert runtime.get_vm(vm.vm_id) is None
        assert not Path(vm.workspace).exists()

    def test_destroy_vm_cleans_workspace(self, runtime):
        vm = runtime.create_vm("s1")
        workspace = Path(vm.workspace)
        (workspace / "tmp" / "test.txt").write_text("data")
        runtime.destroy_vm(vm)
        assert not workspace.exists()


# ─── Code Execution ──────────────────────────────

class TestExecution:
    def test_execute_python(self, runtime):
        vm = runtime.create_vm("s1")
        result = runtime.execute(vm, "print('hello firecracker')", "python")
        assert result.exit_code == 0
        assert "hello firecracker" in result.output
        assert result.duration_ms >= 0
        assert result.vm_id == vm.vm_id

    def test_execute_bash(self, runtime):
        vm = runtime.create_vm("s1")
        result = runtime.execute(vm, "echo 'hello bash'", "bash")
        assert result.exit_code == 0
        assert "hello bash" in result.output

    def test_execute_with_error(self, runtime):
        vm = runtime.create_vm("s1")
        result = runtime.execute(vm, "import nonexistent_module_xyz", "python")
        assert result.exit_code != 0

    def test_execute_multiple_times(self, runtime):
        vm = runtime.create_vm("s1")
        r1 = runtime.execute(vm, "print('first')")
        r2 = runtime.execute(vm, "print('second')")
        assert r1.exit_code == 0
        assert r2.exit_code == 0
        assert "first" in r1.output
        assert "second" in r2.output

    def test_execute_stores_code_in_workspace(self, runtime):
        vm = runtime.create_vm("s1")
        runtime.execute(vm, "print('test')")
        code_file = Path(vm.workspace) / "tmp" / "exec.py"
        assert code_file.exists()
        assert "print('test')" in code_file.read_text()

    def test_firecracker_serial_path_uses_hardened_fallback(self, tmp_path):
        runtime = FirecrackerRuntime(workspace_root=str(tmp_path / "fc-workspace"))
        runtime._backend = "firecracker"
        workspace = tmp_path / "fc-workspace" / "tenant" / "vm"
        (workspace / "tmp").mkdir(parents=True)
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        vm = VMInstance(
            vm_id="fc-test",
            session_id="sess-serial",
            socket_path=str(workspace / "fc.sock"),
            api_socket=str(workspace / "fc.sock"),
            state=VMState.RUNNING,
            config=VMConfig(network_enabled=False),
            workspace=str(workspace),
        )

        with patch.object(
            runtime,
            "_simulate_execute_raw",
            return_value={"output": "fallback ok", "exit_code": 0},
        ) as fallback:
            result = runtime.execute(
                vm,
                "print('serial fallback')",
                "python",
                session_key="secret",
                env_vars={"CDS_SESSION_ID": "sess-serial"},
                timeout=7,
            )

        assert result.exit_code == 0
        assert result.output == "fallback ok"
        fallback.assert_called_once()
        args, kwargs = fallback.call_args
        assert args[0] is vm
        assert "serial fallback" in args[1]
        assert args[2] == "python"
        assert kwargs["session_key"] == "secret"
        assert kwargs["env_vars"]["CDS_SESSION_ID"] == "sess-serial"
        assert kwargs["timeout"] == 7

    def test_seccomp_invalid_argument_triggers_retry(self):
        assert FirecrackerRuntime._seccomp_retry_needed(
            "bwrap: prctl(PR_SET_SECCOMP): Invalid argument",
            3,
        ) is True
        assert FirecrackerRuntime._seccomp_retry_needed("", None) is False


# ─── File Transfer ──────────────────────────────

class TestFileTransfer:
    def test_transfer_file(self, runtime, tmp_path):
        vm = runtime.create_vm("s1")
        src = tmp_path / "data.csv"
        src.write_text("a,b,c\n1,2,3")
        result = runtime.transfer_file(vm, str(src), "/workspace/input/data.csv")
        assert result is True

    def test_retrieve_file(self, runtime, tmp_path):
        vm = runtime.create_vm("s1")
        # Write to output dir
        output = Path(vm.workspace) / "output" / "result.csv"
        output.write_text("x,y\n1,2")
        dest = tmp_path / "retrieved.csv"
        result = runtime.retrieve_file(vm, "/workspace/output/result.csv", str(dest))
        assert result is True
        assert dest.exists()
        assert dest.read_text() == "x,y\n1,2"

    def test_retrieve_nonexistent_file(self, runtime, tmp_path):
        vm = runtime.create_vm("s1")
        dest = tmp_path / "nope.csv"
        result = runtime.retrieve_file(vm, "/workspace/output/nope.csv", str(dest))
        assert result is False


# ─── VM State Management ──────────────────────────────

class TestVMState:
    def test_pause_vm(self, runtime):
        vm = runtime.create_vm("s1")
        assert runtime.pause_vm(vm) is True
        assert vm.state == VMState.PAUSED

    def test_resume_vm(self, runtime):
        vm = runtime.create_vm("s1")
        runtime.pause_vm(vm)
        assert runtime.resume_vm(vm) is True
        assert vm.state == VMState.RUNNING

    def test_pause_resume_cycle(self, runtime):
        vm = runtime.create_vm("s1")
        runtime.pause_vm(vm)
        runtime.resume_vm(vm)
        result = runtime.execute(vm, "print('still works')")
        assert result.exit_code == 0


# ─── Metrics ──────────────────────────────

class TestMetrics:
    def test_get_vm_metrics(self, runtime):
        vm = runtime.create_vm("s1")
        metrics = runtime.get_vm_metrics(vm)
        assert metrics["vm_id"] == vm.vm_id
        assert metrics["state"] == VMState.RUNNING.value
        assert metrics["mem_total_mb"] == 512
        assert metrics["vcpu_count"] == 2
        assert metrics["uptime_seconds"] >= 0


# ─── VMConfig ──────────────────────────────

class TestVMConfig:
    def test_default_config(self):
        config = VMConfig()
        assert config.vcpu_count == 2
        assert config.mem_size_mb == 512
        assert config.network_enabled is False
        assert config.disk_size_mb == 1024

    def test_custom_config(self):
        config = VMConfig(vcpu_count=4, mem_size_mb=1024, network_enabled=True)
        assert config.vcpu_count == 4
        assert config.mem_size_mb == 1024
        assert config.network_enabled is True


# ─── Simulation Mode ──────────────────────────────

class TestSimulationMode:
    def test_simulation_mode_detected(self, runtime):
        assert runtime._simulation_mode is True

    def test_simulation_execute_works(self, runtime):
        vm = runtime.create_vm("s1")
        result = runtime.execute(vm, "x = 1 + 1\nprint(x)")
        assert result.exit_code == 0
        assert "2" in result.output

    def test_simulation_destroy_cleans_up(self, runtime):
        vm = runtime.create_vm("s1")
        runtime.destroy_vm(vm)
        assert not Path(vm.workspace).exists()


# ─── SandboxRuntime Integration ──────────────────────

class TestSandboxRuntimeIntegration:
    def test_firecracker_adapter_provision(self):
        from app.services.sandbox_runtime import FirecrackerAdapter
        adapter = FirecrackerAdapter()
        result = adapter.provision("test-sess", "", 3600)
        assert result["status"] == "running"
        assert result["container_id"].startswith("fc-")

    @pytest.mark.asyncio
    async def test_firecracker_adapter_execute(self):
        from app.services.sandbox_runtime import FirecrackerAdapter
        adapter = FirecrackerAdapter()
        provision = adapter.provision("test-sess-exec", "", 3600)
        result = await adapter.execute(provision["container_id"], "print('adapter test')")
        assert result["exit_code"] == 0
        assert "adapter test" in result["output"]

    def test_firecracker_adapter_terminate(self):
        from app.services.sandbox_runtime import FirecrackerAdapter
        adapter = FirecrackerAdapter()
        provision = adapter.provision("test-sess-term", "", 3600)
        assert adapter.terminate(provision["container_id"]) is True

    def test_firecracker_adapter_get_status(self):
        from app.services.sandbox_runtime import FirecrackerAdapter
        adapter = FirecrackerAdapter()
        provision = adapter.provision("test-sess-status", "", 3600)
        cid = provision["container_id"]
        assert adapter.get_status(cid) == "running"
        adapter.terminate(cid)
        assert adapter.get_status(cid) == "terminated"
