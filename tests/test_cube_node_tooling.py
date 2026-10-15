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


def _make_unix_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()  # 文件节点保留


def build_mock_root(root: Path, *, with_kvm: bool = True, fs_type: str = "xfs",
                    xfs_info_exit: int = 0, with_bpftool: bool = True,
                    cubelet_version_line: str = "cubelet version v0.7.3-rc2 (9b75d33907ed7336b1eef73a935ae2d1990d9b35)",
                    with_sock: bool = True, mem_total_kb: int = 16_000_000,
                    ebpf_syscall: int = 1, disk_pct: int = 42,
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
                 "tls_sidecar", "containerd", "resources"):
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
