# Static-review claims vs. real code — app/services/agent_service.py

Verified 2026-10-18 against working tree (file is 476 lines; `grep`/read line numbers below are from the file as it stands).
The 20 findings are **10 unique claims duplicated in pairs** (F01≈F06, F02≈F07, F03≈F08, F04≈F10, F05≈F09, F11≈F15, F12≈F16, F13≈F17, F14≈F18, F19≈F20); each pair is judged once.

## Module context (applies to every verdict — read first)

`app/services/agent_service.py` has exactly two parts:

1. **Lines 19–333: a string literal** `_AGENT_RUNNER_HEADER = '''\` (L19) … closing `'''` at L333. Everything named `_tool_compute`, `_net_allowed`, `_tool_http`, `_load_bytes`, `_tool_db_query`, `_generative_answer`, `_planner`, `main()` in the findings lives **inside this template string**. None of it executes in the API process. It is the source of a self-contained runner script that:
   - is generated server-side by `build_agent_runner` (host fn, L342–392) — `app/api/agent.py:86` is the only production caller;
   - is stored KMS-encrypted (`app/api/agent.py:103` → `task_code_security.encrypt_task_code`) and only ever replayed by the pipeline after **byte-exact re-validation against the template** (`app/services/task_pipeline.py:641-643, 646` — `validate_agent_runner(code, ...)` … `code != expected` at agent_service L446 rejects any byte difference);
   - executes **inside the sandbox** as a subprocess: L0 = bubblewrap with `--unshare-pid` and conditional `--unshare-net` (`app/services/sandbox_runtime.py:106-107`; `isolate_network` defaults to `True` at L74/L309), and `RuntimeError` refusal when bwrap is missing and the dev fallback is disabled (`sandbox_runtime.py:278-281`);
   - runs a **deterministic planner**: every tool's `args` are hardcoded in `_planner` (template L260–278) — the compute expression is the literal string at L266, the db_query at L268, and the http URL at L269–272 is derived from the allowlist itself, never from user input. **No HTTP/API input ever reaches `args` at all.**
2. **Host-side functions**: `_validate_tool_ids` (L336), `build_agent_runner` (L342), `validate_agent_runner` (L395), `extract_agent_metrics` (L451).

**Live reachability of the module**: `POST /api/v1/agent/tasks` — `app/api/agent.py:51`, mounted at `app/main.py:360` (`prefix="/api/v1/agent"`, `dependencies=[Depends(require_group("agent"))]`), per-route gate `require_roles(ADMIN, OPERATOR, DATA_PROVIDER, BUYER)` at `agent.py:55` plus session-ownership check `agent.py:63` and per-tool role check `_enforce_tool_roles` (`agent.py:39-48`, called L71). Not a simulation path — real subprocess execution pipeline (`task_pipeline.py:387-416, 640-663`); tests run it as a real subprocess (`tests/test_agent_service.py:18-22`). There is no `AGENT_ENABLED` flag; the router is always mounted.

**Key dead input**: `http_allow` is a keyword of `build_agent_runner` (L351) that **no production caller ever passes** (`app/api/agent.py:86-94` passes prompt/tool_ids/budgets/rag/model ids only; repo-wide grep for `http_allow` matches only agent_service.py and `tests/test_agent_service.py:108`). Therefore every generated production runner embeds `_AGENT_HTTP_ALLOW = {"domains": [], "ips": []}` (L386), so `_net_allowed` (L98) returns `False` for every URL and the planner's http branch (L269-272: `first = (_AGENT_HTTP_ALLOW.get("domains") or [""])[0]; if first and not first.startswith("*.")`) never fires. **The `http` tool is inert in production.**

---

## F01 + F06 — `_tool_compute` AST allowlist escape (claimed: critical)

**Verdict: LATENT (weakness in the guardrail is real; no attacker-reachable input; and the blast radius is inside the sandbox, not the host)**
**My severity: medium (defense-in-depth hardening), not critical**

Evidence (all inside the template string, which never runs in the host process):
- `app/services/agent_service.py:19` — `_AGENT_RUNNER_HEADER = '''\` (L333 closes it)
- `app/services/agent_service.py:131` — `_ast.Call, _ast.Attribute, _ast.List, _ast.Tuple, _ast.Dict,`
- `app/services/agent_service.py:140-143` — `ns = {"data": ..., "len": len, "str": str, ... "dict": dict, "tuple": tuple, "set": set, ...}`
- `app/services/agent_service.py:154` — `return eval(expr, {"__builtins__": {}}, ns)`

The escape claim itself is technically correct: `Attribute`+`Call`+`Subscript` plus names like `str` in `ns` do allow `().__class__.__bases__[0].__subclasses__()` traversal; `{"__builtins__": {}}` is not a sandbox. But reachability fails on every leg the claim asserts:
- `expr` is **not** user-influenced. The planner hardcodes the only compute expression: L266 `return ("compute", {"expression": "len(data) if hasattr(data, '__len__') else str(data)", "data": data})`. The claim "prompt injection changes args → eval" is false: prompt only feeds `rag_retrieve.query` / `llm_reason.context` (L262-263, L275-277), never `compute.expression`.
- "runner 被替换成通过其它校验的变体" is false by construction: any byte deviation fails `code != expected` (L446-447) before execution (`task_pipeline.py:643` → REJECTED at `:655-659`), and the code column is KMS-encrypted (`task_code_security.py:15-18`).
- Even a hypothetically reachable escape executes **inside the bwrap jail** (`sandbox_runtime.py:106-107` `--unshare-pid`, `*(["--unshare-net"] if isolate_network else [])` with `isolate_network: bool = True` default at `:74`), on the same trust level as any ordinary sandboxed user code.

Worst-case impact if `expr` ever became user-controlled: arbitrary code execution *inside the sandbox* (own-session `input/` corpus + `CDS_DEK_HEX`, which is injected into the workspace env for encrypted workspaces at `app/api/sandbox_sessions.py:1291`) — i.e. data the session is already authorized to decrypt; no host escape (bwrap+seccomp are the boundary). Adjacent risk noted, out of this file's scope: the L0 bwrap launcher passes `os.environ.copy()` (`sandbox_runtime.py:276`) and I did not find a `--clearenv` in `_build_l0_bwrap_args` — worth its own verification in sandbox_runtime.py.

Minimal fix: in the template's `_tool_compute` walk, ban `ast.Attribute` and require every `Call.func` to be an `ast.Name` whose id is in the `ns` callables set.

---

## F02 + F07 — `build_agent_runner` lacks authorization on `tool_ids`/`http_allow`; validate is "self-comparison" (claimed: critical)

**Verdict: LATENT (accurate description of the code, wrong model of who supplies it; authorization exists one layer up; `http_allow` has no production supplier)**
**My severity: low-medium (hardening), not critical**

Evidence:
- `app/services/agent_service.py:336-339` — `_validate_tool_ids` only checks registry membership via `tool_registry.validate_run` (implemented `app/services/agent_tools.py:113-124`: unknown/duplicate only).
- `app/services/agent_service.py:431` — `if tool_ids is not None and set(extracted_tools) != set(tool_ids):` (cross-check skipped when caller passes None).
- `app/services/agent_service.py:436-445` — `expected = build_agent_runner(literals["_AGENT_PROMPT"], extracted_tools, ... )` — yes, `expected` is regenerated from the code's own literals.

Why it is not an exploitable defect:
- Only production caller of `build_agent_runner` is the API endpoint `app/api/agent.py:51-96`, which enforces: session ownership (`:63`), registry validation (`:68-70`), **per-tool role authorization** `_enforce_tool_roles` (`:39-48`, `:71`; e.g. the `http` tool is restricted to admin/operator/data_provider in `app/services/agent_tools.py:183-197`), and tool-count cap (`:73-74`). A user can never obtain a runner containing a tool their role may not select.
- `http_allow`: no caller passes it (`app/api/agent.py:86-94`); production runners always embed the empty allowlist (L386), so "把出网权限交给调用方" has no counterparty — the API body `AgentTaskCreate` (`agent.py:27-34`) has no http_allow/network field at all.
- The only production caller of `validate_agent_runner` always passes the task's tool set (`task_pipeline.py:643` `validate_agent_runner(code, task.payload.get("agent_tool_ids"))`, and `agent_tool_ids` is always set at creation, `agent.py:132`), so the `tool_ids is None` skip is test-surface only. And "self-comparison" is by design: the byte-exact gate (L446) is a **code-integrity** check (template or not), not an authorization gate — authorization is enforced at creation where the runner is generated, and a forged runner for *any* tool set still cannot be persisted without the KMS path (`task_code_security.py:9-18`) and would still run inside the sandbox with that task's own data.

Worst-case impact: none reachable today; if `build_agent_runner` gained a second, less-careful caller, unvalidated `tool_ids`/`http_allow` would silently widen that runner. Fix: give `_validate_tool_ids` an `allowed_ids` parameter and validate `http_allow` shape (reject non-`dict[str, list[str]]`, reject `0.0.0.0/0`/`::/0`/loopback/private CIDRs) in `build_agent_runner`.

---

## F03 + F08 — `_tool_http` DNS rebinding / redirect bypass / unbounded read (claimed: critical)

**Verdict: LATENT (the three weaknesses are real in isolation; the tool is unreachable in production and doubly gated)**
**My severity: latent — medium to fix *before* ever wiring the http tool; currently no defect exposure**

Evidence:
- `app/services/agent_service.py:100` — `host = urlparse(url).hostname` — string check only; no resolution to IP, so an allowlisted domain resolving to `169.254.169.254` would pass: claim TRUE in principle (L98-121 has no IP post-check).
- `app/services/agent_service.py:198-201` — `req = urllib.request.Request(url, method=method)` … `with urllib.request.urlopen(req, timeout=timeout) as resp:` — urllib's default opener follows 3xx and `_net_allowed` (called once at L193) is not re-applied to the redirect target: claim TRUE in principle. (Note: Python's `HTTPRedirectHandler` converts POST→GET on 301/302/303, so the "含 POST body 重发" wording is imprecise.)
- `app/services/agent_service.py:202` — `return resp.read().decode("utf-8", errors="replace")[:4000]` — unbounded `read()` before truncation: claim TRUE.

Reachability: **none in production.** (a) `_AGENT_HTTP_ALLOW` is always the empty default (see Module context — no caller passes `http_allow`), so `_tool_http` raises at L194 `raise RuntimeError("url host is not in the session network allowlist")` for any URL, and the planner never even emits the step (L269-272 requires a non-empty, non-wildcard first domain); (b) even if it fired under L0 with default isolation, the process has no network (`--unshare-net` default, `sandbox_runtime.py:74,107`); (c) `url`/`method`/`timeout` come from the planner, hardcoded as `"https://" + first` with `timeout: 10` (L270-272) — the `args`-driven variants (`timeout=10**9`, missing `url` KeyError) have no input source. The only place `http_allow` is non-empty is `tests/test_agent_service.py:104-108`.

Worst-case impact: zero today. If/when session network policy is wired into `http_allow`, all three become live SSRF/DoS issues. Fix (one line each): custom opener with a redirect handler that re-runs `_net_allowed` (or `redirect=False`); `resp.read(4096)`; post-resolution IP deny-list for loopback/RFC1918/link-local before dispatch.

---

## F04 + F10 — `_load_bytes` resource leak + misleading crypto errors (claimed: high)

**Verdict: PARTIALLY TRUE — error-semantics issues real; the "resource leak" framing is FALSE under CPython**
**My severity: low**

Evidence:
- `app/services/agent_service.py:78` — `raw = open(path, "rb").read()` — no `with`: fact TRUE, but the only exception source is `read()` itself; the file object then has zero references and CPython refcounting closes it immediately. No accumulation is possible across calls; "句柄依赖 GC 回收" is technically true and practically vacuous in this runtime (and in a one-shot sandbox process).
- `app/services/agent_service.py:82-84` — `nonce = raw[:12]` / `tag = raw[12:28]` / `ct = raw[28:]` — a <28-byte file silently yields short/empty parts and dies later as `InvalidTag`/`ValueError`: TRUE (misleading diagnostics).
- `app/services/agent_service.py:79-81` — `dek_hex = os.environ.get("CDS_DEK_HEX", "")` / `dek = bytes.fromhex(dek_hex)` — no length/charset validation; bad DEK surfaces as `ValueError: non-hexadecimal number` or a backend key-length error: TRUE.
- `app/services/agent_service.py:85-90` — `except ImportError:` fallback Cryptodome→cryptography; `InvalidTag` propagates raw: TRUE.

All of this is template code that runs **inside the sandbox**; every tool exception is caught per-step by `main()` (template L308-312: `except Exception as e: result = f"tool error: {e}"; ok = False`), so worst case = an obscure error message in one failed trace step of the user's own task. No security or availability impact. Fix: `with open(...)`, then `if len(raw) < 28: raise ValueError(f"{path} too short to be an encrypted bundle")` and validate `len(dek) in (16, 24, 32)`.

---

## F05 + F09 — `validate_agent_runner` raises instead of returning `(False, reason)`; `_AGENT_MAX_TOOLS` tamper-undetectable (claimed: high)

**Verdict: PARTIALLY TRUE — the contract violation is real but every consequence claimed is wrong (single caller catches; `dict(None)` claim false; MAX_TOOLS tamper claim false)**
**My severity: low**

Evidence (host-side code — this one is not template):
- `app/services/agent_service.py:436-445` — the `expected = build_agent_runner(...)` regeneration at the end of `validate_agent_runner` is **not** inside any `try` (the function's only `try` is L404-407 around `ast.parse`).
- Raise paths inside `build_agent_runner`: `:371-372` `int(...)` on a string literal → `ValueError`/`TypeError`; `:373-376` `if effective_steps < 1: raise ValueError("step_budget must be >= 1")` (same for tokens); `:366-367` empty-prompt raise; `:368-369` tool-count raise. A stored runner with `_AGENT_STEP_BUDGET = 0` therefore makes `validate_agent_runner` *raise*, contradicting its documented "Returns (passed, reason)" (L402). Claim TRUE so far.
- But: `dict(None)` TypeError claim is **false** — `:386` is `dict(http_allow or {"domains": [], "ips": []})`; the `or` catches `None`. (A non-mapping like `5` would still raise.) The "`_AGENT_TOOLS` 含未注册工具 → `_validate_tool_ids` 抛" claim is **false** — `:433-434` already returns `(False, "runner references unknown or duplicate tools")` *before* the rebuild.
- The "500 / fail-open" consequence is **false**: the only production caller wraps it in a catch-all — `app/services/task_pipeline.py:642` `try:` … `:661-663` `except Exception as e: … return TaskStatus.FAILED, {"reason": f"Agent runner validation error: {e}"}`. An anomalous runner fails closed (FAILED), never fail-open, never an HTTP 500 (validation runs asynchronously in the pipeline, `agent.py:122`).
- "`_AGENT_MAX_TOOLS` 被改小永远不会导致校验失败" is **false**: `build_agent_runner:385` always writes `_AGENT_MAX_TOOLS = {len(tool_ids)}`, so any tampered value makes `code != expected` at `:446` → rejected. What *is* true (see F19/F20) is that the runner itself never reads the literal at runtime.

Worst-case impact: a mislabeled terminal state (FAILED instead of REJECTED) for an already-rejected runner. Fix: wrap L436-445 in `try/except (ValueError, TypeError) as e: return False, f"runner literals invalid: {e}"`.

---

## F11 + F15 — `_tool_db_query` "read-only SELECT" bypass via `; ATTACH` (claimed: medium)

**Verdict: FALSE as claimed (input unreachable and the claimed vector does not work); the read-only-URI hardening suggestion is still reasonable**
**My severity: info/low (hardening note)**

Evidence:
- `app/services/agent_service.py:176` — `if not query.lower().startswith("select"):` — the guard exists; `ATTACH …`/`PRAGMA …` as a single statement are blocked by this prefix.
- Multi-statement claim fails at the driver: `:184-185` `eng = sqlalchemy.create_engine(f"sqlite:///{dbfile}")` … `conn.execute(sqlalchemy.text(query))` goes through `sqlite3.Cursor.execute`, which accepts exactly one statement (`sqlite3` raises on trailing extra statements); `executescript` is never used. `SELECT 1; ATTACH …` dies with a driver error, contradicting the claim's core example.
- Unreachable input: `query` comes from `args`, and the sole producer of args is the template's planner, hardcoded — `:268` `return ("db_query", {"query": "SELECT name FROM sqlite_master WHERE type='table'"})`. The finding itself concedes "query 由 planner 生成"; the "planner 一旦扩展" scenario is hypothetical future code.
- Also template-side, sandbox-executed, DB file is the session's own `input/db.sqlite3` (`:178`), and writes would land in tmpfs/ro-binds anyway (`sandbox_runtime.py:143-146` mounts `/workspace` tmpfs, `input` ro-bind).

Impact: none. Fix (optional hardening): open with `sqlite3.connect(f"file:{dbfile}?mode=ro", uri=True)` / `PRAGMA query_only=ON` instead of relying on the string prefix.

---

## F12 + F16 — f-string reuses `"` inside `{...}` → SyntaxError on Python < 3.12 (claimed: medium)

