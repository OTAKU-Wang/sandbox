# SS-10 CB-02 / CB-06：deploy/cube-node 脚本不变量自动门（ocr-ops 修复轮的测试交付物）。
#
# deploy/cube-node/ 在生产前唯一的自动门就是 tests/（cube-node-smoke.yml 是
# workflow_dispatch-only）。本文件把「bash -n 拦不住」的不变量钉死：
#   - 语法门：4 个 .sh 过 bash -n；2 个 .py 明确 *不是* bash 语法（防止把
#     bash -n 当成它们的门），改走 py_compile；
#   - 结构不变量：heredoc 命令替换的换行重排、fail-fast 不中断全检、
#     mock 钩子隔离与显式披露、CIDR 校验强度、明文回退三态判定、
#     verify 契约（status ∈ pass|fail|skipped、JSON 坏行显式 FATAL、
#     未知参数 usage+exit 2）、bringup 的锁/blkid 三态/挂载来源校验、
#     tailer 的 peek-then-ack 与 TLS fail-closed；
#   - 功能不变量（CDS_CUBE_MOCK_ROOT 演练路径，Windows git-bash 与远端 Linux
#     双口径可跑，不依赖 AF_UNIX/findmnt/systemd/docker）：整跑 preflight/verify
#     断言 PASS/FAIL 矩阵与退出码语义。
# 真节点行为（真实 KVM/XFS/systemd/证书链）本机跑不了——那些属于 FG-017 部署验收。
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CN = REPO / "deploy" / "cube-node"
PREFLIGHT = CN / "preflight.sh"
INSTALL = CN / "install.sh"
VERIFY = CN / "verify.sh"
BRINGUP = CN / "node_bringup.sh"
TAILER_PY = CN / "audit_tailer.py"
PROBE_PY = CN / "tee_probe.py"
MANIFEST = REPO / "scripts" / "cube-bundle-manifest.json"
GATE_PY = Path(sys.executable)

SHELL_SCRIPTS = [PREFLIGHT, INSTALL, VERIFY, BRINGUP]
PY_SCRIPTS = [TAILER_PY, PROBE_PY]

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash 不在 PATH")

# 真实 CPython 的 python3 垫片（git bash 里没有 python3；脚本内嵌 heredoc 都靠它）。
_PYDIR = Path(tempfile.mkdtemp(prefix="cds-w5-py3-"))
atexit.register(lambda: shutil.rmtree(_PYDIR, ignore_errors=True))
_PY3SHIM = _PYDIR / "bin"
_PY3SHIM.mkdir()
_PY3SHIM.joinpath("python3").write_text(
    f'#!/bin/sh\nexec "{GATE_PY.as_posix()}" "$@"\n', encoding="utf-8"
)
_PY3SHIM.joinpath("python3").chmod(0o755)


def read_src(p: Path) -> str:
    # 本机 cp936 坑：读源码文本必须显式 utf-8
    return p.read_text(encoding="utf-8")


def body_of(src: str, fn: str) -> str:
    m = re.search(rf"^{re.escape(fn)}\(\)\s*\{{.*?^\}}", src, re.M | re.S)
    assert m, f"函数 {fn} 不存在"
    return m.group(0)


def run_bash(args: list[str], env: dict[str, str] | None = None,
             timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", *[str(a) for a in args]], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TcpAccepter:
    """accept 即关 —— 满足 preflight tls_sidecar 的 create_connection 探针。"""

    def __enter__(self) -> "TcpAccepter":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def __init__(self) -> None:
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self.port = self._srv.getsockname()[1]
        self._srv.listen(8)
        self._stop = False
        import threading
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self) -> None:
        self._srv.settimeout(0.2)
        while not self._stop:
            try:
                c, _ = self._srv.accept()
            except (TimeoutError, socket.timeout, OSError):
                continue
            c.close()

    def stop(self) -> None:
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass


