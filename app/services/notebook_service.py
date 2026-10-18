"""Notebook service — 在线 Notebook 文档模型与沙箱内 kernel 执行桥（SS-15）。

执行只有一条策略通路：单元格代码经 app.api.sandbox_sessions.execute_in_sandbox
（静态扫描 -> KMS 注入 -> 场景运行时 -> 输出 DLP -> 审计），本模块不自行调用 runtime。
但会话级输出 DLP 看到的那一层是 base64 信封密文（A111）：run_cell 解开信封后必须对
解码出的明文（stdout / stderr / repr）再过一次同一套输出策略，否则 Notebook 就是
输出策略的旁路。

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

# 降级口径（前端 display.ts 按 backend + stateful + degraded_reason 渲染，不得新增 backend 取值）
KERNEL_UNAVAILABLE_REASON = "沙箱内 kernel 守护进程不可用，本单元格按无状态方式独立执行（不保留变量）"
KERNEL_NOT_RESIDENT_REASON = "kernel 由单元格自行拉起，平台未能确认其跨单元格常驻（不保证保留变量）"

_CELL_DRIVER = '''
import base64, contextlib, io, json, os, socket, sys, time

_PAYLOAD = json.loads(base64.b64decode("%(payload)s").decode("utf-8"))
_SESSION = os.environ.get("CDS_SESSION_ID", "")
_RUNTIME_DIR = os.environ.get("CDS_RUNTIME_DIR") or os.path.join("/tmp", "cds-kernel-" + (_SESSION or "local"))
_SOCK = os.path.join(_RUNTIME_DIR, "kernel.sock")
_DAEMON = os.path.join(_RUNTIME_DIR, "kernel_daemon.py")
_LOG = os.path.join(_RUNTIME_DIR, "kernel.log")
# managed=1 表示平台（控制面）为该会话起了常驻 kernel：驱动只连不自建，
# 避免第二个守护进程抢同一个 socket 路径，也避免把一次性 kernel 说成有状态。
_MANAGED = os.environ.get("CDS_KERNEL_MANAGED") == "1"
try:
    _CELL_TIMEOUT = float(os.environ.get("CDS_CELL_TIMEOUT") or "0")
except ValueError:
    _CELL_TIMEOUT = 0.0
_READ_TIMEOUT = (_CELL_TIMEOUT - 5.0) if _CELL_TIMEOUT > 30.0 else 300.0


def _emit(kind, obj):
    sys.stdout.write(kind + base64.b64encode(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii") + "\\n")
    sys.stdout.flush()


def _daemon_src():
    return base64.b64decode("%(daemon)s").decode("utf-8")


def _connect(timeout):
    deadline = time.time() + timeout
    last = None
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(_SOCK)
            return s
        except OSError as exc:
            last = exc
            if time.time() >= deadline:
                raise RuntimeError("kernel connect failed: " + str(last))
            time.sleep(0.1)


def _rpc(req, connect_timeout, read_timeout):
    s = _connect(connect_timeout)
    try:
        s.settimeout(read_timeout)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\\n").encode("utf-8"))
        buf = b""
        while b"\\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        try:
            s.close()
        except OSError:
            pass
    if not buf.strip():
        raise RuntimeError("kernel closed the connection without answering")
    return json.loads(buf.decode("utf-8").strip())


def _cell_env():
    return dict((k, v) for k, v in os.environ.items() if k.startswith("CDS_"))


def _diag():
    # 只陈述观测到的事实（目录/socket/日志尾），不猜原因：这条字符串就是用户的 kernel_error。
    bits = ["runtime_dir=" + _RUNTIME_DIR + (" [dir]" if os.path.isdir(_RUNTIME_DIR) else " [missing]"),
            "socket=" + _SOCK + (" [present]" if os.path.exists(_SOCK) else " [absent]"),
            "managed=" + str(_MANAGED), "uid=" + str(os.getuid())]
    try:
        if os.path.exists(_LOG):
            tail = open(_LOG, "r", encoding="utf-8", errors="replace").read()[-1200:]
            if tail.strip():
                bits.append("kernel.log tail: " + tail.strip()[-800:])
    except OSError:
        pass
    return " | ".join(bits)


def _spawn_daemon():
    with open(_DAEMON, "w", encoding="utf-8") as fh:
        fh.write(_daemon_src())
    import subprocess
    with open(_LOG, "ab") as log:
        subprocess.Popen([sys.executable, _DAEMON, _SOCK], stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)
    return _rpc({"op": "info", "session_id": _SESSION}, 8.0, 10.0)


def _cell_code():
    return base64.b64decode(_PAYLOAD["code"]).decode("utf-8")


def _degrade(reason):
    # 无 kernel：在本进程内独立执行一次（驱动自身的 stdout 是信封通道，必须截流）
    res = {"backend": "cds_stateless", "stateful": False, "ok": True, "exit_code": 0,
           "stdout": "", "stderr": "", "repr": "", "kernel_error": str(reason)[:2000]}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(_cell_code(), "<cell>", "exec"), {"__name__": "__cds_cell__"})
    except BaseException:
        import traceback
        res["ok"] = False
        res["exit_code"] = 1
        res["stderr"] = traceback.format_exc()
    res["stdout"] = buf.getvalue()
    _emit("CDS_CELL_B64::", res)


_kernel = None
_failure = None
if _MANAGED:
    # 常驻 kernel 由控制面负责拉起；这里只等它，等不到就如实降级，绝不自建。
    try:
        _kernel = _rpc({"op": "info", "session_id": _SESSION}, 20.0, 20.0)
    except Exception as exc:
        _failure = "platform session kernel not reachable: " + str(exc)
else:
    try:
        _kernel = _rpc({"op": "info", "session_id": _SESSION}, 0.3, 20.0)
    except Exception:
        _kernel = None
    if _kernel is None:
        try:
            if not os.path.isdir(_RUNTIME_DIR):
                os.makedirs(_RUNTIME_DIR, exist_ok=True)
            _kernel = _spawn_daemon()
        except Exception as exc:
            _kernel = None
            _failure = "self-spawned kernel unavailable: " + str(exc)

if isinstance(_kernel, dict) and _kernel.get("ok"):
    # stateful 只能被证明，不能被声明：常驻 kernel（控制面拉起）或已经为本会话
    # 跑过至少一个单元格的 kernel，才算跨单元格保留状态。
    _prior = int(_kernel.get("prior_cells") or 0)
    _stateful = bool(_kernel.get("resident")) or _prior > 0
    try:
        _resp = _rpc({"op": "exec", "id": _PAYLOAD["id"], "session_id": _SESSION,
                      "code": _cell_code(), "env": _cell_env()}, 20.0, _READ_TIMEOUT)
    except Exception as exc:
        _resp = {"ok": False, "exit_code": -1, "stdout": "", "repr": "", "stderr": "kernel exec failed: " + str(exc)}
        _stateful = False
    _resp["backend"] = "cds_kernel"
    _resp["stateful"] = _stateful
    _resp["kernel_resident"] = bool(_kernel.get("resident"))
    _resp["kernel_prior_cells"] = _prior
    _emit("CDS_CELL_B64::", _resp)
else:
    _degrade(((_failure or "kernel did not answer") + " :: " + _diag()).strip())
'''

_KERNEL_DAEMON = '''
import ast, contextlib, io, json, os, socket, sys, time, traceback

_ARGV = sys.argv[1:]
_sock_path = _ARGV[0]
# --resident 只由控制面（会话常驻 kernel）传入：自行拉起的一次性 kernel 没有这个
# 参数，因此永远不会被上报成有状态后端（禁伪造铁律）。
_resident = "--resident" in _ARGV
_hint = _ARGV[_ARGV.index("--session") + 1] if "--session" in _ARGV else ""
_ns = {"__name__": "__cds_kernel__"}
_seen: dict = {}
_kernel_id = str(os.getpid()) + "-" + str(int(time.time()))

try:
    os.unlink(_sock_path)
except OSError:
    pass
_srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
_srv.bind(_sock_path)
_srv.listen(16)
sys.stdout = sys.stderr
print("cds-kernel ready resident=" + str(_resident) + " kernel_id=" + _kernel_id, flush=True)


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


def _handle(req):
    op = req.get("op") or "exec"
    sid = str(req.get("session_id") or _hint)
    if op == "info":
        return {"ok": True, "op": "info", "resident": _resident, "kernel_id": _kernel_id,
                "pid": os.getpid(), "session_id": sid, "prior_cells": _seen.get(sid, 0)}
    for key, val in (req.get("env") or {}).items():
        # 会话密钥/DEK 只走环境变量，与逐单元格 bwrap --setenv 等价（不落盘）
        if isinstance(key, str) and key.startswith("CDS_") and isinstance(val, str):
            os.environ[key] = val
    prior = _seen.get(sid, 0)
    try:
        out, repr_out = _run(req["code"])
        resp = {"ok": True, "exit_code": 0, "stdout": out, "stderr": "", "repr": repr_out,
                "cell_id": req.get("id")}
    except BaseException:
        resp = {"ok": False, "exit_code": 1, "stdout": "", "stderr": traceback.format_exc(),
                "repr": "", "cell_id": req.get("id")}
    _seen[sid] = prior + 1
    resp["prior_cells"] = prior
    resp["kernel_id"] = _kernel_id
    return resp


# 单线程串行处理：一个 kernel 就是一个命名空间，单元格之间本来就要串行；
# 不建线程也避开了 seccomp 白面下的 clone/信号开销。卡死由控制面回收。
while True:
    try:
        _conn, _addr = _srv.accept()
    except OSError as _exc:
        # 不静默退出：seccomp 缺 accept4 一类环境问题会在这里暴露到 kernel.log，
        # 控制面才能把真实原因带进 degraded_reason（禁伪造 + 可诊断）。
        print("cds-kernel accept failed: " + repr(_exc), flush=True)
        break
    try:
        with _conn:
            _data = b""
            while b"\\n" not in _data:
                _chunk = _conn.recv(65536)
                if not _chunk:
                    break
                _data += _chunk
            if not _data.strip():
                continue
            try:
                _resp = _handle(json.loads(_data.decode("utf-8").strip()))
            except Exception:
                _resp = {"ok": False, "exit_code": -1, "stdout": "", "stderr": traceback.format_exc()}
            _conn.sendall((json.dumps(_resp, ensure_ascii=False) + "\\n").encode("utf-8"))
    except Exception:
        pass
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


# 受信任的常驻 kernel 源码：由控制面（BwrapAdapter.ensure_kernel）写入会话运行目录。
# 驱动的字节级信任（split_trusted_driver）与它就是同一份源，两处不会漂移。
KERNEL_DAEMON_SOURCE = _KERNEL_DAEMON


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


OUTPUT_DLP_FIELDS = ("stdout", "stderr", "repr")
OUTPUT_BLOCKED_REASON = "output_inspection_blocked"


def _withhold_cell_outputs(result: dict[str, Any], reason: str = OUTPUT_BLOCKED_REASON) -> dict[str, Any]:
    """整格撤下输出：只清空一半等于把上下文泄一半。

    只动输出字段；backend / stateful / degraded_reason / kernel_error 这些如实披露
    一律保留（拦截不得顺手抹掉降级事实）。
    """
    for field in OUTPUT_DLP_FIELDS:
        result[field] = ""
    result["output_blocked"] = True
    result["blocked_reason"] = reason
    return result


def _inspect_decoded_output(
    result: dict[str, Any],
    *,
    user_id: str = "",
    session_id: str = "",
    sandbox_mode: str = "develop",
) -> dict[str, Any]:
    """对解码后的单元格真实输出走一次输出策略（A111）。

    会话级检查发生在 execute_in_sandbox 里，那时输出还封在 CDS_CELL_B64:: 信封里，
    DLP 匹到的是密文；明文随后被原样写进 notebook 文档并回给前端。这里补上对明文
    的检查，口径与会话级完全一致：命中阻断级 ⇒ 整格输出撤下并如实标记 output_blocked；
    未阻断 ⇒ 逐字段落 redacted 版（DLP 脱敏 + 零宽水印）。
    """
    from app.core.metrics import record_output_inspection
    from app.services.output_security import (
        inspect_text_output,
        inspection_to_report,
        should_block,
    )

    reports: dict[str, Any] = {}
    blocked = False
    for field in OUTPUT_DLP_FIELDS:
        text = result.get(field)
        if not isinstance(text, str) or not text:
            continue
        inspection = inspect_text_output(
            text, user_id=user_id, session_id=session_id, sandbox_mode=sandbox_mode,
        )
        reports[field] = inspection_to_report(inspection)
        if should_block(inspection):
            blocked = True
            record_output_inspection("blocked")
            continue
        redacted = inspection.redacted_output or ""
        record_output_inspection("redacted" if redacted != text else "passed")
        result[field] = redacted
    if blocked:
        return _withhold_cell_outputs(result)
    if reports:
        # 把审查摘要附在结果里（_clean 不对外回显，留给调用方审计）。
        result["output_inspection"] = {
            field: {key: report.get(key) for key in ("passed", "blocked", "findings_count", "severities")}
            for field, report in reports.items()
        }
    return result


async def run_cell(
    executor: Executioner,
    code: str,
    *,
    cell_id: str | None = None,
    prefer_kernel: bool = True,
    user_id: str = "",
    session_id: str = "",
    sandbox_mode: str = "develop",
) -> dict[str, Any]:
    """跑一个单元格，并把解码后的真实输出送过输出 DLP。

    user_id / session_id 只用于输出策略的归属（水印与审计关联）；缺省时
    inspect_text_output 自己退到 unknown-user/unknown-session，审查本身照跑。
    sandbox_mode 默认 develop：Notebook 属于开发型沙箱场景。
    """
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
            if envelope and not result.get("kernel_error"):
                result["kernel_error"] = str(envelope.get("kernel_error") or "")[:2000]
            if result.get("backend") == "cds_stateless":
                result["degraded_reason"] = KERNEL_UNAVAILABLE_REASON
            elif result.get("backend") == "cds_kernel" and result.get("stateful") is False:
                # kernel 应答了，但跨单元格常驻未被证实：照实降级，不因 backend=cds_kernel 就当有状态。
                result["degraded_reason"] = KERNEL_NOT_RESIDENT_REASON
            if raw.get("output_blocked"):
                # 会话级已判拦截：解出来的明文必须一起撕下，不能只打个标记。
                _withhold_cell_outputs(result, str(raw.get("blocked_reason") or OUTPUT_BLOCKED_REASON))
            else:
                # A111：会话级 DLP 检的是信封密文，明文在这里必须再过一次同样的策略。
                _inspect_decoded_output(
                    result, user_id=user_id, session_id=session_id, sandbox_mode=sandbox_mode,
                )
            return result
        if raw.get("output_blocked"):
            return {"ok": False, "backend": "cds_kernel", "stdout": "", "stderr": "", "exit_code": -2, "stateful": False,
                    "output_blocked": True, "blocked_reason": raw.get("blocked_reason", OUTPUT_BLOCKED_REASON)}
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
        "degraded_reason": KERNEL_UNAVAILABLE_REASON,
    }


JUPYTER_PROBE = "import json,shutil,os\n_p=shutil.which('jupyter')\nprint('CDS_ENV_B64::'+__import__('base64').b64encode(json.dumps({'jupyter_path':_p,'lab':bool(_p and os.path.exists(_p))}).encode()).decode())"


TRUSTED_PLATFORM_SCRIPTS: dict[str, str] = {JUPYTER_PROBE: "jupyter_probe"}


async def probe_jupyter(executor: Executioner) -> dict[str, Any]:
    raw = await executor(JUPYTER_PROBE)
    env = _extract_envelope(raw.get("output") or "", ENVELOPE_MARKER) or {}
    path = env.get("jupyter_path")
    return {"jupyter_available": bool(path), "jupyter_path": path, "disclosed": True}
