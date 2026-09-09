"""N10: sandbox-internal agent execution — runner generation + validation.

Reuses the N5/N6 "system-generated runner + byte-exact template validation"
pattern. The runner is a self-contained script that loops a deterministic
planner over platform-fixed tools, enforces step/token budgets in-sandbox
(fail-closed) and emits a per-step ``agent_trace`` JSONL the host audits and
metered on completion. The code_scanner import whitelist is NEVER widened —
agent runners are validated byte-exactly against the trusted template.
"""
import ast
import json
import logging
import textwrap

from app.services.agent_tools import tool_registry

logger = logging.getLogger(__name__)

_AGENT_RUNNER_HEADER = '''\
# CDS agent runner (system-generated; N10 controlled tool orchestration).
# Tools are platform-fixed implementations; the planner is deterministic.
import base64
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile

_AGENT_PROMPT = ""
_AGENT_TOOLS = []
_AGENT_STEP_BUDGET = 15
_AGENT_TOKEN_BUDGET = 20000
_AGENT_MAX_TOOLS = 4
_AGENT_HTTP_ALLOW = {"domains": [], "ips": []}
_RAG_INDEX_FILE = None
_RAG_GENERATIVE_MODEL_ID = None
_INFERENCE_MODEL_ID = None

TF_DIM = 512
_CJK_RE = re.compile(r"[\\u4e00-\\u9fff]")


def _hash_token(token):
    h = 2166136261
    for ch in token.encode("utf-8"):
        h = (h ^ ch) * 16777619 & 0xFFFFFFFF
    return h


def _l2(vec):
    norm = sum(x * x for x in vec) ** 0.5
    if norm <= 0.0:
        return vec
    return [x / norm for x in vec]


def tf_embed(text, dim=TF_DIM):
    vec = [0.0] * dim
    t = (text or "").strip()
    if not t:
        return vec
    for n in (2, 3):
        for i in range(len(t) - n + 1):
            tok = t[i:i + n]
            h = _hash_token(tok)
            vec[h % dim] += 1.0 if (h // dim) % 2 == 0 else -1.0
    for ch in t:
        if _CJK_RE.match(ch):
            h = _hash_token(ch)
            vec[h % dim] += 1.0 if (h // dim) % 2 == 0 else -1.0
    return _l2(vec)


def _load_bytes(path):
    raw = open(path, "rb").read()
    dek_hex = os.environ.get("CDS_DEK_HEX", "")
    if dek_hex:
        dek = bytes.fromhex(dek_hex)
        nonce = raw[:12]
        tag = raw[12:28]
        ct = raw[28:]
        try:
            from Cryptodome.Cipher import AES
            raw = AES.new(dek, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag)
        except ImportError:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = AESGCM(dek).decrypt(nonce, ct + tag, None)
    return raw


def _sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _net_allowed(url):
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    if not host:
        return False
    domains = _AGENT_HTTP_ALLOW.get("domains", [])
    for pattern in domains:
        pattern = pattern.strip().lower()
        if pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[1:].lstrip("."):
            return True
        if host == pattern:
            return True
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    for cidr in _AGENT_HTTP_ALLOW.get("ips", []):
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


# ── tools (platform-fixed) ────────────────────────────────────────
def _tool_compute(args):
    expr = args.get("expression", "")
    import ast as _ast
    tree = _ast.parse(expr, mode="eval")
    allowed = (_ast.Expression, _ast.Constant, _ast.Name, _ast.Load,
               _ast.BinOp, _ast.UnaryOp, _ast.Compare, _ast.BoolOp,
               _ast.Call, _ast.Attribute, _ast.List, _ast.Tuple, _ast.Dict,
               _ast.Set, _ast.Subscript, _ast.Slice, _ast.keyword, _ast.IfExp,
               _ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.FloorDiv, _ast.Mod,
               _ast.Pow, _ast.USub, _ast.UAdd, _ast.Not, _ast.And, _ast.Or,
               _ast.Eq, _ast.NotEq, _ast.Lt, _ast.LtE, _ast.Gt, _ast.GtE,
               _ast.Is, _ast.IsNot, _ast.In, _ast.NotIn)
    for node in _ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("compute expression uses an unsupported construct")
    ns = {"data": args.get("data"), "len": len, "str": str, "int": int, "float": float,
          "sum": sum, "min": min, "max": max, "sorted": sorted, "list": list,
          "dict": dict, "tuple": tuple, "set": set, "abs": abs, "round": round,
          "hasattr": hasattr}
    try:
        import numpy as np
        ns["np"] = np
    except Exception:
        pass
    try:
        import pandas as pd
        ns["pd"] = pd
    except Exception:
        pass
    return eval(expr, {"__builtins__": {}}, ns)


def _tool_rag_retrieve(args):
    if not _RAG_INDEX_FILE or not os.path.exists(_RAG_INDEX_FILE):
        raise RuntimeError("no rag corpus shipped (rag_retrieve unavailable)")
    data = json.loads(_load_bytes(_RAG_INDEX_FILE).decode("utf-8"))
    qvec = tf_embed(args.get("query", ""))
    top_k = int(args.get("top_k", 3) or 3)
    scored = []
    for c in data.get("chunks", []):
        vec = c.get("vector") or []
        if len(vec) != len(qvec):
            continue
        score = sum(a * b for a, b in zip(qvec, vec))
        scored.append((score, c.get("doc_id", ""), c.get("text", "")))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"doc_id": d, "text": t[:500], "score": round(s, 4)} for s, d, t in scored[:top_k]]


def _tool_db_query(args):
    query = (args.get("query") or "").strip()
    if not query.lower().startswith("select"):
        raise RuntimeError("db_query only allows read-only SELECT")
    dbfile = "input/db.sqlite3"
    if not os.path.exists(dbfile):
        raise RuntimeError("no sandbox database shipped (db_query unavailable)")
    import sqlalchemy
    eng = sqlalchemy.create_engine(f"sqlite:///{dbfile}")
    try:
        with eng.connect() as conn:
            rows = conn.execute(sqlalchemy.text(query)).fetchmany(50)
            return [list(r) for r in rows]
    finally:
        eng.dispose()


def _tool_http(args):
    url = args["url"]
    if not _net_allowed(url):
        raise RuntimeError("url host is not in the session network allowlist")
    import urllib.request
    method = str(args.get("method", "GET")).upper()
    timeout = int(args.get("timeout", 10) or 10)
    req = urllib.request.Request(url, method=method)
    for k, v in (args.get("headers") or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")[:4000]


def _generative_answer(context_text):
    bundle = _load_bytes("input/generative_model.bundle")
    zf = zipfile.ZipFile(io.BytesIO(bundle))
    vocab = [ln for ln in zf.read("vocab.txt").decode("utf-8").splitlines() if ln.strip()]
    import onnxruntime as ort
    import numpy as np
    sess = ort.InferenceSession(zf.read("model.onnx"), providers=["CPUExecutionProvider"])
    vec = tf_embed(context_text)[:512]
    padded = (vec + [0.0] * 512)[:512]
    logits = sess.run(["answer_logits"], {"context": np.array([padded], dtype=np.float32)})[0][0]
    idx = int(np.argmax(logits))
    tok = vocab[idx] if 0 <= idx < len(vocab) else f"<token:{idx}>"
    return f"{tok} [CDS-WM:{_sha256(str(_RAG_GENERATIVE_MODEL_ID or ''))[:8]}]"


def _tool_llm_reason(args):
    context = args.get("context", "")
    if _RAG_GENERATIVE_MODEL_ID and os.path.exists("input/generative_model.bundle"):
        return _generative_answer(context)
    return f"[确定性规划器] 基于上下文的确定性摘要：{context[:500]}"


def _tool_inference(args):
    if not _INFERENCE_MODEL_ID or not os.path.exists("input/model.onnx"):
        raise RuntimeError("no inference model provisioned (inference unavailable)")
    import onnxruntime as ort
    import numpy as np
    bundle = _load_bytes("input/model.onnx")
    sess = ort.InferenceSession(bundle, providers=["CPUExecutionProvider"])
    feeds = {
        name: np.asarray(val, dtype=np.float32)
        for name, val in (args.get("inputs") or {}).items()
    }
    outputs = sess.run(None, feeds)
    out_names = [o.name for o in sess.get_outputs()]
    return {
        "outputs": {
            name: (o.tolist() if hasattr(o, "tolist") else o)
            for name, o in zip(out_names, outputs)
        },
        "inference_model": _INFERENCE_MODEL_ID,
    }


_TOOL_IMPL = {
    "compute": _tool_compute,
    "rag_retrieve": _tool_rag_retrieve,
    "db_query": _tool_db_query,
    "http": _tool_http,
    "llm_reason": _tool_llm_reason,
    "inference": _tool_inference,
}


# ── deterministic planner ─────────────────────────────────────────
def _planner(prompt, history):
    done = {h["tool"] for h in history}
    if "rag_retrieve" in _AGENT_TOOLS and "rag_retrieve" not in done and _RAG_INDEX_FILE:
        return ("rag_retrieve", {"query": prompt, "top_k": 3})
    if "compute" in _AGENT_TOOLS and "compute" not in done:
        data = history[-1]["result"] if history else prompt
        return ("compute", {"expression": "len(data) if hasattr(data, '__len__') else str(data)", "data": data})
    if "db_query" in _AGENT_TOOLS and "db_query" not in done:
        return ("db_query", {"query": "SELECT name FROM sqlite_master WHERE type='table'"})
    if "http" in _AGENT_TOOLS and "http" not in done:
        first = (_AGENT_HTTP_ALLOW.get("domains") or [""])[0]
        if first and not first.startswith("*."):
            return ("http", {"url": "https://" + first, "timeout": 10})
    if "inference" in _AGENT_TOOLS and "inference" not in done and _INFERENCE_MODEL_ID:
        return ("inference", {"inputs": {}})
    if "llm_reason" in _AGENT_TOOLS and "llm_reason" not in done:
        context = "\\n".join(str(h["result"])[:800] for h in history) or prompt
        return ("llm_reason", {"context": context})
    return None


def _compose_final(prompt, history):
    if not history:
        return "[确定性规划器] 无工具步骤可执行（未配置可用工具或依赖）。"
    lines = ["[确定性规划器] 已完成以下步骤："]
    for i, h in enumerate(history, 1):
        status = "成功" if h.get("ok") else "失败"
        lines.append(f"  {i}. {h['tool']}（{status}）: {str(h['result'])[:300]}")
    lines.append("最终结论：基于上述工具结果（确定性规划，无 LLM 参与，除非挂了 N6 钩子）。")
    return "\\n".join(lines)


def main():
    steps = 0
    tokens = 0
    history = []
    if _AGENT_STEP_BUDGET <= 0:
        print(json.dumps({"type": "agent_error", "reason": "step budget exceeded", "steps_used": 0, "tokens_used": 0}, ensure_ascii=False))
        return 1
    while steps < _AGENT_STEP_BUDGET:
        plan = _planner(_AGENT_PROMPT, history)
        if plan is None:
            break
        tool_id, args = plan
        if tool_id not in _TOOL_IMPL:
            print(json.dumps({"type": "agent_error", "reason": "unknown tool " + tool_id, "steps_used": steps, "tokens_used": tokens}, ensure_ascii=False))
            return 1
        t0 = time.time()
        try:
            result = _TOOL_IMPL[tool_id](args)
            ok = True
        except Exception as e:
            result = f"tool error: {e}"
            ok = False
        ms = int((time.time() - t0) * 1000)
        tokens += len(json.dumps(args, ensure_ascii=False)) // 4 + len(str(result)) // 4 + 16
        history.append({"tool": tool_id, "args": args, "ok": ok, "result": str(result)[:4000]})
        steps += 1
        print(json.dumps({
            "type": "agent_trace", "step": steps, "tool": tool_id,
            "args_hash": _sha256(json.dumps(args, sort_keys=True, ensure_ascii=False))[:12],
            "status": "ok" if ok else "error", "tokens": tokens, "ms": ms,
        }, ensure_ascii=False))
        sys.stdout.flush()
        if tokens > _AGENT_TOKEN_BUDGET:
            print(json.dumps({"type": "agent_error", "reason": "token budget exceeded", "steps_used": steps, "tokens_used": tokens}, ensure_ascii=False))
            return 1
    final = _compose_final(_AGENT_PROMPT, history)
    print(json.dumps({
        "type": "agent_result", "steps_used": steps, "tokens_used": tokens,
        "tools_used": sorted({h["tool"] for h in history}), "final": final,
    }, ensure_ascii=False))
    return 0
'''


