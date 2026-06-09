"""Tests for sandbox security hardening — L0 bwrap, L3 cgroup namespace, multi-tenant quotas."""
import asyncio
import os
import pytest
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services.sandbox_runtime import ProcessAdapter, _build_l0_bwrap_args
from app.services.sandbox_security import build_bwrap_args, SandboxSecurityConfig
from app.services.sandbox_manager import (
    get_tenant_quota, update_tenant_usage, check_tenant_quota,
    release_tenant_usage, TENANT_DEFAULTS,
)


class TestL0BwrapHardening:
    """Test L0 ProcessAdapter uses bwrap namespace isolation."""

    @pytest.fixture
    def adapter(self, tmp_path):
        return ProcessAdapter(workspace_root=str(tmp_path / "l0-secure"))

    def test_bwrap_available_detected(self, adapter):
        """ProcessAdapter detects bwrap availability."""
        assert hasattr(adapter, '_bwrap_available')
        assert isinstance(adapter._bwrap_available, bool)

    def test_l0_bwrap_args_include_namespaces(self, tmp_path):
        """L0 bwrap args include PID, network, IPC namespace isolation."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        result = _build_l0_bwrap_args(
            workspace=workspace,
            code_file="/workspace/tmp/exec.py",
            language="python",
        )
        args = result[0] if isinstance(result, tuple) else result
        assert "bwrap" in args
        assert "--unshare-pid" in args
        assert "--unshare-net" in args
        assert "--unshare-ipc" in args
        assert "--die-with-parent" in args

    def test_l0_bwrap_args_restrict_dev(self, tmp_path):
        """L0 bwrap args restrict /dev to minimal set."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        result = _build_l0_bwrap_args(workspace=workspace, code_file="/workspace/tmp/exec.py", language="python")
        args = result[0] if isinstance(result, tuple) else result
        assert "--dev" in args
        assert "/dev/null" in args
        assert "/dev/zero" in args
        assert "/dev/urandom" in args

    def test_l0_bwrap_args_block_sys(self, tmp_path):
        """L0 bwrap args block /sys with tmpfs."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        result = _build_l0_bwrap_args(workspace=workspace, code_file="/workspace/tmp/exec.py", language="python")
        args = result[0] if isinstance(result, tuple) else result
        assert "--tmpfs" in args
        # /sys should be tmpfs (blocked)
        sys_idx = args.index("/sys:size=1m")
        assert args[sys_idx - 1] == "--tmpfs"

    def test_l0_bwrap_args_readonly_system(self, tmp_path):
        """L0 bwrap args mount system dirs read-only."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        result = _build_l0_bwrap_args(workspace=workspace, code_file="/workspace/tmp/exec.py", language="python")
        args = result[0] if isinstance(result, tuple) else result
        assert "--ro-bind" in args
        assert "/usr" in args

    def test_l0_bwrap_args_session_key_injection(self, tmp_path):
        """L0 bwrap args inject session key via env var, not file."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        result = _build_l0_bwrap_args(
            workspace=workspace,
            code_file="/workspace/tmp/exec.py",
            language="python",
            session_key="secret-123",
        )
        args = result[0] if isinstance(result, tuple) else result
        assert "--setenv" in args
        key_idx = args.index("CDS_SESSION_KEY")
        assert args[key_idx + 1] == "secret-123"

    def test_l0_bwrap_args_env_vars(self, tmp_path):
        """L0 bwrap args inject custom env vars."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        result = _build_l0_bwrap_args(
            workspace=workspace,
            code_file="/workspace/tmp/exec.py",
            language="python",
            env_vars={"MY_VAR": "hello"},
        )
        args = result[0] if isinstance(result, tuple) else result
        assert "MY_VAR" in args
        assert "hello" in args

    def test_l0_execute_with_bwrap(self, adapter):
        """L0 execute uses bwrap for isolation when available."""
        if not adapter._bwrap_available:
            pytest.skip("bwrap not available")

        result = adapter.provision("bwrap-test", "", timeout=60)
        container_id = result["container_id"]
        result = asyncio.run(adapter.execute(container_id, "print('hello bwrap L0')", "python"))
        assert result["exit_code"] == 0
        assert "hello bwrap L0" in result["output"]
        assert result["sandbox_level"] == "L0"

    def test_l0_execute_session_key_via_env(self, adapter):
        """L0 execute injects session key via environment."""
        if not adapter._bwrap_available:
            pytest.skip("bwrap not available")

        result = adapter.provision("key-env-test", "", timeout=60)
        container_id = result["container_id"]
        result = asyncio.run(adapter.execute(
            container_id,
            "import os; print(os.environ.get('CDS_SESSION_KEY', 'none'))",
            "python",
            session_key="my-secret-key",
        ))
        assert result["exit_code"] == 0
        assert "my-secret-key" in result["output"]

    def test_l0_execute_network_isolation(self, adapter):
        """L0 bwrap blocks network access."""
        if not adapter._bwrap_available:
            pytest.skip("bwrap not available")

        result = adapter.provision("net-test", "", timeout=60)
        container_id = result["container_id"]
        # curl should fail because network is isolated
        result = asyncio.run(adapter.execute(
            container_id,
            "import urllib.request; urllib.request.urlopen('http://example.com', timeout=2)",
            "python",
            timeout=5,
        ))
        assert result["exit_code"] != 0  # Should fail

    def test_l0_seccomp_bpf_compiled(self, adapter):
        """L0 bwrap compiles seccomp BPF filter."""
        from app.services.sandbox_security import get_seccomp_bpf_fd, _SECCOMP_JSON
        import json

        # Verify seccomp JSON spec blocks dangerous syscalls
        profile = json.loads(_SECCOMP_JSON)
        assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
        allowed = profile["syscalls"][0]["names"]
        assert "ptrace" not in allowed
        assert "mount" not in allowed
        assert "reboot" not in allowed

        # Verify BPF compilation works (may return None if libseccomp not installed)
        fd = get_seccomp_bpf_fd()
        if fd is not None:
            assert isinstance(fd, int)
            assert fd > 0
            # Don't close fd — it's cached globally and reused by bwrap


