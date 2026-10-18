# Verification report — claims-runtime.md (37 claims, 5 modules)

Verified against `D:/workspace/sandbox` @ working tree (branch `feat/ss-11-productization`).
Every verdict below cites `file:line` that was read, and behavioural claims were **executed** with
`PYTHONPATH=/d/workspace/sandbox .venv-gate/Scripts/python.exe` (probe scripts:
`probe_runtime.py`, `probe_runtime2.py`, `probe_runtime3.py` in this temp dir — read-only against the repo,
`PYTHONDONTWRITEBYTECODE=1`). No file in `D:/workspace/sandbox` was modified.

Iron rule applied throughout: *honest degradation* — a simulated/failed capability must be **disclosed**, never
reported as available.

---

## Reachability map

| Module | `rev_imports3.py` direct importers | main.py reaches it? | dynamic-import check (`importlib` / `__import__`) | Production entry point that actually executes it | Grade |
|---|---|---|---|---|---|
| `app.services.vision_multimodal_runtime` | `NONE` | **False** | none — repo-wide grep hits only `tests/test_vision_multimodal_runtime.py:5`, `app/services/AGENTS.md:36`, `docs/mock-remediation-plan.md:35,297`, `specs/SS-gap-analysis.md` | **none**. `docs/mock-remediation-plan.md:297`: "全模拟（字节标记"变换"、伪造 loss/accuracy、伪水印）+ **无 API 接线**（训练 API 只接 `llm_sft_runtime`)" | **LATENT** (all claims) |
| `app.services.trust_evaluator` | `app.services.cross_space_policy`, `app.services.federation_connector` | **True** | none | `evaluate()` **live**: `app/services/federation_connector.py:559` ← `app/api/federation.py:297` (route) → score gates the federated request at `:565`. `cross_space_policy.py:218` wrapper `evaluate_trust_score` has **zero** prod callers. `evaluate_provider` (`:153`) and `can_access` (`:189`): **zero callers repo-wide** (only `tests/test_trust_evaluator.py`) | **LIVE** for `evaluate`, LATENT for `evaluate_provider` / `can_access` |
| `app.services.task_circuit_breaker` | `NONE` | **False** | none — only `tests/test_task_circuit_breaker.py:5` | **none** (singleton `:149` is imported by no app module) | **LATENT** (all claims) |
| `app.services.tee_capability` | `app.api.monitoring`, `app.services.sandbox_runtime` | **True** | lazy in-function import is the real path: `app/services/sandbox_runtime.py:1289` | **two live sinks**: (1) `GET /api/v1/monitoring/security-posture` — `app/main.py:333` → `app/api/monitoring.py:61-97`; (2) TEE provisioning decision — `app/services/sandbox_runtime.py:1314-1316` (`TEEAdapter.provision`) | **LIVE** (all claims) |
| `app.services.clickhouse_schema` | `NONE` | **False** | none — only `tests/test_clickhouse_schema.py`, `tests/test_gateway.py:134-151`, `tests/test_p0_security_gaps.py:259` | **none, and not at boot.** `scripts/clickhouse-init.sql` is a *static* file mounted into the ClickHouse container's initdb dir (`docker-compose.middleware.yml:130`, `docker-compose.prod.yml:40`, `k8s/clickhouse.yaml:60` + `k8s/configmaps.yaml:18`); it never calls this module, and `app/main.py` contains no occurrence of "clickhouse". `generate_create_sql` is executed only by tests | **LATENT** (all claims) |