def _validate_tool_ids(tool_ids: list[str]) -> None:
    err = tool_registry.validate_run(tool_ids)
    if err:
        raise ValueError(err)


def build_agent_runner(
    prompt: str,
    tool_ids: list[str],
    *,
    step_budget: int | None = None,
    token_budget: int | None = None,
    rag_index_file: str | None = "input/rag_index.json",
    generative_model_id: str | None = None,
    inference_model_id: str | None = None,
    http_allow: dict | None = None,
) -> str:
    """Generate the self-contained agent runner source for an AGENT task.

    The prompt / tool selection / budgets / dependency ids travel as module
    literals (the code is stored encrypted as ``code_content``), so they never
    land in a plaintext column. The runner is validated byte-exactly against
    this template before execution — the code_scanner whitelist is never
    widened.
    """
    from app.core.config import get_settings
    settings = get_settings()

    _validate_tool_ids(tool_ids)
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("agent prompt cannot be empty")
    if len(tool_ids) > settings.AGENT_MAX_TOOLS:
        raise ValueError(f"too many tools (max {settings.AGENT_MAX_TOOLS})")

    effective_steps = int(settings.AGENT_STEP_BUDGET if step_budget is None else step_budget)
    effective_tokens = int(settings.AGENT_TOKEN_BUDGET if token_budget is None else token_budget)
    if effective_steps < 1:
        raise ValueError("step_budget must be >= 1")
    if effective_tokens < 1:
        raise ValueError("token_budget must be >= 1")

    body = textwrap.dedent(_AGENT_RUNNER_HEADER).rstrip()
    tail = (
        "\n\n# --- generated per-task params ---\n"
        f"_AGENT_PROMPT = {prompt!r}\n"
        f"_AGENT_TOOLS = {tool_ids!r}\n"
        f"_AGENT_STEP_BUDGET = {effective_steps}\n"
        f"_AGENT_TOKEN_BUDGET = {effective_tokens}\n"
        f"_AGENT_MAX_TOOLS = {len(tool_ids)}\n"
        f"_AGENT_HTTP_ALLOW = {dict(http_allow or {"domains": [], "ips": []})!r}\n"
        f"_RAG_INDEX_FILE = {rag_index_file!r}\n"
        f"_RAG_GENERATIVE_MODEL_ID = {generative_model_id!r}\n"
        f"_INFERENCE_MODEL_ID = {inference_model_id!r}\n"
        "\nif __name__ == '__main__':\n    sys.exit(main())\n"
    )
    return body + tail


