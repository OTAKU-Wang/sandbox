"""Notebook service — 在线 Notebook 文档模型与沙箱内 kernel 执行桥（SS-15）。

执行只有一条策略通路：单元格代码经 app.api.sandbox_sessions.execute_in_sandbox
（静态扫描 -> KMS 注入 -> 场景运行时 -> 输出 DLP -> 审计），本模块不自行调用 runtime，
因此 Notebook 不可能成为输出策略的旁路。

执行后端如实披露（项目铁律：禁伪造）：
- "cds_kernel"    沙箱内常驻 kernel，跨单元格保留变量状态
- "cds_stateless" kernel 不可用时的降级后端，单元格独立执行、不保留状态
- "jupyter"       仅当沙箱内确实探测到 JupyterLab 且配置了代理基址时才会出现
"""
import base64
import copy
import json
import uuid
from typing import Any, Awaitable, Callable

from app.utils.crypto import sm3_hash

Executioner = Callable[[str], Awaitable[dict[str, Any]]]

KERNEL_MARKER = "CDS_CELL_B64::"
ENVELOPE_MARKER = "CDS_ENV_B64::"

_CELL_DRIVER = '''
import base64, json, os, socket, sys, time

_PAYLOAD = json.loads(base64.b64decode("%(payload)s").decode("utf-8"))
_RUNTIME_DIR = os.environ.get("CDS_RUNTIME_DIR") or os.path.join("/tmp", "cds-kernel-" + os.environ.get("CDS_SESSION_ID", "local"))
_SOCK = os.path.join(_RUNTIME_DIR, "kernel.sock")
_DAEMON = os.path.join(_RUNTIME_DIR, "kernel_daemon.py")
_LOG = os.path.join(_RUNTIME_DIR, "kernel.log")


def _emit(kind, obj):
    sys.stdout.write(kind + base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii") + "\\n")

def _daemon_src():
    return base64.b64decode("%(daemon)s").decode("utf-8")


def _connect(timeout):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(_SOCK)
            return s
        except OSError as exc:
            last = exc
            time.sleep(0.2)
    raise RuntimeError("kernel connect failed: " + str(last))


def _alive(timeout=1.0):
    try:
        s = _connect(timeout)
        s.close()
        return True
    except Exception:
        return False


if not os.path.isdir(_RUNTIME_DIR):
    os.makedirs(_RUNTIME_DIR, exist_ok=True)
if not _alive():
    with open(_DAEMON, "w", encoding="utf-8") as fh:
        fh.write(_daemon_src())
    import subprocess
    _log = open(_LOG, "ab")
    subprocess.Popen([sys.executable, _DAEMON, _SOCK], stdin=subprocess.DEVNULL, stdout=_log, stderr=_log, start_new_session=True, close_fds=True)
    _log.close()
    if not _alive(8.0):
        _emit("CDS_ENV_B64::", {"backend": "cds_stateless", "kernel_error": (open(_LOG, "r", encoding="utf-8", errors="replace").read()[-2000:] if os.path.exists(_LOG) else "daemon did not start")})
        _r = {"backend": "cds_stateless", "stateful": False, "mode": "stateless-in-driver"}
        _g = {"__name__": "__cds_cell__"}
        try:
            exec(compile(base64.b64decode(_PAYLOAD["code"]).decode("utf-8"), "<cell>", "exec"), _g)
            _r["ok"] = True
            _r["exit_code"] = 0
            _r["stdout"] = ""
            _r["stderr"] = ""
            _r["repr"] = ""
        except BaseException:
            import traceback
            _r["ok"] = False
            _r["exit_code"] = 1
            _r["stdout"] = ""
            _r["stderr"] = traceback.format_exc()
            _r["repr"] = ""
        _emit("CDS_CELL_B64::", _r)
        raise SystemExit(0)

_req = {"id": _PAYLOAD["id"], "code": base64.b64decode(_PAYLOAD["code"]).decode("utf-8")}
_s = _connect(20)
_s.sendall((json.dumps(_req) + "\\n").encode("utf-8"))
_buf = b""
while b"\\n" not in _buf:
    _chunk = _s.recv(65536)
    if not _chunk:
        break
    _buf += _chunk
_s.close()
_resp = json.loads(_buf.decode("utf-8").strip())
_resp["backend"] = "cds_kernel"
_resp["stateful"] = True
_emit("CDS_CELL_B64::", _resp)
'''