class TestL3BwrapCgroupNamespace:
    """Test L3 bwrap cgroup namespace isolation."""

    def test_bwrap_args_include_cgroup_namespace(self, tmp_path):
        """L3 bwrap args include --unshare-cgroup."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        config = SandboxSecurityConfig(use_pid_namespace=True)
        result = build_bwrap_args(
            workspace=workspace,
            code_file="/workspace/tmp/exec.py",
            language="python",
            config=config,
        )
        args = result[0] if isinstance(result, tuple) else result
        assert "--unshare-cgroup" in args

    def test_bwrap_args_dev_minimal(self, tmp_path):
        """L3 bwrap args mount only null/zero/urandom in /dev."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        result = build_bwrap_args(
            workspace=workspace,
            code_file="/workspace/tmp/exec.py",
            language="python",
        )
        args = result[0] if isinstance(result, tuple) else result
        assert "/dev/null" in args
        assert "/dev/zero" in args
        assert "/dev/urandom" in args

    def test_bwrap_args_seccomp_bpf_fd(self, tmp_path):
        """L3 bwrap returns seccomp BPF file descriptor."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        config = SandboxSecurityConfig(use_seccomp=True)
        result = build_bwrap_args(
            workspace=workspace,
            code_file="/workspace/tmp/exec.py",
            language="python",
            config=config,
        )
        # Should return (args, seccomp_fd) tuple
        assert isinstance(result, tuple)
        args, seccomp_fd = result
        # seccomp_fd may be None if libseccomp not available
        if seccomp_fd is not None:
            assert isinstance(seccomp_fd, int)
            assert seccomp_fd > 0
            # --seccomp FD should be in args
            assert "--seccomp" in args


class TestMultiTenantQuotas:
    """Test per-tenant resource quota management."""

    def test_get_tenant_quota_creates_defaults(self):
        """get_tenant_quota creates default quota for new tenant."""
        quota = get_tenant_quota("new-tenant-123")
        assert quota["max_sessions"] == 5
        assert quota["max_cpu_cores"] == 8
        assert quota["max_memory_mb"] == 8192

    def test_update_tenant_usage_tracks_resources(self):
        """update_tenant_usage correctly aggregates resource usage."""
        uid = "usage-test-tenant"
        update_tenant_usage(uid, cpu_cores=2, memory_mb=1024, disk_mb=2048)
        quota = get_tenant_quota(uid)
        assert quota.get("used_cpu_cores", 0) == 2
        assert quota.get("used_memory_mb", 0) == 1024
        assert quota.get("used_disk_mb", 0) == 2048

    def test_check_tenant_quota_within_limits(self):
        """check_tenant_quota allows requests within limits."""
        uid = "quota-ok-tenant"
        update_tenant_usage(uid, cpu_cores=2, memory_mb=1024, disk_mb=1024)
        allowed, reason = check_tenant_quota(uid, cpu_cores=2, memory_mb=1024, disk_mb=1024)
        assert allowed is True
        assert reason == ""

    def test_check_tenant_quota_cpu_exceeded(self):
        """check_tenant_quota rejects when CPU quota exceeded."""
        uid = "cpu-exceed-tenant"
        update_tenant_usage(uid, cpu_cores=7, memory_mb=1024, disk_mb=1024)
        allowed, reason = check_tenant_quota(uid, cpu_cores=2, memory_mb=512, disk_mb=512)
        assert allowed is False
        assert "CPU quota" in reason

    def test_check_tenant_quota_memory_exceeded(self):
        """check_tenant_quota rejects when memory quota exceeded."""
        uid = "mem-exceed-tenant"
        update_tenant_usage(uid, cpu_cores=1, memory_mb=7680, disk_mb=1024)
        allowed, reason = check_tenant_quota(uid, cpu_cores=1, memory_mb=1024, disk_mb=512)
        assert allowed is False
        assert "Memory quota" in reason

    def test_release_tenant_usage_decrements(self):
        """release_tenant_usage decrements resource usage."""
        uid = "release-test-tenant"
        update_tenant_usage(uid, cpu_cores=4, memory_mb=2048, disk_mb=4096)
        release_tenant_usage(uid, cpu_cores=2, memory_mb=1024, disk_mb=2048)
        quota = get_tenant_quota(uid)
        assert quota.get("used_cpu_cores", 0) == 2
        assert quota.get("used_memory_mb", 0) == 1024
        assert quota.get("used_disk_mb", 0) == 2048

    def test_release_tenant_usage_floor_at_zero(self):
        """release_tenant_usage doesn't go below zero."""
        uid = "floor-test-tenant"
        update_tenant_usage(uid, cpu_cores=1, memory_mb=512, disk_mb=512)
        release_tenant_usage(uid, cpu_cores=5, memory_mb=2048, disk_mb=2048)
        quota = get_tenant_quota(uid)
        assert quota.get("used_cpu_cores", 0) == 0
        assert quota.get("used_memory_mb", 0) == 0
        assert quota.get("used_disk_mb", 0) == 0


