"""A96d — firecracker_runtime 的会话密钥/DEK 不得走 argv（A96 同类缺陷第四处）。

钉住三件事，语义与 A96（sandbox_security.GuestSecrets）一致：
1. 构造出来的 bwrap argv / ssh 远端命令里既没有密钥**值**也没有密钥**变量名**；
2. 有通道时值经 exec 的 stdin 交给客侧 bootstrap —— 用替身子进程真跑一遍，
   子进程自己读 /proc/self/cmdline 证明 argv 干净、环境变量拿得到；
3. 拿不到通道时 fail-closed：bootstrap 以 rc=97 拒绝执行并在 stderr 自报 A96，
   绝不静默无密钥跑成。

取证边界（不夸大）：本机没有真 Firecracker，本文件**未验真 Firecracker**；断言对象是
argv/env 构造与替身进程的 bootstrap 真执行。可达性：本适配器只在 legacy 门后注册。
"""

import os
import subprocess
from pathlib import Path

from app.services.sandbox_security import (
    GUEST_SECRET_ARGV0,
    GUEST_SECRET_BOOTSTRAP,
    SECRET_ENV_NAMES,
    GuestSecrets,
)

SESSION_KEY = "sk-session-0123456789abcdef0123456789abcdef"
DEK_HEX = "de" * 16
KEY_ID = "key-id-sm4-00000001"

PROBE = """
import os
print("KEY=" + os.environ.get("CDS_SESSION_KEY", "<none>"))
print("DEK=" + os.environ.get("CDS_DEK_HEX", "<none>"))
print("KEYID=" + os.environ.get("CDS_KEY_ID", "<none>"))
print("PLAIN=" + os.environ.get("CDS_SANDBOX_LEVEL", "<none>"))
print("CMDLINE=" + open("/proc/self/cmdline", "rb").read().decode(errors="replace"))
"""


def _close_fd(fd: int | None) -> None:
    if fd is not None:
        os.close(fd)


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    for sub in ("input", "output", "tmp"):
        (ws / sub).mkdir(parents=True)
    return ws


def _assert_zero_secret(argv, where: str) -> None:
    """argv/命令串里既不得出现密钥值，也不得出现密钥变量名（同 UID 任意进程可读 cmdline）。"""
    joined = argv if isinstance(argv, str) else "\x00".join(argv)
    for secret in (SESSION_KEY, DEK_HEX, KEY_ID):
        assert secret not in joined, (
            f"A96d: 明文密钥出现在 {where} 里（同 UID 进程可从 /proc/<pid>/cmdline 读到）: {secret}"
        )
    for name in SECRET_ENV_NAMES:
        assert name not in joined, f"A96d: 密钥变量名 {name} 出现在 {where} 里"


def _bootstrap_argv(argv: list[str], probe_path: str) -> list[str]:
    """取构造出的 argv 里的 bootstrap 段，换成探针命令后交给替身子进程真执行。"""
    assert GUEST_SECRET_ARGV0 in argv, "A96d: argv 里没有带外通道 bootstrap（密钥值无处投递）"
    idx = argv.index(GUEST_SECRET_ARGV0)
    count = argv[idx + 1]
    assert count.isdigit(), f"A96d: bootstrap 的变量个数应为数字，实际 {count!r}"
    return ["sh", "-c", GUEST_SECRET_BOOTSTRAP, GUEST_SECRET_ARGV0, count, "python3", probe_path]


def test_sim_bwrap_argv_carries_no_secret(tmp_path):
    """会话密钥与 DEK 不进 bwrap argv；普通（非密钥）变量照旧 --setenv 注入。"""
    from app.services.firecracker_runtime import FirecrackerRuntime

    secrets = GuestSecrets()
    argv, seccomp_fd = FirecrackerRuntime._build_sim_bwrap_args(
        workspace=_workspace(tmp_path),
        code_file="/workspace/tmp/exec.py",
        language="python",
        session_key=SESSION_KEY,
        env_vars={"CDS_DEK_HEX": DEK_HEX, "CDS_KEY_ID": KEY_ID, "CDS_SANDBOX_LEVEL": "L2"},
        secrets=secrets,
    )
    try:
        _assert_zero_secret(argv, "firecracker sim bwrap argv")
        assert "CDS_SANDBOX_LEVEL" in argv
        assert set(secrets.names) >= {"CDS_SESSION_KEY", "CDS_DEK_HEX", "CDS_KEY_ID"}
    finally:
        _close_fd(seccomp_fd)
        secrets.close()


