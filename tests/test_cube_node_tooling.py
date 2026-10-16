# SS-10 CB-02：Cube 节点部署/升级工具链（deploy/cube-node/）bash 级测试。
#
# 策略：preflight.sh / verify.sh 的 CDS_CUBE_MOCK_ROOT 钩子——fixture 目录提供
#   dev/kvm、proc/{meminfo,filesystems}、sys/fs/bpf、etc/{fs_type,disk_pct,ebpf_syscall}
#   与 bin/ 下工具垫片（xfs_info/bpftool/cubelet/containerd/cp/systemctl），
#   网络类检查（TLS sidecar、cubelet gRPC）用真 socket 在 ephemeral 端口起真实服务，
#   不做假——只 mock 内核/文件系统/工具二进制这类容器内无法伪造的层。
# 断言 PASS/FAIL 矩阵：全好→exit 0；逐项缺失→exit 1 + 对应 FAIL 行。
# 运行时长 < 60s（无 sleep 轮询，socket 检查带超时）。

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

CUBE_NODE_DIR = Path(__file__).resolve().parent.parent / "deploy" / "cube-node"
PREFLIGHT = CUBE_NODE_DIR / "preflight.sh"
VERIFY = CUBE_NODE_DIR / "verify.sh"
MANIFEST = Path(__file__).resolve().parent.parent / "scripts" / "cube-bundle-manifest.json"