def load_script(p: Path):
    mod_name = p.stem
    spec = importlib.util.spec_from_file_location(mod_name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def bashpath(p: Path | str) -> str:
    """git-bash 的 PATH/命令解析只吃 POSIX 形式（盘符 D:/… 在 PATH 里无效）；
    原生 python.exe 收到 /d/… 参数时由 msys 自动换算回 D:\\…，两端都成立。"""
    s = Path(p).as_posix()
    if os.name == "nt" and re.match(r"^[A-Za-z]:/", s):
        return "/" + s[0].lower() + s[2:]
    return s


# ---------------------------------------------------------------- 语法门


@needs_bash
@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.name)
def test_shell_scripts_pass_bash_syntax(script: Path) -> None:
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_bash
def test_python_scripts_are_not_bash_and_pass_py_compile() -> None:
    # audit_tailer.py 恰好是合法 bash 的话，说明有人把 bash -n 当成了它的门——
    # 它必须是 Python；Python 门用 py_compile。
    for p in PY_SCRIPTS:
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        assert r.returncode != 0, f"{p.name} 竟同时是合法 bash —— bash -n 门是假绿"
    code = ("import py_compile,sys\n"
            "for f in sys.argv[1:]: py_compile.compile(f, doraise=True)\nprint('ok')")
    r = subprocess.run([sys.executable, "-c", code, *[str(p) for p in PY_SCRIPTS]],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- mock 演练夹具

IP_CLEAN = """1: lo    inet 127.0.0.1/8 scope host lo
2: eth0    inet 172.20.5.21/24 brd 172.20.5.255 scope global eth0
default via 172.20.5.1 dev eth0 proto dhcp src 172.20.5.21 metric 100
172.20.5.0/24 dev eth0 proto kernel scope link src 172.20.5.21"""

# 已建站节点：沙箱网段的桥自己就带着 192.168.0.0/18 —— 它不得算“本机前缀”
IP_CUBE_BRIDGE = IP_CLEAN + """
7: cube0    inet 192.168.0.1/18 brd 192.168.63.255 scope global cube0
192.168.0.0/18 dev cube0 proto kernel scope link src 192.168.0.1"""

IP_LAN_CLASH = """1: lo    inet 127.0.0.1/8 scope host lo
2: wlp0s20f3    inet 192.168.0.190/24 brd 192.168.0.255 scope global dynamic wlp0s20f3
default via 192.168.0.1 dev wlp0s20f3 proto dhcp src 192.168.0.190 metric 600
192.168.0.0/24 dev wlp0s20f3 proto kernel scope link src 192.168.0.190 metric 600"""

CUBELET_VERSION_LINE = ("cubelet v0.7.3-rc2 (b0c4cc33fbfccbacc1a72241918ef280e9a32772) "
                        "built at 2026-10-12T06:28:39Z")


def _sh(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def make_mock(root: Path, *, ip_body: str = IP_CLEAN,
              meminfo: str | None = "MemTotal:       16000000 kB\n",
              disk_pct: int | None = 42, kvm: bool = True,
              fs_type: str = "xfs", with_data_dir: bool = True) -> Path:
    (root / "etc").mkdir(parents=True, exist_ok=True)
    (root / "proc").mkdir(parents=True, exist_ok=True)
    (root / "sys" / "fs" / "bpf").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "fs_type").write_text(fs_type + "\n", encoding="utf-8")
    (root / "etc" / "ebpf_syscall").write_text("1\n", encoding="utf-8")
    if disk_pct is not None:
        (root / "etc" / "disk_pct").write_text(f"{disk_pct}\n", encoding="utf-8")
    if meminfo is not None:
        (root / "proc" / "meminfo").write_text(meminfo, encoding="utf-8")
    (root / "proc" / "filesystems").write_text("\tproc\tprocfs\n\tsysfs\n\tbpf\n",
                                               encoding="utf-8")
    if with_data_dir:
        (root / "data" / "cubelet").mkdir(parents=True, exist_ok=True)
    if kvm:
        dev = root / "dev" / "kvm"
        dev.parent.mkdir(parents=True, exist_ok=True)
        dev.write_bytes(b"")
        dev.chmod(0o666)
    b = root / "bin"
    _sh(b / "xfs_info", 'echo "name: /data (dev /dev/loop0)"\n'
                        'echo "meta-data = ...\\treflink=1"\nexit 0')
    _sh(b / "bpftool", "exit 0")
    _sh(b / "containerd", 'echo "containerd containerd.io 1.7.20 abc"')
    _sh(b / "cubelet", f'echo "{CUBELET_VERSION_LINE}"')
    _sh(b / "cp", "exit 0")
    ipfix = root / "etc" / "ip_prefixes"
    ipfix.write_text(ip_body + "\n", encoding="utf-8", newline="\n")
    _sh(b / "ip", 'case "$*" in\n'
                  '  *"route get"*) echo "10.100.0.2 dev cube-dev proto kernel '
                  'scope link src 10.100.0.1" ;;\n'
                  f'  *) cat "{bashpath(ipfix)}" ;;\n'
                  "esac")
    return root


def base_env(mock: Path, **extra: str | int | None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "CDS_CUBE_MOCK_ROOT": bashpath(mock),
        "CDS_CUBE_DATA_DIR": bashpath(mock / "data" / "cubelet"),
        "CDS_CUBE_CUBELET_BIN": bashpath(mock / "bin" / "cubelet"),
        "CDS_CUBE_VOLUME_PLUGIN_SOCKET": bashpath(mock / "run" / "cds.sock"),
        "CDS_CUBE_BUNDLE_MANIFEST": bashpath(MANIFEST),
        "CDS_CUBE_TLS_PROXY_PORT": str(free_port()),
        # 宿主环境污染隔离：这些键若从开发者环境漏进来，检查语义会整体漂移
        "CDS_CUBE_TLS_CA": "",
        "CDS_CUBE_CDS_API_URL": "",
        "CDS_CUBE_SANDBOX_CIDR": "",
        "CUBE_SANDBOX_NETWORK_CIDR": "",
        "CDS_CUBE_ACCEPT_MOCK": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PATH": os.pathsep.join([str(_PY3SHIM), env.get("PATH", "")]),
    })
    for k, v in extra.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    return env


CHECK_NAMES = ["kvm", "tee_probe", "data_fs", "ebpf", "cubelet_bin", "volume_sock",
               "tls_sidecar", "containerd", "sandbox_cidr", "resources"]


# ---------------------------------------------------------------- preflight