class TestCgroupV2Enhanced:
    """Test enhanced cgroup v2 resource limits."""

    def test_cgroup_cpu_max(self, tmp_path):
        """cgroup creation includes cpu.max quota."""
        from app.services.sandbox_runtime import ProcessAdapter
        adapter = ProcessAdapter(workspace_root=str(tmp_path / "l0"))
        cgroup_path = tmp_path / "cgroup-test"
        cgroup_path.mkdir()

        # Mock the cgroup files
        cpu_max = cgroup_path / "cpu.max"
        cpu_weight = cgroup_path / "cpu.weight"
        memory_max = cgroup_path / "memory.max"
        memory_high = cgroup_path / "memory.high"
        pids_max = cgroup_path / "pids.max"
        io_max = cgroup_path / "io.max"

        # Create parent with subtree_control
        parent = cgroup_path.parent
        (parent / "cgroup.subtree_control").write_text("+cpu +memory +pids +io")

        for f in [cpu_max, cpu_weight, memory_max, memory_high, pids_max, io_max]:
            f.write_text("0")

        adapter._create_cgroup(cgroup_path, memory_mb=256, cpu_percent=50, max_pids=32)

        assert cpu_max.exists()
        content = cpu_max.read_text()
        assert "50000" in content  # 50% of 100000us

    def test_cgroup_memory_high_watermark(self, tmp_path):
        """cgroup creation sets memory.high throttle."""
        from app.services.sandbox_runtime import ProcessAdapter
        adapter = ProcessAdapter(workspace_root=str(tmp_path / "l0"))
        cgroup_path = tmp_path / "cgroup-test"
        cgroup_path.mkdir()

        memory_high = cgroup_path / "memory.high"
        memory_high.write_text("0")

        adapter._create_cgroup(cgroup_path, memory_mb=256)

        content = memory_high.read_text()
        # memory.high = memory_mb - 64 = 192 MB
        assert str(192 * 1024 * 1024) in content