def _write_shim(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


# 检查项 10（沙箱网段冲突）的 `ip` 输出 fixture：形状照真实 `ip -o -4 addr show` /
# `ip -o -4 route show table main`（垫片对两种子命令输出同一份，采集端只提前缀并去重）。
# CLEAN 与两种候选网段（上游 192.168.0.0/18、CDS 10.100.0.0/18）均不重叠；
# LAN_CLASH 把 LAN 换成最常见的 192.168.0.0/24（附 VPN 静态路由，验证只靠路由也能拦）。
IP_PREFIXES_CLEAN = """1: lo    inet 127.0.0.1/8 scope host lo
2: eth0    inet 172.20.5.21/24 brd 172.20.5.255 scope global eth0
3: tun0    inet 2.0.1.111/24 scope global tun0
default via 172.20.5.1 dev eth0 proto dhcp src 172.20.5.21 metric 100
172.20.5.0/24 dev eth0 proto kernel scope link src 172.20.5.21
2.0.1.0/24 dev tun0 proto kernel scope link src 2.0.1.111
100.112.0.0/16 via 2.0.1.1 dev tun0 proto static metric 50"""

IP_PREFIXES_LAN_CLASH = """1: lo    inet 127.0.0.1/8 scope host lo
2: wlp0s20f3    inet 192.168.0.190/24 brd 192.168.0.255 scope global dynamic wlp0s20f3
3: tun0    inet 2.0.1.111/24 scope global tun0
default via 192.168.0.1 dev wlp0s20f3 proto dhcp src 192.168.0.190 metric 600
192.168.0.0/24 dev wlp0s20f3 proto kernel scope link src 192.168.0.190 metric 600
2.0.1.0/24 dev tun0 proto kernel scope link src 2.0.1.111
100.112.0.0/16 via 2.0.1.1 dev tun0 proto static metric 50"""


def _make_unix_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()  # 文件节点保留


def build_mock_root(root: Path, *, with_kvm: bool = True, fs_type: str = "xfs",
                    xfs_info_exit: int = 0, with_bpftool: bool = True,
                    cubelet_version_line: str = "cubelet v0.7.3-rc2 (b0c4cc33fbfccbacc1a72241918ef280e9a32772) built at 2026-10-12T06:28:39Z",
                    with_sock: bool = True, mem_total_kb: int = 16_000_000,
                    ebpf_syscall: int = 1, disk_pct: int = 42,
                    ip_prefixes: str = IP_PREFIXES_CLEAN,
                    ip_route_get_body: str = 'echo "10.100.0.2 dev cube-dev proto kernel scope link src 10.100.0.1"',
                    with_systemctl: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "etc").mkdir(exist_ok=True)
    (root / "proc").mkdir(exist_ok=True)
    (root / "sys" / "fs" / "bpf").mkdir(parents=True, exist_ok=True)
    (root / "data" / "cubelet").mkdir(parents=True, exist_ok=True)

    if with_kvm:
        dev = root / "dev" / "kvm"
        dev.parent.mkdir(parents=True, exist_ok=True)
        dev.write_bytes(b"")
        dev.chmod(0o666)

    (root / "etc" / "fs_type").write_text(fs_type + "\n")
    (root / "etc" / "ebpf_syscall").write_text(str(ebpf_syscall) + "\n")
    (root / "etc" / "disk_pct").write_text(str(disk_pct) + "\n")
    (root / "proc" / "meminfo").write_text(f"MemTotal:       {mem_total_kb} kB\n")
    (root / "proc" / "filesystems").write_text("\tproc\tprocfs\n\tsysfs\n\tbpf\n")

    if with_sock:
        _make_unix_socket(root / "run" / "cds.sock")

    b = root / "bin"
    _write_shim(b / "xfs_info",
                f'echo "name: {root}/data/cubelet (dev /dev/loop0)"\necho "meta-data = ...\\treflink=1"\nexit {xfs_info_exit}')
    if with_bpftool:
        _write_shim(b / "bpftool", "exit 0")
    _write_shim(b / "containerd", 'echo "containerd containerd.io 1.7.20 98535bd5e642f4758b1a9be0b00e5b98985c1c2a"')
    _write_shim(b / "cubelet", f'echo "{cubelet_version_line}"')
    # FICLONE 实测探针垫片（生产走 coreutils cp --reflink=always）
    _write_shim(b / "cp", "exit 0")
    # `ip` 垫片：把“本机网段”固定成 fixture，否则检查项 10 会随跑测试机器的真实网段翻脸
    # （开发机 LAN 常为 192.168.0.0/24，与上游默认沙箱网段天然重叠）
    _write_shim(b / "ip",
                'case "$*" in\n'
                '  *"route get"*) ' + ip_route_get_body + ' ;;\n'
                "  *) cat <<'FIXTURE'\n" + ip_prefixes + "\nFIXTURE\n  ;;\n"
                "esac")
    if with_systemctl:
        _write_shim(b / "systemctl",
                    'case "$1" in is-active) echo "active";; *) exit 0;; esac')
    return root


def base_env(mock_root: Path, **extra: str | int | None) -> dict[str, str]:
    env = os.environ.copy()
    env["CDS_CUBE_MOCK_ROOT"] = str(mock_root)
    env["CDS_CUBE_DATA_DIR"] = str(mock_root / "data" / "cubelet")
    env["CDS_CUBE_VOLUME_PLUGIN_SOCKET"] = str(mock_root / "run" / "cds.sock")
    env["CDS_CUBE_CUBELET_BIN"] = str(mock_root / "bin" / "cubelet")
    env["CDS_CUBE_BUNDLE_MANIFEST"] = str(MANIFEST)
    for k, v in extra.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    return env


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _TcpListener:
    """接受连接后回一个合法 HTTP/2 帧头（SETTINGS，type=4）——gRPC 应答的最小真身。"""

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


class _TlsListener:
    """真 TLS 服务：自签证书（CN 含 'cds' 标记），模拟 mTLS sidecar 出示 CDS 证书。"""

    def __init__(self, port: int, cert: Path, key: Path) -> None:
        import ssl
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(str(cert), str(key))
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(8)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self._srv.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                wrapped = self._ctx.wrap_socket(conn, server_side=True)
                wrapped.recv(16)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


@pytest.fixture(scope="module")
def cds_cert(tmp_path_factory: pytest.TempPathFactory):
    """自签 'cds' 标记证书（openssl 在场时生成一次）；无 openssl → 相关用例 skip。"""
    if shutil.which("openssl") is None:
        pytest.skip("openssl 不可用，TLS 证书用例跳过")
    d = tmp_path_factory.mktemp("cds-cert")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", str(d / "key.pem"), "-out", str(d / "cert.pem"),
         "-days", "2", "-nodes", "-subj", "/CN=cds-cube-node-test"],
        check=True, capture_output=True, timeout=60,
    )
    return d


# ---------------------------------------------------------------- preflight