**Reachability finding not in the claims file (worth the parent's attention).**
`gateway_metering` and `sandbox_activity_events` are **written by live code and read by live endpoints**
(`app/services/gateway_service.py:217` INSERT, `app/api/gateway.py:364` SELECT;
`app/services/sandbox_audit.py:259,844` INSERT, `app/api/sandbox_sessions.py:1543` SELECT), yet **no DDL for
either table exists anywhere in the repository** — `scripts/clickhouse-init.sql` creates only 5 tables
(`audit_events`, `policy_decisions`, `cdc_events`, `data_product_versions`, `certificate_events`; verified by
grep of `CREATE TABLE`/`ENGINE`), and helm/k8s/deploy carry none. `clickhouse_schema.py` *defines* both tables
but nothing executes its output. Net effect: metering/sandbox-activity inserts fail into a swallowed warning
(`gateway_service.py:238` `logger.warning("[gateway] Metering write failed: %s")`, and `app/api/gateway.py:375-376`
`except Exception: pass`) and the endpoint silently reports zeros. That is a second, independent honest-degradation
violation sitting one layer below claims [1]/[12]/[13].

---

### [1] `gateway_metering` uses `SummingMergeTree()` with a per-request-unique `timestamp` as first ORDER BY key
**Verdict: TRUE·LATENT** (`clickhouse_schema.py:312` `engine="SummingMergeTree()"`, `:314`
`order_by="(timestamp, contract_id, app_id, product_id, operation)"`, `:294` `ColumnDef("timestamp", "DateTime64(3, 'UTC')")`)

The ClickHouse semantics in the claim are correct: SummingMergeTree only sums rows whose **full** sort key is
identical during a merge, and `DateTime64(3)` millisecond timestamps make that essentially never happen, so the
table degrades to plain MergeTree; conversely a same-millisecond duplicate collapses and sums non-key numerics
(`rows_returned`, `bytes_returned`, `duration_ms`, and `status_code UInt16` → 400+200=600), while non-numeric
non-key columns (`request_id`, `consumer_id`) keep an arbitrary merged row.

Why LATENT: the DDL never reaches a server. Executed `generate_create_sql("gateway_metering")` produces the
SummingMergeTree statement, but `generate_create_sql` is called only from
`tests/test_gateway.py:149` and `tests/test_pz04`-style schema tests — and (per the reachability finding) the
deployed table has no DDL at all, so the *actual* production symptom is "metering never lands", not "metering
is aggregated wrongly".

Fix: make `scripts/clickhouse-schema`-authority real (emit this module's DDL into the init SQL / run it at boot),
and when doing so switch the detail table to `MergeTree()` with `ORDER BY (contract_id, app_id, product_id,
operation, timestamp)` plus a `SummingMergeTree` MV keyed on `toStartOfMinute(timestamp)`, with `status_code`
moved to an `AggregatingMergeTree` aggregate-function column.

---

### [2] Circuit breaker: failure-window decay is dead code (`now - stats.last_failure_time` always 0)
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:97-105`)

```
97:        now = time.time()
98:        stats.last_failure_time = now        # <-- assigned BEFORE the window test
102:        if now - stats.last_failure_time > self.config.failure_window_seconds:   # == 0
103:            stats.failure_count = 0            # unreachable
105:        stats.failure_count += 1
```

Executed proof: `CircuitConfig(failure_threshold=3, failure_window_seconds=0)` — a window of **zero** must decay
every failure — still reaches `state=open, failure_count=3`. With `failure_window_seconds=1` and no sleep between
records the same happens; `failure_count` never resets. The consequence in the claim is exact: failures accumulate
for the process lifetime of the singleton (`:149`), so failures days apart, with successes in between, still trip
the breaker.

LATENT only because no production module imports this breaker (see reachability map) — it is not "wrong thread
model", it is simply unwired. Severity is exactly `critical` for whatever wires it next.

Fix: exactly as suggested — capture `prev_failure_time = stats.last_failure_time` before overwriting, then test
`(now - prev_failure_time) > window`.

---

### [3] `record_success` never clears `failure_count` in CLOSED
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:82-92`; reset exists only inside `if stats.state == HALF_OPEN:` at `:88-91`)

Executed proof with `failure_threshold=3`: 2×`record_failure` → 3×`record_success` → `failure_count` is still **2**,
state `closed`; one further failure → **`state=open`** (total_failures=3). So a session that failed twice, then ran
hundreds of successful tasks, is opened by the third-ever failure. Combined with [2] the counter has **no** decay
path at all — that is the fail-*closed*/flaky-availability direction (availability loss for the tenant, not an
attacker gain), which is the honest way to characterise it.

Fix: clear `stats.failure_count = 0` on success in the CLOSED state too (standard closed-window semantics), or at
minimum subtract elapsed-window credit.

---

### [4] HALF_OPEN has no single-probe throttle and one success closes the circuit
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:77-78` unconditional `return True`; `:69-71` OPEN→HALF_OPEN transition; `:88-91` success closes)

Executed proof (`failure_threshold=2, recovery_timeout_seconds=0`): after opening, `can_execute()` called 10×
returned `[True]*10` while `state=half_open` — i.e. half-open is full-pass, exactly as claimed. Then a **single**
`record_success` flipped state to `closed` with `failure_count=0`; the remaining 9 "probes" then re-opened the
circuit and `circuit_opened_count` reached **9** (each re-entry re-counts). So the "one lucky probe declares
recovery while the rest fail" scenario is real and reproducible without threads (sequential asyncio tasks hit it).

The claim's extra half-open-timeout point is also supported: nothing times HALF_OPEN out; if a probe never calls
`record_*` (task killed, exception path that skips it), the state stays `HALF_OPEN` and `:77-78` grants access
forever — that is a silent **fail-open** window in a component whose whole job is to refuse.

Fix: add `half_open_inflight` (bounded by `half_open_max_attempts`), decrement in `record_success`/`record_failure`,
require N consecutive successes before CLOSED, and add a `half_open_timeout_seconds` that returns the state to OPEN.

---

### [5] Shared mutable state with no concurrency protection
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:8-11` imports only `time/logging/dataclasses/enum` — no `threading`;
`:55-57` check-then-act; `:69-71` and `:102-115` non-compound state transitions; `:149` process-lifetime singleton)

Executed inspection: `"import threading" in source == False`, `"Lock(" in source == False`.

Two corrections to the claim's framing, both in the direction of *lower* present risk:
- "由于全局单例必然被跨请求/跨线程共享" — **not today**: zero production importers (reachability map).
- Under a single asyncio event loop the check-then-act at `:55-57` and the OPEN→HALF_OPEN transition contain no
  `await`, so they are effectively atomic; the race needs `run_in_threadpool` / multiple worker threads / a
  background monitor thread calling the singleton. Given this project runs sync service singletons from FastAPI
  handlers (many sync `def` routes → threadpool), the race is a real property of the class once wired, so the fix
  is still the right one.

Fix: one `threading.RLock` around every method that touches `_sessions` or a `CircuitStats` field (a `RLock`,
because `can_execute` calls `_get_stats`).

---

### [6] TEE device detection too weak — "device node exists" reported as "hardware TEE available"
**Verdict: TRUE·LIVE** (`tee_capability.py:76-78` — `if Path(device).exists(): evidence.append(...)`;
`/dev/sev` under `sev_snp` at `:33`, `/dev/teec` under `itrustee` at `:41`)

Executed proof: `TEECapabilityDetector._detect_provider({"devices": (<a plain regular file>,), "commands": ()})`
returned `['device:...\\sev-guest']` for a file with `st_mode=0o100666`, `stat.S_ISCHR == False`. So a
non-char-device, permission-irrelevant path is accepted as hardware evidence, and `detect()` turns any evidence
into `hardware_available=True` (`:59-65`).

Live sinks and what the false positive buys:
1. `app/api/monitoring.py:62,71-77` — `hardware_available and hardware_hooks_configured` publishes
   `tee_status = "verified"` (or `"configured"`) on `GET /api/v1/monitoring/security-posture`, i.e. the endpoint
   the release-gate/ops story reads as "CPU TEE verified". That is a **forged security capability claim** — the
   exact thing 禁伪造 forbids — and it is *worse* than honest degradation because it also flips `tee_gate` to
   `pass`/`conditional` and `tee_risk` to `ok`/`info`.
2. `app/services/sandbox_runtime.py:1314-1316` — `capability.hardware_available and settings.TEE_HARDWARE_EXEC_CMD`
   routes to `_provision_hardware`. Mitigation that limits blast radius (and should be stated): if provisioning then
   fails the adapter returns `status=FAILED` with the error (`:1318-1326`), and with
   `TEE_ALLOW_SOFTWARE_FALLBACK=false` it refuses rather than degrading (`:1328-1337`). So a false positive does
   **not** make code silently run un-isolated; the gain is a false *claim* plus denial-of-service-shaped behaviour
   (hardware path attempted / fallback skipped) when the operator disabled fallback.