**Verdict: TRUE (real PEP 701 dependency; breaks the py3.11 CI lane; production py3.12 unaffected). The F16 addendum about dict key-order false-"tampered" is FALSE.**
**My severity: medium (CI/toolchain breakage), not runtime**

Evidence:
- `app/services/agent_service.py:386` — `f"_AGENT_HTTP_ALLOW = {dict(http_allow or {"domains": [], "ips": []})!r}\n"` — double-quoted dict inside a double-quoted f-string; legal only since PEP 701 (3.12), `SyntaxError: f-string: unmatched '('` on 3.8–3.11. This is host-side code, executed at import.
- CI backend runs 3.11: `.github/workflows/ci.yml:25` — `python-version: "3.11"` — and compiles the tree: `.github/workflows/ci.yml` step "Compile check": `python -m compileall -q app tests alembic` → fails on this file. (Per AGENTS.md the *authoritative* suite runs on the remote py3.12 production image, and the production image is `python:3.12-slim` — `Dockerfile.api:3,14` — so production runtime and the remote gate lane are unaffected; the GitHub 3.11 job, however, cannot import the module at all: `app/main.py:30` imports `app.api.agent`, which imports `agent_service` at `agent.py:20`.)
- F16's second half is wrong: `validate_agent_runner` never receives `http_allow` from a caller — it re-extracts the dict from the stored code's own literal (`:422` `ast.literal_eval`, which preserves insertion order) and re-serializes that dict (`:444`), so key order round-trips and cannot produce a false "tampered" verdict.