def test_preflight_all_good(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock")
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "FAIL:" not in r.stdout
    for name in ("kvm", "data_fs", "ebpf", "cubelet_bin", "volume_sock",
                 "tls_sidecar", "containerd", "sandbox_cidr", "resources"):
        assert f"PASS: {name}" in r.stdout, f"缺少 PASS: {name}\n{r.stdout}"


def test_preflight_missing_kvm_fails(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock", with_kvm=False)
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: kvm" in r.stdout


def test_preflight_xfs_info_fails(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock", xfs_info_exit=1)
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: data_fs" in r.stdout


def test_preflight_wrong_fs_type(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock", fs_type="ext4")
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: data_fs" in r.stdout


def test_preflight_bpftool_missing(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock", with_bpftool=False)
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: ebpf" in r.stdout


def test_preflight_cubelet_version_mismatch(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock",
                           cubelet_version_line="cubelet version v0.7.2 (1111111111)")
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: cubelet_bin" in r.stdout


def test_preflight_cubelet_right_version_wrong_commit(tmp_path: Path) -> None:
    """版本号对、commit 不是钉住发布构建 → 仍必须 FAIL（旧 OR 语义会假 PASS）。"""
    root = build_mock_root(
        tmp_path / "mock",
        cubelet_version_line="cubelet v0.7.3-rc2 (1111111111111111111111111111111111111111)",
    )
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: cubelet_bin" in r.stdout
    assert "commit 非钉住发布构建" in r.stdout


def test_preflight_volume_sock_missing(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock", with_sock=False)
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: volume_sock" in r.stdout


def test_preflight_tls_sidecar_down(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock")
    # 不起任何监听 → sidecar 不可达必须 FAIL（红线：无明文回退）
    dead_port = free_port()
    r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                       env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=dead_port), timeout=60)
    assert r.returncode == 1
    assert "FAIL: tls_sidecar" in r.stdout


def test_preflight_low_memory(tmp_path: Path) -> None:
    root = build_mock_root(tmp_path / "mock", mem_total_kb=1_000_000)  # ≈1GB < 8GB 门禁
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port), timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: resources" in r.stdout


# ---------------------------------------------------------------- verify


def _run_verify(root: Path, **extra: str | int | None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(VERIFY), "--json"], capture_output=True, text=True,
                          env=base_env(root, **extra), timeout=60)


def test_verify_json_contract_all_green(tmp_path: Path, cds_cert: Path) -> None:
    root = build_mock_root(tmp_path / "mock", with_systemctl=True)
    grpc_port = free_port()
    tls_port = free_port()
    grpc_srv = _TcpListener(grpc_port)
    tls_srv = _TlsListener(tls_port, cds_cert / "cert.pem", cds_cert / "key.pem")
    try:
        r = _run_verify(root, CDS_CUBE_GRPC_PORT=grpc_port, CDS_CUBE_TLS_PROXY_PORT=tls_port,
                        CDS_CUBE_TLS_CA=None, CDS_CUBE_CDS_API_URL=None)
    finally:
        grpc_srv.stop()
        tls_srv.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    doc = json.loads(r.stdout)
    # JSON 契约：ok 布尔 + checks.{name}.{status,detail}
    assert doc["ok"] is True
    checks = doc["checks"]
    assert set(checks) == {"cubelet_grpc", "tls_sidecar", "volume_sock", "audit_tailer", "cds_node_probe", "tee_probe"}
    assert checks["cubelet_grpc"]["status"] == "pass"
    assert checks["tls_sidecar"]["status"] == "pass"
    assert checks["volume_sock"]["status"] == "pass"
    # systemctl 垫片在场 → unit active → pass
    assert checks["audit_tailer"]["status"] == "pass"
    # CB-01 端点未配置 → skipped
    assert checks["cds_node_probe"]["status"] == "skip"
    for c in checks.values():
        assert set(c) == {"status", "detail"}


def test_verify_fails_without_grpc(tmp_path: Path, cds_cert: Path) -> None:
    root = build_mock_root(tmp_path / "mock", with_systemctl=True)
    tls_port = free_port()
    tls_srv = _TlsListener(tls_port, cds_cert / "cert.pem", cds_cert / "key.pem")
    try:
        r = _run_verify(root, CDS_CUBE_GRPC_PORT=free_port(), CDS_CUBE_TLS_PROXY_PORT=tls_port)
    finally:
        tls_srv.stop()
    assert r.returncode == 1
    doc = json.loads(r.stdout)
    assert doc["ok"] is False
    assert doc["checks"]["cubelet_grpc"]["status"] == "fail"


def test_verify_tailer_pending_skips(tmp_path: Path, cds_cert: Path) -> None:
    """systemctl 垫片缺席（非 systemd/单步调试环境）→ audit_tailer 如实 skip，不计失败。"""
    root = build_mock_root(tmp_path / "mock", with_systemctl=False)
    grpc_port = free_port()
    tls_port = free_port()
    grpc_srv = _TcpListener(grpc_port)
    tls_srv = _TlsListener(tls_port, cds_cert / "cert.pem", cds_cert / "key.pem")
    try:
        r = _run_verify(root, CDS_CUBE_GRPC_PORT=grpc_port, CDS_CUBE_TLS_PROXY_PORT=tls_port)
    finally:
        grpc_srv.stop()
        tls_srv.stop()
    assert r.returncode == 0
    doc = json.loads(r.stdout)
    assert doc["ok"] is True
    assert doc["checks"]["audit_tailer"]["status"] == "skip"


def test_preflight_sandbox_cidr_upstream_default_clash_fails(tmp_path: Path) -> None:
    """检查项 10：本机 LAN 落在上游默认沙箱网段 192.168.0.0/18 内 → 硬 FAIL（2026-10-16 现场教训）。"""
    root = build_mock_root(tmp_path / "mock", ip_prefixes=IP_PREFIXES_LAN_CLASH)
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port,
                                        CUBE_SANDBOX_NETWORK_CIDR=None,
                                        CDS_CUBE_SANDBOX_CIDR=None),
                           timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1
    assert "FAIL: sandbox_cidr" in r.stdout
    assert "192.168.0.0/24" in r.stdout
    assert "10.100.0.0/18" in r.stdout  # 失败文案必须自带可执行的改法


def test_preflight_sandbox_cidr_migrated_passes(tmp_path: Path) -> None:
    """同一冲突 LAN 下把沙箱网段显式迁到独立私网段 → 检查项 10 放行（整个门禁仍绿）。"""
    root = build_mock_root(tmp_path / "mock", ip_prefixes=IP_PREFIXES_LAN_CLASH)
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port,
                                        CUBE_SANDBOX_NETWORK_CIDR="10.100.0.0/18"),
                           timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS: sandbox_cidr" in r.stdout


def test_preflight_sandbox_cidr_tun_captured_fails(tmp_path: Path) -> None:
    """检查项 10 归属探针：段与本机前缀不重叠，但代理策略路由把整段吞进 TUN 表 → 必须 FAIL（2026-10-16 实测：`ip route get` 落 `dev Meta table 2022`）。"""
    root = build_mock_root(
        tmp_path / "mock",
        ip_prefixes=IP_PREFIXES_CLEAN,
        ip_route_get_body='if [ "$3" = "240.0.0.1" ]; then '
                          'echo "240.0.0.1 via 172.20.5.1 dev eth0"; else '
                          'echo "10.100.0.2 via 198.18.0.2 dev Meta table 2022 src 198.18.0.1"; fi',
    )
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port,
                                        CUBE_SANDBOX_NETWORK_CIDR="10.100.0.0/18"),
                           timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL: sandbox_cidr" in r.stdout
    assert "route-exclude-address" in r.stdout  # 文案必须直接给出可执行的修法