The two semantic sub-points (`/dev/sev` is the host-side PSP node that appears once the ccp driver loads, `/dev/teec`
is a generic GP-TEE client node) are not machine-checkable from this repo, but they are consistent with what the
code does: provider attribution is assigned purely from presence of one path (`:76-78` + `:60-65`) with **no**
CPUID/`/proc/cpuinfo`/ACPI table/attestation-report corroboration, so mis-attribution is possible by construction.

Fix: require `stat.S_ISCHR(st.st_mode) and os.access(path, R_OK|W_OK)`, and make a *verifiable* signal a necessary
condition (CPUID flag / `/sys` firmware table / one real attestation-report generation) before
`hardware_available=True`; otherwise return `hardware_available=False` with the weak signal kept only in `evidence`.

---

### [7] `evaluate_provider` deducts for violations twice (15 pts here, then 10 pts/violation again in `evaluate`)
**Verdict: TRUE·LATENT** (`trust_evaluator.py:179` `compliance = max(0, 100 - violations * 15)` then `:181-186` passes
`violations=violations` into `evaluate()`, which applies `:95-96` `penalty = min(violations * 10, 50)`)

Executed proof (`total_products=10, avg_quality=80`):

| violations | compliance returned | score | level |
|---|---|---|---|
| 1 | 75 | 71 | gold |
| 2 | 50 | 64 | silver |
| 3 | 25 | 56 | silver |
| 4 | **0** | **49** | **new** |
| 5 / 10 | 0 | 49 | new |

Exactly the arithmetic in the claim (100−60=40, then −40 → 0), and every provider with ≥4 violations is pinned to
`NEW` (below the SILVER threshold 50) regardless of quality/reputation. LATENT: `evaluate_provider` has **zero**
callers outside `tests/test_trust_evaluator.py`. It matters because trust levels feed access decisions
(`can_access`, and the federation score gate), so a wired-up version systematically under-rates providers.

Fix: keep one deduction site — either pass `violations=0` at `:185` (self-scored), or leave `compliance_score=100`
and let `evaluate()` own the penalty.

---

### [8] `can_access` fails open on unrecognized / missing `required_level` (and claimed str-Enum hash bypass)
**Verdict: TRUE·LATENT for the fail-open half — FALSE for the "str Enum hash bypass" half**
(`trust_evaluator.py:209` `return level_order.get(trust_level, 0) >= level_order.get(required_level, 0)`)

Part 1 — **real**. Executed: `can_access(TrustLevel.NEW, None) == True`;
`can_access(TrustLevel.NEW, "BOGUS") == True`; `level_order.get(None, 0) == 0`. A missing or misspelled
required level in configuration means "no requirement" → every caller is admitted. Under the iron rule that is a
fail-open authorization primitive. LATENT because `can_access` has zero callers repo-wide (only
`tests/test_trust_evaluator.py:175-183`), so no live endpoint is currently bypassable.

Part 2 — **disproven by execution**. `TrustLevel` is `class TrustLevel(str, Enum)` (`:22`), and its MRO is
`['TrustLevel', 'str', 'Enum', 'object']` — `str.__hash__` is found **before** `Enum.__hash__`, so member hashes
are value hashes:

```
hash(TrustLevel.GOLD) = 3661336549452138353
hash('gold')          = 3661336549452138353     # identical
hash('GOLD')          = -8011484862290729445    # different
{TrustLevel.GOLD: 2}.get("gold")                -> 2      # NOT a miss
level_order.get("new", 0) -> 0 ; level_order.get("platinum", 0) -> 3   # both resolve
can_access("new", "platinum")                   -> False
can_access(TrustLevel.NEW, "platinum")          -> False
can_access(TrustLevel.NEW, TrustLevel.PLATINUM) -> False
```

So plain strings deserialised from JSON/DB behave correctly; the claimed "NEW 也能访问 PLATINUM 资源" bypass via
`Enum.__hash__` (name-based) does **not** exist here — that analysis would apply to a bare `Enum`, not to a
`str`-mixin enum.

Fix (keep, for the true half): normalize with `TrustLevel(x)` and return `False` (with a warning) on
`ValueError/TypeError` instead of defaulting to 0 — fail closed. Do not carry the hash-bypass narrative.

---

### [9] `FaceBlurStep.apply` is a no-op — appends a text marker, never blurs pixels
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:170-172`, marker helper `:201-206`)

Executed proof with a JPEG-headed byte stream: output is byte-identical over the whole input
(`out[:len(jpeg)] == jpeg → True`) with only `b'|cds_step=face_blur:gaussian_k51'` appended (27 → 59 bytes).
The file header advertises "Face detection + Gaussian blur" (`:15`) and `RedactionRules.blur_method`
(`:86`) reads like a real kernel parameter. Nothing discloses the absence: `TrainingResult` (`:126-137`) has no
`redaction_applied`/`is_simulation` field, and `error` stays `None`.

Why this is not an excluded "intentional simulation switch": it is *not* gated or flagged by
`is_simulation`/`ALLOW_SIMULATION`. It *is* recorded in the mock registry — `docs/mock-remediation-plan.md:35,297`
("字节标记"变换"…伪水印 + 无 API 接线") — but `specs/SS-gap-analysis.md:32` (G-006) marks the pass-through gap as
"已修复" on the grounds that steps "改写 payload 并写入可审计处理标记", i.e. the repo's own ledger calls a marker
append a redaction fix. LATENT only because the module has no API wiring.

Fix: implement pixel redaction, or make the absence loud — raise `NotImplementedError` / set
`redaction_applied=False, is_simulation=True` on the result and have `main.py` surface it at boot like the other
fallbacks (T12).

---

### [10] DICOM strip regex cannot touch a real DICOM payload (claims file said `located=false` — it IS locatable)
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:182`
`pattern = rb"(?i)(?:^|[;\n\r])" + re.escape(tag) + rb"\s*=\s*[^;\n\r]*"`; loop `:181`; marker `:184`)

Executed proof against a real Part-10 explicit-VR-le element for `PatientName (0010,0010)`:
`b'\x10\x00\x10\x01PN\x0e\x00ZHANG^SAN '` → after `DICOMStripStep().apply(...)` the patient name is **still present**
(`b"ZHANG^SAN" in out == True`). The text form does work:
`b'Study;PatientName=ZHANG^SAN;PatientID=12345'` → `b'Study|cds_step=dicom_tags_stripped'` — i.e. the step only
redacts the synthetic `k=v;` payloads that this module's own `load()` fabricates (`:235-241`), while returning a
`dicom_tags_stripped` marker that reads as "PHI removed".