@needs_bash
def test_preflight_full_pass_matrix(tmp_path: Path) -> None:
    """全好 fixture：除 volume_sock（Windows 造不出 AF_UNIX socket 文件，-S 必假）
    外 9 项全 PASS，且汇总行存在、失败数为 1。"""
    root = make_mock(tmp_path / "mock")
    acc = TcpAccepter()
    try:
        r = run_bash([PREFLIGHT], env=base_env(root, CDS_CUBE_TLS_PROXY_PORT=acc.port),
                     timeout=180)
    finally:
        acc.stop()
    out = r.stdout + r.stderr
    lines = [l for l in out.splitlines() if re.match(r"^(PASS|FAIL): ", l)]
    seen_names = [re.match(r"^(?:PASS|FAIL): (\S+)", l).group(1).rstrip(":") for l in lines]
    assert set(seen_names) == set(CHECK_NAMES), \
        f"必须十项检查全有结果行（不得中途静默退出）:\n{out}"
    fails = [l for l in lines if l.startswith("FAIL:")]
    assert all(l.startswith("FAIL: volume_sock") for l in fails), f"意外失败:\n{out}"
    assert "结果: 1 项失败" in out
    for n in CHECK_NAMES:
        if n != "volume_sock":
            assert f"PASS: {n}" in out, f"{n} 未 PASS:\n{out}"


@needs_bash
def test_preflight_broken_manifest_falls_back_to_pins(tmp_path: Path) -> None:
    """P1：manifest 非法 JSON 时必须告警并回退内置钉住值，继续跑完十项——
    而不是 read/EOF 在 set -e 下静默 exit 1（一行输出都没有）。"""
    root = make_mock(tmp_path / "mock")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    env = base_env(root, CDS_CUBE_BUNDLE_MANIFEST=bashpath(bad))
    r = run_bash([PREFLIGHT], env=env, timeout=180)
    out = r.stdout + r.stderr
    assert "回退内置钉住" in out, f"manifest 解析失败必须显式披露:\n{out}"
    assert "v0.7.3-rc2" in out
    assert "结果:" in out, f"必须跑完全部检查并出汇总:\n{out}"


@needs_bash
def test_preflight_cube_bridge_prefix_excluded(tmp_path: Path) -> None:
    """P2：已建站节点的 cube0 桥自带沙箱网段地址——`ip addr` 行没有 ` dev `，
    旧 grep 完全没排除它 → 每台已部署节点永久误报重叠。修复后必须 PASS。"""
    root = make_mock(tmp_path / "mock", ip_body=IP_CUBE_BRIDGE)
    env = base_env(root, CDS_CUBE_SANDBOX_CIDR="192.168.0.0/18",
                   CUBE_SANDBOX_NETWORK_CIDR=None)
    r = run_bash([PREFLIGHT], env=env, timeout=180)
    assert "PASS: sandbox_cidr" in r.stdout, r.stdout + r.stderr
    assert "FAIL: sandbox_cidr" not in r.stdout


@needs_bash
def test_preflight_ds_abort_paths_still_summarize(tmp_path: Path) -> None:
    """P4/P15：DATA_DIR 不可 df（无 disk_pct fixture + 目录不存在）时，
    必须 FAIL: resources（含可读原因）并继续出汇总；resources 不得排在最后
    被前面的 abort 吞掉（头部清单顺序：resources 在 tee/cidr 之前）。"""
    root = make_mock(tmp_path / "mock", disk_pct=None)
    env = base_env(root, CDS_CUBE_DATA_DIR=bashpath(tmp_path / "no-such-mount"))
    r = run_bash([PREFLIGHT], env=env, timeout=180)
    out = r.stdout + r.stderr
    assert "FAIL: resources" in out, out
    assert "结果:" in out, f"df 失败不得掐死汇总:\n{out}"
    idx = {n: re.search(rf"^(?:PASS|FAIL): {n}\b", out, re.M) for n in
           ("data_fs", "resources", "sandbox_cidr")}
    assert all(idx.values()) and \
        idx["data_fs"].start() < idx["resources"].start() < idx["sandbox_cidr"].start(), \
        f"检查顺序必须与头部清单一致 (…data_fs < resources < sandbox_cidr):\n{out}"


@needs_bash
def test_preflight_mock_mode_redirects_data_dir(tmp_path: Path) -> None:
    """P10：mock 演练里 DATA_DIR 也得进 MOCK_ROOT——未显式设 CDS_CUBE_DATA_DIR 时，
    data_fs 检查的是 <mock>/data/cubelet（旧代码读写真实 /data/cubelet）。"""
    root = make_mock(tmp_path / "mock")
    env = base_env(root)
    env.pop("CDS_CUBE_DATA_DIR")
    r = run_bash([PREFLIGHT], env=env, timeout=180)
    assert "FAIL: data_fs" not in r.stdout, \
        f"演练模式不得碰真实 /data/cubelet:\n{r.stdout}{r.stderr}"


@needs_bash
def test_preflight_mock_banner(tmp_path: Path) -> None:
    """P9：CDS_CUBE_MOCK_ROOT 会静默弱化整个硬门禁——必须在输出里一眼可见。"""
    root = make_mock(tmp_path / "mock")
    r = run_bash([PREFLIGHT], env=base_env(root), timeout=180)
    assert "MOCK" in r.stdout, "演练模式必须打印醒目标记（生产误设可被发现）"