def test_preflight_sandbox_cidr_blanket_tunnel_passes(tmp_path: Path) -> None:
    """整机默认出口本就是隧道（探针与对照地址同路）时不得误拦——此时 egress 的 link 路由更具体，仍会赢。"""
    root = build_mock_root(tmp_path / "mock", ip_prefixes=IP_PREFIXES_CLEAN,
                           ip_route_get_body='echo "10.100.0.2 via 2.0.1.1 dev tun0"')
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port,
                                        CUBE_SANDBOX_NETWORK_CIDR="10.100.0.0/18"),
                           timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "如实放行" in r.stdout


def test_preflight_sandbox_cidr_bridge_attribution_passes(tmp_path: Path) -> None:
    """建站后探针直落 cube 网桥 → 归属正常（防止只比对前缀、不看归属的旧行为回归）。"""
    root = build_mock_root(tmp_path / "mock", ip_prefixes=IP_PREFIXES_CLEAN)
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port,
                                        CUBE_SANDBOX_NETWORK_CIDR="10.100.0.0/18"),
                           timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "归属正常" in r.stdout
    assert "dev cube-dev" in r.stdout


def test_preflight_missing_data_dir_does_not_abort(tmp_path: Path) -> None:
    """数据盘目录不存在时必须 FAIL data_fs 并继续跑完剩下八项（`set -e + pipefail` 下 `df` 失败会让脚本中途退出，只剩两行结果——2026-10-16 远程实测到此缺陷）。"""
    root = build_mock_root(tmp_path / "mock")
    port = free_port()
    listener = _TcpListener(port)
    try:
        r = subprocess.run(["bash", str(PREFLIGHT)], capture_output=True, text=True,
                           env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=port,
                                        CDS_CUBE_DATA_DIR=str(tmp_path / "no-such-mount")),
                           timeout=60)
    finally:
        listener.stop()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAIL: data_fs" in r.stdout
    assert "PASS: resources" in r.stdout
    assert r.stdout.count(": ") >= 9