Secondary point also confirmed: the 4 patterns are re-`re.escape`d and re-compiled on every call (`:181-183`),
inside the per-image pipeline loop.

Fix: reject non-textual payloads on `DICOM_STRIP` (magic-byte sniff for the DICM preamble / explicit-transfer
tag pairs) and require a real parser (pydicom), reporting `redaction_applied=False` otherwise; hoist the compiled
patterns to class-level constants.

---

### [11] `_inject_watermark` writes no watermark yet returns `True`
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:418-421` and the identical
`:510-513`)

Executed: `run_training("sess-demo", VisionTrainingConfig(num_epochs=1), ["dp-1"])` →
`watermark_injected == True` while `os.path.exists(result.model_path) == False` — the module never opens a file
at all. The function body is: hash `session_id`, log `"Vision model watermark injected: <12 hex>"`, `return True`.
`TrainingResult.watermark_injected` (`:134`) is the field an auditor reads as "this model is traceable", so the
claim of "虚假的安全能力声明" is exact; `app/services/AGENTS.md` anti-patterns already flag `model_watermark` as a
seeded-PRNG scheme, but that is a *different* module and this runtime does not use it. Same honest-degradation
violation as [9] (no disclosure flag, no error path).

Fix: perform a real injection and return its actual result (log `error` on failure); until then rename the log to
"watermark payload computed" and return `False` / add `watermark_status="not_implemented"`.

---

### [12] `validate_schema_completeness` validates nothing and its key name misleads
**Verdict: TRUE·LATENT** (`clickhouse_schema.py:344-363`; the only test is `:355` `if not col.name or not col.dtype`)

Executed: all 7 registered tables return `valid=True, missing_columns=[]`. Then I injected, on the in-memory
table object, a **duplicate** `timestamp` column, a `ghost_col`, `ORDER BY (timestamp, does_not_exist)`,
`TTL missing_col + INTERVAL 3 YEAR` and `INDEX idx_ghost ghost_expr` — validation still returned
`{'valid': True, 'column_count': 14, 'missing_columns': []}` while `generate_create_sql` emitted DDL that
ClickHouse rejects (duplicate column / unknown identifier in ORDER BY, TTL and index expr).

One imprecision in the claim: "永远返回 valid: True" — an unknown table name does return `valid: False` (`:351`).
Also the docstring/key mismatch is real (`:347` documents `missing_columns`; `:355-362` reports *incomplete*
columns). Confirmed the current definitions are internally consistent today (probe K6: no duplicate columns, every
index expr and ORDER BY key resolves to a column) — the defect is the checker, not the data. LATENT: only tests
call it.

Fix: check duplicate column names, and parse identifiers out of `order_by` / `partition_key` / `ttl` /
`IndexDef.expr` against the column set; rename the key to `invalid_columns` or fix the docstring.

---

### [13] Unknown table name returns empty result instead of an error
**Verdict: TRUE·LATENT** (`clickhouse_schema.py:322-323` `return []`, `:329-330` `return {}`, `:368-369` `return ""`)

Executed: `get_columns("nope") == []`, `get_table_info("nope") == {}`, `generate_create_sql("nope") == ''` — no
exception, no log (the module defines `logger` at `:10` and uses it **0** times, see [32]). A typo'd table name in a
provisioning flow therefore produces an empty DDL string that is silently skipped, and the failure only surfaces
on first INSERT. Today the blast radius is zero because nothing in `app/` calls these methods.

Fix: raise `UnknownTableError` (or `KeyError`) for unregistered names; at minimum `logger.warning` before the
empty return.

---

### [14] `get_all_open_circuits` iterates the shared dict without a snapshot
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:140-146` —
`sid for sid, stats in self._sessions.items()` with no `list()` and no lock)

Real property, subject to the same two corrections as [5]: with a single event loop no other coroutine can insert
between the comprehension's steps (the body never awaits), so `RuntimeError: dictionary changed size during
iteration` requires concurrent threads (`can_execute`/`get_stats` from a worker thread or a monitoring thread while
the sweep runs). No production caller exists today.

Fix: snapshot under the lock — `with self._lock: snapshot = list(self._sessions.items())` — then filter.

---

### [15] Unbounded session growth; read-only methods mutate state
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:54-57` lazy create, called from `can_execute` `:61` and
`get_stats` `:134`; removal only via `reset` `:136-138`; singleton `:149`)

Executed: 1000 `can_execute(f"session-{i}")` calls → `len(_sessions) == 1000`; a single
`get_stats("readonly-probe")` created an entry. There is no TTL, no LRU, no eviction on session end, and — as the
claim notes — `get_all_open_circuits` (`:140`) scans the whole retained set, so cost grows linearly with the
historical session count of the process.

Fix: stamp `last_access_time`, evict entries idle beyond `recovery_timeout_seconds` (or session lifetime) from a
sweep, cap with an LRU, and make `can_execute`/`get_stats` return a default `CircuitStats` without inserting.

---

### [16] `shutil.which` trusts the process `PATH` as TEE hardware evidence
**Verdict: TRUE·LIVE** (`tee_capability.py:80` — `path = shutil.which(command)`, no `path=` argument)

Executed proof end-to-end through the production code path: planting `sgx-detect.bat` in an attacker-writable temp
dir and prepending it to `PATH` made

```
TEECapabilityDetector().detect("sgx")
 -> provider='sgx' hardware_available=True evidence=['command:...\\tmpp84bwvge\\sgx-detect.BAT']
    reason='sgx hardware/runtime signal detected'
```

What the attacker gains, concretely: `app/api/monitoring.py:71-77` turns that into
`tee_status="verified"` / `tee_gate="pass"` on `GET /api/v1/monitoring/security-posture` (mounted at
`app/main.py:333`) — a **forged "hardware TEE verified" posture** reachable by anyone who can write to one PATH
directory or mutate the service's environment, no device forgery needed. This is the cheapest 禁伪造 violation in
the five files, and it is strictly cheaper than [6]. Second-order effect on provisioning is limited by the fact
that `sandbox_runtime.py:1316` additionally requires `TEE_HARDWARE_EXEC_CMD` and reports `FAILED` honestly if the
hardware command errors (`:1318-1326`).

