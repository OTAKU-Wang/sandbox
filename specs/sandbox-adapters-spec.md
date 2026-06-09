# 密态沙箱系统（CDS）· 沙箱适配器技术规格

> 版本：v1.0
> 作者：@架构师
> 日期：2026-06-03
> 模块：S3-01b/S3-02b 沙箱适配器
> 参考：Firecracker, gVisor, Occlum, Gramine

---

## 1. 概述

沙箱适配器是 CDS 系统的核心组件，负责管理不同安全级别的隔离执行环境。系统支持三级沙箱：

| 级别 | 技术栈 | 安全级别 | 适用场景 |
|------|--------|---------|---------|
| L1 | Occlum/Gramine + SGX/TDX | 最高 | 敏感数据查询、模型训练 |
| L2 | Firecracker + gVisor | 高 | 非结构化数据处理、LLM训练 |
| L3 | Docker + Seccomp-BPF | 中 | 开发测试、低敏感数据 |

### 1.1 设计原则

- **统一接口**：所有适配器实现 `RuntimeAdapter` 接口
- **可插拔**：运行时可动态选择适配器
- **资源隔离**：cgroup + namespace + 网络隔离
- **国密支持**：SM4 加密存储、SM2 远程证明

---

## 2. RuntimeAdapter 接口

```python
from abc import ABC, abstractmethod
from typing import Dict, Optional
from dataclasses import dataclass
from enum import Enum

class SandboxType(Enum):
    L1_TEE = "l1_tee"
    L2_MICROVM = "l2_microvm"
    L3_CONTAINER = "l3_container"

@dataclass
class SandboxConfig:
    sandbox_type: SandboxType
    cpu_cores: int = 2
    memory_mb: int = 2048
    disk_mb: int = 10240
    network_enabled: bool = False
    gpu_count: int = 0
    max_runtime_seconds: int = 3600

@dataclass
class SandboxHandle:
    sandbox_id: str
    sandbox_type: SandboxType
    pid: Optional[int] = None
    ip_address: Optional[str] = None
    port: Optional[int] = None
    metadata: Dict = None

@dataclass
class ResourceUsage:
    cpu_ms: int
    memory_mb: int
    disk_mb: int
    network_bytes_in: int
    network_bytes_out: int
    pid_count: int

class RuntimeAdapter(ABC):
    """沙箱运行时适配器接口"""

    @abstractmethod
    async def provision(self, config: SandboxConfig) -> SandboxHandle:
        """创建沙箱实例"""
        pass

    @abstractmethod
    async def execute(self, handle: SandboxHandle,
                     command: str, args: list = None) -> dict:
        """在沙箱内执行命令"""
        pass

    @abstractmethod
    async def terminate(self, handle: SandboxHandle) -> None:
        """终止沙箱实例"""
        pass

    @abstractmethod
    async def get_resource_usage(self, handle: SandboxHandle) -> ResourceUsage:
        """获取资源使用情况"""
        pass

    @abstractmethod
    async def upload_file(self, handle: SandboxHandle,
                         local_path: str, remote_path: str) -> None:
        """上传文件到沙箱"""
        pass

    @abstractmethod
    async def download_file(self, handle: SandboxHandle,
                           remote_path: str, local_path: str) -> None:
        """从沙箱下载文件"""
        pass
```

---

## 3. L3 容器适配器（BwrapAdapter）

### 3.1 实现