Impact: any deployment/toolchain pinned below 3.12 cannot import the module (agent feature down); the shipped CI job is the concrete live instance. Fix: hoist the default — `_http_default = {"domains": [], "ips": []}` before L379 and interpolate `dict(http_allow or _http_default)`; or bump the CI image to 3.12 (project's actual floor).

---

## F13 + F17 — `_net_allowed`: no scheme allowlist, string-only host matching, no FQDN trailing-dot normalization, no post-resolution IP check (claimed: medium)

**Verdict: LATENT (accurate description of the string checks; zero current reachability — same dead-input argument as F03/F08)**
**My severity: low now; fix together with F03/F08 when the http tool is wired**

Evidence:
- `app/services/agent_service.py:98-109` — `_net_allowed` never inspects `urlparse(url).scheme`; matching is `pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[1:].lstrip(".")` (L106) or `host == pattern` (L108); no `host.rstrip(".")`, no IDN/punycode handling, no resolution (the `ipaddress` branch L110-121 only tests **literal** IPs against `ips` CIDRs).
- Trailing-dot false negative (`allowed.com.` ≠ `allowed.com`) — real. Scheme: `file:///etc/passwd` is rejected via `hostname is None` (L101-102), and a planner URL is always `"https://" + domain` (template L272), so `gopher://`/`file://` have no dispatch path; `urlopen` would raise `unknown url type` anyway.
- Reachability: identical to F03/F08 — production `_AGENT_HTTP_ALLOW` is empty (L386 default; no caller supplies it) → `_net_allowed` returns `False` for everything → tool inert; tests are the only place it's non-empty (`tests/test_agent_service.py:104-108`).

Impact: none today. Fix: `if parsed.scheme not in ("http", "https"): return False`, normalize `host = host.lower().rstrip(".")`, and add the post-resolution private-IP deny-list from F03/F08.

---

## F14 + F18 — `_generative_answer` re-inits ONNX session per call; unclosed `ZipFile` (claimed: medium/performance)

**Verdict: PARTIALLY TRUE — no caching / unclosed ZipFile are real; the "called repeatedly in the loop" premise is FALSE, and the process is one-shot**
**My severity: low**

Evidence:
- `app/services/agent_service.py:206-211` — `bundle = _load_bytes("input/generative_model.bundle")` / `zf = zipfile.ZipFile(io.BytesIO(bundle))` … `sess = ort.InferenceSession(zf.read("model.onnx"), ...)` — no `with` on `zf`, no session cache: TRUE.
- But the repeat premise fails: the planner emits `llm_reason` **at most once** per run — L275 `if "llm_reason" in _AGENT_TOOLS and "llm_reason" not in done:` and `done` is the set of already-run tools (L261), so within the step budget the tool never re-executes; `_tool_llm_reason` (L220-224) calls `_generative_answer` once. `_tool_inference`/`_tool_rag_retrieve` likewise run once each. One initialization per sandbox process, and the process exits after `main()`.
- Memory: bundle bytes + model live simultaneously — true but bounded by the one model the session was given; worst case is the task's own wall-clock, inside its own cgroup/tmpfs (`sandbox_runtime.py:129-143`).

Impact: a slower final step for `llm_reason` tasks; a garbage-collected zip handle. Fix: `with zipfile.ZipFile(io.BytesIO(bundle)) as zf:` and cache the session on a function attribute.

---

## F19 + F20 — `_AGENT_MAX_TOOLS` is a dead config; `import base64` unused (claimed: low)

**Verdict: TRUE**
**My severity: low (cosmetic/misleading-documentation)**

Evidence:
- Grep over the file for `_AGENT_MAX_TOOLS` yields exactly three hits: `:36` (template default `_AGENT_MAX_TOOLS = 4`), `:385` (build writes `f"_AGENT_MAX_TOOLS = {len(tool_ids)}\n"`), `:411` (listed in `validate`'s `_PER_TASK_LITERALS` extraction set). Neither `_planner` (L260-278) nor `main()` (L292-332) reads it — the runner enforces `step`/`token` budgets (`:296-299, 299, 324`) but never re-checks tool count; the real cap is server-side only (`app/api/agent.py:73` and `agent_service.py:368`).
- Grep for `base64`: single hit at `:22` — the template imports it and never uses it (unused import baked into every generated runner).

Impact: none functional; misleading to maintainers reading the runner ("looks like a enforced limit"). Fix: delete `_AGENT_MAX_TOOLS` and `import base64` from the template (a template edit also requires regenerating the template-lock expectations in `tests/test_agent_service.py`, which byte-compares against `build_agent_runner`).

---

## Summary table

| IDs | Claim | Verdict | My severity | Live? |
|---|---|---|---|---|
| F01/F06 | `_tool_compute` AST-allowlist escape | LATENT | medium (harden) | no — expression hardcoded by planner (L266); byte-exact gate L446; runs inside bwrap |
| F02/F07 | build/validate tool-authorization gap | LATENT | low-medium | no — authz lives in `agent.py:39-71`; `http_allow` has no caller |
| F03/F08 | `_tool_http` SSRF/rebind/redirect/unbounded read | LATENT | medium when wired | no — allowlist always empty; `--unshare-net` default |
| F04/F10 | `_load_bytes` leak + error semantics | PARTIAL TRUE | low | leak claim false (refcounting); diagnostics real |
| F05/F09 | `validate_agent_runner` raises vs tuple contract | PARTIAL TRUE | low | real raises (budget/count), but caller catches (`pipeline:661`); `dict(None)` & MAX_TOOLS sub-claims false |
| F11/F15 | db_query `;ATTACH` bypass | FALSE | info | planner-hardcoded query; sqlite3 single-statement; prefix blocks ATTACH/PRAGMA |
| F12/F16 | PEP 701 f-string → SyntaxError <3.12 | TRUE | medium | yes on py3.11 CI (`ci.yml:25` + compileall); prod image py3.12 |
| F13/F17 | `_net_allowed` scheme/normalization/IP checks | LATENT | low | same dead input as F03/F08 |
| F14/F18 | `_generative_answer` no cache / unclosed zip | PARTIAL TRUE | low | one call per run (planner `done` set), one-shot process |
| F19/F20 | dead `_AGENT_MAX_TOOLS`, unused base64 | TRUE | low | cosmetic |

Assumptions recorded: (1) template-string code is judged as "the generated runner's code" — its findings are graded on production reachability, not host-process reachability; (2) the GitHub py3.11 job status at this exact commit was not executed here — the SyntaxError is established from the grammar + `ci.yml:25` + the `compileall` step, not from a CI run; (3) `--clearenv` absence in `_build_l0_bwrap_args` was noticed but belongs to `sandbox_runtime.py` and was not judged.