The claim's "PATH 空串条目隐含当前工作目录" sub-point did not reproduce on this Windows box
(`shutil.which` with an empty PATH entry returned `None`); it is a Linux-side nuance and should be treated as
unverified rather than as demonstrated. The second point — "the package being installed is not proof of a usable
runtime" — is a correct design criticism (evidence is presence-only, never a version/attestation probe).

Fix: `shutil.which(command, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")`, reject
results that are group-/world-writable or not owned by root, and demote command evidence to *auxiliary*: require
`device/firmware evidence AND command evidence` before `hardware_available=True`.

---

### [17] Explicit unknown provider is reported as "no hardware TEE", with the typo echoed back as the provider
**Verdict: TRUE·LIVE** (`tee_capability.py:55-57` `spec = self._PROVIDERS.get(provider); if not spec: continue`
→ falls to `:67-71` which returns `provider=<mode>` and `reason="No hardware TEE signal detected for mode=<mode>"`)

Executed:

```
detect("tdx_guest") -> provider='tdx_guest' hardware_available=False evidence=[] reason='No hardware TEE signal detected for mode=tdx_guest'
detect("sev-snp")   -> provider='sev-snp'   hardware_available=False evidence=[] reason='No hardware TEE signal detected for mode=sev-snp'
```

(`"sgx "`/`"TDx"` are correctly normalized at `:43`, so only genuinely unknown names hit this.)
Live: `settings.TEE_MODE` is passed verbatim at `app/api/monitoring.py:62` and
`app/services/sandbox_runtime.py:1314`. Consequence chain: a config typo is indistinguishable from "hardware
genuinely absent"; with `TEE_ALLOW_SOFTWARE_FALLBACK=true` the session is provisioned on the
software-confidential backend — that part *is* disclosed (`sandbox_runtime.py:1300-1307` warning, `is_simulation`
property `:1306-1309`) — but the posture payload and the provision result carry `tee_provider="tdx_guest"`, a
provider that does not exist, which downstream per-provider dispatch/labeling cannot distinguish. Honest
degradation is thus half-satisfied: the fallback is disclosed, the *cause* is misattributed.

Fix: `if mode != "auto" and spec is None: raise ValueError(...)` (or return a distinct
`reason="unknown TEE provider"`), keeping `continue` only for `mode == "auto"`.

---

### [18] `evaluate()` never clamps its inputs to the documented 0-100 contract
**Verdict: TRUE·LIVE** (`trust_evaluator.py:64-77` signature, docstring contract `:79-90`, clamping only of the
weighted total at `:132`; `TrustScore` fields documented `0-100` at `:33-37`)

Executed: `evaluate(500,500,500,500)` → `score=100` but the returned components are `500/500/500/500`
(violating the `0-100` annotation) and level `platinum`; `evaluate(-40,-40,-40,-40)` → `score=0`, component
`data_quality=-40`; `evaluate(data_quality_score=90, data_age_days=-9999)` → `quality=95` with factor
`"fresh data boosted quality"` — a negative age (clock skew / bad timestamp) **increases** the quality score, via
`:113-114` `elif data_age_days < 7: quality = min(100, quality + 5)`.
`evaluate_provider` also forwards `int(avg_quality)` unclamped (`:182`), see [7].

Reachability nuance (stated so severity is not oversold): the only live call site,
`app/services/federation_connector.py:559-564`, reads from `_trust_scores`, which `_update_trust_score` maintains
with `min(100, …)`/`max(0, …)` (`federation_connector.py:632-648`) — so out-of-range values cannot arrive there
today; the unclamped surface is `cross_space_policy.evaluate_trust_score` (`:207-222`, no prod caller) and
`data_age_days`, which no production caller passes.

Fix: clamp all four component inputs and `violations`/`data_age_days` at entry (`_clamp` as suggested), before any
adjustment is applied.

---

### [19] Float weights + `int()` truncation misclassify level at the thresholds
**Verdict: TRUE·LIVE** (`trust_evaluator.py:49-54` float weights `0.30/0.30/0.20/0.20`, `:131-132`
`total = int(min(100, max(0, total)))`, thresholds `:57-62`)

Executed over the full integer input space (dq=compliance, rep, sec ∈ 0..100, 10⁶ combos, compared against exact
`Decimal` arithmetic):

- truncation differs from rounding in **2926** combos (e.g. raw `1.9999999999999998` → `int` → `1`);
- **10003** combos truncate below the exact value;
- **the misclassification is real**: 26 combos fall across the PLATINUM threshold, 73 across GOLD, 265 across SILVER.
  Example `data_quality=compliance=29, reputation=86, security=77` → float `49.99999999999999` → `int` → **49** →
  level `new`, where the exact score is 50 → `silver`.

Live consequence: `app/services/federation_connector.py:565-570` compares this integer score against the
per-operation minimum (`_min_trust_by_op`, e.g. 50) to allow or 403 a federated request, so a one-point truncation
artifact at exactly the configured threshold changes the authorization decision.

One correction to the claim's illustration: the uniform case does **not** reproduce — `evaluate(90,90,90,90)`
evaluates to exactly `90.0` and returns `90/platinum`. The mechanism is right, the "90 → 89" example is not; use
the 29/29/86/77 → 49 case instead.

Fix: integer basis points (`{"data_quality": 30, …}` with `/ 100`) or `int(round(total))` before clamping.

---

### [20] "`(?i)` only covers the `(plate|license_plate)` group, so `[A-Z0-9-]` stays case-sensitive"
**Verdict: FALSE** — disproven by execution (`vision_multimodal_runtime.py:194`
`rb"(?i)(plate|license_plate)\s*=\s*[A-Z0-9-]{5,12}"`)

```
re.sub(rb"(?i)(plate|license_plate)\s*=\s*[A-Z0-9-]{5,12}", rb"\1=[MOSAIC]", b"license_plate=abc123")
  -> b'license_plate=[MOSAIC]'          # lowercase value IS replaced
re.sub(rb"(?i:(plate|license_plate))\s*=\s*[A-Z0-9-]{5,12}", ..., b"license_plate=abc123")
  -> b'license_plate=abc123'            # the SCOPED form is what would fail
LicensePlateStep().apply(b'plate=abc-123') -> b'plate=[MOSAIC]|cds_step=license_plate:mosaic'
```