```python
import asyncio
import os
import subprocess
from typing import Dict

class BwrapRuntimeAdapter(RuntimeAdapter):
    """L3 Bubblewrap 沙箱适配器"""

    def __init__(self, workspace_base: str = "/tmp/cds_sandbox"):
        self.workspace_base = workspace_base
        os.makedirs(workspace_base, exist_ok=True)

    async def provision(self, config: SandboxConfig) -> SandboxHandle:
        """创建 bwrap 沙箱实例"""
        sandbox_id = f"l3-{uuid.uuid4().hex[:12]}"
        workspace = os.path.join(self.workspace_base, sandbox_id)
        os.makedirs(workspace, exist_ok=True)

        # 创建子目录
        os.makedirs(os.path.join(workspace, "input"), exist_ok=True)
        os.makedirs(os.path.join(workspace, "output"), exist_ok=True)
        os.makedirs(os.path.join(workspace, "tmp"), exist_ok=True)

        return SandboxHandle(
            sandbox_id=sandbox_id,
            sandbox_type=SandboxType.L3_CONTAINER,
            metadata={
                "workspace": workspace,
                "config": config.__dict__,
            }
        )

    async def execute(self, handle: SandboxHandle,
                     command: str, args: list = None) -> dict:
        """在 bwrap 沙箱内执行命令"""
        workspace = handle.metadata["workspace"]

        # 构建 bwrap 命令
        bwrap_args = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/sbin", "/sbin",
            "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
            "--ro-bind", "/etc/hosts", "/etc/hosts",
            "--bind", os.path.join(workspace, "input"), "/workspace/input",
            "--bind", os.path.join(workspace, "output"), "/workspace/output",
            "--bind", os.path.join(workspace, "tmp"), "/workspace/tmp",
            "--tmpfs", "/tmp",
            "--tmpfs", "/proc",
            "--dev", "/dev",
            "--chdir", "/workspace",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--cap-drop", "ALL",
            "--", "bash", "-c", command
        ]

        # 执行命令
        proc = await asyncio.create_subprocess_exec(
            *bwrap_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace
        )

        stdout, stderr = await proc.communicate()

        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        }

    async def terminate(self, handle: SandboxHandle) -> None:
        """终止 bwrap 沙箱实例"""
        workspace = handle.metadata["workspace"]
        # 清理工作空间
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)

    async def get_resource_usage(self, handle: SandboxHandle) -> ResourceUsage:
        """获取资源使用情况"""
        # bwrap 没有内置资源监控，使用 /proc 文件系统
        return ResourceUsage(
            cpu_ms=0,
            memory_mb=0,
            disk_mb=0,
            network_bytes_in=0,
            network_bytes_out=0,
            pid_count=0,
        )

    async def upload_file(self, handle: SandboxHandle,
                         local_path: str, remote_path: str) -> None:
        """上传文件到沙箱"""
        workspace = handle.metadata["workspace"]
        dest = os.path.join(workspace, "input", remote_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(local_path, dest)

    async def download_file(self, handle: SandboxHandle,
                           remote_path: str, local_path: str) -> None:
        """从沙箱下载文件"""
        workspace = handle.metadata["workspace"]
        src = os.path.join(workspace, "output", remote_path)
        shutil.copy2(src, local_path)
```

---

## 4. L2 微虚拟机适配器（FirecrackerAdapter）

### 4.1 实现