_KERNEL_DAEMON = '''
import ast, io, json, os, socket, sys, threading, traceback, contextlib

_sock_path = sys.argv[1]
_ns = {"__name__": "__cds_kernel__"}
_lock = threading.Lock()

try:
    os.unlink(_sock_path)
except OSError:
    pass
_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
_srv.bind(_sock_path)
_srv.listen(8)
sys.stdout = sys.stderr
print("cds-kernel ready", flush=True)


def _run(code):
    tree = ast.parse(code, filename="<cell>", mode="exec")
    stdout = io.StringIO()
    result = None
    with contextlib.redirect_stdout(stdout):
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            head = ast.Module(body=tree.body[:-1], type_ignores=[])
            last = ast.Expression(body=tree.body[-1].value)
            exec(compile(head, "<cell>", "exec"), _ns)
            result = eval(compile(last, "<cell>", "eval"), _ns)
            if result is not None:
                _ns["_"] = result
        else:
            exec(compile(tree, "<cell>", "exec"), _ns)
    return stdout.getvalue(), ("" if result is None else repr(result))


while True:
    conn, _ = _srv.accept()
    def _serve(conn=conn):
        try:
            data = b""
            while b"\\n" not in data:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
            req = json.loads(data.decode("utf-8").strip())
            with _lock:
                try:
                    out, repr_out = _run(req["code"])
                    resp = {"ok": True, "exit_code": 0, "stdout": out, "stderr": "", "repr": repr_out, "cell_id": req.get("id")}
                except BaseException:
                    resp = {"ok": False, "exit_code": 1, "stdout": "", "stderr": traceback.format_exc(), "repr": "", "cell_id": req.get("id")}
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\\n").encode("utf-8"))
        except Exception:
            try:
                conn.sendall((json.dumps({"ok": False, "exit_code": -1, "stderr": traceback.format_exc(), "stdout": ""}) + "\\n").encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()
    threading.Thread(target=_serve, daemon=True).start()
'''


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def make_notebook(title: str, kernel: str = "python3", language: str = "python") -> dict[str, Any]:
    return {
        "cells": [],
        "metadata": {"kernelspec": {"name": kernel, "language": language, "display_name": kernel}, "language_info": {"name": language}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def normalize_notebook(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("notebook content must be a JSON object")
    if doc.get("nbformat") != 4:
        raise ValueError("only nbformat 4 is supported")
    cells = doc.get("cells")
    if not isinstance(cells, list):
        raise ValueError("notebook.cells must be a list")
    for i, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ValueError(f"cells[{i}] must be an object")
        if cell.get("cell_type") not in ("code", "markdown", "raw"):
            raise ValueError(f"cells[{i}].cell_type must be code|markdown|raw")
        if "source" in cell and not isinstance(cell["source"], (str, list)):
            raise ValueError(f"cells[{i}].source must be a string or list of strings")
    doc.setdefault("metadata", {})
    doc.setdefault("nbformat_minor", 5)
    return doc


def notebook_hash(doc: dict[str, Any]) -> str:
    return sm3_hash(json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def cell_source(cell: dict[str, Any]) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else str(src)


def code_cells(doc: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    return [(i, c) for i, c in enumerate(doc.get("cells", [])) if c.get("cell_type") == "code"]


def extract_entrypoint(doc: dict[str, Any], max_cells: int | None = None) -> str:
    parts = [cell_source(c) for _, c in code_cells(doc)]
    if max_cells is not None:
        parts = parts[:max_cells]
    return "\n\n".join(p for p in parts if p.strip())


def apply_cell_result(doc: dict[str, Any], index: int, result: dict[str, Any], source: str | None = None) -> dict[str, Any]:
    # 返回新对象：content 是普通 JSON 列（无 MutableDict），就地改同一个 dict 再
    # 赋回同一引用不会标脏，执行结果会静默不落库。
    doc = copy.deepcopy(doc)
    cells = doc.setdefault("cells", [])
    # index == len(cells) = 工作台的“跑下一个单元格”：追写一个新 code cell；跳号仍是错误。
    if index < 0 or index > len(cells):
        raise ValueError("cell index out of range")
    if index == len(cells):
        cells.append({"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": ""})
    outputs: list[dict[str, Any]] = []
    stdout = result.get("stdout") or ""
    if stdout:
        outputs.append({"output_type": "stream", "name": "stdout", "text": stdout.splitlines(keepends=True)})
    stderr = result.get("stderr") or ""
    if stderr:
        outputs.append({"output_type": "stream", "name": "stderr", "text": stderr.splitlines(keepends=True)})
    if result.get("repr"):
        outputs.append({
            "output_type": "execute_result",
            "execution_count": result.get("execution_count") or 1,
            "data": {"text/plain": result["repr"].splitlines(keepends=True)},
            "metadata": {},
        })
    if source is not None:
        cells[index]["source"] = source
    cells[index]["outputs"] = outputs
    cells[index]["execution_count"] = result.get("execution_count") or 1
    return doc


def _extract_envelope(output: str, marker: str) -> dict[str, Any] | None:
    if not output:
        return None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith(marker):
            try:
                return json.loads(base64.b64decode(line[len(marker):]).decode("utf-8"))
            except Exception:
                return None
    return None


def build_cell_driver(code: str, cell_id: str | None = None) -> str:
    payload = {"id": cell_id or uuid.uuid4().hex, "code": _b64(code)}
    return _CELL_DRIVER % {"payload": _b64(json.dumps(payload, ensure_ascii=False)), "daemon": _b64(_KERNEL_DAEMON)}


def _driver_parts() -> tuple[str, str, str]:
    head, rest = _CELL_DRIVER.split("%(payload)s", 1)
    mid, tail = rest.split("%(daemon)s", 1)
    return head, mid, tail


def split_trusted_driver(script: str) -> dict[str, str] | None:
    # 信任不来自调用方声明：常量探针必须与平台内置字节完全相等才免扫，
    # 任何一字节的偏移都会退回到全量扫描（fail-closed）。
    constant = TRUSTED_PLATFORM_SCRIPTS.get(script)
    if constant is not None:
        return {"code": "", "cell_id": constant}
    # 信任不来自调用方声明：只有重建后与提交脚本逐字节相同的脚本才是平台驱动，
    # 因此可变面仅限于两个 base64 字段，kernel 源码还需与受信任模板完全一致。
    head, mid, tail = _driver_parts()
    if not (script.startswith(head) and mid in script and script.endswith(tail)):
        return None
    rest = script[len(head):]
    payload_b64 = rest[: rest.index(mid)]
    rest = rest[len(payload_b64) + len(mid):]
    daemon_b64 = rest[: len(rest) - len(tail)] if tail else rest
    if _CELL_DRIVER % {"payload": payload_b64, "daemon": daemon_b64} != script:
        return None
    try:
        if base64.b64decode(daemon_b64).decode("utf-8") != _KERNEL_DAEMON:
            return None
        payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
        code = base64.b64decode(str(payload["code"])).decode("utf-8")
    except Exception:
        return None
    return {"code": code, "cell_id": str(payload.get("id") or "")}


async def run_cell(executor: Executioner, code: str, *, cell_id: str | None = None, prefer_kernel: bool = True) -> dict[str, Any]:
    if not code or not code.strip():
        return {"ok": True, "backend": "cds_kernel" if prefer_kernel else "cds_stateless", "stdout": "", "stderr": "", "repr": "", "exit_code": 0, "skipped_empty": True}
    if prefer_kernel:
        driver = build_cell_driver(code, cell_id)
        raw = await executor(driver)
        output = raw.get("output") or ""
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False, default=str)
        envelope = _extract_envelope(output, ENVELOPE_MARKER)
        result = _extract_envelope(output, KERNEL_MARKER)
        if result is not None:
            if envelope:
                result["kernel_error"] = str(envelope.get("kernel_error") or "")[:2000]
            if result.get("backend") == "cds_stateless":
                result["degraded_reason"] = "沙箱内 kernel 守护进程不可用，本单元格按无状态方式独立执行（不保留变量）"
            if raw.get("output_blocked"):
                result["output_blocked"] = True
                result["blocked_reason"] = raw.get("blocked_reason", "output_inspection_blocked")
            return result
        if raw.get("output_blocked"):
            return {"ok": False, "backend": "cds_kernel", "stdout": "", "stderr": "", "exit_code": -2, "stateful": False,
                    "output_blocked": True, "blocked_reason": raw.get("blocked_reason", "output_inspection_blocked")}
        if envelope:
            stateless = await run_stateless(executor, code)
            stateless["kernel_error"] = str(envelope.get("kernel_error") or "")[:2000]
            return stateless
        if raw.get("error"):
            return {"ok": False, "backend": "cds_stateless", "stdout": "", "stderr": str(raw.get("error")), "exit_code": -1, "stateful": False, "kernel_error": "transport error"}
    return await run_stateless(executor, code)


async def run_stateless(executor: Executioner, code: str) -> dict[str, Any]:
    raw = await executor(code)
    output = raw.get("output") or ""
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False, default=str)
    return {
        "ok": (raw.get("exit_code") or 0) == 0,
        "backend": "cds_stateless",
        "stateful": False,
        "stdout": output,
        "stderr": raw.get("stderr") or "",
        "repr": "",
        "exit_code": raw.get("exit_code", 0),
        "output_blocked": bool(raw.get("output_blocked")),
        "blocked_reason": raw.get("blocked_reason"),
        "security_report": raw.get("security_report"),
        "degraded_reason": "沙箱内 kernel 守护进程不可用，本单元格按无状态方式独立执行（不保留变量）",
    }


JUPYTER_PROBE = "import json,shutil,os\n_p=shutil.which('jupyter')\nprint('CDS_ENV_B64::'+__import__('base64').b64encode(json.dumps({'jupyter_path':_p,'lab':bool(_p and os.path.exists(_p))}).encode()).decode())"


TRUSTED_PLATFORM_SCRIPTS: dict[str, str] = {JUPYTER_PROBE: "jupyter_probe"}


async def probe_jupyter(executor: Executioner) -> dict[str, Any]:
    raw = await executor(JUPYTER_PROBE)
    env = _extract_envelope(raw.get("output") or "", ENVELOPE_MARKER) or {}
    path = env.get("jupyter_path")
    return {"jupyter_available": bool(path), "jupyter_path": path, "disclosed": True}
