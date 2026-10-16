# SS-13 RT-03：Cube 节点 TEE 硬件探测（deploy/cube-node/tee_probe.py + preflight/verify 接线）。
#
# 策略：
#   - probe() 双条件单测：设备/命令分别 mock（注入 which/exists 可调用，不碰真实 /dev、PATH）；
#     tdx 优先于 sev_snp；无硬件 = none（诚实降级铁律）。
#   - capabilities JSON upsert：既有键保留、仅 upsert tee。
#   - preflight.sh 干跑（复用 test_cube_node_tooling 的 CDS_CUBE_MOCK_ROOT 夹具）：
#     mock 无硬件 → stdout 单行 tee=none + capabilities 文件落地；
#     mock tdx 设备 + 命令垫片 → tee=tdx。
#   - 真实干跑（RT-03 VERIFY）：本机直跑 tee_probe.py 与 preflight（本机无 TEE 硬件），
#     断言 tee=none 且 --json 输出可解析。

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CUBE_NODE_DIR = ROOT / "deploy" / "cube-node"
TEE_PROBE = CUBE_NODE_DIR / "tee_probe.py"
PREFLIGHT = CUBE_NODE_DIR / "preflight.sh"
MANIFEST = ROOT / "scripts" / "cube-bundle-manifest.json"


def _load_tee_probe():
    spec = importlib.util.spec_from_file_location("cds_cube_tee_probe", TEE_PROBE)
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


TEE_PROBE_MOD = _load_tee_probe()


# ---------------------------------------------------------------- probe() 双条件


def test_probe_none_when_nothing_present() -> None:
    r = TEE_PROBE_MOD.probe(which=lambda name: None, exists=lambda p: False)
    assert r["tee"] == "none"
    assert r["providers"]["tdx"] == []
    assert r["providers"]["sev_snp"] == []


def test_probe_tdx_by_device() -> None:
    r = TEE_PROBE_MOD.probe(which=lambda name: None, exists=lambda p: p == "/dev/tdx-guest")
    assert r["tee"] == "tdx"
    assert r["providers"]["tdx"] == ["device:/dev/tdx-guest"]


def test_probe_tdx_by_command() -> None:
    r = TEE_PROBE_MOD.probe(which=lambda name: "/usr/local/bin/tdx-attest" if name == "tdx-attest" else None,
                            exists=lambda p: False)
    assert r["tee"] == "tdx"
    assert r["providers"]["tdx"] == ["command:/usr/local/bin/tdx-attest"]


def test_probe_sev_snp_by_device() -> None:
    r = TEE_PROBE_MOD.probe(which=lambda name: None, exists=lambda p: p == "/dev/sev-guest")
    assert r["tee"] == "sev_snp"
    assert r["providers"]["sev_snp"] == ["device:/dev/sev-guest"]


def test_probe_sev_snp_by_command() -> None:
    r = TEE_PROBE_MOD.probe(which=lambda name: "/usr/bin/snpguest" if name == "snpguest" else None,
                            exists=lambda p: False)
    assert r["tee"] == "sev_snp"


def test_probe_tdx_priority_over_sev_snp() -> None:
    def which(name: str) -> str | None:
        if name == "snpguest":
            return "/usr/bin/snpguest"
        if name == "trustauthority-cli":
            return "/usr/bin/trustauthority-cli"
        return None

    r = TEE_PROBE_MOD.probe(which=which, exists=lambda p: False)
    assert r["tee"] == "tdx"  # 先 tdx 后 sev_snp（ss-13 §2.2）


def test_probe_tdx_alt_device_alias() -> None:
    r = TEE_PROBE_MOD.probe(which=lambda name: None, exists=lambda p: p == "/dev/tdx_guest")
    assert r["tee"] == "tdx"


# ---------------------------------------------------------------- capabilities upsert


