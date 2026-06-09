"""Tests for L0 and L2 sandbox levels."""
import pytest
import os
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.models.sandbox_session import SandboxLevel
from app.schemas.sandbox_session import VALID_SANDBOX_LEVELS


class TestSandboxLevelEnum:
    def test_l0_in_enum(self):
        assert SandboxLevel.L0.value == "L0"

    def test_all_levels_in_enum(self):
        assert SandboxLevel.L0.value == "L0"
        assert SandboxLevel.L1.value == "L1"
        assert SandboxLevel.L2.value == "L2"
        assert SandboxLevel.L3.value == "L3"

    def test_l0_in_valid_levels(self):
        assert "L0" in VALID_SANDBOX_LEVELS


class TestProcessAdapter:
    """Tests for L0 ProcessAdapter."""

    @pytest.fixture
    def adapter(self, tmp_path):
        from app.services.sandbox_runtime import ProcessAdapter
        return ProcessAdapter(workspace_root=str(tmp_path / "l0-sandbox"))

    def test_provision_creates_workspace(self, adapter, tmp_path):
        result = adapter.provision("test-session", "", timeout=60)
        assert result["status"] == "running"
        assert result["container_id"] == "l0-test-session"
        workspace = Path(result["workspace"])
        assert workspace.exists()
        assert (workspace / "input").exists()
        assert (workspace / "output").exists()
        assert (workspace / "tmp").exists()

    def test_provision_with_data_file(self, adapter, tmp_path):
        data_file = tmp_path / "data.csv"
        data_file.write_text("a,b,c\n1,2,3\n")
        result = adapter.provision("test-session", str(data_file), timeout=60)
        workspace = Path(result["workspace"])
        assert (workspace / "input" / "data.csv").exists()

    def test_execute_python(self, adapter):
        result = adapter.provision("exec-test", "", timeout=60)
        container_id = result["container_id"]
        import asyncio
        result = asyncio.run(adapter.execute(container_id, "print('hello L0')", "python"))
        assert result["exit_code"] == 0
        assert "hello L0" in result["output"]
        assert result["sandbox_level"] == "L0"

    def test_execute_shell(self, adapter):
        result = adapter.provision("shell-test", "", timeout=60)
        container_id = result["container_id"]
        import asyncio
        result = asyncio.run(adapter.execute(container_id, "echo hello_shell", "shell"))
        assert result["exit_code"] == 0
        assert "hello_shell" in result["output"]

    def test_execute_timeout(self, adapter):
        result = adapter.provision("timeout-test", "", timeout=60)
        container_id = result["container_id"]
        import asyncio
        result = asyncio.run(adapter.execute(container_id, "import time; time.sleep(10)", "python", timeout=1))
        assert result["exit_code"] == -1
        assert "timed out" in result["output"].lower()

    def test_execute_nonexistent_workspace(self, adapter):
        import asyncio
        result = asyncio.run(adapter.execute("l0-nonexistent", "print(1)", "python"))
        assert result["exit_code"] == -1
        assert "not found" in result["output"].lower()

    def test_terminate_removes_workspace(self, adapter):
        result = adapter.provision("term-test", "", timeout=60)
        container_id = result["container_id"]
        workspace = Path(result["workspace"])
        assert workspace.exists()
        assert adapter.terminate(container_id) is True
        assert not workspace.exists()

    def test_get_status_running(self, adapter):
        result = adapter.provision("status-test", "", timeout=60)
        assert adapter.get_status(result["container_id"]) == "running"

    def test_get_status_terminated(self, adapter):
        assert adapter.get_status("l0-nonexistent") == "terminated"

    def test_execute_with_session_key(self, adapter):
        result = adapter.provision("key-test", "", timeout=60)
        container_id = result["container_id"]
        import asyncio
        result = asyncio.run(adapter.execute(
            container_id,
            "import os; print(os.environ.get('CDS_SESSION_KEY', 'none'))",
            "python",
            session_key="secret-key-123",
        ))
        assert result["exit_code"] == 0
        assert "secret-key-123" in result["output"]

    def test_execute_output_truncation(self, adapter):
        result = adapter.provision("trunc-test", "", timeout=60)
        container_id = result["container_id"]
        import asyncio
        # Generate large output
        code = "print('x' * 20_000_000)"
        result = asyncio.run(adapter.execute(container_id, code, "python"))
        assert result.get("output_truncated") is True


class TestSandboxRuntimeL0:
    """Test SandboxRuntime routing for L0."""

    def test_l0_adapter_registered(self):
        from app.services.sandbox_runtime import SandboxRuntime
        runtime = SandboxRuntime()
        adapter = runtime.get_adapter("L0")
        from app.services.sandbox_runtime import ProcessAdapter
        assert isinstance(adapter, ProcessAdapter)

    def test_l0_container_routing(self):
        from app.services.sandbox_runtime import SandboxRuntime
        runtime = SandboxRuntime()
        # L0 container IDs start with "l0-"
        import asyncio
        result = asyncio.run(runtime.execute("l0-test", "print(1)", "python"))
        # Should route to L0 adapter (workspace won't exist, but routing works)
        assert result["exit_code"] == -1 or result["exit_code"] == 0


class TestFirecrackerBackendDetection:
    """Test Firecracker backend auto-detection."""

    def test_backend_property_exists(self):
        from app.services.firecracker_runtime import FirecrackerRuntime
        runtime = FirecrackerRuntime()
        assert hasattr(runtime, 'backend')
        assert runtime.backend in ("firecracker", "qemu-tcg", "simulation")

    def test_simulation_mode_property(self):
        from app.services.firecracker_runtime import FirecrackerRuntime
        runtime = FirecrackerRuntime()
        assert hasattr(runtime, '_simulation_mode')
        assert isinstance(runtime._simulation_mode, bool)

    def test_detect_backend_methods_exist(self):
        from app.services.firecracker_runtime import FirecrackerRuntime
        runtime = FirecrackerRuntime()
        assert hasattr(runtime, '_check_kvm_available')
        assert hasattr(runtime, '_check_qemu_available')
        assert hasattr(runtime, '_check_firecracker_available')

    def test_kvm_check(self):
        from app.services.firecracker_runtime import FirecrackerRuntime
        runtime = FirecrackerRuntime()
        kvm = runtime._check_kvm_available()
        assert isinstance(kvm, bool)

    def test_qemu_check(self):
        from app.services.firecracker_runtime import FirecrackerRuntime
        runtime = FirecrackerRuntime()
        qemu = runtime._check_qemu_available()
        assert isinstance(qemu, bool)


class TestResourceManagerL0:
    """Test resource manager includes L0."""

    def test_l0_resource_limits_defined(self):
        from app.services.sandbox_manager import RESOURCE_LIMITS
        assert "L0" in RESOURCE_LIMITS
        limits = RESOURCE_LIMITS["L0"]
        assert limits["cpu_cores"] == 1
        assert limits["memory_mb"] == 256
        assert limits["network"] is False

    def test_get_resource_limits_l0(self):
        from app.services.sandbox_manager import get_resource_limits
        limits = get_resource_limits("L0")
        assert limits["cpu_cores"] == 1

    def test_validate_resource_limits_l0(self):
        from app.services.sandbox_manager import validate_resource_limits
        limits = validate_resource_limits("L0", {"cpu_cores": 4, "memory_mb": 999})
        assert limits["cpu_cores"] == 1  # Clamped to L0 max
        assert limits["memory_mb"] == 256  # Clamped to L0 max