@needs_bash
def test_preflight_tls_sidecar_discloses_port_only(tmp_path: Path) -> None:
    """P11：这一项只能证明端口有监听者；pass 文案必须如实限定范围。"""
    root = make_mock(tmp_path / "mock")
    with TcpAccepter() as acc:
        env = base_env(root, CDS_CUBE_TLS_PROXY_PORT=acc.port)
        r = run_bash([PREFLIGHT], env=env, timeout=180)
    m = re.search(r"PASS: tls_sidecar .*", r.stdout)
    assert m and ("端口" in m.group(0) or "verify.sh" in m.group(0)), \
        f"tls_sidecar PASS 必须声明其仅为端口可达性: {m}"


@needs_bash
def test_preflight_meminfo_unreadable_fails_honestly(tmp_path: Path) -> None:
    """P12：meminfo 无 MemTotal 行 → 必须如实 FAIL「无法读取」：旧版把空串送进
    [[ -lt ]]（表达式报错走 else）把读不到伪装成 PASS 0GB（fail-open）。"""
    root = make_mock(tmp_path / "mock", meminfo="HugePages: 0\n")
    r = run_bash([PREFLIGHT], env=base_env(root), timeout=180)
    assert re.search(r"FAIL: resources.*无法从.*读取 MemTotal", r.stdout), r.stdout + r.stderr
    assert "PASS: resources" not in r.stdout


@needs_bash
def test_preflight_oneclick_env_export_form_parsed(tmp_path: Path) -> None:
    """P13：.one-click.env 里 `export KEY=` / 缩进写法都必须解析出来——
    否则在已迁移网段的机器上按上游默认误 FAIL。"""
    root = make_mock(tmp_path / "mock", ip_body=IP_LAN_CLASH)
    oc = root / "usr/local/services/cubetoolbox"
    oc.mkdir(parents=True, exist_ok=True)
    (oc / ".one-click.env").write_text(
        "  export CUBE_SANDBOX_NETWORK_CIDR=10.100.0.0/18\n", encoding="utf-8")
    env = base_env(root, CDS_CUBE_SANDBOX_CIDR=None, CUBE_SANDBOX_NETWORK_CIDR=None)
    r = run_bash([PREFLIGHT], env=env, timeout=180)
    assert "PASS: sandbox_cidr" in r.stdout, \
        f"export 行必须被读到（否则按 192.168.0.0/18 误拦）:\n{r.stdout}{r.stderr}"


@needs_bash
def test_preflight_cds_key_wins_over_upstream_key(tmp_path: Path) -> None:
    """P8：运维显式设 CDS_CUBE_SANDBOX_CIDR 预验候选段时，它必须赢过环境里
    残留的上游键 CUBE_SANDBOX_NETWORK_CIDR（与文件头注释一致）。"""
    root = make_mock(tmp_path / "mock", ip_body=IP_LAN_CLASH)
    env = base_env(root, CDS_CUBE_SANDBOX_CIDR="10.100.0.0/18",
                   CUBE_SANDBOX_NETWORK_CIDR="192.168.0.0/18")
    r = run_bash([PREFLIGHT], env=env, timeout=180)
    assert "PASS: sandbox_cidr" in r.stdout, \
        f"CDS 键必须优先（否则校验的是另一个网段）:\n{r.stdout}{r.stderr}"
    assert "10.100.0.0/18" in r.stdout


@needs_bash
def test_preflight_probe_dir_trap(tmp_path: Path) -> None:
    """P14/P5：dd 写探针失败（只读盘/满盘的真实形态）时，必须给出 FAIL: data_fs
    而不是 set -e 中途夭折；探针目录任何路径都不得残留在数据盘上。"""
    root = make_mock(tmp_path / "mock")
    _sh(root / "bin" / "dd", "exit 1")  # 数据盘只读时 dd 的真实退出形态
    env = base_env(root)
    r = run_bash([PREFLIGHT], env=env, timeout=180)
    assert "FAIL: data_fs" in r.stdout, f"dd 失败必须落为 FAIL 行:\n{r.stdout}{r.stderr}"
    residue = list((root / "data" / "cubelet").glob(".cds-preflight.*"))
    assert not residue, f"探针目录泄漏: {residue}"


def test_preflight_structural_invariants() -> None:
    src = read_src(PREFLIGHT)
    # P7：CIDR 八位组必须限 0-255，ip2int 越界必须拒
    assert "25[0-5]" in src and "2[0-4]" in src, "CIDR 正则必须约束八位组 0-255"
    ip2int = body_of(src, "ip2int")
    assert "255" in ip2int and "return 1" in ip2int, "ip2int 必须拒绝越界八位组"
    # P3：host_prefixes 的采集赋值不得被 pipefail 带倒（失败走 fail-closed）
    assert re.search(r"if\s+!\s+hosts=.*host_prefixes", src), \
        "host_prefixes 命令替换必须在 if 上下文（非零时 fail-closed，不得带崩预检）"
    # P5：dd 写探针必须在错误处理里，python ebpf 探针失败要如实降级到 proc 判定
    dfs = body_of(src, "check_data_fs")
    assert re.search(r"if\s+!\s+dd\b", dfs), "dd 探针必须纳入 if !/|| true 处理"
    eb = body_of(src, "check_ebpf")
    assert re.search(r"probe=.*\|\| true", eb) or "if ! probe=" in eb \
        or 'probe="err"' in eb, \
        "bpf syscall 探针失败必须显式回退，不得带倒整个 preflight"
    # P6：fs 类型判定优先 findmnt（df -T 长设备名折行会误报 unknown）
    assert "findmnt" in src, "文件系统类型必须走不折行的 findmnt（df -PT 兜底）"
    # P16：`python3 - args <<'EOF' ... EOF` 之后紧跟 then/)) 的写法在 bash 4.4
    # （git-bash CI 冒烟门）里直接语法错，全部内嵌脚本必须走 -c / 换行重排。
    assert not re.search(r"<<'EOF'\n(?:[^\n]*\n)*?EOF[ \t]*(?:then|\)\)|;)", src), \
        "heredoc 结束标记后不得紧跟 then/)) —— bash 4.4 语法门会红"


