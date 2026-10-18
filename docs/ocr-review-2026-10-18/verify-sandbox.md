# Verification: `app/services/firecracker_runtime.py` + `app/services/app_output_proxy.py`

Verified 2026-10-18 against the working tree at `D:/workspace/sandbox` (branch `feat/ss-11-productization`).
Read-only pass — no file in the repo was modified. Findings source: `ocr-findings.json`, filtered to the two paths
(**21 firecracker + 7 proxy = 28 findings**; the 12 headline claims named in the brief are covered as F1–F12 below).

Verdict vocabulary:
- **TRUE** — the code claim holds as written.
- **FALSE** — the claim does not hold (wrong about the code, or wrong about the library/runtime semantics).
- **LATENT** — the code claim holds, but a named precondition is not met in any deployed configuration (dead path / gate off / binary absent).
Every finding additionally gets a *reachability* line, because for these two files reachability, not the code, decides severity.

---

## 0. Reachability ground truth (read this first; every finding below points at it)

### 0.1 The Firecracker adapter is a legacy, dev-only adapter

| Fact | Evidence |
|---|---|
| `firecracker_runtime` is imported by exactly one product caller: `FirecrackerAdapter` | `app/services/sandbox_runtime.py:1195-1198` (`from app.services.firecracker_runtime import firecracker_runtime, VMConfig`) |
| `FirecrackerAdapter` is registered **only behind the legacy gate** | `app/services/sandbox_runtime.py:2136-2144`: `if _settings.SANDBOX_LEGACY_ENABLED or _settings.LEGACY_L2_ENABLED:` → `self._adapters[SandboxLevel.L2.value] = FirecrackerAdapter()` |
| Both gate flags default **false** | `app/core/config.py:313` `LEGACY_L2_ENABLED: bool = False`; `:318` `SANDBOX_LEGACY_ENABLED: bool = False` |
| Production **refuses to boot** with the legacy gate on | `app/core/config.py:537-544` `if self.SANDBOX_LEGACY_ENABLED: raise ValueError("CDS_SANDBOX_LEGACY_ENABLED=true is not permitted in production (SS-13 single-path cutover)…")` |
| Create-session rejects any non-`l2c` level when the gate is off | `app/api/sandbox_sessions.py:296-307` → `CDSError(code="LEGACY_LEVEL_RETIRED", http_status=400)` |
| Exec dispatch to `fc-` containers likewise needs the registered adapter | `app/services/sandbox_runtime.py:2275-2277` `if container_id.startswith("fc-"): … self._adapters[SandboxLevel.L2.value] …` |
| Deploy values: legacy off everywhere except the **dev** compose | `docker-compose.prod.yml:162` `CDS_LEGACY_L2_ENABLED: "false"`; `helm/cds/values.yaml:96` `"false"`; `deploy/e2e-remote/remote_deploy.sh:154-155`, `deploy/local/local_deploy.sh:137-138` both `"false"`; **only** `docker-compose.yml:195` `CDS_SANDBOX_LEGACY_ENABLED: "true"` (dev compose, comment at :192 says prod rejects this switch) |
| The product is L2' (`l2c`) | `app/services/capability_matrix.py:71-76` — `L2 … "deprecated": True`, "SS-10 后弃用（CDS_LEGACY_L2_ENABLED 观察期），由 L2' 承接" |

### 0.2 Even with the gate on, the *real-VM* code never runs here