def test_sim_bwrap_without_channel_fails_closed(tmp_path):
    """没有带外通道时也不许把密钥写进 argv：argv 零密钥，客侧以 rc=97 拒绝执行并自报原因。"""
    from app.services.firecracker_runtime import FirecrackerRuntime

    argv, seccomp_fd = FirecrackerRuntime._build_sim_bwrap_args(
        workspace=_workspace(tmp_path),
        code_file="/workspace/tmp/exec.py",
        language="python",
        session_key=SESSION_KEY,
    )
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    try:
        _assert_zero_secret(argv, "无通道的 firecracker sim bwrap argv")
        proc = subprocess.run(
            _bootstrap_argv(argv, str(probe)),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    finally:
        _close_fd(seccomp_fd)
    assert proc.returncode == 97, (
        f"A96d: 拿不到密钥时客侧必须 fail-closed（rc=97），实际 rc={proc.returncode} out={proc.stdout!r}"
    )
    assert "A96" in proc.stderr


def test_bootstrap_child_gets_secrets_and_clean_cmdline(tmp_path):
    """替身子进程真跑 bootstrap：环境变量拿得到，自己的 cmdline 里没有密钥。"""
    from app.services.firecracker_runtime import FirecrackerRuntime

    secrets = GuestSecrets()
    argv, seccomp_fd = FirecrackerRuntime._build_sim_bwrap_args(
        workspace=_workspace(tmp_path),
        code_file="/workspace/tmp/exec.py",
        language="python",
        session_key=SESSION_KEY,
        env_vars={"CDS_DEK_HEX": DEK_HEX, "CDS_KEY_ID": KEY_ID},
        secrets=secrets,
    )
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    try:
        _assert_zero_secret(argv, "firecracker sim bwrap argv")
        proc = subprocess.Popen(
            _bootstrap_argv(argv, str(probe)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        secrets.deliver(proc)
        out, err = proc.communicate(timeout=30)
    finally:
        _close_fd(seccomp_fd)
        secrets.close()
    assert proc.returncode == 0, f"bootstrap 执行失败 rc={proc.returncode} stderr={err!r}"
    assert f"KEY={SESSION_KEY}" in out, out
    assert f"DEK={DEK_HEX}" in out, out
    assert f"KEYID={KEY_ID}" in out, out
    cmdline = next(line for line in out.splitlines() if line.startswith("CMDLINE="))
    for secret in (SESSION_KEY, DEK_HEX, KEY_ID):
        assert secret not in cmdline, f"A96d: 子进程 cmdline 暴露了明文密钥 {secret}"


def _vm(tmp_path):
    from app.services.firecracker_runtime import VMConfig, VMInstance, VMState

    return VMInstance(
        vm_id="fc-a96d",
        session_id="sess-a96d",
        socket_path=str(tmp_path / "fc.sock"),
        state=VMState.RUNNING,
        config=VMConfig(network_enabled=False),
        workspace=str(_workspace(tmp_path)),
    )


def test_simulate_execute_delivers_secret_out_of_band(monkeypatch, tmp_path):
    """执行腿：实际使用的 argv 零密钥，值经 stdin 投递到客侧（替身进程真执行 bootstrap）。"""
    from app.services import firecracker_runtime as fr

    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    runtime = fr.FirecrackerRuntime(workspace_root=str(tmp_path / "fc-ws"))
    built: list[list[str]] = []

    def _fake_build(workspace, code_file, language, session_key=None, env_vars=None, secrets=None):
        assert secrets is not None, "A96d: 有会话密钥时执行腿必须建立带外通道并交给 argv 构造"
        secrets.extend(fr._secret_pairs(session_key, env_vars))
        argv = ["sh", "-c", GUEST_SECRET_BOOTSTRAP, GUEST_SECRET_ARGV0, str(len(secrets)),
                "python3", str(probe)]
        built.append(argv)
        return argv, None

    monkeypatch.setattr(runtime, "_build_sim_bwrap_args", _fake_build)
    result = runtime._simulate_execute(_vm(tmp_path), "print(1)", "python", session_key=SESSION_KEY)

    assert built, "_build_sim_bwrap_args 未被调用"
    _assert_zero_secret(built[0], "执行腿实际使用的 argv")
    assert f"KEY={SESSION_KEY}" in result.output, result.output
    assert SESSION_KEY not in result.output.split("CMDLINE=")[1], "A96d: 客侧 cmdline 暴露了明文密钥"


def test_seccomp_retry_rebuilds_secret_channel(monkeypatch, tmp_path):
    """带外通道是一次性的：无 seccomp 重试必须重建通道，否则客侧 rc=97。"""
    from app.core.config import get_settings
    from app.services import firecracker_runtime as fr

    monkeypatch.setattr(get_settings(), "SECCOMP_FALLBACK_ALLOWED", True)

    marker = tmp_path / "first"
    marker.write_text("1")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os\nprint('RETRY_KEY=' + os.environ.get('CDS_SESSION_KEY', '<none>'))\n"
    )
    fake_bwrap = tmp_path / "fake_bwrap.py"
    fake_bwrap.write_text(
        "import os, pathlib, sys\n"
        f"m = pathlib.Path({str(marker)!r})\n"
        "if m.exists():\n"
        "    m.unlink()\n"
        "    print('bwrap: prctl(PR_SET_SECCOMP): Invalid argument', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "a = sys.argv[1:]\n"
        "i = a.index('sh')\n"
        "os.execvp(a[i], a[i:])\n"
    )
    read_fd, write_fd = os.pipe()
    os.close(write_fd)

    runtime = fr.FirecrackerRuntime(workspace_root=str(tmp_path / "fc-ws"))
    calls: list[list[str]] = []

    def _fake_build(workspace, code_file, language, session_key=None, env_vars=None, secrets=None):
        assert secrets is not None
        secrets.extend(fr._secret_pairs(session_key, env_vars))
        argv = ["python3", str(fake_bwrap), "--seccomp", str(read_fd),
                "sh", "-c", GUEST_SECRET_BOOTSTRAP, GUEST_SECRET_ARGV0, str(len(secrets)),
                "python3", str(probe)]
        calls.append(argv)
        return argv, read_fd

    monkeypatch.setattr(runtime, "_build_sim_bwrap_args", _fake_build)
    result = runtime._simulate_execute(_vm(tmp_path), "print(1)", "python", session_key=SESSION_KEY)

    assert len(calls) == 1, "argv 只构造一次（重试复用同一 argv，仅滤掉 --seccomp）"
    assert "RETRY_KEY=" + SESSION_KEY in result.output, (
        f"A96d: 重试未重建带外通道 ⇒ 客侧拿不到密钥（rc={result.exit_code}）: {result.output!r}"
    )


def test_ssh_remote_command_carries_no_secret_and_fails_closed(tmp_path):
    """ssh 的远端命令串同样公开在本机 argv 里：零密钥，未经投递则 rc=97 拒绝执行。"""
    from app.services.firecracker_runtime import FirecrackerRuntime

    probe = tmp_path / "exec.py"
    probe.write_text(PROBE)
    secrets = GuestSecrets()
    remote = FirecrackerRuntime._ssh_remote_command(
        "python3", str(probe),
        session_key=SESSION_KEY,
        env_vars={"CDS_DEK_HEX": DEK_HEX, "CDS_SANDBOX_LEVEL": "L2"},
        secrets=secrets,
    )
    _assert_zero_secret(remote, "ssh 远端命令串")
    assert GUEST_SECRET_ARGV0 in remote and "CDS_SANDBOX_LEVEL" in remote

    proc = subprocess.run(["sh", "-c", remote], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30)
    secrets.close()
    assert proc.returncode == 97, f"A96d: 未经投递时远端必须拒绝执行，实际 rc={proc.returncode}"
    assert "A96" in proc.stderr


def test_ssh_remote_command_delivers_via_stdin(tmp_path):
    """ssh 腿的投递：stdin 就是 ssh 转发给远端的带外通道，远端命令串里零密钥。"""
    from app.services.firecracker_runtime import FirecrackerRuntime

    probe = tmp_path / "exec.py"
    probe.write_text(PROBE)
    secrets = GuestSecrets()
    remote = FirecrackerRuntime._ssh_remote_command(
        "python3", str(probe),
        session_key=SESSION_KEY,
        env_vars={"CDS_DEK_HEX": DEK_HEX, "CDS_SANDBOX_LEVEL": "L2"},
        secrets=secrets,
    )
    _assert_zero_secret(remote, "ssh 远端命令串")
    secrets.push()
    proc = subprocess.run(["sh", "-c", remote], capture_output=True, text=True,
                          stdin=secrets.stdin_fd, timeout=30)
    secrets.close()
    assert proc.returncode == 0, f"rc={proc.returncode} stderr={proc.stderr!r}"
    assert f"KEY={SESSION_KEY}" in proc.stdout, proc.stdout
    assert f"DEK={DEK_HEX}" in proc.stdout, proc.stdout
    assert "PLAIN=L2" in proc.stdout, proc.stdout
    assert SESSION_KEY not in proc.stdout.split("CMDLINE=")[1]