In Python's `re` a bare `(?i)` is a **global** flag when it appears at the start of the pattern — its scope is the
whole pattern, not "up to the next `)`". Scope-limited case-insensitivity requires the `(?i:…)` form, which is
what the claim's reasoning describes; the second line above demonstrates the difference. So the central assertion
("小写车牌不会被替换") is wrong, and the proposed `(?i)`-relocation fix is a no-op.

Two sub-observations do survive, but with a different cause than the claim gives:
- the `{5,12}` **length cap** leaves residue: `license_plate=ABCDEFGHIJKLMN` → `license_plate=[MOSAIC]MN`;
- characters outside `[A-Z0-9-]` (e.g. `京A12345`) are not matched: `plate=京A12345` is returned untouched.
Both are class-content/quantifier issues, and both are moot while the module only ever processes its own
synthetic payloads (`:235-241`) — see [9]/[21].

---

### [21] Appending `|cds_step=…` corrupts binary formats, and the substring dedup silently skips steps
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:201-206`)

Part 1 confirmed: `LicensePlateStep().apply(png)` appended `b'|cds_step=license_plate:mosaic'` **after** the PNG
`IEND` chunk, exactly the "garbage after the terminating chunk / length-vs-payload mismatch" failure the claim
describes; any strict decoder rejects it, so "redacted tensor" is not a coherent artifact.

Part 2 confirmed for the marker-only steps: `FaceBlurStep().apply(b"payload|cds_step=face_blur:gaussian_k51 tail
PatientName=ZHANG")` returned the input **byte-identical** — a payload that merely *contains* the marker string
suppresses the step's bookkeeping entirely, and since the payload bytes come from the data product (`load()`
embeds attacker-influenceable content), a provider can pre-seed markers.
Nuance the claim overstates: for `DICOMStripStep`, which has a real regex, pre-existing the marker suppresses only
the *append*, not the strip — executed
`b'x|cds_step=dicom_tags_stripped|y;PatientName=ZHANG^SAN'` → `b'x|cds_step=dicom_tags_stripped|y'` (name removed).
So "该步骤完全不再执行" is accurate only for face blur / plate / flip / resize / normalize.

Fix: keep processing provenance in a sidecar structure (per-sample `applied_steps` list on the batch record)
instead of mutating the payload, and drive idempotency from that per-step execution record.

---

### [22] Default `RedactionRules` leak PII in the medical task
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:85-88` — `face_blur=True` but
`dicom_strip=False`, `license_plate_mosaic=False`; `VisionTask.MEDICAL_IMAGING` at `:47` is only an enum tag and
`VisionTrainingConfig.redaction_rules` at `:101` keeps the same defaults)

Executed: `RedactionRules()` defaults as above; `MEDICAL_IMAGING` config carries the identical defaults; the
default pipeline is `decode → face_blur → horizontal_flip → resize_crop → normalize` (no strip/mosaic), and the
sample that actually reaches training under the default pipeline is

```
b'image-0;PatientName=Patient-0;PatientID=PID0000;license_plate=ABC000;face=region-0|cds_step=face_blur:...|cds_step=normalize'
```

with `PatientName survives: True | license_plate survives: True`. Combined with [9] even the enabled
`face_blur` step does nothing to pixels, so under defaults a medical job carries PHI end-to-end with `error=None`.
Note the aggravating factor: `load()` *puts PII into the samples by construction* (`:235-241`).

Fix: derive defaults from `config.task` — force `dicom_strip=True` and `license_plate_mosaic=True` for
`MEDICAL_IMAGING` (reject a config that turns them off), and refuse to train when redaction is off for a medical
task instead of silently proceeding.

---

### [23] `session_id` concatenated into the output path without validation (vision)
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:408`
`model_path=f"/sandbox/output/vision_model/{session_id}"`)

Executed: `run_training("../../Windows/win.ini", …)` → `model_path = /sandbox/output/vision_model/../../Windows/win.ini`.
No allow-list, no `PurePosixPath(...).relative_to(base)`, no NUL/newline check anywhere in the file.

Important mitigation found while verifying: this module **never writes** to that path (probe:
`os.path.exists(model_path) == False`; `run_training` only computes strings and metrics), so today the traversal is
a poisoned *returned path contract*, not an executed write — it becomes a real directory escape the moment a caller
persists the model, and `AGENTS.md` documents workspace `…/{session_id}/output` conventions that a caller would
combine it with. Zero production callers, hence LATENT.

Fix: one shared `safe_output_path(base, session_id)` helper with `^[0-9a-fA-F-]{8,64}$` validation plus a
resolved `relative_to(base)` containment check.

---

### [24] Same unvalidated `session_id` path in the multimodal branch
**Verdict: TRUE·LATENT — dup of [23], separate line** (`vision_multimodal_runtime.py:500`)

Executed: `run_training("C:/Windows/notepad", …)` → `/sandbox/output/multimodal_model/C:/Windows/notepad`
(a path-looking segment kept verbatim). Same root cause, same missing helper; tracked separately only because the
fix must be applied at both sites (or, as the claim suggests, via one helper used by both runtimes).

---

### [25] `batch_size` used as `range()` step with no >0 validation (vision) — plus silent `num_epochs=0` success
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:248`
`for i in range(0, len(self._images), self._batch_size)`; config field `:98` `batch_size: int = 32`, no constraint;
`:97` `num_epochs: int = 10`)

Executed: `batch_size=0` → `ValueError: range() arg 3 must not be zero` raised inside `get_batches()`, i.e. the
error text points at `range`, not at the illegal configuration, and surfaces during training rather than at the
entry point. `batch_size=-4` → **no error at all**: `range(0, 100, -4)` is empty, so the epoch runs on zero batches
and still reports metrics. `num_epochs=0` → `TrainingResult(error=None, total_epochs=0, final_loss=0.0,
final_accuracy=0.0, watermark_injected=True)` — indistinguishable from a real (empty) success, matching the claim's
second half.

Fix: validate `batch_size >= 1` and `num_epochs >= 1` in `run_training` (or a `__post_init__` on the config) and
return `TrainingResult(error=…)` for `num_epochs <= 0`.

---

### [26] Same for the multimodal loader
**Verdict: TRUE·LATENT — dup of [25], separate line** (`vision_multimodal_runtime.py:309`, config field `:109`)

Executed: `SecureMultimodalDataLoader(["i"], ["t"], 0).get_batches()` →
`ValueError: range() arg 3 must not be zero`; `-4` → no error. Note the multimodal branch has one partial guard the
vision branch lacks — an unknown `base_model` does return a `TrainingResult` with `error` set (`:440-450`) — so the
entry-validation gap is specifically about batch/epoch parameters.

---

### [27] `max_text_len` is stored and never used
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:296` `self._max_text_len = max_text_len`; config field `:111`)