- `_detect_backend()` (`:137-155`) picks `firecracker` only if `subprocess.run([bin,"--version"])` succeeds **and** `/dev/kvm` is writable (`:167-172`), else `qemu-tcg` (needs qemu **and** `kernel_path` **and** `rootfs_path`, `:151`), else `simulation`.
- The API image installs `bubblewrap` and nothing else VM-related: `Dockerfile.api:22` `ca-certificates curl iptables iproute2 bubblewrap \` (no firecracker, no qemu, no KVM device in prod compose/helm).
- ⇒ In the dev compose and in the remote harness the backend is **`simulation`**, and `execute()` takes the branch at `:568-570` → `_simulate_execute()` → the same hardened bwrap path as the L3/L0 adapters. `_simulation_mode` is then `True` and is honestly disclosed: `FirecrackerAdapter.provision` returns `"is_simulation": self._runtime._simulation_mode` (`app/services/sandbox_runtime.py:1225`), plus a boot `logger.warning("No VM backend available (no firecracker, no /dev/kvm, no usable qemu-tcg); falling back to simulation mode")` (`firecracker_runtime.py:132-135`).
- The project already tracks this: `docs/mock-remediation-plan.md:69` `M-25 | firecracker_runtime.py simulation | 已标注；无 VM 隔离 | — | 环境 | FG-003`, and `:358` "**处置**：保持标注；真 VM 通道属 FG-003".

**Consequence:** every finding inside `_start_firecracker` / `_configure_vm` / `_start_qemu_tcg` / `_qemu_quit` / `_execute_via_ssh` is **LATENT** — those functions are unreachable in every configuration this repo ships. Findings in `_build_sim_bwrap_args` / `_simulate_execute*` / `create_vm` / `destroy_vm` are reachable **only** in dev/test (`CDS_SANDBOX_LEGACY_ENABLED=true`, and even the dev compose pins `CDS_DEV_SANDBOX_LEVEL: "L3"` at `docker-compose.yml:199`).

### 0.3 The mitmproxy output proxy is not wired into the product at all

| Fact | Evidence |
|---|---|
| The only importer of the addon is a launcher nobody calls | `rg "start_proxy\|proxy_launcher"` over the whole repo returns only `app/services/proxy_launcher.py:9,11,29,120` (its own docstring + its own defs). No API router, no `app/main.py`, no compose/k8s/helm entry runs it. |
| `mitmproxy` is **not a dependency** | `rg -n "mitm" requirements.txt` → no match (exit 1). Local `python -c "import mitmproxy"` → `ModuleNotFoundError`. |
| So the addon self-disables | `app_output_proxy.py:30-43` `except ImportError: … _MITMPROXY_AVAILABLE = False`, and `:126-127` `if not _MITMPROXY_AVAILABLE or mhttp is None: return` |
| No deployment starts mitmdump | `rg mitmdump` outside `docs/`/`specs/` hits only `proxy_launcher.py` + `app_output_proxy.py:216` comments |
| No test imports `app_output_proxy` | `rg "app_output_proxy\|CDSOutputInspector"` hits `app/services/{app_output_proxy,proxy_launcher}.py` and `docs/` only |

**Consequence:** all proxy findings are **code-true but product-unreachable** today. They matter for whoever eventually wires the addon (SS-05 §5 "AppOutputProxy in TEE"), and `docs/spec-implementation-analysis.md:536` still records `AppOutputProxyStage (mitmproxy-in-TEE) | ❌ 未实现`.

### 0.4 mitmproxy library semantics used to adjudicate F1 and F4 (upstream source, read today)

- `Message.set_content()` (`mitmproxy/http.py`, docs mirror `docs.mitmproxy.org/stable/api/mitmproxy/http.html`):
  ```python
  ce = self.headers.get("content-encoding")
  try:
      self.raw_content = encoding.encode(value, ce or "identity")
  except ValueError:
      del self.headers["content-encoding"]
      self.raw_content = value
  if "transfer-encoding" in self.headers: pass
  else: self.headers["content-length"] = str(len(self.raw_content))
  ```
  → `set_content()` **re-applies the existing `Content-Encoding` and recomputes `Content-Length`**.
- `Message.get_content(strict=True)` raises `ValueError` when `content-encoding` is invalid → an upstream server can make the addon raise.
- `AddonManager.trigger_event` / `.trigger` wrap every hook call in `safecall()` (`mitmproxy/addonmanager.py`), which catches every `Exception` except `AddonHalt`/`OptionsError`, logs `"Addon error: …"` and **swallows it**; the flow continues with whatever the addon left on the message.
- `Message.stream` docstring: "This attribute must be set in the `requestheaders` or `responseheaders` hook. Setting it in `request` or `response` is already too late, mitmproxy has buffered the message body already."

---

## 1. Findings — `app/services/app_output_proxy.py`

### F1 — DLP review exceptions are unguarded; response leaks unreviewed — **verdict TRUE** (my severity: **High in code / Medium effective**, was *critical*)

**Evidence.** `response()` has no `try` anywhere between the content-type gate and the final `set_content`:
```python
138:        body: bytes = response.get_content()
...
143:        inspector = self._pool.get_or_create(session_id)
...
150:        for offset in range(0, len(body), self._chunk_size):
152:            output_bytes, result = inspector.inspect_chunk(chunk)
...
158:            window = inspector.check_window()
```
`app/services/streaming_inspector.py:196` (`data.decode("utf-8", errors="ignore")`), `:211` (`self._audit_log.append(RedactionEvent(...))`) and mitmproxy's `get_content()` (raises `ValueError` on a bad `content-encoding`, §0.4) are all realistic raisers — the last one is triggerable by the **upstream server**, not by the operator.
Outcome confirmed against upstream: `AddonManager.trigger_event` → `with safecall(): await self.invoke_addon(...)` (`mitmproxy/addonmanager.py`) swallows `Exception` and logs `Addon error: …`, so the flow is forwarded with the **original, un-redacted body** — genuinely fail-open. The partial-state sub-claim is also true: `inspect_chunk` mutates `_total_chars` / `_window_hits` / `_chunk_index` (`streaming_inspector.py:221-224`) before the raise, so the session's inspector is left half-advanced.

**Reachability.** §0.3 — the addon is never instantiated by any shipped path; `mitmproxy` is not installed. Nothing in `app/api/*` or `app/main.py` can raise here today.

**Project rule.** Honest-degradation iron law is about *simulated* paths disclosing via `is_simulation`; this is a different rule — a **security control must fail closed**. Nothing in `docs/` claims it does, so: convention violated (fail-closed posture undefined), no numbered rule (SS-13/MR-xx) violated.

**Impact.** Conditional on the proxy ever being wired: any PII in a response whose review raised is forwarded verbatim, and the failure is only visible in the proxy log. Given the module is unwired, current product impact = 0.

**Minimal fix.** Wrap the body of `response()` in `try/except Exception:` → on error `response.set_content(_BLOCKED_RESPONSE_BODY); response.status_code = 403; logger.exception(...)` (fail closed), and do the `get_content()` decode inside its own `try`.

---

### F2 — Fixed 4 KB chunking misses boundary-straddling PII and silently drops split multibyte chars — **verdict TRUE** (my severity: **High in code / Medium effective**, was *high*)

**Evidence.** The split is byte-precise and the decode is per chunk:
```python
app/services/app_output_proxy.py:49:  _CHUNK_SIZE = 4096  # 4 KB per inspection chunk
app/services/app_output_proxy.py:150: for offset in range(0, len(body), self._chunk_size):
app/services/app_output_proxy.py:151:     chunk = body[offset : offset + self._chunk_size]
```
```python
app/services/streaming_inspector.py:196:  text = data.decode("utf-8", errors="ignore")
```
The PII regexes (`streaming_inspector.py:96-103`: 18-digit ID, 16-19-digit bank card, phone, e-mail) cannot match a token whose digits were cut in two, and a 3-byte UTF-8 char split across a boundary loses its tail bytes in chunk *n* and its head bytes in chunk *n+1* — both discarded by `errors="ignore"`. The corrupted concatenation is then written back because the redaction branch is all-or-nothing:
```python
178:        if all_hits:
179:            redacted_body = b"".join(redacted_chunks)
180:            response.set_content(redacted_body)
```

**Reachability.** §0.3 — same unwired addon. (Nothing else in the repo calls `inspect_chunk` with byte slices; `tests/test_e2e_sandbox.py:307-329` and `tests/test_streaming_inspector.py` exercise whole-string scans.)

**Project rule.** Not SS-13. It defeats the stated control ("4KB chunk-based scanning", module docstring) — a detection gap, i.e. an honesty/quality defect, not a documented-rule violation.

**Impact.** When wired: a data-leak control that misses an ID number straddling a 4 KB edge (trivially reproducible by a sandbox app padding its output), plus observable character loss in any redacted CJK body. The corruption is the more provable half.

**Minimal fix.** Chunk on a UTF-8 lead-byte boundary with a ≥20-byte carry-over tail (`overlap = max(PII max length, 4)`), dedupe hits whose `[start,end)` fall inside the overlap before counting/redacting.

---

### F3 — Content-Type allowlist is fail-open (missing/unknown type ⇒ zero inspection) — **verdict TRUE** (my severity: **Medium**, was *high*)

**Evidence.**
```python
134:        content_type = response.headers.get("content-type", "")
135:        if not _is_text_content(content_type):
136:            return
...
209: def _is_text_content(content_type: str) -> bool:
211:     ct = content_type.split(";")[0].strip().lower()
212:     return any(ct.startswith(prefix) for prefix in _TEXT_PREFIXES)
```
`_TEXT_PREFIXES` (`:193-207`) is a fixed prefix list; `"".startswith(...)` is `False` for every prefix, so a response with **no** `Content-Type` returns at line 136 with zero inspection, as do `application/problem+json`, `application/vnd.*+json`, `application/x-ndjson`, `application/jsonlines`, and any mislabelled `application/octet-stream`.

**Reachability.** §0.3 — unreachable in the shipped product; also note the module's own fail-open fallback at `:126-127` (mitmproxy absent ⇒ inspect nothing).

**Project rule.** Fail-open default for a leak control — same class as F1; no numbered project rule covers it.

**Impact.** Whole-response bypass: the cheapest way for a sandboxed app to exfiltrate is `Content-Type: application/octet-stream` or no header at all. This is the finding with the biggest control-bypass-to-effort ratio, which is why I keep it Medium despite the module being unwired.

**Minimal fix.** Invert the default: inspect unless the type matches an explicit *binary* denylist (`image/`, `video/`, `audio/`, `application/pdf|zip|octet-stream` with a printable-ratio sniff on the first 512 bytes).

---

### F4 — `Content-Encoding` not cleared after rewrite ⇒ client decompression failure — **verdict FALSE** (sub-claim ETag: **TRUE**, severity **Nit**, was *high*)

**Evidence.** The mechanism the finding asserts (plaintext body + `Content-Encoding: gzip`) does not exist. mitmproxy's `set_content` re-encodes with the header that is already there and recomputes `Content-Length` (§0.4, `mitmproxy/http.py` — `self.raw_content = encoding.encode(value, ce or "identity")` then `self.headers["content-length"] = str(len(self.raw_content))`), and only deletes `content-encoding` when the encoding name is unknown. Both rewrite sites therefore stay wire-consistent:
```python
171:            response.set_content(_BLOCKED_RESPONSE_BODY)
...
180:            response.set_content(redacted_body)
```
The claim also mis-states `content-length`: it is *not* left stale.

**Reachability.** n/a (claim invalid); the module itself is unreachable anyway (§0.3).

**Project rule.** None.

**Impact.** None from the asserted failure. Surviving nit: `ETag`/`Last-Modified` are not refreshed after a rewrite, so a client revalidating may see a changed body under an unchanged validator. **Fix (nit):** `response.headers.pop("etag", None)` next to both `set_content` calls.

---

### F5 — `_session_map` keyed by `ip:port`, no TTL/eviction; (claimed) cross-thread race — **verdict TRUE for attribution + unbounded growth, FALSE for the race** (my severity: **Medium**, was *high*)

**Evidence — attribution + leak (TRUE).**
```python
78:        self._session_map: dict[str, str] = {}
89:            return f"{addr[0]}:{addr[1]}"
105:    def _resolve_session_id(self, flow):
110:        if session_id is None:
111:            session_id = f"auto-{key}"
112:            self._session_map[key] = session_id
```
Nothing pops the auto entries — `unbind_session` (`:101-103`) is manual-only, so the dict grows one permanent row per ephemeral client port, and the same key reuses one cumulative inspector from `inspector_pool` (`streaming_inspector.py:291` `self._inspectors: dict[str, StreamingInspector] = {}`; `get_or_create` `:293-307` never evicts; `_audit_log` `:174`/`:211` only ever grows). Because `check_window()` divides by the ever-growing `_total_chars` (`:246 window_chars = max(self._total_chars, 1)`) and `_window_hits` persist per session, port reuse inherits another session's breaker state and audit trail — exactly the mis-attribution described.
**Extra confirmed defect the finding only hints at:** in the only supported launch mode the control plane *cannot* bind at all — `proxy_launcher.start_proxy` spawns a **separate** `mitmdump` subprocess (`proxy_launcher.py:62-97`, `-s <addon path>`), so `bind_session`/`unbind_session` have no caller and no IPC; every flow is therefore `auto-ip:port` forever.

**Evidence — race (FALSE).** There is no second thread touching these dicts: mitmproxy invokes sync hooks on its own event-loop thread (`AddonManager.invoke_addon_sync` / `trigger`), and `bind_session` is unreachable from the API process in that topology (§ table above). `start_proxy_async` (`proxy_launcher.py:120-155`) embeds the master in the same loop. So `get`-then-`set` is not actually racy here.

**Reachability.** §0.3 — unwired addon.

**Project rule.** Session mis-attribution would corrupt audit attribution (the project's single audit funnel + honest attribution), but no numbered rule is breached while the module stays dark.

**Impact.** When wired: cross-session PII-density accounting and unbounded memory in a long-running proxy. **Minimal fix:** key on `flow.client_conn.id` (unique per connection), add an LRU/TTL to `_session_map` and evict the paired inspector via `pool.remove(session_id)`; drop the `Lock` recommendation.

---

### F13 — "Streaming" is full buffering: 3× memory, SSE stalls — **verdict TRUE** (my severity: **Low**, was *medium*)

**Evidence.** The hook is mitmproxy's buffered `response`, the body is read whole, copied into a list, and copied again:
```python
120:    def response(self, flow: mhttp.HTTPFlow) -> None:
138:        body: bytes = response.get_content()
151:            chunk = body[offset : offset + self._chunk_size]
153:            redacted_chunks.append(output_bytes)
179:            redacted_body = b"".join(redacted_chunks)
```
`text/` is in the allowlist (`:194`), so `text/event-stream` is only inspected after the stream ends. Upstream confirms buffering is inherent to this hook, not to this code: `Message.stream` — "must be set in the `requestheaders` or `responseheaders` hook. Setting it in … `response` is already too late" (§0.4). The module docstring's "chunked into 4KB blocks for incremental PII scanning" (`:7`) is therefore misleading rather than broken.

**Reachability.** §0.3.

**Project rule.** No. Docstring/behaviour mismatch only (the same class of honesty nit as F17/F26, but benign).

**Impact.** When wired, a large text export can 3× TEE-proxy memory and long-lived SSE never completes. **Minimal fix:** move to `responseheaders` + `flow.response.stream` callback, or state the buffering contract in the docstring and cap inspected size (e.g. refuse >N MiB with a logged decision).

---

### F24 — Unused `InspectionDecision` import — **verdict TRUE** (severity **Nit**)

**Evidence.** `app/services/app_output_proxy.py:20` imports it; the only other occurrence in the repo is its definition at `app/services/streaming_inspector.py:46` (`class InspectionDecision(str, Enum):`). The block decision uses `window.exceeded` (`app_output_proxy.py:158-160`).
**Reachability.** Static; module irrelevant.
**Rule.** No. **Impact.** Reader misled into thinking a 3-state allow/modify/block policy exists. **Fix.** Delete line 20.

---

## 2. Findings — `app/services/firecracker_runtime.py`

### F6 — `user_id` interpolated into a host path with no validation — **verdict TRUE / LATENT** (my severity: **Medium in code / Low effective**, was *critical*)

**Evidence.**
```python
214:        # Tenant-prefixed workspace: /tmp/cds-firecracker/{user_id}/{vm_id}/
215:        tenant_dir = self._workspace_root / (user_id or "anonymous")
216:        tenant_dir.mkdir(parents=True, exist_ok=True)
217:        workspace = str(tenant_dir / vm_id)
220:        os.makedirs(workspace, exist_ok=True)
```
The `Path.__truediv__` claim is correct Python: `Path("/tmp/cds-firecracker") / "/home/x"` ⇒ `/home/x`; `"../../etc"` escapes. Nothing validates or re-checks containment (`safe_resolve_within` exists at `app/core/path_security.py:32-51` and is not used here), and `destroy_vm` later does `shutil.rmtree(workspace, ignore_errors=True)` (`:963`, `:998`) on that path.

**Reachability.** `create_vm` has exactly one product caller — `app/services/sandbox_runtime.py:1203` `vm = self._runtime.create_vm(session_id, config, user_id=user_id)` — reached via `SandboxRuntime.provision` (`:2204-2217`) behind the legacy gate (§0.1). The four `provision` call sites pass either nothing (`app/api/connectors.py:437-442`, `app/services/contract_fulfillment.py:126-131`, `app/services/data_product_sandbox.py:145-150` → `user_id=""`) or the server-generated UUID: `app/api/sandbox_sessions.py:513` `user_id=str(current_user.id)`. **No HTTP-reachable path lets a caller choose `user_id`.** And even with the gate on, the backend is `simulation` here (§0.2), so the traversal value would still have to be smuggled in by a future caller.

**Project rule.** `app/core/AGENTS.md:13` — "`path_security.py` … use for ANY user-supplied path". Violated in spirit (the parameter is named for a tenant), though today's callers never pass user-controlled text.

**Impact.** Latent cross-tenant directory create/delete primitive: the first caller that forwards a display name / tenant slug gets arbitrary-host `makedirs` + `rmtree`. Fixing it is cheap insurance.
**Minimal fix.** `user_id = re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user_id or "anonymous")` else raise; after `mkdir`, assert `Path(workspace).resolve().is_relative_to(self._workspace_root.resolve())`.

---

### F7 — Firecracker API responses never status-checked ⇒ dead VM reported RUNNING — **verdict TRUE / LATENT** (my severity: **High in code / Low effective**, was *high*)

**Evidence.** Four `PUT`s, all four discard the response:
```python
304:            conn.request("PUT", "/boot-source", json.dumps({
308:                resp = conn.getresponse()
309:                resp.read()
...
314:            conn.request("PUT", "/drives/rootfs", json.dumps({
320:                resp = conn.getresponse()
321:                resp.read()
...
324:        conn.request("PUT", "/machine-config", json.dumps({
328:            resp = conn.getresponse()
329:            resp.read()
...
332:        conn.request("PUT", "/actions", json.dumps({"action_type": "InstanceStart"}))
333:        resp = conn.getresponse()
334:        resp.read()
```
and the caller promotes it unconditionally:
```python
245:                self._start_firecracker(vm)
246:                self._configure_vm(vm)
247:            vm.state = VMState.RUNNING
248:            self._vms[vm_id] = vm
```
The secondary claim is also true: the `except` at `:251-254` only sets `FAILED` and re-raises — no `rmtree`, no socket unlink, no `proc.kill()`, so a failed start leaks the workspace dir, the socket file and the firecracker child.

**Reachability.** LATENT: needs `firecracker` binary **and** `/dev/kvm` (`:142`, `:161-172`), neither of which exists in `Dockerfile.api:22` or any compose/helm file (§0.2). It is also behind the legacy gate (§0.1) and the dev compose pins `CDS_DEV_SANDBOX_LEVEL: "L3"` (`docker-compose.yml:199`).

**Project rule.** Honest degradation: a VM that was never configured is surfaced to the tenant as a RUNNING L2 sandbox, which is exactly "presenting a simulated/non-existent path as real". Violates the iron law *if* the path ever runs.

**Impact.** Latent: with real Firecracker, misconfiguration (missing kernel image, bad memory size) yields a phantom sandbox whose exec silently degrades to host bwrap (F10) — a compounding pair. **Minimal fix.**
```python
if resp.status // 100 != 2:
    raise RuntimeError(f"{path} failed: {resp.status} {resp.read().decode(errors='replace')}")
```
plus a `finally`-style cleanup of `vm.workspace`/`api_socket` and `os.kill(vm.pid, 9)` in the `create_vm` except branch.

---

### F8 — `SECCOMP_FALLBACK_ALLOWED` read fails **open** — **verdict TRUE / LATENT** (my severity: **Low**, was *high*)

**Evidence.**
```python
48:    try:
49:        from app.core.config import get_settings
50:        return bool(get_settings().SECCOMP_FALLBACK_ALLOWED)
51:    except Exception:
52:        return True
```
Docstring at `:42-46` claims parity with `sandbox_runtime`, and the parity is exact — `app/services/sandbox_runtime.py:31-35` is the same `except Exception: return True`. So this is a repo-wide helper pattern, not an isolated slip; the "与注释声明的 fails honestly 语义相反" wording is fair for the `except` branch only.

**Reachability.** Two gates make it effectively dead in production: `get_settings()` is `lru_cached` and warm by the time any exec runs (a raise would have aborted boot), and **production refuses the fail-open value outright** — `app/core/config.py:439-444` `if self.SECCOMP_FALLBACK_ALLOWED: raise ValueError("SECCOMP_FALLBACK_ALLOWED=true is not permitted in production…")`, with `docker-compose.prod.yml:192` `CDS_SECCOMP_FALLBACK_ALLOWED: "false"`. The default is `True` (`config.py:198`) — i.e. dev/test deliberately allow the retry, and `tests/test_pz04_honesty_gates.py:236-249` pins exactly that gate (`assert len(calls) == 2`, `"--seccomp" not in calls[1]`).

**Project rule.** PZ-04/MR-A13 ("S4 closed") requires the gate to exist — it does and is test-pinned. The fail-open `except` is a defect against the fail-closed convention, not against MR-A13's letter.

**Impact.** Near-zero: to exploit it an attacker must make a cached settings lookup raise inside a running process. **Minimal fix.** `except Exception: logger.exception(...); return False` — in both files, to keep the parity the docstring promises.

---

### F9 — Shell string built from env vars with only values quoted; `scp` result unchecked; fixed `/tmp/exec.<ext>` — **verdict TRUE / LATENT (dead code)** (my severity: **Medium in code / none effective**, was *critical*)

**Evidence.**
```python
720:        scp_cmd = ["scp", "-o", "StrictHostKeyChecking=no", code_file, f"root@{vm.ip_address}:/tmp/exec.{ext}"]
721:        subprocess.run(scp_cmd, capture_output=True, timeout=10)
...
730:            env_prefix = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in merged_env.items()) + " "
...
736:            f"{env_prefix}{interpreter} /tmp/exec.{ext}",
```
All three sub-claims are technically right: keys are unquoted and land in a remote `sh -c` string (the whole tail argument is parsed by the guest shell), the `scp` return code is ignored, and `/tmp/exec.<ext>` is a fixed name so a failed copy re-executes the previous session's file. `vm.ip_address` is also interpolated into the same string.

**Reachability — decisive.** `_execute_via_ssh` is unreachable: the only call site is `:596` `if vm.config.network_enabled and vm.ip_address:` and `VMInstance.ip_address` is **never assigned anywhere in the repo** — `rg ip_address app` returns only unrelated audit-log/URL-validation hits (`app/models/audit_log.py:26`, `app/services/agent_tools.py:68`, …), no write to the VM field, and `VMConfig.network_enabled` defaults `False` (`:71`) with no setter. Same for the `scp` branches in `transfer_file:870-873` and `retrieve_file:898-901`.

**Project rule.** Would be a command-injection class defect; today it is dead code. Its real sin is F26's: keeping an unreachable "looks unsafe" path in a security-critical file.

**Impact.** Zero until someone adds TAP networking. **Minimal fix.** Delete `_execute_via_ssh` (preferred, see F26); if it is kept, validate keys with `^[A-Za-z_][A-Za-z0-9_]*$`, check `scp` returncode, and use `uuid`-suffixed remote paths.

---

### F10 — Falls back to host bwrap execution when the guest is unreachable, with no per-execution disclosure — **verdict TRUE** (my severity: **Medium**, was *high*)

**Evidence.** Both serial paths drop to host execution:
```python
741:    def _execute_via_serial(self, vm, code_file, language, ...):
...
754:            agent is configured, keep the request functional by using the same hardened
755:            local bwrap fallback as QEMU TCG instead of returning a placeholder failure.
759:        logger.warning(
760:            "[firecracker] Guest serial execution unavailable for VM %s; using hardened local fallback",
763:        return self._simulate_execute_raw(vm, code, language, session_key=session_key, env_vars=env_vars, timeout=timeout)
```
```python
790:        logger.warning(f"[qemu-tcg] Using simulation fallback for VM {vm.vm_id}")
791:        return self._simulate_execute_raw(...)
```
and the caller's return value is shape-identical to a real VM exec:
```python
616:            return ExecutionResult(
617:                output=result.get("output", ""),
618:                exit_code=result.get("exit_code", -1),
620:                vm_id=vm.vm_id,
```
`ExecutionResult` (`:92-97`) carries no `effective_backend`/`is_simulation`, and `FirecrackerAdapter.execute` reports `"sandbox_level": "L2"` (`app/services/sandbox_runtime.py:1246-1247`). The disclosure that *does* exist is per-provision and backend-derived — `"is_simulation": self._runtime._simulation_mode` (`:1225`) — which is `False` exactly in the case that matters (`backend == "firecracker"`, no guest agent).

**Counter-evidence the finding omits.** The fallback is **intended and test-pinned**: `tests/test_firecracker_runtime.py:114-155 test_firecracker_serial_path_uses_hardened_fallback` sets `runtime._backend = "firecracker"` and asserts `fallback.assert_called_once()` with the same session key/env. `tests/AGENTS.md` forbids "fixing" simulation fallbacks. And it is not silent: `logger.warning` at `:759-762`, plus `docs/mock-remediation-plan.md:69/358` records it as M-25/FG-003 with disposition "保持标注".

**Reachability.** LATENT for the undisclosed case: requires a real firecracker/qemu-tcg backend (§0.2) — no deployment in this repo has one; in `simulation` mode the fallback *is* disclosed via `is_simulation`. Legacy gate additionally required (§0.1).

**Project rule.** SS-13 proper governs `l2c` only (an L2' request with no MicroVM ⇒ 400 `RUNTIME_UNAVAILABLE`, `app/services/sandbox_runtime.py:2166-2176`), so no SS-13 violation. Honest degradation is violated only in the narrow real-backend case: an L2 session running tenant code at L3 strength without a field the caller can read.

**Impact.** Latent isolation downgrade; also the reason F7 is dangerous. **Minimal fix.** Add `effective_backend: str` to `ExecutionResult` and set it from `self._backend`/`"bwrap-host"` in `_simulate_execute_raw`, or gate the fallback behind a config switch exactly like `SECCOMP_FALLBACK_ALLOWED` and return a `RUNTIME_UNAVAILABLE`-style failure when off (would need the existing test updated in the same change).

---

### F11 — Zombie / orphan VM processes — **verdict TRUE / LATENT** (my severity: **Low**, was *high*)

**Evidence.** Both `Popen` handles are dropped; only the pid survives:
```python
271:        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
276:        vm.pid = proc.pid
```
`rg "waitpid|proc\.wait|\.poll\(|Popen" app/services/firecracker_runtime.py` returns **only** `271: subprocess.Popen` and `389: subprocess.Popen` — no reaping anywhere. Termination is signal-only:
```python
984:            if vm.pid:
986:                    os.kill(vm.pid, 15)  # SIGTERM
987:                    time.sleep(0.5)
988:                    os.kill(vm.pid, 9)   # SIGKILL (if still alive)
989:                except ProcessLookupError:
990:                    pass
```
The two raise paths (`:290`, `:410`) likewise leave the spawned child alive. The PID-recycling caveat in the finding is real but negligible; the "no `proc.poll()` during the wait loop" point is accurate and is the useful half (a crashed firecracker is only detected by a 5 s timeout).

**Reachability.** LATENT — a VM backend must exist (§0.2). In `simulation` mode no child is ever spawned by this file, so no zombie can accumulate in any deployment running today.

**Project rule.** No.

**Impact.** Latent fd/zombie growth on a KVM host with a long-lived API process. **Minimal fix.** Keep the `Popen` on `VMInstance`, then `proc.terminate(); proc.wait(timeout=2)` / `proc.kill(); proc.wait()` in `destroy_vm`, and `if proc.poll() is not None: raise` inside both wait loops.

---

### F12 — `destroy_vm` cleanup inside a single `try` — **verdict TRUE / LATENT** (my severity: **Low**, was *high*)

**Evidence.**
```python
969:        try:
971:            if self._backend == "qemu-tcg":
972:                self._qemu_quit(vm)
973:            else:
976:                conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
977:                conn.sock.connect(vm.api_socket)
...
984:            if vm.pid:
993:            for socket_path in (vm.api_socket, f"{vm.workspace}/qmp.sock"):
994:                if os.path.exists(socket_path):
995:                    os.unlink(socket_path)
996:            workspace = Path(vm.workspace)
997:            if workspace.exists():
998:                shutil.rmtree(workspace, ignore_errors=True)
1000:            self._vms.pop(vm.vm_id, None)
1003:            return True
1004:        except Exception as e:
1005:            logger.error(f"Failed to destroy VM {vm.vm_id}: {e}")
1006:            return False
```
A raise at `:977` (the crashed-VM case) skips kill, unlink, `rmtree` **and** the `_vms.pop` — the description is accurate. (Note the simulation branch `:960-967` cleans up correctly.) `_vms` being an unbounded dict on the module-level singleton (`:1064`) is also true.

**Reachability.** LATENT — the whole non-simulation branch needs a VM backend (§0.2).

**Project rule.** No; it does undermine "destroy" semantics for a security sandbox (workspace with tenant data left on host), i.e. the retention/cleanup expectation, but no numbered rule.

**Impact.** Latent: tenant workspace residue + a `RUNNING` entry that keeps being served by `get_vm`/`get_status`. **Minimal fix.** Move `:984-1001` into a `finally:` block with each step individually guarded, and always `self._vms.pop(vm.vm_id, None)`.

---

### F14 — seccomp arguments removed by **value**, not position — **verdict TRUE** (severity **Low**)

**Evidence.** `:671` and `:834` are identical:
```python
cmd_no_seccomp = [c for c in cmd if c != "--seccomp" and c != str(seccomp_fd)]
```
`seccomp_fd` comes from `os.open(f"/proc/self/fd/{_seccomp_bpf_fd}", …)` (`app/services/sandbox_security.py:247`), i.e. a small integer, and `env_vars` values are appended verbatim (`:515-516` `cmd += ["--setenv", k, str(v)]`), so a value of `"3"` is deleted too — which also desynchronises the `--setenv K V` triple. The duplication claim is true as well (`_simulate_execute` `:631-703` vs `_simulate_execute_raw` `:800-852`, and the same filter appears in `sandbox_runtime.py:344`, `:459`, `:853`).
**Reachability.** Executed only when a bwrap exec fails with a seccomp-looking stderr **and** `SECCOMP_FALLBACK_ALLOWED=true`, i.e. dev/test only (prod: `config.py:439-444`, `docker-compose.prod.yml:192`).
**Rule.** No. **Impact.** Corrupted argv and a confusing failure on retry; dev-only. **Fix.** Delete by index — drop the `--seccomp` token and its adjacent value — via one shared helper used by all four call sites.

---

### F15 — Broad `"EINVAL"`/`"Invalid argument"` predicate ⇒ re-exec of user code without seccomp — **verdict TRUE** (severity **Medium in dev / Low prod**)

**Evidence.**
```python
527:    def _seccomp_retry_needed(stderr: str, seccomp_fd: int | None) -> bool:
532:        text = stderr.lower()
533:        return (
534:            "einval" in text
535:            or "invalid argument" in text
536:            or "pr_set_seccomp" in text
537:        )
```
followed by an unconditional second run of the same payload:
```python
832:            if result.returncode != 0 and self._seccomp_retry_needed(result.stderr, seccomp_fd):
833:                if _seccomp_fallback_allowed():
835:                        result = subprocess.run(cmd_no_seccomp, capture_output=True, text=True, timeout=effective_timeout)
```
Two precisions the finding gets directionally right: (a) the predicate is **byte-identical in spirit** to the production L3 adapter (`app/services/sandbox_runtime.py:909-918`, same three substrings, case-insensitive), so this is not a firecracker-only flaw — though the L0 adapter is narrower (`sandbox_runtime.py:340` `"EINVAL" in result.stderr` only); (b) the double-side-effect concern is *strengthened*, not weakened, by a subtlety the finding missed: `bwrap`-setup failures happen **before** the payload runs, but the predicate also matches the **payload's own** stderr — a Python `OSError: [Errno 22] Invalid argument` in tenant code exits non-zero with exactly that text and therefore buys itself a seccomp-less re-run.
**Reachability.** Requires `SECCOMP_FALLBACK_ALLOWED=true`: default `True` (`config.py:198`) and true in dev/remote harness; **production refuses it** (`config.py:439-444`, `docker-compose.prod.yml:192`). Plus the legacy gate for this file (§0.1) — the same code runs for real L3 sessions in dev.
**Rule.** PZ-04/MR-A13's letter is satisfied (gate + honest failure when denied, `:836-847`, pinned by `tests/test_pz04_honesty_gates.py`). The broad predicate plus tenant-influenced trigger undercuts the intent.
**Impact.** Dev/test only: an attacker-controlled stderr string deterministically buys a second, seccomp-less execution of their own code (isolation is still bwrap namespaces; only the syscall filter is dropped) and doubles side effects. **Fix.** Require a seccomp-specific marker (`"pr_set_seccomp" in text or "--seccomp" in text`) and, better, probe capability once (`bwrap --info` / a `PR_SET_SECCOMP` self-test) instead of inferring from stderr; run the probe with a no-op payload so real code never re-runs.

---

### F16 — `/lib` and `/lib64` bound without an existence check while `/bin` is guarded — **verdict TRUE (code) / LATENT (failure), and partly mis-scoped** (severity **Low**)

**Evidence.**
```python
461:            "--ro-bind", "/usr", "/usr",
462:            "--ro-bind", "/lib", "/lib",
463:            "--ro-bind", "/lib64", "/lib64",
464:            *( ["--ro-bind", "/bin", "/bin"] if Path("/bin").exists() else [] ),
```
**But** the identical four lines exist in the other bwrap builders — `app/services/sandbox_security.py:408-411` and `app/services/sandbox_runtime.py:114-117` — so the asymmetry is the repo's convention, not this file's bug, and any fix must land in all three (plus `--ro-bind-try` would be the one-line variant).
**Reachability / accuracy.** The shipped image is `python:3.12-slim` (Debian, `Dockerfile.api:1`) where `/lib64` exists on amd64, and the bwrap-backed suites demonstrably pass on the remote harness — so the "all L2/L2' simulation executions fail" outcome needs an aarch64-musl/Alpine base. The claim's "L2'" is wrong: the production `l2c` path is the Cube node data plane (`app/services/cube_runtime_adapter.py`), which never builds bwrap args.
**Rule.** No. **Impact.** Portability trap for a non-Debian base image; would break dev L3 + legacy L2, not production. **Fix.** `*(["--ro-bind", p, p] if Path(p).exists() else [] for p in ("/usr", "/lib", "/lib64", "/bin"))` in all three helpers.

---

### F17 — `_qemu_quit` speaks HTTP to a QMP socket ⇒ never succeeds — **verdict TRUE** (severity **Low**)

**Evidence.**
```python
1009:        """Send quit command to QEMU via QMP."""
1015:        conn.request("PUT", "/execute", json.dumps({"action_type": "quit"}))
```
QMP is a JSON line protocol over the socket that requires reading the greeting and sending `{"execute":"qmp_capabilities"}` before any command; an `HTTP/1.1` request is not QMP, so the call is a no-op whose failure is swallowed at `:1023-1024`. The fd-leak half is also true (`:1013-1022` has no `try/finally`), and the same pattern repeats in `_configure_vm:297-336`, `pause_vm:915-928`, `resume_vm:936-949`, `destroy_vm:974-981`.
**Reachability.** LATENT — only called from `destroy_vm` when `self._backend == "qemu-tcg"` (`:971-972`), which needs qemu **and** kernel **and** rootfs (`:151`); none of that exists here (§0.2). The SIGTERM at `:986` is the effective teardown, so behaviour is unaffected today.
**Rule.** Honest-degradation: the docstring advertises a protocol the code does not implement (same class as F26's "TAP devices" claim).
**Impact.** None now; a false sense of graceful shutdown once qemu-tcg is used. **Fix.** Replace the body with a raw-socket QMP handshake, or delete the function and rely on SIGTERM (and wrap all unix-socket uses in `contextlib.closing`).

---

### F18 — fd leak in both socket-readiness loops — **verdict TRUE** (severity **Low**)

**Evidence.**
```python
279:        for _ in range(50):
280:            if os.path.exists(api_socket):
281:                try:
282:                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
283:                    sock.connect(api_socket)
284:                    sock.close()
285:                    return
286:                except (ConnectionRefusedError, FileNotFoundError):
287:                    pass
288:            time.sleep(0.1)
```
and verbatim again at `:399-408` (`qmp_socket`). A socket created at `:282` and failing at `:283` is never closed.
**Reachability.** LATENT — VM backends only (§0.2). Bounded by 50 fds per failed start (loop exits after 5 s).
**Rule.** No. **Impact.** Up to ~50 leaked fds per failed VM start on a KVM host. **Fix.** `with socket.socket(...) as s: s.connect(...); return`.

---

### F19 — Session key and all `env_vars` travel in **argv** (`--setenv`) — **verdict TRUE** (severity **Medium**, and it is the only finding in this file on code that actually executes today)

**Evidence.**
```python
509:        # Add environment variables
511:        if session_key:
512:            cmd += ["--setenv", "CDS_SESSION_KEY", session_key]
515:        if env_vars:
516:            for k, v in env_vars.items():
517:                cmd += ["--setenv", k, str(v)]
```
launched by `subprocess.run(cmd, capture_output=True, …)` (`:662`, `:827`). `/proc/<pid>/cmdline` is mode `0444` on Linux, so anything that can see the pid can read the key; it also lands in accounting/core dumps.
**Scope correction.** This is not firecracker-specific — the same construction is used by the reachable adapters: `app/services/sandbox_security.py:487-492` (`CDS_SESSION_KEY`, and worse, `CDS_DEK_HEX` + `CDS_KEY_ID`), `app/services/sandbox_runtime.py:176`, `:1631`.
**Reachability.** Runs today wherever bwrap exec runs (dev compose with the legacy/L3 paths, remote harness). Cross-tenant exposure requires a reader that shares the **host PID namespace**; tenant sandboxes get `--unshare-pid` (`:448`), and one API container per host is the deployment shape, so realistic readers are same-UID host processes — defense-in-depth, not a live cross-tenant leak.
**Rule.** `app/services/cube_runtime_adapter.py:229-230` states "Session/DEK keys travel only via env vars to the sandbox runner (never through argv, logs, or audit metadata)". The comment is about the Cube path, but it documents the intended convention that `--setenv`-in-argv contradicts.
**Impact.** Key material visible in the process table for every bwrap exec; on a shared host that is a tenant-key disclosure. **Fix.** Pass secrets through the child's `env=` (mode `0400` `/proc/<pid>/environ`) and use `--setenv K V` only for non-secret vars, or feed them via a `--` stdin/`--argv0`-free channel; at minimum strip `CDS_SESSION_KEY`/`CDS_DEK_HEX` from argv in all three helpers.

---

### F20 — `_check_firecracker_available` catches too narrow an exception set, at import time — **verdict TRUE / LATENT** (severity **Low**, blast radius: whole app)

**Evidence.**
```python
156:    def _check_firecracker_available(self) -> bool:
160:            result = subprocess.run([self._firecracker_bin, "--version"], capture_output=True, text=True, timeout=5)
164:        except (FileNotFoundError, subprocess.TimeoutExpired):
165:            return False
```
`PermissionError` (present-but-not-executable), `IsADirectoryError`, and any `OSError` therefore escape. And it does run at import: `:1064 firecracker_runtime = FirecrackerRuntime()` → `__init__` `:130 self._backend = self._detect_backend()`. `__init__` also does `self._workspace_root.mkdir(...)` (`:125`) outside any guard.
**Reachability.** LATENT: the image has no `firecracker` binary at all (§0.2), so the `FileNotFoundError` branch is what executes; the module is imported today (the adapter's `__init__` imports it lazily, `sandbox_runtime.py:1196`, and tests import it directly). The failure mode requires a deliberately mis-provisioned host.
**Rule.** Honest degradation: the module docstring (`:1-8`) promises "Falls back to simulation mode when Firecracker is not installed", and this path breaks that promise hard.
**Impact.** Import-time crash of the whole app from one bad file mode. **Fix.** `except OSError as e: logger.warning(...); return False`, and wrap the singleton construction so a constructor failure degrades to `backend="simulation"`.

---

### F21 — `qemu-system-aarch64` candidate with hard-coded `q35`/`qemu64`; conflicting serial flags; inert memory object — **verdict TRUE** (severity **Low**)

**Evidence.**
```python
346:        for candidate in ("qemu-system-x86_64", "qemu-system-aarch64"):
...
366:            "-machine", "type=q35,accel=tcg",
367:            "-cpu", "qemu64",
...
370:            "-nographic",
371:            "-no-reboot",
372:            "-nodefaults",
373:            "-serial", "mon:stdio",
...
385:        cmd += [
386:            "-object", f"memory-backend-ram,id=mem0,size={vm.config.mem_size_mb}M",
387:        ]
```
An aarch64 binary rejects `q35`/`qemu64`; `-nodefaults`/`-nographic` plus `-serial mon:stdio` conflict; the `memory-backend-ram` object is never referenced by `-machine memory-backend=mem0`, so it is inert (`-m` at `:369` does the real work); and stdout/stderr go to `DEVNULL` (`:391-392`), so the only symptom is `:410 "QEMU TCG socket not ready after 5s"`. The duplicate detection loop also repeats `_check_qemu_available` (`:174-186`).
**Reachability.** LATENT — qemu-tcg requires qemu + kernel + rootfs (§0.2); x86-64 only deployments would never hit the aarch64 arm anyway.
**Rule.** Honest degradation (an unusable "VM backend" reported as a timeout) — latent.
**Impact.** Debuggability on a non-x86 host. **Fix.** Derive `-machine`/`-cpu` from the binary name, drop `-object memory-backend-ram` (or wire it), pick `-nographic` **or** `-serial`, and capture stderr into `vm.workspace/qemu.log` instead of `DEVNULL`.

---

### F22 — `transfer_file` / `retrieve_file`: unguarded `copy2`, name-only collision, always `return True` — **verdict TRUE** (severity **Low**)

**Evidence.**
```python
865:        if self._simulation_mode:
866:            dest = Path(vm.workspace) / "input" / Path(vm_path).name
867:            shutil.copy2(local_path, dest)
868:            return True
```
and the same shape at `:876-878`, `:891-896`, `:903-907`. So: `copy2` can raise `FileNotFoundError`/`PermissionError`/`IsADirectoryError` out of a `-> bool` API; two different `vm_path`s with the same basename collide; `vm_path` of `".."`/`"/"`/`""` yields an empty `.name` making `dest` the directory itself; and `True` is returned regardless. The `retrieve_file` `src.exists()` + `copy2` TOCTOU is likewise real.
**Reachability.** `transfer_file` has exactly one product call, with a constant path: `app/services/sandbox_runtime.py:1206` `self._runtime.transfer_file(vm, data_path, f"/workspace/input/data")` (guarded by `Path(data_path).exists()` at `:1205`). `retrieve_file` has **no product caller at all** (`rg` hits: definition + `tests/test_firecracker_runtime.py:167-191`). Both additionally sit behind the legacy gate (§0.1).
**Rule.** No (a `-> bool` contract violation, not a policy rule).
**Impact.** Minimal today: an exception from `provision` is caught at `sandbox_runtime.py:1227-1228` and surfaces as a failed session. **Fix.** Validate `vm_path` with `safe_resolve_within(Path(vm.workspace)/"input", vm_path)`, wrap in `try/except OSError: logger.exception(...); return False`.

---

### F23 — `_detect_backend` validates kernel/rootfs for qemu but not for firecracker — **verdict TRUE / LATENT** (severity **Low–Medium**, pairs with F7)

**Evidence.**
```python
142:        if self._check_firecracker_available() and self._check_kvm_available():
143:            return "firecracker"
145:        # 2. Try QEMU TCG (no KVM required)
151:            if self._check_qemu_available() and (self._kernel_path or ...) and (self._rootfs_path or ...):
```
`VMConfig.kernel_path`/`rootfs_path` default to `""` (`:73-74`), and `_configure_vm` then skips both PUTs (`:303`, `:313`) while still sending `InstanceStart` (`:332`) — whose reply is discarded (F7).
**Reachability.** LATENT — needs the firecracker binary + `/dev/kvm` (§0.2) plus the legacy gate (§0.1).
**Rule.** Honest degradation vs the module docstring `:6-7` ("Falls back to simulation mode when Firecracker is not installed") — the backend probe answers "installed", not "usable".
**Impact.** A "RUNNING" VM that cannot boot; combined with F7/F10 the tenant's code ends up on the host. **Fix.** Add `and os.path.isfile(self._kernel_path) and os.path.isfile(self._rootfs_path)` to the `:142` condition.

---

### F25 — Dead parameters `isolate_network` / `dns_proxy_port` — **verdict TRUE** (severity **Nit**)

**Evidence.** Declared at `:416-417` (`isolate_network: bool = True`, `dns_proxy_port: int | None = None`); the only two call sites pass neither (`:652-658`, `:817-823`), so `--unshare-net` is always on (`:441-444`) and the DNS-proxy branch is unreachable:
```python
484:        if dns_proxy_port is not None and not isolate_network:
```
`workspace / ".resolv.conf"` written there is never bound into the bwrap tree (`:452-455` binds `input`/`output`/`tmp` only).
**Reachability.** Static. **Rule.** No — but "network whitelist supported" is the kind of surface that misleads auditors, which is the finding's point. **Fix.** Delete both parameters and `:484-486`, or thread them from callers.

---

### F26 — `ip_address` is never populated ⇒ SSH/scp branches unreachable; `hashlib` unused — **verdict TRUE** (severity **Nit**, but it is the load-bearing fact for F9)

**Evidence.** `VMInstance.ip_address: str | None = None` (`:85`) and `VMConfig.network_enabled: bool = False` (`:71`); the only reads are `:596`, `:870`, `:898`, all guarded by `vm.config.network_enabled and vm.ip_address`, and `rg "ip_address" app` finds **no assignment** to either field anywhere. `import hashlib` (`:23`) has no other occurrence in the file. The docstring at `:1-8` claims "Network isolation via TAP devices", which this file does not implement.
**Reachability.** Static; makes `_execute_via_ssh` (`:705-739`) and both scp branches dead.
**Rule.** Honest-degradation/honest-documentation: a security file advertising an isolation mechanism that does not exist.
**Impact.** Dead attack surface + misleading docstring. **Fix.** Delete `_execute_via_ssh`, the two scp branches, `ip_address`/`network_enabled` (or implement TAP provisioning and back-fill `ip_address`), and drop `import hashlib`.

---

### F27 — f-string logging — **verdict TRUE** (severity **Nit**)

**Evidence.** `:132-135`, `:238`, `:249`, `:790`, `:966`, `:1002`, `:927` e.g.
```python
966:            logger.info(f"[sim] Destroyed VM {vm.vm_id}")
```
while the same file uses lazy `%s` elsewhere (`:675-677`, `:759-762`, `:837-839`).
**Rule.** Style consistency only. **Impact.** Eager formatting at all levels. **Fix.** `logger.info("[sim] Destroyed VM %s", vm.vm_id)`.

---

### F28 — `_simulate_execute` duplicates `_simulate_execute_raw` — **verdict TRUE** (severity **Nit**)

**Evidence.** `:631-703` and `:800-852` repeat write-code → `_build_sim_bwrap_args` → `subprocess.run(pass_fds=…)` → `close_seccomp_fd` → seccomp retry → assemble result; only the return type differs, and they already drifted (`_simulate_execute` computes `duration_ms` (`:688-693`), `_simulate_execute_raw` does not (`:848`)). The seccomp-fallback edit has to be made twice (`:669-687`, `:832-847`) — exactly the risk MR-A13 ran into (`docs/mock-remediation-plan.md:274`).
**Rule.** No. **Impact.** Maintenance hazard on a security path. **Fix.** `_simulate_execute` = time `self._simulate_execute_raw(...)` and wrap into `ExecutionResult`.

---

## 3. Scoreboard

| # | Claim (short) | Verdict | My severity | Reachability gate |
|---|---|---|---|---|
| F2 | 4 KB chunk boundary PII miss + byte loss | TRUE | High→Medium | proxy unwired (§0.3) |
| F3 | Content-Type allowlist fail-open | TRUE | Medium | proxy unwired |
| F1 | Review exception ⇒ unredacted passthrough | TRUE | High→Medium | proxy unwired; fail-open confirmed via mitmproxy `safecall` |
| F19 | Session key in argv (`--setenv`) | TRUE | Medium | runs today (dev/remote bwrap); repo-wide pattern |
| F5 | ip:port session key, no TTL (race: FALSE) | TRUE / partially FALSE | Medium | proxy unwired + `bind_session` unreachable by design |
| F7 | Firecracker PUTs unchecked ⇒ phantom VM | TRUE | High→Low | needs firecracker + /dev/kvm |
| F10 | Guest unreachable ⇒ host bwrap, undisclosed | TRUE | Medium | needs real VM backend; sim case disclosed; test-pinned |
| F6 | `user_id` in host path, unvalidated | TRUE | Medium→Low | callers pass UUID/empty only |
| F9 | `--setenv` key unquoted in ssh cmd; scp unchecked | TRUE | Medium→none | `_execute_via_ssh` dead (F26) |
| F15 | Broad seccomp predicate ⇒ seccomp-less re-exec | TRUE | Medium (dev) | `SECCOMP_FALLBACK_ALLOWED`; prod refuses true |
| F12 | destroy_vm cleanup in one try | TRUE | Low | needs real VM backend |
| F11 | Zombie/orphan children | TRUE | Low | needs real VM backend |
| F23 | Backend probe ignores kernel/rootfs (fc branch) | TRUE | Low–Med | needs firecracker + /dev/kvm |
| F16 | `/lib64` bound unguarded | TRUE (repo-wide, x86 image unaffected) | Low | portability only; `l2c` claim wrong |
| F17 | QMP over HTTP never works | TRUE | Low | qemu-tcg backend only |
| F18 | fd leak in readiness loops | TRUE | Low | needs real VM backend |
| F20 | Narrow except at import-time singleton | TRUE | Low | misconfigured host only |
| F21 | aarch64 vs q35/qemu64 etc. | TRUE | Low | qemu-tcg on aarch64 only |
| F22 | transfer/retrieve_file `-> bool` lies | TRUE | Low | one constant call site; retrieve unused |
| F24 | Dead `InspectionDecision` import | TRUE | Nit | static |
| F25 | Dead `isolate_network`/`dns_proxy_port` | TRUE | Nit | static |
| F26 | `ip_address` never set ⇒ ssh dead; `hashlib` dead | TRUE | Nit | static |
| F27 | f-string logging | TRUE | Nit | static |
| F28 | Duplicated simulate implementations | TRUE | Nit | static |
| F4 | Content-Encoding ⇒ client decompression error | **FALSE** | (Nit: ETag) | mitmproxy re-encodes + fixes Content-Length |
| F13 | Full buffering, 3× memory, SSE stall | TRUE | Low | proxy unwired; mitmproxy-normal for `response` |

**Headline.** No finding in either file is reachable as a production defect today: the Firecracker adapter is a deprecated (`capability_matrix.py:71-76`), legacy-gated (`sandbox_runtime.py:2137`, prod-rejected `config.py:537`) adapter whose real-VM code needs hardware absent from every image/deploy file, and the mitmproxy output proxy is not wired into anything and its dependency is not even installed. The honest top-tier items are (1) the three proxy chunking/allowlist/exception gaps that will ship as a **fail-open DLP control** the moment someone wires the addon, (2) `--setenv` key exposure in argv — the one pattern here that already runs, and runs in the production L3/L0 helpers too, and (3) `user_id` path interpolation, which is currently harmless only because every caller happens to pass a UUID. The single outright false claim is F4 (mitmproxy re-encodes on `set_content`).