class TestFirecrackerSimBwrapHardening:
    """Test Firecracker simulation mode bwrap args match L3 security level."""

    def test_sim_bwrap_includes_cgroup_namespace(self, tmp_path):
        """Firecracker sim bwrap includes --unshare-cgroup."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        assert "--unshare-cgroup" in args

    def test_sim_bwrap_includes_uts_namespace(self, tmp_path):
        """Firecracker sim bwrap includes --unshare-uts and --hostname."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        assert "--unshare-uts" in args
        assert "--hostname" in args
        assert "cds-sandbox" in args

    def test_sim_bwrap_includes_user_namespace(self, tmp_path):
        """Firecracker sim bwrap includes --unshare-user when unprivileged."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        if os.getuid() != 0:
            assert "--unshare-user" in args
            assert "--uid" in args
            assert "--gid" in args

    def test_sim_bwrap_minimal_dev(self, tmp_path):
        """Firecracker sim bwrap restricts /dev to null/zero/urandom."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        assert "/dev/null" in args
        assert "/dev/zero" in args
        assert "/dev/urandom" in args

    def test_sim_bwrap_restricted_proc(self, tmp_path):
        """Firecracker sim bwrap binds /proc/self/status read-only."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        assert "--ro-bind" in args
        assert "/proc/self/status" in args

    def test_sim_bwrap_blocks_sys(self, tmp_path):
        """Firecracker sim bwrap blocks /sys with tmpfs."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        sys_idx = args.index("/sys:size=1m")
        assert args[sys_idx - 1] == "--tmpfs"

    def test_sim_bwrap_session_key_env(self, tmp_path):
        """Firecracker sim bwrap injects session key via --setenv."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py",
            language="python", session_key="test-key-abc",
        )
        assert "--setenv" in args
        key_idx = args.index("CDS_SESSION_KEY")
        assert args[key_idx + 1] == "test-key-abc"

    def test_sim_bwrap_env_vars(self, tmp_path):
        """Firecracker sim bwrap injects custom env vars."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py",
            language="python", env_vars={"CUSTOM_VAR": "value123"},
        )
        assert "CUSTOM_VAR" in args
        assert "value123" in args

    def test_sim_bwrap_seccomp_fd(self, tmp_path):
        """Firecracker sim bwrap returns seccomp FD."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, seccomp_fd = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        if seccomp_fd is not None:
            assert "--seccomp" in args
            assert isinstance(seccomp_fd, int)
            assert seccomp_fd > 0

    def test_sim_bwrap_network_isolation(self, tmp_path):
        """Firecracker sim bwrap includes --unshare-net."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py", language="python",
        )
        assert "--unshare-net" in args
        assert "--unshare-pid" in args
        assert "--unshare-ipc" in args
        assert "--die-with-parent" in args


class TestL0BwrapArgsSeccomp:
    """Test L0 seccomp profile content."""

    def test_seccomp_blocks_dangerous_syscalls(self):
        """L0 seccomp profile blocks ptrace, mount, reboot."""
        from app.services.sandbox_security import _SECCOMP_JSON
        import json
        profile = json.loads(_SECCOMP_JSON)
        assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
        allowed = profile["syscalls"][0]["names"]
        assert "ptrace" not in allowed
        assert "mount" not in allowed
        assert "reboot" not in allowed
        assert "init_module" not in allowed

    def test_seccomp_allows_basic_syscalls(self):
        """L0 seccomp profile allows read/write/execve."""
        from app.services.sandbox_security import _SECCOMP_JSON
        import json
        profile = json.loads(_SECCOMP_JSON)
        allowed = profile["syscalls"][0]["names"]
        assert "read" in allowed
        assert "write" in allowed
        assert "execve" in allowed
        assert "fork" in allowed
        assert "clone" in allowed


