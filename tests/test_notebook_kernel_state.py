"""SS-15 Notebook 常驻 kernel：单元格跨状态与降级披露的可观测契约（禁伪造双门）。

钉两件事（两者都是"禁伪造"铁律的直接表述）：
1. kernel 可用时，第二个单元格必须真的看见第一个单元格的变量；
2. kernel 不可用时，信封必须继续写 cds_stateless + degraded_reason（并把 kernel_error 带出来）。

这里跑的是真实的驱动与真实的 kernel 守护进程（子进程 + unix socket），不是打桩：
`build_cell_driver` 产出的脚本被 sys.executable 直接执行，与沙箱里 `python3 exec.py` 同构。
L3/bwrap 专项断言另见 tests/test_notebook_kernel_state.py 里无需 bwrap 的纯函数用例。
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.services import notebook_service as nb


def _wait_socket(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(1.0)
                probe.connect(str(path))
                probe.close()
                return True
            except OSError:
                pass
        time.sleep(0.05)
    return False


def _start_daemon(runtime: Path, *, resident: bool, session: str = "sess-A"):
    """Start the trusted kernel daemon exactly as the control plane does."""
    runtime.mkdir(parents=True, exist_ok=True)
    daemon = runtime / "kernel_daemon.py"
    daemon.write_text(nb.KERNEL_DAEMON_SOURCE)
    argv = [sys.executable, str(daemon), str(runtime / "kernel.sock")]
    if resident:
        argv += ["--resident", "--session", session]
    log = open(runtime / "kernel.log", "ab")
    proc = subprocess.Popen(argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
    log.close()
    assert _wait_socket(runtime / "kernel.sock"), "trusted kernel daemon did not come up"
    return proc


def _run_driver(tmp_path: Path, code: str, env_extra: dict[str, str], cell_id: str = "c") -> dict:
    """Execute one cell through the real driver script (same shape as in-sandbox exec)."""
    driver = tmp_path / "exec.py"
    driver.write_text(nb.build_cell_driver(code, cell_id))
    env = {**os.environ, "CDS_SESSION_ID": "sess-A", "CDS_RUNTIME_DIR": str(tmp_path / "run"),
           "CDS_SESSION_KEY": "sk-forbidden", "CDS_SANDBOX_LEVEL": "L3"}
    env.update(env_extra)
    proc = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True,
                          timeout=90, env=env, cwd=str(tmp_path))
    raw = proc.stdout + proc.stderr
    payload = nb._extract_envelope(raw, nb.KERNEL_MARKER)
    assert payload is not None, f"driver emitted no cell envelope: rc={proc.returncode} raw={raw[:1500]!r}"
    return payload


def test_two_cells_share_state_through_resident_kernel(tmp_path):
    """kernel 在位：第二个单元格必须读到第一个单元格定义的变量。"""
    runtime = tmp_path / "run"
    proc = _start_daemon(runtime, resident=True)
    try:
        first = _run_driver(tmp_path, "probe = 41 + 1\nprint('answer=', end='')\nprint(probe)\n",
                            {"CDS_KERNEL_MANAGED": "1"}, cell_id="c1")
        assert first["backend"] == "cds_kernel"
        assert first["stateful"] is True
        assert first["ok"] is True
        assert "answer=42" in first["stdout"]

        second = _run_driver(tmp_path, "print('seen', probe)\n", {"CDS_KERNEL_MANAGED": "1"}, cell_id="c2")
        assert second["backend"] == "cds_kernel"
        assert second["stateful"] is True
        assert "seen 42" in second["stdout"], second
        assert second["kernel_prior_cells"] == 1
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_resident_kernel_keeps_session_env(tmp_path):
    """kernel 常驻后，会话密钥只经 env 变量继续可见（且不落盘）。"""
    runtime = tmp_path / "run"
    proc = _start_daemon(runtime, resident=True)
    try:
        got = _run_driver(tmp_path, "import os\nprint('key=' + str(os.environ.get('CDS_SESSION_KEY')))\n",
                          {"CDS_KERNEL_MANAGED": "1"})
        assert got["ok"] is True and "key=sk-forbidden" in got["stdout"], got
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_degraded_envelope_when_managed_kernel_absent(tmp_path):
    """控制面说"有常驻 kernel"却没有进程在听：必须 cds_stateless + 原因 + kernel_error。"""
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    result = _run_driver(tmp_path, "print('ran-anyway')\n", {"CDS_KERNEL_MANAGED": "1"})
    assert result["backend"] == "cds_stateless"
    assert result["stateful"] is False
    assert result["stdout"].startswith("ran-anyway")  # 单元格仍然执行了一次
    assert "platform session kernel not reachable" in result["kernel_error"]
    assert "[absent]" in result["kernel_error"]  # 诊断带 socket 观测，便于运维定位
    assert result.get("degraded_reason") is None  # 文案由 run_cell 统一给，驱动不造第二套


def test_unmanaged_kernel_never_claims_stateful_it_cannot_prove(tmp_path):
    """非托管运行面：允许自举 kernel，但 stateful 必须与"真的跨单元格保住了状态"一致。"""
    first = _run_driver(tmp_path, "probe2 = 7\nprint('one', end='')\nprint(probe2)\n", {}, cell_id="c1")
    second = _run_driver(tmp_path, "print('seen2', probe2)\n", {}, cell_id="c2")
    for cell in (first, second):
        assert cell["backend"] in ("cds_kernel", "cds_stateless")
        if cell.get("backend") == "cds_kernel":
            # 禁伪造门：stateful=true 只允许由"控制面常驻"或"已为本会话跑过单元格"证明
            proven = bool(cell.get("kernel_resident")) or int(cell.get("kernel_prior_cells") or 0) > 0
            assert bool(cell.get("stateful")) is proven, cell
        else:
            assert cell.get("stateful") is False and cell.get("kernel_error"), cell
    if second.get("stateful"):
        # 声明有状态就必须真的接力上（禁伪造）
        assert "seen2 7" in second["stdout"], second
    else:
        assert "NameError" in second["stderr"] or "name 'probe2' is not defined" in second["stderr"], second


async def test_run_cell_maps_backend_and_degradation_text():
    """run_cell 的映射：stateless→不可用文案；kernel 但未证实常驻→未证实常驻文案。"""
    async def executor(code: str, cell_id=None) -> dict:
        payload = {"backend": "cds_stateless", "stateful": False, "ok": True, "exit_code": 0,
                   "stdout": "", "stderr": "", "repr": "", "kernel_error": "boom"}
        return {"output": nb.KERNEL_MARKER + nb._b64(json.dumps(payload, ensure_ascii=False)), "exit_code": 0}

    result = await nb.run_cell(executor, "print(1)")
    assert result["backend"] == "cds_stateless"
    assert result["degraded_reason"] == nb.KERNEL_UNAVAILABLE_REASON
    assert result["kernel_error"] == "boom"

    async def executor2(code: str, cell_id=None) -> dict:
        payload = {"backend": "cds_kernel", "stateful": False, "ok": True, "exit_code": 0,
                   "stdout": "x\n", "stderr": "", "repr": "", "kernel_prior_cells": 0}
        return {"output": nb.KERNEL_MARKER + nb._b64(json.dumps(payload, ensure_ascii=False)), "exit_code": 0}

    result2 = await nb.run_cell(executor2, "print(1)")
    assert result2["backend"] == "cds_kernel"
    assert result2["stateful"] is False
    assert result2["degraded_reason"] == nb.KERNEL_NOT_RESIDENT_REASON


async def test_stateful_kernel_result_keeps_no_degraded_reason():
    async def executor(code: str, cell_id=None) -> dict:
        payload = {"backend": "cds_kernel", "stateful": True, "ok": True, "exit_code": 0,
                   "stdout": "x\n", "stderr": "", "repr": "", "kernel_resident": True}
        return {"output": nb.KERNEL_MARKER + nb._b64(json.dumps(payload, ensure_ascii=False)), "exit_code": 0}

    result = await nb.run_cell(executor, "print(1)")
    assert result["backend"] == "cds_kernel" and result["stateful"] is True
    assert "degraded_reason" not in result


def test_generated_driver_is_still_byte_trusted():
    """驱动模板改写后，trusted_runner 的字节级信任仍然成立（扫描对象=内嵌用户代码）。"""
    driver = nb.build_cell_driver("import socket\nprint(1)\n", "c9")
    split = nb.split_trusted_driver(driver)
    assert split is not None
    assert "import socket" in split["code"]
    assert nb.split_trusted_driver(driver.replace("kernel.sock", "kernel2.sock")) is None
    assert nb.split_trusted_driver(driver.replace("cds-kernel-", "cds-kernelX-")) is None


def test_bwrap_runtime_dir_is_session_scoped():
    """驱动默认运行目录必须按会话隔离：两个会话共用 /tmp 目录会串状态。"""
    driver_a = nb.build_cell_driver("print(1)\n", "a")
    assert "CDS_RUNTIME_DIR" in driver_a and "CDS_SESSION_ID" in driver_a
    assert nb.TRUSTED_PLATFORM_SCRIPTS.get(nb.JUPYTER_PROBE) == "jupyter_probe"


def test_bwrap_kernel_env_only_advertises_registered_kernel():
    """没有登记常驻 kernel 时不得告诉驱动"平台会兜住"（否则驱动连不上就双双失败）。"""
    from app.services.sandbox_runtime import BwrapAdapter  # noqa: F401

    ad = BwrapAdapter(workspace_root="/tmp/cds-kernel-contract-test")
    assert ad._kernel_env("bwrap-unknown") == {}
    ad._kernels["bwrap-known"] = {"proc": None, "sock": "/x/kernel.sock", "misses": 0}
    assert ad._kernel_env("bwrap-known") == {
        "CDS_RUNTIME_DIR": BwrapAdapter.KERNEL_GUEST_DIR, "CDS_KERNEL_MANAGED": "1"}


def test_seccomp_bpf_fd_is_single_use_fresh_offset():
    """bwrap 会把过滤器程序从 fd 读到 EOF：共享缓存 fd 会让第二次之后的 exec 全部 EINVAL。

    钉住可观察契约：每次借出的是一个偏移为 0 的独立 fd，读满它不会伤到其它借出的 fd。
    """
    import os

    from app.services.sandbox_security import close_seccomp_fd, get_seccomp_bpf_fd

    first = get_seccomp_bpf_fd()
    if first is None:  # 没有 libseccomp 的环境不碰硬件相关契约
        pytest.skip("libseccomp unavailable")
    try:
        assert os.read(first, 4)  # 偏移 0 处取得到编译好的程序
        second = get_seccomp_bpf_fd()
        assert second is not None
        assert second != first, "必须是独立的 open file description"
        # 第一个 fd 读到 EOF 之后，后续借出的 fd 仍然从偏移 0 开始（否则 bwrap 读到空程序）
        while os.read(first, 4096):
            pass
        assert os.lseek(second, 0, os.SEEK_CUR) == 0
        third = get_seccomp_bpf_fd()
        try:
            assert len(os.read(third, 4096)) > 0, "前一次 spawn 读满后，后一次不能拿到空过滤器"
        finally:
            close_seccomp_fd(third)
    finally:
        close_seccomp_fd(first)
        close_seccomp_fd(second)


def test_bwrap_args_command_override_keeps_hardening(tmp_path):
    """会话 kernel 走的仍是同一套 bwrap 加固参数，只是换了 argv。"""
    from app.services.sandbox_runtime import BwrapAdapter
    from app.services.sandbox_security import build_bwrap_args

    class GuestPath:
        DAEMON = BwrapAdapter.KERNEL_DAEMON_GUEST
        SOCK = BwrapAdapter.KERNEL_SOCK_GUEST

    ws = tmp_path / "ws"
    for sub in ("tmp", "output", "input", "files"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    args, _fd = build_bwrap_args(
        workspace=ws, code_file="/workspace/tmp/exec.py", language="python",
        command=["python3", GuestPath.DAEMON, GuestPath.SOCK, "--resident"],
        session_id="s1", user_id="u1", sandbox_level="L3",
    )
    assert args[:1] == ["bwrap"]
    assert "--unshare-pid" in args and "--die-with-parent" in args
    assert args[-4:] == ["python3", GuestPath.DAEMON, GuestPath.SOCK, "--resident"]
    assert "/workspace/tmp/exec.py" not in args


# 驱动与 kernel 守护进程用 AF_UNIX；无该 socket 族的开发机上这些用例跳过（不是失败）。
_needs_af_unix = not hasattr(socket, "AF_UNIX")
pytestmark = pytest.mark.skipif(_needs_af_unix, reason="驱动与 kernel 守护进程依赖 AF_UNIX")


def test_control_sock_stays_inside_af_unix_sun_path(tmp_path):
    """工作区路径比 sun_path 还长时，控制面必须改走短别名，而不是拿 15s 探活超时当“kernel 起不来”。

    实测事故：/tmp/cds-sandbox/<user>/<session>/tmp/.kernel/kernel.sock = 114 字节 > 107，
    控制面的 connect 连系统调用都进不去（AF_UNIX path too long），kernel 明明在听也被当成
    kernel_did_not_start 回收，有状态单元格永远退回每格自举的无状态 kernel。
    """
    from app.services.sandbox_runtime import BwrapAdapter

    deep = tmp_path / ("d" * 40) / ("s" * 40) / "tmp" / BwrapAdapter.KERNEL_SUBDIR
    deep.mkdir(parents=True)
    ad = BwrapAdapter(workspace_root=str(tmp_path))
    cid = "bwrap-deep-session"
    assert len(os.fsencode(str(deep / "kernel.sock"))) > ad.SUN_PATH_MAX, "用例要复现的正是超长工作区"
    got = ad._control_sock(cid, deep)
    assert len(os.fsencode(got)) <= ad.SUN_PATH_MAX, f"控制面路径仍然超长: {got}"
    assert os.path.dirname(os.path.realpath(got)) == os.path.realpath(str(deep)), "别名必须指回同一个 kernel 目录"
    ad._drop_kernel_alias(cid)
    assert not os.path.islink(ad._kernel_alias(cid)), "terminate 后别名不得残留"