def validate_agent_runner(code: str, tool_ids: list[str] | None = None) -> tuple[bool, str]:
    """Verify an agent runner is EXACTLY the system-generated template.

    Extract the embedded literals via AST, regenerate the runner, and require
    a byte-exact match. Any tampering — extra imports, calls, network, tool
    list changes — fails the comparison and the runner is rejected.
    When ``tool_ids`` is provided it must match the embedded tool selection.
    Returns (passed, reason).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"runner is not valid python: {e}"

    _PER_TASK_LITERALS = {
        "_AGENT_PROMPT", "_AGENT_TOOLS", "_AGENT_STEP_BUDGET", "_AGENT_TOKEN_BUDGET",
        "_AGENT_MAX_TOOLS", "_AGENT_HTTP_ALLOW", "_RAG_INDEX_FILE",
        "_RAG_GENERATIVE_MODEL_ID", "_INFERENCE_MODEL_ID",
    }
    literals: dict[str, object] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id in _PER_TASK_LITERALS):
            continue
        try:
            literals[target.id] = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return False, f"{target.id} must be a literal (no code injection)"

    if "_AGENT_PROMPT" not in literals:
        return False, "runner missing _AGENT_PROMPT literal"
    extracted_tools = literals.get("_AGENT_TOOLS", [])
    if not isinstance(extracted_tools, list):
        return False, "_AGENT_TOOLS must be a list"
    if tool_ids is not None and set(extracted_tools) != set(tool_ids):
        return False, "runner tool selection differs from the task tools"
    if tool_registry.validate_run(extracted_tools) is not None:
        return False, "runner references unknown or duplicate tools"

    expected = build_agent_runner(
        literals["_AGENT_PROMPT"],
        extracted_tools,
        step_budget=literals.get("_AGENT_STEP_BUDGET"),
        token_budget=literals.get("_AGENT_TOKEN_BUDGET"),
        rag_index_file=literals.get("_RAG_INDEX_FILE"),
        generative_model_id=literals.get("_RAG_GENERATIVE_MODEL_ID"),
        inference_model_id=literals.get("_INFERENCE_MODEL_ID"),
        http_allow=literals.get("_AGENT_HTTP_ALLOW"),
    )
    if code != expected:
        return False, "runner differs from the system-generated template (tampered or stale)"
    return True, "runner matches the system-generated template"


def extract_agent_metrics(output: str) -> dict:
    """Parse agent_trace / agent_result lines for metering + audit."""
    metrics: dict = {"steps_used": 0, "tokens_used": 0, "tools_used": [], "traces": [], "error": None}
    if not output:
        return metrics
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        mtype = obj.get("type")
        if mtype == "agent_trace":
            metrics["traces"].append(obj)
        elif mtype == "agent_result":
            metrics["steps_used"] = obj.get("steps_used", 0)
            metrics["tokens_used"] = obj.get("tokens_used", 0)
            metrics["tools_used"] = obj.get("tools_used", [])
        elif mtype == "agent_error":
            metrics["error"] = obj.get("reason")
    return metrics