class TestNetworkPolicy:
    """Test network policy engine and DNS proxy."""

    def test_deny_all_policy_blocks_all(self):
        """deny_all policy blocks all connections."""
        from app.services.network_policy import NetworkPolicyEngine, NetworkPolicyConfig
        engine = NetworkPolicyEngine()
        config = NetworkPolicyConfig(mode="deny_all")
        engine._policies["test-session"] = config
        assert engine.check_connection("test-session", "8.8.8.8", 443) is False
        assert engine.check_connection("test-session", "10.0.0.1", 80) is False

    def test_allowlist_policy_allows_whitelisted_ip(self):
        """allowlist policy allows connections to whitelisted IPs."""
        from app.services.network_policy import NetworkPolicyEngine, NetworkPolicyConfig
        engine = NetworkPolicyEngine()
        config = NetworkPolicyConfig(
            mode="allowlist",
            allowed_ips=["10.0.0.0/8", "192.168.1.100/32"],
            allowed_ports=[443, 80],
        )
        engine._policies["test-session"] = config
        assert engine.check_connection("test-session", "10.0.0.5", 443) is True
        assert engine.check_connection("test-session", "192.168.1.100", 80) is True

    def test_allowlist_policy_blocks_non_whitelisted_ip(self):
        """allowlist policy blocks connections to non-whitelisted IPs."""
        from app.services.network_policy import NetworkPolicyEngine, NetworkPolicyConfig
        engine = NetworkPolicyEngine()
        config = NetworkPolicyConfig(
            mode="allowlist",
            allowed_ips=["10.0.0.0/8"],
            allowed_ports=[443],
        )
        engine._policies["test-session"] = config
        assert engine.check_connection("test-session", "8.8.8.8", 443) is False
        assert engine.check_connection("test-session", "10.0.0.5", 80) is False

    def test_allowlist_policy_blocks_port(self):
        """allowlist policy blocks non-whitelisted ports."""
        from app.services.network_policy import NetworkPolicyEngine, NetworkPolicyConfig
        engine = NetworkPolicyEngine()
        config = NetworkPolicyConfig(
            mode="allowlist",
            allowed_ips=["10.0.0.0/8"],
            allowed_ports=[443],
        )
        engine._policies["test-session"] = config
        assert engine.check_connection("test-session", "10.0.0.5", 443) is True
        assert engine.check_connection("test-session", "10.0.0.5", 8080) is False

    def test_no_policy_denies_all(self):
        """No policy for session means deny all."""
        from app.services.network_policy import NetworkPolicyEngine
        engine = NetworkPolicyEngine()
        assert engine.check_connection("nonexistent", "8.8.8.8", 443) is False

    def test_model_is_ip_allowed(self):
        """NetworkPolicy model correctly checks IP allowlist."""
        from app.models.network_policy import NetworkPolicy
        policy = NetworkPolicy(
            session_id="test",
            user_id="user1",
            mode="allowlist",
            allowed_ips=["10.0.0.0/8", "172.16.0.0/12"],
        )
        assert policy.is_ip_allowed("10.0.0.5") is True
        assert policy.is_ip_allowed("172.16.0.1") is True
        assert policy.is_ip_allowed("8.8.8.8") is False
        assert policy.is_ip_allowed("192.168.1.1") is False

    def test_model_is_ip_allowed_deny_all(self):
        """NetworkPolicy model deny_all blocks all IPs."""
        from app.models.network_policy import NetworkPolicy
        policy = NetworkPolicy(session_id="test", user_id="user1", mode="deny_all")
        assert policy.is_ip_allowed("10.0.0.5") is False

    def test_model_is_domain_allowed(self):
        """NetworkPolicy model correctly checks domain allowlist."""
        from app.models.network_policy import NetworkPolicy
        policy = NetworkPolicy(
            session_id="test",
            user_id="user1",
            mode="allowlist",
            allowed_domains=["api.example.com", "*.internal.com"],
        )
        assert policy.is_domain_allowed("api.example.com") is True
        assert policy.is_domain_allowed("sub.internal.com") is True
        assert policy.is_domain_allowed("internal.com") is True
        assert policy.is_domain_allowed("evil.com") is False
        assert policy.is_domain_allowed("notinternal.com") is False

    def test_model_is_domain_allowed_deny_all(self):
        """NetworkPolicy model deny_all blocks all domains."""
        from app.models.network_policy import NetworkPolicy
        policy = NetworkPolicy(session_id="test", user_id="user1", mode="deny_all")
        assert policy.is_domain_allowed("api.example.com") is False

    def test_model_is_port_allowed(self):
        """NetworkPolicy model correctly checks port allowlist."""
        from app.models.network_policy import NetworkPolicy
        policy = NetworkPolicy(
            session_id="test",
            user_id="user1",
            mode="allowlist",
            allowed_ports=[443, 80],
        )
        assert policy.is_port_allowed(443) is True
        assert policy.is_port_allowed(80) is True
        assert policy.is_port_allowed(8080) is False

    def test_dns_proxy_nxdomain(self):
        """DNS proxy returns NXDOMAIN for blocked domains."""
        from app.services.network_policy import DNSProxyProtocol
        protocol = DNSProxyProtocol(allowed_domains=["api.example.com"])

        # Build a minimal DNS query for "blocked.com"
        query = bytearray(12 + 5 + len("blocked") + 3 + 4)
        # ID
        query[0] = 0x12
        query[1] = 0x34
        # Flags: standard query
        query[2] = 0x01
        query[3] = 0x00
        # QDCOUNT = 1
        query[4] = 0x00
        query[5] = 0x01
        # ANCOUNT, NSCOUNT, ARCOUNT = 0
        for i in range(6, 12):
            query[i] = 0x00
        # Question: blocked.com
        offset = 12
        for label in ["blocked", "com"]:
            query[offset] = len(label)
            offset += 1
            query[offset:offset + len(label)] = label.encode()
            offset += len(label)
        query[offset] = 0  # root label
        offset += 1
        # QTYPE = A (1), QCLASS = IN (1)
        query[offset] = 0
        query[offset + 1] = 1
        query[offset + 2] = 0
        query[offset + 3] = 1

        response = DNSProxyProtocol._build_nxdomain(bytes(query[:offset + 4]))
        # Check NXDOMAIN flag (RCODE = 3)
        assert response[3] & 0x0F == 3

    def test_bwrap_l0_isolate_network_false(self, tmp_path):
        """L0 bwrap with isolate_network=False omits --unshare-net."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        from app.services.sandbox_runtime import _build_l0_bwrap_args
        args, _ = _build_l0_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py",
            language="python", isolate_network=False,
        )
        assert "--unshare-net" not in args

    def test_bwrap_l0_isolate_network_true(self, tmp_path):
        """L0 bwrap with isolate_network=True includes --unshare-net."""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        from app.services.sandbox_runtime import _build_l0_bwrap_args
        args, _ = _build_l0_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py",
            language="python", isolate_network=True,
        )
        assert "--unshare-net" in args

    def test_sim_bwrap_isolate_network_false(self, tmp_path):
        """Firecracker sim bwrap with isolate_network=False omits --unshare-net."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py",
            language="python", isolate_network=False,
        )
        assert "--unshare-net" not in args

    def test_sim_bwrap_dns_proxy_resolv_conf(self, tmp_path):
        """Firecracker sim bwrap with dns_proxy_port creates resolv.conf."""
        from app.services.firecracker_runtime import FirecrackerRuntime
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "input").mkdir()
        (workspace / "output").mkdir()
        (workspace / "tmp").mkdir()

        args, _ = FirecrackerRuntime._build_sim_bwrap_args(
            workspace=workspace, code_file="/workspace/tmp/exec.py",
            language="python", isolate_network=False, dns_proxy_port=10053,
        )
        resolv_conf = workspace / ".resolv.conf"
        assert resolv_conf.exists()
        assert "nameserver 127.0.0.1" in resolv_conf.read_text()