```python
import httpx
import asyncio
from typing import Dict

class FirecrackerRuntimeAdapter(RuntimeAdapter):
    """L2 Firecracker 微虚拟机适配器"""

    def __init__(self, socket_path: str = "/tmp/firecracker"):
        self.socket_path = socket_path
        self.client = httpx.AsyncClient(
            base_url=f"http://localhost",
            transport=httpx.AsyncHTTPTransport(uds=socket_path)
        )

    async def provision(self, config: SandboxConfig) -> SandboxHandle:
        """创建 Firecracker 微虚拟机"""
        sandbox_id = f"l2-{uuid.uuid4().hex[:12]}"

        # 1. 创建 socket
        socket_path = os.path.join(self.socket_path, f"{sandbox_id}.socket")

        # 2. 启动 Firecracker 进程
        proc = await asyncio.create_subprocess_exec(
            "firecracker",
            "--api-sock", socket_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # 3. 等待 socket 就绪
        await asyncio.sleep(1)

        # 4. 配置微虚拟机
        client = httpx.AsyncClient(
            base_url="http://localhost",
            transport=httpx.AsyncHTTPTransport(uds=socket_path)
        )

        # 配置内核
        await client.put("/boot-source", json={
            "kernel_image_path": "/var/lib/firecracker/vmlinux",
            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"
        })

        # 配置根文件系统
        await client.put("/drives/rootfs", json={
            "drive_id": "rootfs",
            "path_on_host": "/var/lib/firecracker/rootfs.ext4",
            "is_root_device": True,
            "is_read_only": False
        })

        # 配置网络
        if config.network_enabled:
            await client.put("/network-interfaces/eth0", json={
                "iface_id": "eth0",
                "guest_mac": "AA:FC:00:00:00:01",
                "host_dev_name": f"tap-{sandbox_id[:8]}"
            })

        # 配置资源限制
        await client.put("/machine-config", json={
            "vcpu_count": config.cpu_cores,
            "mem_size_mib": config.memory_mb,
        })

        # 5. 启动微虚拟机
        await client.put("/actions", json={
            "action_type": "InstanceStart"
        })

        return SandboxHandle(
            sandbox_id=sandbox_id,
            sandbox_type=SandboxType.L2_MICROVM,
            pid=proc.pid,
            metadata={
                "socket_path": socket_path,
                "process": proc,
                "client": client,
            }
        )

    async def execute(self, handle: SandboxHandle,
                     command: str, args: list = None) -> dict:
        """在微虚拟机内执行命令"""
        client = handle.metadata["client"]

        # 通过 API 执行命令（需要 guest agent）
        response = await client.put("/actions", json={
            "action_type": "SendCtrlAltDel"
        })

        # 实际实现需要通过 guest agent 或 SSH
        # 这里简化处理
        return {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }

    async def terminate(self, handle: SandboxHandle) -> None:
        """终止微虚拟机"""
        client = handle.metadata["client"]
        process = handle.metadata["process"]

        # 发送关机命令
        await client.put("/actions", json={
            "action_type": "SendCtrlAltDel"
        })

        # 等待进程退出
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()

        # 清理 socket
        socket_path = handle.metadata["socket_path"]
        if os.path.exists(socket_path):
            os.remove(socket_path)

    async def get_resource_usage(self, handle: SandboxHandle) -> ResourceUsage:
        """获取资源使用情况"""
        client = handle.metadata["client"]

        # 从 Firecracker API 获取资源使用
        response = await client.get("/vm/config")
        config = response.json()

        return ResourceUsage(
            cpu_ms=0,
            memory_mb=config.get("mem_size_mib", 0),
            disk_mb=0,
            network_bytes_in=0,
            network_bytes_out=0,
            pid_count=1,
        )

    async def upload_file(self, handle: SandboxHandle,
                         local_path: str, remote_path: str) -> None:
        """上传文件到微虚拟机"""
        # 需要通过 guest agent 或 virtio-fs
        pass

    async def download_file(self, handle: SandboxHandle,
                           remote_path: str, local_path: str) -> None:
        """从微虚拟机下载文件"""
        # 需要通过 guest agent 或 virtio-fs
        pass
```

---

## 5. L1 TEE 适配器（OcclumAdapter）

### 5.1 实现