def test_write_capabilities_preserves_existing_keys(tmp_path: Path) -> None:
    cap = tmp_path / "cap" / "NODE_CAPABILITIES.json"
    cap.parent.mkdir(parents=True)
    cap.write_text(json.dumps({"cubelet_endpoint": "10.0.0.1:9443", "runtime_version": "v0.7.3-rc2"}) + "\n")
    TEE_PROBE_MOD.write_capabilities(cap, {"tee": "none", "providers": {"tdx": [], "sev_snp": []}})
    doc = json.loads(cap.read_text())
    assert doc["cubelet_endpoint"] == "10.0.0.1:9443"
    assert doc["runtime_version"] == "v0.7.3-rc2"
    assert doc["tee"] == "none"
    assert "tee_evidence" not in doc  # none 无证据，不伪造


def test_write_capabilities_creates_file(tmp_path: Path) -> None:
    cap = tmp_path / "NODE_CAPABILITIES.json"
    TEE_PROBE_MOD.write_capabilities(cap, {"tee": "tdx", "providers": {"tdx": ["device:/dev/tdx_guest"], "sev_snp": []}})
    doc = json.loads(cap.read_text())
    assert doc["tee"] == "tdx"
    assert doc["tee_evidence"] == ["tdx:device:/dev/tdx_guest"]


def test_write_capabilities_recovers_corrupt_file(tmp_path: Path) -> None:
    cap = tmp_path / "NODE_CAPABILITIES.json"
    cap.write_text("{not json")
    TEE_PROBE_MOD.write_capabilities(cap, {"tee": "none", "providers": {"tdx": [], "sev_snp": []}})
    doc = json.loads(cap.read_text())
    assert doc["tee"] == "none"
    assert doc.get("_capabilities_file_recovered_from_corrupt") is True


# ---------------------------------------------------------------- preflight 接线