# ---------------------------------------------------------------- install.sh


def test_install_root_required_without_mock(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("CDS_CUBE_MOCK_ROOT", None)
    env.pop("CDS_CUBE_DATA_DIR", None)
    r = run_bash([INSTALL], env=env, timeout=60)
    assert r.returncode == 1
    assert "root" in (r.stdout + r.stderr)


def test_install_structural_invariants() -> None:
    src = read_src(INSTALL)
    # I2/I5：服务令牌 env 文件必须 0600 且先预建（umask/install -m），不得 sed 直出全局可读
    assert re.search(r"umask 077|install -m 0600 /dev/null", src), \
        "audit-tailer.env 必须先收紧 umask/预建再渲染（0640+umask 竞态）"
    assert re.search(r"chmod 0600 .*audit-tailer\.env|0600 .*audit-tailer\.env", src), \
        "含服务令牌的文件必须 0600"
    # I4：明文回退三态——任一证书变量已设而文件缺失必须 die，而不是静默走明文
    tls = src.split("② TLS 前置代理")[1].split("③ 审计 tailer")[0] if "② TLS 前置代理" in src else src
    assert re.search(r"已设置|部分配置|partial", tls), "证书部分缺失必须 die（不得静默明文回退）"
    # I6：tailer 二进制渲染进 systemd/JSON 前必须校验绝对路径
    assert "绝对路径" in src, "tailer 二进制路径必须校验（绝对路径、无空格/|）"
    # I7/I10：结果目录默认值与 export 只落一处
    assert src.count("/var/lib/cds/sandbox-results") <= 2, \
        "RESULTS_DIR 默认值不得三处字面量重复"
    assert re.search(r"export[^\n]*CDS_CUBE_RESULTS_DIR", src), \
        "CDS_CUBE_RESULTS_DIR 必须 export 进组件子 shell"
    # I8：endpoint fail-closed；状态 JSON 必须过 json.load 校验后再发布
    assert "audit-tailer.env.template" in src
    assert "json.load" in src, "INSTALL_STATE.json 必须校验合法后再原子替换"
    # I9：私钥属主收紧 root；stunnel 路径可覆盖
    assert "chown root" in src, "tls.key 需显式收紧属主"
    assert "CDS_CUBE_STUNNEL_BIN" in src, "stunnel 二进制路径不得硬编码 /usr/bin"
    # I3：状态落盘必须在 EXIT trap 里，部分失败也要可观测；config 追加需要锁
    assert re.search(r"trap .*EXIT", src), "INSTALL_STATE.json 需要失败路径落盘（trap EXIT）"
    assert "flock" in src, "config.toml 追加/状态写盘需要 flock（并发安装/重跑）"
    # I11：TOML 注册必须防重复表/单引号写法
    assert "['\\\"]cds-encrypted" in src, "TOML 幂等判据需同时覆盖单/双引号"
    assert 'grep -qF' in src and "io.cubelet.internal.v1.storage" in src, \
        "追加前必须探测既有表头，防重复表（非法 TOML）"
    # I5：上游目录默认值不得是个人工作目录
    assert "/home/kratos" not in src, "生产默认路径不得是个人目录"
    # I12：非 mock 模式交付物缺席必须显式告警
    assert "降级交付" in src, "非 mock 下 CB-03/CB-06 缺席必须显式披露"
    # P16 同款：不得有 bash4.4 炸裂的 heredoc 形状
    assert not re.search(r"<<'EOF'\n(?:[^\n]*\n)*?EOF[ \t]*(?:then|\)\)|;)", src)


# ---------------------------------------------------------------- verify.sh


def _run_verify(root: Path, **extra: str | int | None) -> subprocess.CompletedProcess[str]:
    return run_bash([VERIFY, "--json"], env=base_env(root, **extra), timeout=180)


@needs_bash
def test_verify_contract_statuses_and_exit(tmp_path: Path) -> None:
    """AGENTS.md 契约：status ∈ pass|fail|skipped；有 fail → 非零；--json 可解析。"""
    root = make_mock(tmp_path / "mock")
    r = _run_verify(root, CDS_CUBE_GRPC_PORT=free_port(), CDS_CUBE_CDS_API_URL=None)
    doc = json.loads(r.stdout)
    assert doc["ok"] is False
    assert r.returncode == 1
    for name, c in doc["checks"].items():
        assert c["status"] in ("pass", "fail", "skipped"), \
            f"{name}: status={c['status']} 违反 pass|fail|skipped 契约"
    assert doc["checks"]["audit_tailer"]["status"] == "skipped"
    assert doc["checks"]["cds_node_probe"]["status"] == "skipped"
    assert doc["checks"]["volume_sock"]["status"] == "fail"
    assert doc["checks"]["cubelet_grpc"]["status"] == "fail"
    assert "tee" in doc


@needs_bash
def test_verify_http2_probe_requires_settings_frame(tmp_path: Path) -> None:
    """V3：裸 TCP echo（第 4 字节恰落在 0..8）不得被判成 gRPC/HTTP2 应答——
    必须校验首帧 type==SETTINGS(4) 且长度自洽。"""
    root = make_mock(tmp_path / "mock")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(4)
    import threading

    def _echo():
        try:
            conn, _ = srv.accept()
            conn.recv(64)
            conn.sendall(b"\x16\x03\x03\x00\x00\x00\x00\x00\x00")  # TLS record hdr
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=_echo, daemon=True)
    t.start()
    try:
        r = _run_verify(root, CDS_CUBE_GRPC_PORT=port, CDS_CUBE_CDS_API_URL=None)
    finally:
        srv.close()
    doc = json.loads(r.stdout)
    assert doc["checks"]["cubelet_grpc"]["status"] == "fail", \
        "TLS 记录头被当成 HTTP/2 SETTINGS —— 假阳性"