Executed: the string `_max_text_len` occurs exactly **once** in `SecureMultimodalDataLoader`, and that occurrence is
the assignment; `load()` (`:299-305`) and `get_batches()` (`:307-310`) neither receive nor truncate text
(`load()` emits fixed `"Description for image {i}"` strings). So `MultimodalTrainingConfig.max_text_len` is dead
configuration, and any downstream assumption of a token bound derived from it is unfounded.

Fix: truncate in `load()`/`get_batches()` (`text[:self._max_text_len]` or a real tokenizer cut), or delete the
parameter.

---

### [28] `DICOM_STRIP` ignores the configured `tags` whitelist
**Verdict: TRUE·LATENT** (`build_image_pipeline` configures `:333`
`{"tags": ["PatientName", "PatientID"]}`, but `_apply_pipeline_step` `:267` constructs `DICOMStripStep()` with no
arguments, so the hardcoded `SENSITIVE_TAGS` at `:176` — `PatientName, PatientID, PatientBirthDate,
InstitutionName` — is what runs)

Executed: the built step carries `params={'tags': ['PatientName', 'PatientID']}`; feeding
`b'x;PatientName=A;InstitutionName=Hospital X'` through `_apply_pipeline_step` returns
`b'x|cds_step=dicom_tags_stripped'` — i.e. `InstitutionName` was stripped although it is not in the configured
list. Direction of the mismatch is *over*-stripping relative to configuration, so it is not a PII leak; it is a
config↔behaviour contract break (and a per-dataset tunability claim that does not hold), exactly as the claim says.

Fix: `DICOMStripStep.__init__(self, tags=None)` and pass `step.params.get("tags")` at `:267`; union-with-hardcoded
if a floor is desired, and log the effective set.

---

### [29] `load()` never uses `product_ids` — the "authorized data product" contract is unenforced
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:222` stores `self._product_ids`, `load()` `:228-241` never
reads it; multimodal equivalent `:293-294`)

Executed: `product_ids=[] → num_samples=100`, `["bogus"] → 100`, `["dp-1"] → 100`; the multimodal loader returns 50
pairs with empty ids. `run_training("s", VisionTrainingConfig(num_epochs=1), [])` finishes with `error=None,
total_epochs=1` — the "silent fake success on unenforced authorization" the claim describes. Mitigating context
that bounds the risk: real product authorization is enforced upstream (`workflow_service` admission gate,
`data_product_sandbox`, per `app/services/AGENTS.md`), and this module has **no** production wiring at all — so
today nothing is bypassed. The honest-degradation defect is that the loader's name, docstring ("Load images from
authorized products") and the `Loaded 100 images` INFO line (`:243`) all assert an interaction with the catalog
that does not exist.

Fix: raise / return `error=` for empty or unresolvable `product_ids`, and name the synthetic path explicitly
(e.g. `_synthetic_samples_for_dry_run()`) so a real datasource can be substituted without inheriting the fake
success.

---

### [30] Performance: `get_batches()` re-run every epoch + one SM3 per batch
**Verdict: TRUE·LATENT, with one inverted detail** (`vision_multimodal_runtime.py:373-375` `batches =
dataloader.get_batches()` inside `for epoch in range(...)`, multimodal `:466-468`; SM3 jitter at `:381,390,474,483`)

Both structural points confirmed: the pipeline is deterministic, so re-running the full redaction/augmentation
chain per epoch is pure `num_epochs`× duplication. Measured SM3 jitter cost:
`_stable_unit_interval` = **279.4 µs/call** vs `hashlib.sha256` = **0.673 µs/call** (~416×).

Correction to the claim's reasoning: gmssl is **not** an accelerator that goes missing — `gmssl/sm3.py` is the pure
Python implementation (probe: module file under site-packages, `__compiled__` absent) and it **is installed** in
this env; the *fallback* when gmssl is missing is C `hashlib.sha256` (`app/utils/crypto.py:42-58`), i.e. the
fallback is ~400× **faster**. So the right complaint is "pure-Python SM3 per batch", not "SM3 without an
accelerator". Magnitude today: with the fixed 100-image dataset that is ~5 SM3 calls per epoch ≈ 1.4 ms/epoch —
invisible until `load()` is replaced by real data, at which point it becomes the dominant cost.

Fix: hoist `dataloader.get_batches()` above the epoch loop (or memoize by pipeline+dataset fingerprint), and
precompute the epoch-level jitter or switch to `hashlib.blake2b(digest_size=4)`.

---

### [31] `ColumnDef.comment` is never populated and never emitted
**Verdict: TRUE·LATENT** (`clickhouse_schema.py:19` `comment: str = ""`;
`generate_create_sql` `:373-375` emits only `{name} {dtype}` + optional `DEFAULT`)

Executed: `comment=` occurs **0** times in the file (all `ColumnDef(...)` calls are positional name/dtype/default),
`col.comment` occurs **0** times, and `"COMMENT"` is absent from `generate_create_sql("audit_events")` output.
Field order confirmed `['name', 'dtype', 'default', 'comment']` (`:16-19`), so a positional 4th argument would be
silently dropped into a field that is never rendered. Contradicts the module docstring's "schema … migration
support" (`:1-5`).

Fix: emit `COMMENT '<escaped>'` after the default, or delete the field.

---

### [32] `logger` (and `logging`) defined but unused — dead code in `clickhouse_schema.py`
**Verdict: TRUE·LATENT** (`clickhouse_schema.py:8` `import logging`, `:10` `logger = logging.getLogger(__name__)`)

Executed: `logger.` occurs **0** times in the file. The suggestion in the claim is exactly the right pairing with
[13] — this is the logger that should warn on unknown table names and validation failures.

Fix: use it in the three silent-empty returns and the validation path (preferred), or drop both lines.

---

### [33] Unused `field` import in `task_circuit_breaker.py`
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:10` `from dataclasses import dataclass, field`)

