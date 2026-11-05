"""D5c（2026-11-05 分离拓扑现场）: structured_query 会话 SQL /execute 500 的回归锁。

现场: POST /sandbox-sessions/{id}/execute {"language":"sql","code":"SELECT ..."} →
500 INTERNAL_ERROR @ cube_runtime_adapter._read_sandbox_file
(``rootfs volume dir for cubebox … not found``)。两层根因:

1. ``language="sql"`` 落进适配器 else 分支被原样拼进 ``sh -c`` 命令行 —— SQL 不是
   shell 语法, 外层整串 parse 失败, 连 mkdir/marker 都不写 → 轮询空转到超时;
2. 超时窗口内会话被终止, rootfs 卷目录回收, 轮询读抛 gRPC NOT_FOUND 直接冒泡 → 500。

修复: sql 走 VM 内 stdlib sqlite3 runner（表名 data = 注入 CSV）; 非 python 原始
命令 base64 落盘成脚本文件再执行（marker 恒写）; 轮询读经 _read_sandbox_file_poll,
NOT_FOUND → SandboxRootfsGoneError → execute() 诚实返回 exit_code -1 + 披露文案。
"""
from __future__ import annotations

import ast
import base64
import re

import grpc
import pytest

from app.services.cube_runtime_adapter import (
    GUEST_EXEC_DIR,
    CubeRuntimeAdapter,
    SandboxRootfsGoneError,
)

_SQL_B64_RE = re.compile(r"base64\.b64decode\('([A-Za-z0-9+/=]+)'\)")


class _StubClient:
    def __init__(self):
        self.requests = []

    def exec(self, request):
        self.requests.append(request)


class _NotFound(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.NOT_FOUND

    def details(self):
        return "rootfs volume dir for cubebox deadbeef not found"


class _Internal(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.INTERNAL

    def details(self):
        return "plugin socket gone"


def _adapter(client: _StubClient):
    adapter = CubeRuntimeAdapter(client_factory=lambda node: client)
    adapter._node_cache["n1"] = {
        "node_id": "n1", "endpoint": "10.0.0.1:9443", "runtime_version": "v",
    }
    return adapter


async def _run_with_cmd(monkeypatch, code: str, language: str, marker: bytes = b"7\n5\nDONE\n", out: bytes = b"out\n"):
    client = _StubClient()
    adapter = _adapter(client)

    def _fake_read(cubebox_id, session_id, vm_path):
        if vm_path.endswith(".code"):
            return True, marker
        return True, out

    monkeypatch.setattr(adapter, "_read_sandbox_file", _fake_read)
    result = await adapter.execute("l2c-n1-" + "a" * 32, code, language, timeout=5)
    request = client.requests[0]
    cmd = request.args[-1]
    return result, cmd


# ── 1. SQL 不再原样进 shell, 而是 stdlib sqlite3 runner ─────────────────────


async def test_sql_language_uses_sqlite_runner_not_raw_shell(monkeypatch):
    sql = "SELECT city, ROUND(AVG(freight_yuan),2) FROM data GROUP BY city"
    result, cmd = await _run_with_cmd(monkeypatch, sql, "sql", marker=b"0\n5\nDONE\n")

    assert result["exit_code"] == 0
    assert cmd.startswith(f"mkdir -p {GUEST_EXEC_DIR}")
    # SQL 明文绝不进 sh 命令行（旧缺陷正是把它交给 sh）
    assert "SELECT city" not in cmd
    # 内层 = python3 -c + base64 的 runner; runner 是 sqlite3 且内嵌原始 SQL 的 b64
    outer_b64 = _SQL_B64_RE.search(cmd)
    assert outer_b64, f"no base64 runner in cmd: {cmd[:200]}"
    runner = base64.b64decode(outer_b64.group(1)).decode()
    compile(runner, "runner", "exec")  # runner 语法必须合法（外层 parse 失败的教训）
    assert "sqlite3" in runner
    assert "CREATE TABLE data" in runner
    sql_b64 = base64.b64encode(sql.encode()).decode()
    assert sql_b64 in runner


# ── 2. 非 python 原始命令: 语法错误也必须写 marker（base64 落盘） ───────────


async def test_shell_syntax_error_still_produces_marker(monkeypatch):
    broken = "if this-is-not-shell( then"  # 交给外层 sh 会整串 parse 失败
    result, cmd = await _run_with_cmd(monkeypatch, broken, "bash", marker=b"2\n5\nDONE\n")

    assert "base64 -d >" in cmd and "&& sh" in cmd and cmd.endswith("sync")
    assert broken not in cmd  # 只以 b64 形态存在
    assert result["exit_code"] == 2


# ── 3. rootfs 中途消失: 诚实 exit -1, 不再 500 ──────────────────────────────


async def test_not_found_mid_poll_returns_honest_error(monkeypatch):
    adapter = _adapter(_StubClient())

    def _gone(cubebox_id, session_id, vm_path):
        raise _NotFound()

    monkeypatch.setattr(adapter, "_read_sandbox_file", _gone)
    result = await adapter.execute("l2c-n1-" + "b" * 32, "print(1)", "python", timeout=5)

    assert result["exit_code"] == -1
    assert "disappeared" in result["error"]
    assert "rootfs volume dir" in result["error"]


async def test_output_copy_not_found_discloses(monkeypatch):
    adapter = _adapter(_StubClient())

    def _flaky(cubebox_id, session_id, vm_path):
        if vm_path.endswith(".code"):
            return True, b"0\n9\nDONE\n"
        raise _NotFound()

    monkeypatch.setattr(adapter, "_read_sandbox_file", _flaky)
    result = await adapter.execute("l2c-n1-" + "c" * 32, "print(1)", "python", timeout=5)

    assert result["exit_code"] == 0
    assert result["output"] == ""
    assert "copying output" in result["error"]


async def test_other_rpc_errors_still_propagate(monkeypatch):
    """非 NOT_FOUND 的 gRPC 错误保持原 fail-loud 语义（不得吞成正常返回）。"""
    adapter = _adapter(_StubClient())

    def _boom(cubebox_id, session_id, vm_path):
        raise _Internal()

    monkeypatch.setattr(adapter, "_read_sandbox_file", _boom)
    with pytest.raises(grpc.RpcError):
        await adapter.execute("l2c-n1-" + "d" * 32, "print(1)", "python", timeout=5)


# ── 4. SQL runner 语义自证: 模板合法 + 单语句约束 ──────────────────────────


def test_sql_runner_template_executes_and_rejects_multi_statement(tmp_path):
    import app.services.cube_runtime_adapter as mod

    data = tmp_path / "session-data"
    data.write_text("a,n\nx,1\ny,2\n", encoding="utf-8")

    def _run(sql: str):
        import subprocess
        import sys

        runner = mod._SQL_RUNNER_SRC.format(
            sql_b64=base64.b64encode(sql.encode()).decode(),
            data_path=str(data),
        )
        compile(runner, "runner", "exec")
        return subprocess.run(
            [sys.executable, "-c", runner], capture_output=True, text=True, timeout=30
        )

    ok = _run("SELECT SUM(n) AS s FROM data")
    assert ok.returncode == 0 and ok.stdout.splitlines()[1] == "3.0", ok.stderr
    multi = _run("SELECT 1; SELECT 2")
    assert multi.returncode != 0  # sqlite3 标准库拒多语句, 诚实非零退出