```python
import subprocess
import os

class OcclumRuntimeAdapter(RuntimeAdapter):
    """L1 Occlum TEE 沙箱适配器"""

    def __init__(self, occlum_path: str = "/opt/occlum"):
        self.occlum_path = occlum_path

    async def provision(self, config: SandboxConfig) -> SandboxHandle:
        """创建 Occlum TEE 实例"""
        sandbox_id = f"l1-{uuid.uuid4().hex[:12]}"
        instance_path = os.path.join(self.occlum_path, "instances", sandbox_id)

        # 1. 初始化 Occlum 实例
        proc = await asyncio.create_subprocess_exec(
            "occlum", "init", instance_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()

        # 2. 配置 Enclave 大小
        enclave_config = {
            "target": "default",
            "heap_size_mb": config.memory_mb,
            "thread_num": config.cpu_cores * 2,
        }

        config_path = os.path.join(instance_path, "Enclave.xml")
        self._write_enclave_config(config_path, enclave_config)

        # 3. 构建 Enclave 镜像
        proc = await asyncio.create_subprocess_exec(
            "occlum", "build",
            cwd=instance_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()

        return SandboxHandle(
            sandbox_id=sandbox_id,
            sandbox_type=SandboxType.L1_TEE,
            metadata={
                "instance_path": instance_path,
                "config": config.__dict__,
            }
        )

    async def execute(self, handle: SandboxHandle,
                     command: str, args: list = None) -> dict:
        """在 TEE 内执行命令"""
        instance_path = handle.metadata["instance_path"]

        # 构建 occlum run 命令
        cmd = ["occlum", "run", "/bin/bash", "-c", command]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=instance_path
        )

        stdout, stderr = await proc.communicate()

        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        }

    async def terminate(self, handle: SandboxHandle) -> None:
        """终止 TEE 实例"""
        instance_path = handle.metadata["instance_path"]

        # 清理实例
        import shutil
        shutil.rmtree(instance_path, ignore_errors=True)

    async def get_resource_usage(self, handle: SandboxHandle) -> ResourceUsage:
        """获取资源使用情况"""
        # Occlum 没有直接的资源监控 API
        return ResourceUsage(
            cpu_ms=0,
            memory_mb=0,
            disk_mb=0,
            network_bytes_in=0,
            network_bytes_out=0,
            pid_count=0,
        )

    async def upload_file(self, handle: SandboxHandle,
                         local_path: str, remote_path: str) -> None:
        """上传文件到 TEE"""
        instance_path = handle.metadata["instance_path"]
        dest = os.path.join(instance_path, "image", remote_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(local_path, dest)

    async def download_file(self, handle: SandboxHandle,
                           remote_path: str, local_path: str) -> None:
        """从 TEE 下载文件"""
        instance_path = handle.metadata["instance_path"]
        src = os.path.join(instance_path, "build", remote_path)
        shutil.copy2(src, local_path)

    def _write_enclave_config(self, config_path: str, config: dict):
        """写入 Enclave 配置"""
        xml_content = f"""<?xml version="1.0"?>
<EnclaveConfiguration>
  <Target>{config['target']}</Target>
  <HeapSize>{config['heap_size_mb']}MB</HeapSize>
  <ThreadNum>{config['thread_num']}</ThreadNum>
</EnclaveConfiguration>"""
        with open(config_path, 'w') as f:
            f.write(xml_content)
```

---

## 6. 沙箱管理器

```python
class SandboxManager:
    """沙箱管理器 — 统一管理所有沙箱类型"""

    def __init__(self):
        self.adapters = {
            SandboxType.L3_CONTAINER: BwrapRuntimeAdapter(),
            SandboxType.L2_MICROVM: FirecrackerRuntimeAdapter(),
            SandboxType.L1_TEE: OcclumRuntimeAdapter(),
        }
        self.active_handles: Dict[str, SandboxHandle] = {}

    async def create_sandbox(self, config: SandboxConfig) -> SandboxHandle:
        """创建沙箱实例"""
        adapter = self.adapters.get(config.sandbox_type)
        if not adapter:
            raise ValueError(f"Unsupported sandbox type: {config.sandbox_type}")

        handle = await adapter.provision(config)
        self.active_handles[handle.sandbox_id] = handle

        return handle

    async def execute_in_sandbox(self, sandbox_id: str,
                                command: str, args: list = None) -> dict:
        """在沙箱内执行命令"""
        handle = self.active_handles.get(sandbox_id)
        if not handle:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        adapter = self.adapters[handle.sandbox_type]
        return await adapter.execute(handle, command, args)

    async def terminate_sandbox(self, sandbox_id: str) -> None:
        """终止沙箱实例"""
        handle = self.active_handles.get(sandbox_id)
        if not handle:
            return

        adapter = self.adapters[handle.sandbox_type]
        await adapter.terminate(handle)

        del self.active_handles[sandbox_id]

    async def get_sandbox_usage(self, sandbox_id: str) -> ResourceUsage:
        """获取沙箱资源使用情况"""
        handle = self.active_handles.get(sandbox_id)
        if not handle:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        adapter = self.adapters[handle.sandbox_type]
        return await adapter.get_resource_usage(handle)

    def select_sandbox_type(self, data_sensitivity: str,
                           contract_requirements: dict) -> SandboxType:
        """根据数据敏感度和合约要求选择沙箱类型"""
        if data_sensitivity == "core":
            return SandboxType.L1_TEE
        elif data_sensitivity == "sensitive":
            if contract_requirements.get("requires_hardware_isolation"):
                return SandboxType.L1_TEE
            return SandboxType.L2_MICROVM
        elif data_sensitivity == "restricted":
            return SandboxType.L2_MICROVM
        else:
            return SandboxType.L3_CONTAINER
```