@needs_bash
def test_verify_socket_liveness_not_stale(tmp_path: Path) -> None:
    """V10：只判 -S 会把插件崩溃后的 stale socket 记 pass——至少要探测/声明。"""
    src = read_src(VERIFY)
    body = body_of(src, "check_volume_sock")
    assert "AF_UNIX" in src or "陈旧" in body or "未做活性探测" in body, \
        "volume_sock 需要 connect 探测或如实降级声明"


@needs_bash
def test_verify_tls_ca_when_configured(tmp_path: Path) -> None:
    """V4/V5：设了 CDS_CUBE_TLS_CA 后握手必须做链+主机名校验；CDS 标记只查
    subject/issuer（不再整 blob 子串）。"""
    src = read_src(VERIFY)
    body = body_of(src, "check_tls_sidecar")
    assert "CERT_NONE" in body  # 无 CA 时仍宽松（自签 CI 冒烟）
    assert re.search(r"if ca|ca[^\n]*:|check_hostname = True", body)
    assert re.search(r"cafile", body), "CA 在场必须 load_verify_locations(cafile=…)"
    assert "blob.encode(\"latin-1\").decode" not in src, \
        "禁止在整份 DER 二进制上找 'cds' 子串"
    assert "subject" in src and "issuer" in src


@needs_bash
def test_verify_mock_guard(tmp_path: Path) -> None:
    """V9：CDS_CUBE_MOCK_ROOT 是 test-only 钩子——被生产误继承时必须拒绝，
    除非显式 CDS_CUBE_ACCEPT_MOCK=1（演练入口本身要设它）。"""
    root = make_mock(tmp_path / "mock")
    env = base_env(root, CDS_CUBE_ACCEPT_MOCK=None)
    env.pop("CDS_CUBE_ACCEPT_MOCK", None)
    r = run_bash([VERIFY], env=env, timeout=60)
    assert r.returncode != 0
    assert "MOCK" in (r.stdout + r.stderr)
    # 显式确认后（本文件其余 verify 用例走这条）演练照常
    env2 = base_env(root, CDS_CUBE_ACCEPT_MOCK="1")
    r2 = run_bash([VERIFY, "--json"], env=env2, timeout=180)
    assert json.loads(r2.stdout)["checks"], "显式确认后 mock 演练必须照常"


@needs_bash
def test_verify_unknown_args_rejected(tmp_path: Path) -> None:
    """V11：--json 的拼写变体/多余参数必须 usage+exit 2，不得静默出人类文本。"""
    root = make_mock(tmp_path / "mock")
    r = run_bash([VERIFY, "--json=1"], env=base_env(root), timeout=60)
    assert r.returncode == 2
    assert "usage" in (r.stdout + r.stderr).lower()


@needs_bash
def test_verify_tee_uses_device_root_and_stderr(tmp_path: Path) -> None:
    """V8：必须把 mock 的 device root 传给 tee_probe 并捕获 stderr（失败原因可见）。"""
    src = read_src(VERIFY)
    body = body_of(src, "check_tee_probe")
    assert "CDS_CUBE_TEE_DEVICE_ROOT" in body
    assert "2>" in body or "err" in body, "tee_probe stderr 必须捕获进 detail"


def test_verify_record_sanitizes_detail() -> None:
    """V1/V2：detail 嵌命令输出，TSV 里出现 tab/换行会让 JSON 段静默丢行、
    awk 列错位——record() 必须清洗；JSON 段对坏行必须显式 FATAL 非零。"""
    src = read_src(VERIFY)
    m = re.search(r"record\(\)\s*\{.*", src)
    assert m and "| tr" in m.group(0), "record() 必须把 detail 里的 tab/换行压平（| tr …）"
    assert "FATAL" in src, \
        "结果行 !=3 列必须显式 FATAL，不得静默丢弃（静默丢 fail 行 = 假通过）"


