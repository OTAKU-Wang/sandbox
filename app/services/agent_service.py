"""N10: sandbox-internal agent execution — runner generation + validation.

Reuses the N5/N6 "system-generated runner + byte-exact template validation"
pattern. The runner is a self-contained script that loops a deterministic
planner over platform-fixed tools, enforces step/token budgets in-sandbox
(fail-closed) and emits a per-step ``agent_trace`` JSONL the host audits and
metered on completion. The code_scanner import whitelist is NEVER widened —
agent runners are validated byte-exactly against the trusted template.
"""
import ast
import ipaddress
import json
import logging
import re
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
# A157: the http tool's egress caps are part of the TRUSTED TEMPLATE, not
# per-task literals — a settings-derived cap would make byte-exact template
# validation depend on the host config the sandbox must not be able to move.
_AGENT_HTTP_MAX_TIMEOUT = 60
_AGENT_HTTP_DEFAULT_TIMEOUT = 10
_AGENT_HTTP_METHODS = ("GET", "POST")
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
    """A157: default-deny egress match for the http tool.

    The old matcher only compared hostnames, so a whitelisted host on an
    arbitrary port (``https://allowed-host:6379/``) silently turned a domain
    allowlist into a TCP tunnel, and non-http schemes were only rejected by
    accident. Here: http(s) only, scheme-default port only, and the FQDN
    trailing dot is normalised on BOTH sides so a pattern and a URL written in
    different styles cannot disagree. Anything unreadable is denied.
    """
    from urllib.parse import urlparse
    default_ports = {"http": 80, "https": 443}
    try:
        parsed = urlparse(url or "")
        explicit_port = parsed.port
    except ValueError:
        return False
    port = default_ports.get(parsed.scheme)
    if port is None:
        return False
    if (explicit_port if explicit_port is not None else port) != port:
        return False
    host = parsed.hostname
    if not host:
        return False
    host = host.strip().lower().rstrip(".")
    if not host:
        return False
    allow = _AGENT_HTTP_ALLOW or {}
    for pattern in allow.get("domains") or []:
        pattern = str(pattern).strip().lower().rstrip(".")
        if not pattern or pattern == "*":
            continue
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == pattern:
            return True
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    for cidr in allow.get("ips") or []:
        try:
            if ip in ipaddress.ip_network(str(cidr).strip(), strict=False):
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


def _clamp_http_timeout(raw):
    """A157: the caller never chooses its own ceiling.

    A non-numeric/missing timeout falls back to the template default, and every
    value is clamped into ``[1, _AGENT_HTTP_MAX_TIMEOUT]`` (``timeout: 10**9``
    used to be honoured verbatim).
    """
    try:
        value = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        value = _AGENT_HTTP_DEFAULT_TIMEOUT
    return max(1, min(value, _AGENT_HTTP_MAX_TIMEOUT))


def _tool_http(args):
    """A157: method whitelist + allowlist + timeout cap, all BEFORE any socket.

    The order is the product: the description said GET/POST while ``method`` was
    forwarded verbatim (DELETE went out on the wire) and the socket was opened
    without a method gate. Nothing below runs for a refused call, so no
    connection is ever made to a host the session was not granted.
    """
    import urllib.request
    url = args.get("url")
    method = str(args.get("method", "GET")).upper()
    if method not in _AGENT_HTTP_METHODS:
        raise ValueError(
            "http tool method not allowed: " + method[:32]
            + " (only " + "/".join(_AGENT_HTTP_METHODS) + ")"
        )
    if not _net_allowed(url):
        raise ValueError("http tool url is not in the session network allowlist")
    body = args.get("body")
    data = None
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    timeout = _clamp_http_timeout(args.get("timeout"))
    req = urllib.request.Request(url, data=data, method=method)
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


def _validate_tool_ids(
    tool_ids: list[str], *, role: str | None = None, budget: int | None = None
) -> None:
    # A158: forward the run context to validate_run — the single fail-closed
    # entry point (never re-implement roles/budget checks here).
    err = tool_registry.validate_run(tool_ids, role=role, budget=budget)
    if err:
        raise ValueError(err)


# A157: bare hostname / wildcard-hostname pattern (optional single leading
# ``*.``, optional single trailing dot). Deliberately strict: a scheme, a port,
# a path or whitespace never matches, so those typos are rejected here instead
# of quietly never matching again inside the sandbox.
_HOST_PATTERN_RE = re.compile(
    r"^(?:\*\.)?(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.?$"
)
_HTTP_ALLOW_KEYS = ("domains", "ips")