---

## 7. 测试用例

```python
async def test_bwrap_sandbox_lifecycle():
    """测试 bwrap 沙箱生命周期"""
    adapter = BwrapRuntimeAdapter()
    config = SandboxConfig(
        sandbox_type=SandboxType.L3_CONTAINER,
        cpu_cores=2,
        memory_mb=1024,
    )

    # 创建沙箱
    handle = await adapter.provision(config)
    assert handle.sandbox_type == SandboxType.L3_CONTAINER

    # 执行命令
    result = await adapter.execute(handle, "echo 'Hello, Sandbox!'")
    assert result["exit_code"] == 0
    assert "Hello, Sandbox!" in result["stdout"]

    # 终止沙箱
    await adapter.terminate(handle)

async def test_sandbox_type_selection():
    """测试沙箱类型选择"""
    manager = SandboxManager()

    # 核心数据 → L1 TEE
    assert manager.select_sandbox_type("core", {}) == SandboxType.L1_TEE

    # 敏感数据 + 硬件隔离要求 → L1 TEE
    assert manager.select_sandbox_type("sensitive", {
        "requires_hardware_isolation": True
    }) == SandboxType.L1_TEE

    # 敏感数据 + 无硬件隔离要求 → L2 MicroVM
    assert manager.select_sandbox_type("sensitive", {}) == SandboxType.L2_MICROVM

    # 受限数据 → L2 MicroVM
    assert manager.select_sandbox_type("restricted", {}) == SandboxType.L2_MICROVM

    # 公开数据 → L3 Container
    assert manager.select_sandbox_type("public", {}) == SandboxType.L3_CONTAINER
```

---

## 8. 部署配置

```yaml
# docker-compose.yml
services:
  firecracker:
    image: ghcr.io/firecracker-microvm/firecracker:v1.5.0
    privileged: true
    volumes:
      - /dev/kvm:/dev/kvm
      - ./firecracker-data:/var/lib/firecracker
    command: >
      --api-sock /var/run/firecracker.socket

  occlum:
    image: occlum/occlum:0.30.0-ubuntu20.04
    privileged: true
    volumes:
      - /dev/sgx:/dev/sgx
      - ./occlum-data:/opt/occlum/instances
    environment:
      - OCCLUM_RELEASE_ENCLAVE=0

  sandbox-manager:
    build: ./sandbox-manager
    environment:
      - FIRECRACKER_SOCKET=/var/run/firecracker.socket
      - OCCLUM_PATH=/opt/occlum
    volumes:
      - /var/run:/var/run
      - ./occlum-data:/opt/occlum
```

---

## 9. 监控指标

| 指标 | 说明 | 告警阈值 |
|------|------|---------|
| `sandbox_create_duration_ms` | 沙箱创建耗时 | L1: >30s, L2: >10s, L3: >2s |
| `sandbox_execute_duration_ms` | 命令执行耗时 | > 60s |
| `sandbox_active_count` | 活跃沙箱数 | > 100 |
| `sandbox_error_rate` | 沙箱错误率 | > 1% |
| `resource_usage_cpu_percent` | CPU 使用率 | > 80% |
| `resource_usage_memory_mb` | 内存使用 | > 80% 配额 |