def _write_shim(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


# 垫片须在 build_mock_root 定义前（其内部调用）


def _make_unix_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()  # 文件节点保留


# 检查项 10（沙箱网段冲突）的 `ip` fixture：与上游默认 192.168.0.0/18 及 CDS 默认 10.100.0.0/18
# 均不重叠；不打垫片时，本检查会随跑测试机器的真实网段翻脸（远程测试机实有 192.168.17.0/24）。
IP_PREFIXES_CLEAN = """1: lo    inet 127.0.0.1/8 scope host lo
2: eth0    inet 172.20.5.21/24 brd 172.20.5.255 scope global eth0
3: tun0    inet 2.0.1.111/24 scope global tun0
default via 172.20.5.1 dev eth0 proto dhcp src 172.20.5.21 metric 100
172.20.5.0/24 dev eth0 proto kernel scope link src 172.20.5.21
2.0.1.0/24 dev tun0 proto kernel scope link src 2.0.1.111
100.112.0.0/16 via 2.0.1.1 dev tun0 proto static metric 50"""


def build_mock_root(root: Path) -> Path:
    """自包含版 test_cube_node_tooling.build_mock_root（该文件不在 sys.path，不能跨文件导入）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "etc").mkdir(exist_ok=True)
    (root / "proc").mkdir(exist_ok=True)
    (root / "sys" / "fs" / "bpf").mkdir(parents=True, exist_ok=True)
    (root / "data" / "cubelet").mkdir(parents=True, exist_ok=True)

    dev = root / "dev" / "kvm"
    dev.parent.mkdir(parents=True, exist_ok=True)
    dev.write_bytes(b"")
    dev.chmod(0o666)

    (root / "etc" / "fs_type").write_text("xfs\n")
    (root / "etc" / "ebpf_syscall").write_text("1\n")
    (root / "etc" / "disk_pct").write_text("42\n")
    (root / "proc" / "meminfo").write_text(f"MemTotal:       16000000 kB\n")
    (root / "proc" / "filesystems").write_text("\tproc\tprocfs\n\tsysfs\n\tbpf\n")

    _make_unix_socket(root / "run" / "cds.sock")

    b = root / "bin"
    _write_shim(b / "xfs_info",
                f'echo "name: {root}/data/cubelet (dev /dev/loop0)"\necho "meta-data = ...\\treflink=1"\nexit 0')
    _write_shim(b / "bpftool", "exit 0")
    _write_shim(b / "containerd", 'echo "containerd containerd.io 1.7.20 98535bd5e642f4758b1a9be0b00e5b98985c1c2a"')
    _write_shim(b / "cubelet", "echo \"cubelet v0.7.3-rc2 (b0c4cc33fbfccbacc1a72241918ef280e9a32772) built at 2026-10-12T06:28:39Z\"")
    _write_shim(b / "cp", "exit 0")  # FICLONE 实测探针垫片
    _write_shim(b / "ip", "cat <<'FIXTURE'\n" + IP_PREFIXES_CLEAN + "\nFIXTURE")
    return root


class _TcpListener:
    def __init__(self, port: int) -> None:
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(8)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._srv.settimeout(0.2)
        frame = b"\x00\x00\x00\x04\x00\x00\x00\x00\x00"
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn.recv(64)
                conn.sendall(frame)
            except OSError:
                pass
            finally:
                conn.close()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run_preflight(root: Path, tls_port: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CDS_CUBE_MOCK_ROOT"] = str(root)
    env["CDS_CUBE_DATA_DIR"] = str(root / "data" / "cubelet")
    env["CDS_CUBE_VOLUME_PLUGIN_SOCKET"] = str(root / "run" / "cds.sock")
    env["CDS_CUBE_CUBELET_BIN"] = str(root / "bin" / "cubelet")
    env["CDS_CUBE_BUNDLE_MANIFEST"] = str(MANIFEST)
    env["CDS_CUBE_TLS_PROXY_PORT"] = str(tls_port)
    return subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True, env=env, timeout=60)


def test_preflight_no_tee_reports_none_and_writes_capabilities(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock")
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = _run_preflight(root, port)
    finally:
        listener.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tee=none" in r.stdout
    assert "PASS: tee_probe" in r.stdout
    cap = root / "var" / "lib" / "cds" / "cube-node" / "NODE_CAPABILITIES.json"
    assert cap.exists()
    assert json.loads(cap.read_text())["tee"] == "none"


def test_preflight_tdx_device_and_command_reports_tdx(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock")
    # 设备条件
    dev = root / "dev" / "tdx_guest"
    dev.parent.mkdir(parents=True, exist_ok=True)
    dev.write_bytes(b"")
    # 命令条件（MOCK_ROOT/bin 前置 PATH）
    _write_shim(root / "bin" / "tdx-attest", "exit 0")
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = _run_preflight(root, port)
    finally:
        listener.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tee=tdx" in r.stdout
    assert "PASS: tee_probe" in r.stdout
    cap = root / "var" / "lib" / "cds" / "cube-node" / "NODE_CAPABILITIES.json"
    doc = json.loads(cap.read_text())
    assert doc["tee"] == "tdx"
    assert any(e.startswith("tdx:device:/dev/tdx_guest") for e in doc["tee_evidence"])
    assert any(e.startswith("tdx:command:") for e in doc["tee_evidence"])


def test_tee_probe_json_flag_parses(tmp_path: Path) -> None:
    r = subprocess.run(
        ["python3", str(TEE_PROBE), "--json", "--capabilities-file", str(tmp_path / "cap.json")],
        capture_output=True, text=True, env=os.environ.copy(), timeout=30,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tee=none" in r.stdout  # stdout 恒为单行 tee=...
    doc = json.loads(r.stderr)  # --json 机器可读输出走 stderr
    assert doc["tee"] in ("tdx", "sev_snp", "none")
    assert set(doc["providers"]) == {"tdx", "sev_snp"}


# ---------------------------------------------------------------- 真实干跑（RT-03 VERIFY）


def test_real_host_dryrun_reports_none() -> None:
    """本机无 TEE 硬件：probe 与 preflight（真实 /dev、PATH，无 mock）均如实报 tee=none。"""
    r = subprocess.run(["python3", str(TEE_PROBE)], capture_output=True, text=True,
                       env=os.environ.copy(), timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tee=none" in r.stdout

    if not Path("/dev/kvm").exists():
        # 本机无 KVM 时 preflight 整体 FAIL 属环境性（与 TEE 探测无关）；
        # 仍要求 tee 探测行本身如实输出（不伪造、不静默）。
        r2 = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                            env=os.environ.copy(), timeout=120)
        assert "tee=none" in r2.stdout
        return
    r2 = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                        env=os.environ.copy(), timeout=120)
    assert "tee=none" in r2.stdout
    assert "PASS: tee_probe" in r2.stdout