Executed: `field(` occurs **0** times in the file; the only line containing `field` is the import itself. Both
dataclasses (`CircuitConfig` `:24-31`, `CircuitStats` `:34-45`) use only immutable scalar defaults, so
`field(default_factory=…)` is genuinely unnecessary.

Fix: `from dataclasses import dataclass`.

---

### [34] f-string logging in `task_circuit_breaker.py`
**Verdict: TRUE·LATENT** (`task_circuit_breaker.py:92`, `:111`, and the multi-line f-string at `:117-120`)

Executed: exactly 3 `logger.*` call sites, all f-string-formatted, matching the claim's "三处". The overhead point
is real but trivial at this call rate; the durable argument is structured-log采集 (a formatted sentence is not a
queryable field), which the suggested `%s` form restores.

Fix: `logger.info("Circuit closed for session %s — recovery confirmed", session_id)` etc.

---

### [35] f-string logging in `trust_evaluator.py`
**Verdict: TRUE·LIVE** (`trust_evaluator.py:141`
`logger.info(f"Trust evaluation: {level.value} ({total}/100) — {', '.join(factors[:3])}")`)

This is the one logging claim on a live path: `evaluate()` runs on every federated request
(`federation_connector.py:559` ← `api/federation.py:297`), so the `', '.join(...)` runs per request even when INFO
is disabled. Severity is genuinely `low` — one join per request — and it is also the only INFO line logging an
authorization-relevant score, which is why the lazy-`%s` form (or demoting to DEBUG) is worth the one-line change.

---

### [36] Unused imports in `vision_multimodal_runtime.py` (`uuid`, `datetime`, `timezone`, `Any`)
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:25` `import uuid`, `:28`
`from datetime import datetime, timezone`, `:30` `from typing import Any`)

Executed AST name scan: `uuid`, `datetime`, `timezone`, `Any` → all **unreferenced**; and the claim's parenthetical
is accurate — `field`, `crypto_service`, `logger`, `re`, `time`, `ABC`, `abstractmethod`, `dataclass`, `Enum` are
all used, and `_stable_unit_interval` (`:35`) is defined once and reused at `:381/390/474/483`.

Fix: delete the three import lines (keep `field`).

---

### [37] f-string logging in `vision_multimodal_runtime.py` (+ watermark fingerprint in INFO)
**Verdict: TRUE·LATENT** (`vision_multimodal_runtime.py:420` and `:512` watermark lines; the claim's other two
examples are `:243` `f"Loaded {len(self._images)} images"` and `:305` `f"Loaded {len(self._pairs)} image-text pairs"`)

All four sites confirmed f-string formatted. On the watermark-leakage concern: the logged value is
`sm3_hash(session_id)[:12]` (`:419`, `:511`) — a truncated digest of a session identifier that this code uses as
the watermark *input*. Since no watermark exists at all ([11]), there is nothing to leak yet; but if real injection
is implemented, `session_id` becomes the watermark key and this INFO line becomes a key-disclosure channel, so the
claim's caution is right to record. Fix: lazy `%s` formatting everywhere, and never log watermark input material at
INFO.

---

## Score

| file | claims | TRUE·LIVE | TRUE·LATENT | FALSE |
|---|---|---|---|---|
| `app/services/tee_capability.py` | 3 — [6] [16] [17] | **3** | 0 | 0 |
| `app/services/trust_evaluator.py` | 5 — [7] [8] [18] [19] [35] | **3** ([18] [19] [35]) | 2 ([7] [8]†) | 0 (1 sub-claim FALSE‡) |
| `app/services/clickhouse_schema.py` | 5 — [1] [12] [13] [31] [32] | 0 | **5** | 0 |
| `app/services/task_circuit_breaker.py` | 8 — [2] [3] [4] [5] [14] [15] [33] [34] | 0 | **8** | 0 |
| `app/services/vision_multimodal_runtime.py` | 16 — [9] [10] [11] [20] [21] [22] [23] [24] [25] [26] [27] [28] [29] [30] [36] [37] | 0 | **15** | **1** ([20]) |
| **Total** | **37** | **6** | **30** | **1** |

† [8] is counted as TRUE·LATENT on its fail-open half.
‡ [8] part 2 (the "更严重" str-Enum hash bypass) is **FALSE** — `hash(TrustLevel.GOLD) == hash("gold")`, so string
inputs resolve correctly and no bypass exists; the score table counts whole claims, so this sub-falsity shows up in
the note rather than the FALSE column. [19]'s "90 → 89" illustration is likewise not reproducible
(`evaluate(90,90,90,90) == 90/platinum`) while the mechanism itself reproduces elsewhere.

### Reviewer-accuracy summary
- **36/37** claims describe real code; the single outright miss ([20]) fails on a Python-`re` fact (bare `(?i)` is a
  global flag, not a group-scoped one) and is testable in one `re.sub` call.
- **0/37** were rated live by the reviewer, yet only 6 actually are — all 6 are in `tee_capability.py` (3) and
  `trust_evaluator.py` `evaluate()` (3). The three unwired modules (`vision_multimodal_runtime`, `task_circuit_breaker`,
  `clickhouse_schema`) carry 29 of the 30 LATENT claims; `docs/mock-remediation-plan.md:35,297` already lists
  `vision_multimodal_runtime.py` as orphan/mock code with no API wiring, which the claims file did not reflect.
- Severity calibration: [2] (breaker window dead) is correctly `critical` **for a wired breaker** but cannot affect
  today's deployment; conversely [16] (PATH-trusted TEE probe) was rated `medium` yet is the only claim in the set
  that lets an attacker forge a **"hardware TEE verified"** security-posture assertion on a live endpoint without
  touching any device — under the project's 禁伪造 / honest-degradation rule that is the highest-priority item here.

### Nothing here duplicates the excluded known facts
The excluded items (AES labelled SM4 in dev, SM3→SHA-256 fallback, `is_simulation`/`ALLOW_SIMULATION` switches) are
not the subject of any of the 37 claims. Where they touched a claim I used them only as corrections: [30] (the SM3
fallback is *faster*, not slower, and gmssl is the pure-Python side) and [9]/[11]/[22] (the vision mocks are **not**
disclosed by `is_simulation`/`ALLOW_SIMULATION` at all — that absence is the defect, and `specs/SS-gap-analysis.md:32`
G-006 even records the marker-append design as "已修复").