# ---------------------------------------------------------------- node_bringup.sh


def test_bringup_structural_invariants() -> None:
    src = read_src(BRINGUP)
    # B1：blkid 三态（0=有 FS，2=确无，其他=中止），失败绝不落 mkfs
    assert "blkid_rc" in src and re.search(r"\$\{blkid_rc\}.*-eq 2", src) and \
        "无法确认" in src, \
        "blkid 非 0/2 退出码必须中止（不得把探测失败当无文件系统直接 mkfs -f）"
    # B2：已有 loop 附着必须复用（losetup -j），mkfs/mount 失败必须 trap 回滚
    assert "losetup -j" in src, "必须复用已存在的 loop 附着，防同镜像双附着"
    assert re.search(r"trap .* (ERR|EXIT)", src), "磁盘段需要失败回滚 trap"
    # B3：并发/重入需要 flock
    assert "flock" in src, "bring-up 需要独占锁（cloud-init/人工/CI 并发）"
    # B4：挂载点必须校验来源设备 + fstype；已挂载但缺 fstab 行必须补
    assert "findmnt" in src and ("source" in src or "SOURCE" in src), \
        "必须校验 $DATA_DIR 的挂载来源，错配即中止"
    assert re.search(r"grep -qF .*fstab|\$\(grep", src)
    # B5：上游目录属主/可写性校验，禁个人目录默认值
    assert "stat -c" in src or "stat -c '%U'" in src.replace('"', "'"), \
        "root 执行的上游目录必须校验属主/权限"
    assert "/home/kratos" not in src, "生产默认路径不得是个人工作目录"
    # B6/B7：fstab 追加防无换行、legacy 清理无条件且正则元字符转义
    assert re.search(r"tail -c ?1", src), "追加 fstab 前须保证末行有换行"
    assert "无条件" in src, "legacy /dev/loopN 清理必须与挂载状态无关（无条件执行）"
    assert re.search(r"sed_escaped|esc=", src), "sed/正则里的路径必须转义元字符"
    # B8：明文 gRPC 回退要显式确认
    assert "CDS_ALLOW_INSECURE_GRPC" in src, \
        "prod 误跑不得静默关 TLS——需显式 CDS_ALLOW_INSECURE_GRPC=yes"
    # B9：loop 设备与 mkfs.xfs 在场检查必须前置到磁盘操作之前
    assert "loop-control" in src, "loop 可用性检查前置"
    assert src.index("loop-control") < src.index("losetup -f"), \
        "前置自检必须早于 losetup/mkfs"
    # B8'：CDS 安装段失败不得继续 verify / 打 BRINGUP_DONE
    assert re.search(r"install\.sh.*\|\|.*exit|install\.sh.*&& ", src.replace("\n", " ")), \
        "install 失败必须终止 bring-up（否则 BRINGUP_DONE 说谎）"


# ---------------------------------------------------------------- tee_probe.py


def test_tee_probe_non_dict_doc_preserved(tmp_path: Path) -> None:
    """T1：capabilities 文件是合法 JSON 但顶层非 dict → 必须走 recovered 路径
    （保留标记 + warn），不得静默整体覆盖既有节点能力键。"""
    mod = load_script(PROBE_PY)
    p = tmp_path / "caps.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    mod.write_capabilities(p, {"tee": "none", "providers": {}})
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc.get("tee") == "none"
    assert doc.get("_capabilities_file_recovered_from_corrupt") is True, \
        "非 dict 文档也必须留 recovered 标记（既有键丢失必须可归因）"


def test_tee_probe_atomic_write(tmp_path: Path, monkeypatch) -> None:
    """T2：capabilities 重写必须 tmp+os.replace 原子替换——中途失败不得留下
    被截断的 JSON（否则节点能力键就此丢失）。"""
    mod = load_script(PROBE_PY)
    p = tmp_path / "caps.json"
    p.write_text(json.dumps({"gpu": "a100", "tee": "none"}), encoding="utf-8")
    real_replace = os.replace

    def boom(a, b, **kw):
        raise OSError("crash mid-write")

    monkeypatch.setattr(os, "replace", boom)
    try:
        with pytest.raises(OSError):
            mod.write_capabilities(p, {"tee": "tdx", "providers": {}})
    finally:
        monkeypatch.setattr(os, "replace", real_replace)
    doc = json.loads(p.read_text(encoding="utf-8"))  # 原文件必须仍是合法 JSON
    assert doc["gpu"] == "a100"