def _validate_http_allow(http_allow: dict | None) -> dict:
    """A157: validate + normalise the session http allowlist at GENERATION time.

    Every malformed entry used to be dropped by the in-sandbox matcher
    (``except ValueError: continue`` / a never-matching FQDN-with-dot), so a
    typo meant "no egress" with no error anywhere — an operator believes the
    allowlist is live while it is inert. Rejection here is fail-closed: the
    task is refused with 422 rather than shipped with a silently-dead gate.

    Returns the canonical ``{"domains": [...], "ips": [...]}`` dict embedded in
    the runner; normalisation is idempotent, so ``validate_agent_runner`` can
    re-feed the extracted literal and still hit a byte-exact template match.
    """
    if http_allow is None:
        return {"domains": [], "ips": []}
    if not isinstance(http_allow, dict):
        raise ValueError("http_allow must be a dict of 'domains'/'ips' lists")
    unknown = sorted(set(http_allow) - set(_HTTP_ALLOW_KEYS))
    if unknown:
        raise ValueError(f"http_allow has unsupported keys {unknown} (allowed: {list(_HTTP_ALLOW_KEYS)})")

    domains_in = http_allow.get("domains") or []
    ips_in = http_allow.get("ips") or []
    if isinstance(domains_in, (str, bytes)) or not isinstance(domains_in, (list, tuple)):
        raise ValueError("http_allow['domains'] must be a list of hostnames")
    if isinstance(ips_in, (str, bytes)) or not isinstance(ips_in, (list, tuple)):
        raise ValueError("http_allow['ips'] must be a list of IPs or CIDRs")

    domains: list[str] = []
    for raw in domains_in:
        if not isinstance(raw, str):
            raise ValueError(f"http_allow['domains'] entry {raw!r} is not a string")
        entry = raw.strip().lower().rstrip(".")
        if not entry or entry == "*" or not _HOST_PATTERN_RE.match(entry):
            raise ValueError(
                f"http_allow['domains'] entry {raw!r} is not a bare hostname "
                "(no scheme, port, path or wildcard-only form)"
            )
        domains.append(entry)

    ips: list[str] = []
    for raw in ips_in:
        if not isinstance(raw, str):
            raise ValueError(f"http_allow['ips'] entry {raw!r} is not a string")
        try:
            net = ipaddress.ip_network(raw.strip(), strict=False)
        except ValueError as exc:
            raise ValueError(f"http_allow['ips'] entry {raw!r} is not a valid IP or CIDR") from exc
        ips.append(str(net))

    return {"domains": domains, "ips": ips}


def build_agent_runner(
    prompt: str,
    tool_ids: list[str],
    *,
    role: str | None = None,
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
    widened. ``role`` is validation context only (A158 second gate) and never
    affects the generated bytes, so the byte-exact template check is unchanged.
    """
    from app.core.config import get_settings
    settings = get_settings()

    _validate_tool_ids(tool_ids, role=role)
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

    # A158 budget gate: the tool selection's budget_cost sum must fit the
    # run's effective step budget (validate_run stays the single gate; this
    # is the service-layer fallback for every builder, not just the API).
    _validate_tool_ids(tool_ids, role=role, budget=effective_steps)

    # A157: a malformed allowlist is refused here, at generation time — never
    # smuggled into the runner where it would silently match nothing.
    canonical_allow = _validate_http_allow(http_allow)

    body = textwrap.dedent(_AGENT_RUNNER_HEADER).rstrip()
    tail = (
        "\n\n# --- generated per-task params ---\n"
        f"_AGENT_PROMPT = {prompt!r}\n"
        f"_AGENT_TOOLS = {tool_ids!r}\n"
        f"_AGENT_STEP_BUDGET = {effective_steps}\n"
        f"_AGENT_TOKEN_BUDGET = {effective_tokens}\n"
        f"_AGENT_MAX_TOOLS = {len(tool_ids)}\n"
        f"_AGENT_HTTP_ALLOW = {canonical_allow!r}\n"
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

    try:
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
    except ValueError as e:
        # A tampered per-task literal can now be rejected by the A157 generator
        # gates; keep the (passed, reason) contract instead of raising.
        return False, f"runner params are invalid: {e}"
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