def test_tee_probe_stream_contract(tmp_path: Path) -> None:
    """T3：文档承诺必须与实现一致——--json 时 stdout 恒为单行 tee=...，
    JSON 走 stderr（改文档不改行为：preflight 抓的就是 stdout 单行）。"""
    caps = tmp_path / "caps.json"
    r = subprocess.run(
        [sys.executable, str(PROBE_PY), "--json", "--capabilities-file", str(caps)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0
    assert re.fullmatch(r"tee=(tdx|sev_snp|none)", r.stdout.strip()), \
        f"--json 的 stdout 必须是单行 tee=: {r.stdout!r}"
    doc = json.loads(r.stderr)
    assert doc["tee"] in ("tdx", "sev_snp", "none")
    # 参数错误按文档 = 2
    r2 = subprocess.run([sys.executable, str(PROBE_PY), "--bogus"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=60)
    assert r2.returncode == 2
    doc_src = read_src(PROBE_PY)
    assert "stderr" in doc_src.split("退出码")[0] or "stderr" in doc_src
    assert "2 = 参数错误" in doc_src or "2 = argparse" in doc_src


def test_tee_probe_device_root_isolates_commands(tmp_path: Path) -> None:
    """T4：device_root != "/" 时命令探测也必须落在 <root>/bin，
    否则宿主机真实 tdx-attest/snpguest 会让 fixture 场景假阳性。"""
    mod = load_script(PROBE_PY)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    hit = fake_bin / "snpguest"
    hit.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hit.chmod(0o755)
    res = mod.probe(device_root=tmp_path)
    assert res["tee"] == "sev_snp", f"命令探测必须限定在 device_root/bin: {res}"
    with pytest.raises(ValueError):
        mod.probe(device_root="relative/dir")  # T5：相对路径拒绝（结果随 cwd 漂移）


# ---------------------------------------------------------------- audit_tailer.py


def test_tailer_tls_fail_closed_rejects_plaintext_endpoint(tmp_path: Path, monkeypatch) -> None:
    """A7：endpoint 写 http:// 会把 Bearer 令牌明文发出——启动必须拒绝。"""
    mod = load_script(TAILER_PY)
    for name in ("crt", "key", "ca"):
        (tmp_path / f"t.{name}").write_text("PEM", encoding="utf-8")
    base = {
        "AUDIT_TAILER_NODE_ID": "n1",
        "AUDIT_TAILER_CDS_ENDPOINT": "http://cds:8000",
        "AUDIT_TAILER_TLS_CERT": str(tmp_path / "t.crt"),
        "AUDIT_TAILER_TLS_KEY": str(tmp_path / "t.key"),
        "AUDIT_TAILER_TLS_CA": str(tmp_path / "t.ca"),
        "AUDIT_TAILER_SERVICE_TOKEN": "t0k",
    }
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(mod.TailerConfigError):
        mod.build_from_env(pusher=object())


def test_tailer_tls_material_must_load(tmp_path: Path, monkeypatch) -> None:
    """A7：空/坏 PEM 得过存在性检查，必须真加载校验（否则跑到推送期才烂）。"""
    mod = load_script(TAILER_PY)
    (tmp_path / "t.crt").write_text("not a pem", encoding="utf-8")
    (tmp_path / "t.key").write_text("not a pem", encoding="utf-8")
    (tmp_path / "t.ca").write_text("not a pem", encoding="utf-8")
    for k, v in {
        "AUDIT_TAILER_NODE_ID": "n1",
        "AUDIT_TAILER_CDS_ENDPOINT": "https://cds:8000",
        "AUDIT_TAILER_TLS_CERT": str(tmp_path / "t.crt"),
        "AUDIT_TAILER_TLS_KEY": str(tmp_path / "t.key"),
        "AUDIT_TAILER_TLS_CA": str(tmp_path / "t.ca"),
        "AUDIT_TAILER_SERVICE_TOKEN": "t0k",
        "AUDIT_TAILER_OFFSET_FILE": str(tmp_path / "offset.json"),
        "AUDIT_TAILER_QUEUE_FILE": str(tmp_path / "queue.jsonl"),
        "AUDIT_TAILER_OVERFLOW_MARKER": str(tmp_path / "marker"),
        "AUDIT_TAILER_AUDIT_DIR": str(tmp_path / "audit"),
    }.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(mod.TailerConfigError):
        mod.build_from_env(pusher=object())


def test_tailer_poll_interval_validated_at_build(tmp_path: Path, monkeypatch) -> None:
    """A12：poll 间隔解析/取值错误必须走 fail-closed（TailerConfigError→exit 2），
    不得 traceback，也不得 0 间隔 busy-loop。"""
    mod = load_script(TAILER_PY)
    for name in ("crt", "key", "ca"):
        (tmp_path / f"t.{name}").write_text("PEM", encoding="utf-8")
    for k, v in {
        "AUDIT_TAILER_NODE_ID": "n1",
        "AUDIT_TAILER_CDS_ENDPOINT": "https://cds:8000",
        "AUDIT_TAILER_TLS_CERT": str(tmp_path / "t.crt"),
        "AUDIT_TAILER_TLS_KEY": str(tmp_path / "t.key"),
        "AUDIT_TAILER_TLS_CA": str(tmp_path / "t.ca"),
        "AUDIT_TAILER_SERVICE_TOKEN": "t0k",
        "AUDIT_TAILER_OFFSET_FILE": str(tmp_path / "offset.json"),
        "AUDIT_TAILER_QUEUE_FILE": str(tmp_path / "queue.jsonl"),
        "AUDIT_TAILER_OVERFLOW_MARKER": str(tmp_path / "marker"),
        "AUDIT_TAILER_AUDIT_DIR": str(tmp_path / "audit"),
        "AUDIT_TAILER_POLL_INTERVAL_SEC": "500ms",
    }.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(mod.TailerConfigError):
        mod.build_from_env(pusher=object())
