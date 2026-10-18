# ocr findings adjudication — anchor / DP / quota / masking / policy cluster

Repo `D:/workspace/sandbox`, branch `feat/ss-11-productization`. Read-only task; no repo file touched
(`git status --porcelain` empty before and after). 60 claims in scope, from
`ocr-findings.json` (1913 total). Every claim below is keyed by the extraction index `[n]`.

Method: claims extracted by script (never the raw JSON); code re-read at current line numbers;
reachability by grep over `app/ sdk/ cds-volume-plugin/ tests/`; behaviour checked at runtime with
`.venv-gate/Scripts/python.exe` (3.12.10, `PYTHONPATH=D:/workspace/sandbox`), probe scripts
`verify_anchor_probe.py` / `verify_anchor_probe2.py` in this directory. `[rt]` = runtime-observed.

## Reachability map

| module | production importers → entry point | verdict |
|---|---|---|
| `merkle_service.py` | `app/api/audit.py:26` → `/api/v1/audit/anchor:362`, `/verify:423`, `/merkle-proof:500`; `cube_egress_audit.py:223` → `_anchor_batch:233`; `merkle_pipeline.py:99` (gated) | **LIVE** (verify/verify_batch_root themselves: 0 callers) |
| `dp_budget.py` | `api/sandbox_tasks.py:451` `consume`; `api/contracts.py:209` + `contract_service.py:171` `allocate`; `output_inspection.py:230/549/552/558` → `api/output_control.py:12`, `api/deliveries.py:125`, `api/sandbox_sessions.py:1922` | **LIVE** |
| `quota_manager.py` | LIVE: `gateway_service.py:185/202` → `api/gateway.py:227/263`; `api/quota.py:188-189`; `set_limits`/`reset` from `api/sandbox_sessions.py:368/372`, `api/connectors.py:433/463`, `contract_fulfillment.py:122`; Redis wired at `main.py:58`. **No production caller**: `check_and_increment:107`, `check_batch:210`, and `reset(contract_limits=…)` | **LIVE** for the gateway + admin paths; **LATENT** for the session-counter paths (tests only: `tests/test_quota_manager.py`, `tests/test_ss17_quota_readonly.py:195/271`) |
| `field_mask.py` | only prod importer `encrypted_db.py:19` — `encrypted_db` itself has **no** importer in `app/` (`secure_duckdb.py:855` is a method name, not an import); `policy_evaluator.py` imports it only inside its own trailing `def test_*` (937/952/962) and is itself unwired (`secure_duckdb.py:136` imports a same-named symbol from `policy_compiler.py:496`) | **LATENT** — tests only (`tests/test_field_mask.py`, `test_e2e_flows.py`). The live masking path is `output_security.mask_rows_by_field_rules` (`output_gateway.py:163-164`), a different module. Not to be confused with `field_acl.py` |
| `alert_engine.py` | `alert_center.py:17` imports **only the `Alert` dataclass**; `AlertRuleEngine.evaluate/record_*` have 0 callers in `app/`. `alert_center.ingest_alerts` itself has 0 callers | **LATENT** — the rule engine never runs in production (`api/monitoring.py` uses only `list_alerts/acknowledge/resolve`) |
| `cross_space_policy.py` | no importer anywhere except `tests/test_cross_space_policy.py` | **LATENT** (fully unwired engine) |
| `contract_fulfillment.py` | `api/contracts.py:215` `fulfill_contract` (contract activation), `:245` + `contract_service.py:213-214` `terminate_contract_sessions` | **LIVE** |

AGENTS.md filters honoured: `app/services/AGENTS.md` crypto section (AES-labelled SM4, SM3→SHA-256
fallback disclosure, two SM3 paths in `crypto_service`) treated as known-and-documented; nothing
below re-reports them except where a claim adds a new consequence.

## Merkle pipeline — the extra check

**One shared tree implementation; two different leaf definitions; one dead sibling implementation.**

- `merkle_service` is the only Merkle code the production anchoring path uses. `api/audit.py:362`
  (`/anchor`), `:423` (`/verify`) and `:500` (`/merkle-proof`) all build leaf hashes with
  `crypto_service.sm3_hash(f"{id}|{action}|{resource_type}|{resource_id}|{created_at}")`
  (`api/audit.py:358-359`) and then call `merkle_service.get_root(record_hashes)` — i.e. **leaf
  hashing is `crypto_service`, only the node fold is `merkle_service._hash`** (two different
  gmssl/SHA-256 gates inside one tree, `merkle_service.py:36-57`).
- `merkle_service.hash_leaf` (domain-separated `b"\x00" + data`, line 92) and `build_batch` are used
  **only** by `merkle_pipeline.py:169`. Roots from the two paths are therefore not comparable.
- `merkle_pipeline.py` is a second *pipeline*, not a second tree: same `merkle_service`, gated by
  `MERKLE_PIPELINE_ENABLED: bool = False` (`app/core/config.py:154`, started at `app/main.py:165`)
  and it has **no producer** — `push_event` (`merkle_pipeline.py:58`) has zero callers — so even when
  armed it consumes nothing. Proofs live in a process dict (`merkle_pipeline.py:183`), never persisted.
- A genuine parallel implementation exists and is dead: `blockchain_service.compute_merkle_root`
  (`blockchain_service.py:35-51`) folds with `crypto_service.sm3_hash((left+right).encode())`
  (no domain separator, no odd-leaf duplication rule). Callers: `tests/test_blockchain.py` only.
  `contract_service.py:178` uses `anchor_to_blockchain`, not that root function.
- **Production write targets** (what anchoring actually writes):
  - `blockchain_anchors` ← `BlockchainAnchor(...)` at `blockchain_adapter.py:442`, `session.add` `:451`
  - `chain_anchoring_records` ← `insert(ChainAnchoringRecord)` at `blockchain_adapter.py:113` and `:453`
  - `audit_logs.blockchain_tx_hash` ← `api/audit.py:375` and `cube_egress_audit.py:253`
  - `merkle_leaves` (`app/models/merkle_leaf.py:22`) — **no writer anywhere in the repo**; only the
    baseline migration creates it (`alembic/versions/0000_baseline_all_tables.py:648`) and
    `retention_janitor.py:50` deletes from it. Already recorded as a dead slot in
    `specs/developer-portal-review-plan.md:139`, so not counted as a new defect here.

---

## merkle_service.py

### [1] `_sm3_fallback_allowed()` returns True on any exception — fail-open hash gate
**TRUE (code) · LATENT.**
```
app/services/merkle_service.py:29-33
29:     try:
30:         from app.core.config import get_settings
31:         return bool(get_settings().SM3_FALLBACK_ALLOWED)
32:     except Exception:
33:         return True
```
Not new: it is a byte-identical copy of the already-documented `app/utils/crypto.py:29-32`, and
`app/AGENTS.md:43` records the gated fallback. Two facts cap it: `gmssl>=3.2.2` is declared
(`requirements.txt:18`) and `[rt] _GMSSL_AVAILABLE=True / _HASH_NAME="SM3"` in the gate venv, so
`_hash` returns at line 44 and the gate never executes; and if `get_settings()` raises,
`validate_security_config` (which rejects `SM3_FALLBACK_ALLOWED=true` in prod, `config.py:469`) never
runs either — the process does not boot. The claim's "没有任何日志提示" is also wrong: lines 51-55 log
a process-lifetime warning.
**Fix (cosmetic):** drop the local copy, import `app.utils.crypto._sm3_fallback_allowed`.

### [2] `bytes.fromhex` on attacker-controlled `proof_path` → 500
**TRUE (code) · LATENT (function has no caller).**
```
app/services/merkle_service.py:96-97
96:         left_bytes = bytes.fromhex(left)
97:         right_bytes = bytes.fromhex(right)
```
`[rt]` `verify_proof(leaf_hash="zz", …)` → `ValueError: non-hexadecimal number found in fromhex()`;
`proof_path=[{"hash": None,…}]` → `TypeError: fromhex() argument must be str, not None`.
But `verify_proof` has **zero callers** in `app/` and `tests/` (grep: only its own `def` at `:190`),
so it is not reachable from HTTP and there is no DoS surface today. `generate_proof` (the LIVE
direction, `api/audit.py:500`) only ever feeds it internally-built hex.
**Fix:** none needed while dead; if wired, wrap `verify_proof` input validation (`len(h)==64 and is_hex`).

### [3] Verification is self-attesting (root comes from the proof itself); no leaf-count binding
**TRUE (code) · LATENT (unused).**
```
app/services/merkle_service.py:204
204:         return current == proof.root_hash
```
`[rt]` forging `root_hash = hash_internal(sibling, leaf)` yields `verify_proof == True` for the chosen
leaf; `MerkleProof` fields are `['leaf_index','leaf_hash','proof_path','root_hash']` — no tree size,
so a 1-node "proof" for any hex string also verifies. The claim's premise that this guards tamper
detection is wrong for production: the LIVE verify path takes the trusted root from the anchor record,
`api/audit.py:423-430` → `verified = await blockchain_adapter.verify(anchor_id, merkle_root.encode())`.
**Fix:** delete `verify_proof`/`verify_batch_root` or bind them to a trusted root + leaf count before
anyone wires them.

### [4] Empty input passes: `get_root([]) == ""`, `verify_batch_root([], "") == (True, "")`
**TRUE (code) · LATENT.**
```
app/services/merkle_service.py:106-107
106:         if not leaves:
107:             return [[]]
```
`[rt]` `build_tree([]) == [[]]`, `get_root([]) == ""`, `verify_batch_root([], "") == (True, "")`.
Not reachable in production: `/anchor` rejects an empty batch first —
`app/api/audit.py:352-353  if not records: raise HTTPException(404, "No records found")` — and
`verify_batch_root` has no caller. Case sensitivity of `==` is real but narrow: the fold is
case-insensitive (`fromhex`), only the final compare is not.
**Fix:** `if not leaves: raise ValueError` in `build_tree`; reject empty `expected_root`.

### [5] Dead fallback branches in `generate_proof`
**TRUE · LIVE module, zero behavioural effect.**
```
app/services/merkle_service.py:156-157
156:         if not tree:
157:             return None
...
187:             root_hash=tree[-1][0] if tree[-1] else "",
```
`build_tree` returns `[[]]` (truthy) at `:107` and never `[]`; and `leaf_index` is bounds-checked at
`:152-153`, so `leaves` is non-empty ⇒ `tree[-1]` non-empty.
**Fix:** delete both branches.

## dp_budget.py — LIVE

### [6] `consume()` has a check-then-act race
**FALSE as stated (no in-process race) · LIVE.** The mechanism is wrong: there is no await between
the read and the write, so two coroutines cannot interleave inside one loop.
```
app/services/dp_budget.py:133-139
133:         await self._ensure_loaded(db, contract_id)
134:         remaining = self._cache.get(contract_id, 0.0)
135:         if remaining < epsilon:
136:             return False
137:
138:         # Write-through: update cache and persist to DB
139:         self._cache[contract_id] = remaining - epsilon
```
The await (`:133`) precedes the read; the DB write (`:147-148`) follows the write. The overspend the
claim wants is real but comes from **cross-process** caches — see [10].
**Fix:** none for the stated mechanism.

### [7] `consume_sync` unlocked for threaded callers
**TRUE (no lock) · LATENT (no threaded caller exists).**
```
app/services/dp_budget.py:228-233
228:         remaining = self._cache.get(budget_id, 0.0)
229:         if remaining < epsilon:
230:             return False
231:         self._cache[budget_id] = remaining - epsilon
232:         self._loaded.add(budget_id)
233:         return True
```
grep for `asyncio.Lock|threading.Lock` in the file: **none**. But every caller is on the event-loop
thread: `output_inspection.py:558` ← `dp_engine.consume` ← `output_inspector.inspect(...)` called
directly in `api/output_control.py:134`, `api/deliveries.py:133/523`, `api/sandbox_sessions.py:1925/2000`
— no `asyncio.to_thread`/executor anywhere on that path.
**Fix:** none now; if the sync inspection is ever offloaded to a thread, guard with a lock.

### [8] Cache decremented before the DB commit; DB failure swallowed → permanent cache/DB divergence
**TRUE · LIVE (direction corrected: over-restriction, not overspend).**
```
app/services/dp_budget.py:139-148   cache written first, then
147:         db.add(entry)
148:         await db.flush()
...
163:         except Exception:
164:             # Keep compatibility with tests that use AsyncMock DB sessions.
165:             pass
166:         return True
```
`consume` never commits; the request-scoped transaction does. On any later rollback the entry
disappears while `self._cache` stays decremented and `_loaded` stays marked (`:49-50`), so the ledger
is never re-read. Note the `except` wraps only `_sync_status` — a `flush()` failure propagates to the
caller (`api/sandbox_tasks.py:451`), it is not swallowed.
**Caller chain:** `api/sandbox_tasks.py:445-459` → `dp_budget_ledger.consume`.
**Harm:** a rolled-back completion permanently lowers that worker's view of the contract's remaining ε
→ later legitimate releases rejected with `409 DP budget insufficient`
(`api/sandbox_tasks.py:456-459`), unrecoverable without a restart.
**Fix:** reload from DB when the transaction aborts (or drop `_loaded` on rollback), and make
`_sync_status` failure loud instead of `pass`.

### [9] `unittest.mock` namespace sniffing inside production logic
**TRUE · LIVE.**
```
app/services/dp_budget.py:150-151
150:         if self._is_mock_session(db):
151:             return True
...
168:     def _is_mock_session(self, db: AsyncSession) -> bool:
169:         """Return True for unittest.mock sessions used by legacy tests."""
170:         return db.__class__.__module__.startswith("unittest.mock")
```
`[rt]` `_is_mock_session(AsyncMock()) → True`, a plain object → `False`. Any session-like object whose
class happens to be defined under `unittest.mock` skips the status projection update in production.
**Fix:** delete the branch; make the tests use a real/`sqlite` session, or inject a fake ledger.

### [10] Cache never invalidated (`_loaded` short-circuits for the process lifetime)
**TRUE · LIVE — the real multi-worker defect.**
```
app/services/dp_budget.py:47-50
47:     async def _ensure_loaded(self, db: AsyncSession, contract_id: str):
48:         """Load budget from DB into cache if not already loaded."""
49:         if contract_id in self._loaded:
50:             return
```
Nothing removes from `_loaded` (only `:54/:79/:115/:209/:232` add). The API runs 2 replicas
(`k8s/cds-app.yaml:9  replicas: 2`; `helm/cds/templates/deployment-api.yaml:9  replicas: {{ .Values.replicaCount }}`).
**Caller chain:** `api/contracts.py:209` allocate (worker A) / `api/sandbox_tasks.py:451` consume
(worker B) — each replica keeps its own `_cache`.
**Harm:** DP ε is over-consumed across replicas (the privacy budget that the ledger exists to enforce
is exceeded) or permanently under-reported; DB-side corrections/migrations never propagate.
**Fix:** make the DB the authority for the async path — `SELECT … FOR UPDATE` on the allocation/status
row inside `consume`, drop the process cache there, and keep `*_sync` purely process-local (as the
comment at `:172-181` already concedes).

### [11] `allocate()` read-modify-write without row lock or upsert
**TRUE (mechanism) · LIVE, low frequency.**
```
app/services/dp_budget.py:94-103
94:         result = await db.execute(
95:             select(DPBudgetAllocation).where(DPBudgetAllocation.contract_id == contract_id)
96:         )
97:         alloc = result.scalar_one_or_none()
98:         if alloc:
99:             alloc.total_epsilon = epsilon
100:        else:
101:            alloc = DPBudgetAllocation(contract_id=contract_id, total_epsilon=epsilon)
102:            db.add(alloc)
103:        await db.flush()
```
Two concurrent activations of the same contract both see `None` → second `flush` raises
`IntegrityError`, which surfaces as a 500 from `api/contracts.py:209` after the contract was already
set to `active` at `:204-205`.
**Fix:** `insert(...).on_conflict_do_update(index_elements=["contract_id"], …)`, or
`select(...).with_for_update()`.

### [12] `last_consumed_at=None` hardcoded in the snapshot rebuild
**TRUE · LIVE, cosmetic/audit field.**
```
app/services/dp_budget.py:70-76   (inside _ensure_loaded, status row missing but allocation exists)
70:             await self._sync_status(
…
75:                 last_consumed_at=None,
```
Both sibling rebuilds resolve the real value: `:112` and `:281`
(`last_consumed_at = await self._get_last_consumed_at(db, contract_id)`).
**Harm:** the DP status projection shows NULL for last consumption even though entries exist → DP
audit trail gap in `api/contracts.py:286` `get_dp_budget_status`.
**Fix:** pass `await self._get_last_consumed_at(db, contract_id)`.

## quota_manager.py

### [13] `check_batch` promises atomicity it does not have
**TRUE (code) · LATENT (no production caller).**
```
app/services/quota_manager.py:215
215:         """Check multiple quotas atomically. All must pass or none are consumed."""
...
257:         # Second pass: increment all if all allowed
258:         if all_allowed:
…
263:                         await self._redis.eval(
264:                             self._lua_script,
265:                             keys=[key],
266:                             args=[limit, amount],
267:                         )
268:                     except Exception:
269:                         pass
```
The second-pass return (`{current, 0}` = rejected) is discarded, so a caller sees `allowed=True` while
nothing was written; a mid-loop `eval` failure leaves a partial increment. But `check_batch` has zero
callers in `app/` (grep) — the only `check_batch` in use elsewhere is `memorization_check.py:112`, a
different class.
**Fix:** check the second-pass `allowed` flags and roll back, or do the whole batch in one Lua script.

### [14] `get_limit()` returns 0 on error / missing / expired TTL, and 0 means "unlimited"
**TRUE (code) · LATENT for the bypass (its only enforcement consumer is unwired).**
```
app/services/quota_manager.py:96-105
 96:     async def get_limit(self, session_id, quota_type) -> int:
 97:         """Get stored quota limit from Redis. Returns 0 if not set."""
 99:         if self._redis:
100:             try:
101:                 val = await self._redis.get(key)
102:                 return int(val) if val else 0
103:             except Exception:
104:                 pass
105:         return self._local_limits.get(key, 0)
```
The TTL that can expire it: `:92  await self._redis.set(key, limit, ex=86400 * 7)`. "0 = unlimited"
is confirmed at `:55 (if limit > 0 …)`, `:148 (allowed = limit <= 0 or …)`, `:230`. LIVE callers are
read-only (`api/quota.py:189`); the enforcement callers are `check_and_increment:122` and
`check_batch` — both unwired. `api/quota.py:199-204` already documents and nulls out this semantic.
**Fix:** return `None` for "unknown" and let callers decide; never collapse error/unset/unlimited.

### [15] Gateway counters incremented before the limit check, never rolled back
**TRUE · LIVE.**
```
app/services/quota_manager.py:310 (inside the pipeline, executed at :320)
310:                 pipe.incrby(rows_key, rows_increment)
…
335:                 if rows_current > rows_limit:
336:                     return False, f"daily row quota exceeded ({rows_current}/{rows_limit})"
```
`[rt]` local twin (`:364-388`) — `_check_gateway_quota_local(app_id, rows_increment=5, rows_limit=3)`
returns `(False, 'daily row quota exceeded (5/3)')` **and** leaves `get_gateway_usage() == {'rows': 5}`:
the rejected consumption is permanent. Two claim details corrected: bytes is *not* skipped (both
`INCRBY`s are inside the same pipeline executed at `:320`, before any comparison), and the DoS framing
is overstated — `increment_quota` runs only after execution (`api/gateway.py:263`), and the next
request is stopped earlier by the pre-check `gateway_service.py:188-191`.
**Caller chain:** `api/gateway.py:263` → `gateway_service.increment_quota:195-208` → `check_gateway_quota`.
**Harm:** one over-limit response pins the app at/over its daily cap for the rest of the UTC day
(permanent self-lockout), and the limit is transiently exceeded by one response's worth.
**Fix:** on overage `DECRBY` the increment (or move check-then-increment into one Lua script).

### [16] `check_batch` does not auto-read configured limits the way `check_and_increment` does
**TRUE · LATENT.**
```
app/services/quota_manager.py:121-122 (check_and_increment)
121:         if not limit:
122:             limit = await self.get_limit(session_id, quota_type)
```
`check_batch` (`:210-260`) has no equivalent — it uses only the caller-supplied tuple limit.
`[rt]` `check_batch(sid, {ROWS: (10, 0)})` on a session whose ROWS limit is 50 → `{rows: (True, 0)}`
(unlimited). Both functions are unwired in production.
**Fix:** same auto-read, or require an explicit limit.

### [17] `expire()` recomputes the gateway key after the write; TTL outside the pipeline
**TRUE · LIVE, low impact.**
```
app/services/quota_manager.py:309-310 vs :334
309:                 rows_key = self._gateway_key(app_id, "rows")
310:                 pipe.incrby(rows_key, rows_increment)
…
334:                     await self._redis.expire(self._gateway_key(app_id, "rows"), ttl_seconds)
```
`_gateway_key` re-reads the date (`:277-280`), so a call straddling UTC midnight sets the TTL on a
different key and leaves the written one forever; a failed `expire` (separate round trip) also leaves
`TTL = -1`. Because the key embeds the date, the residue is a leaked key, not a stuck limit.
**Fix:** `await self._redis.expire(rows_key, ttl_seconds)`; or `SET … EX` inside the pipeline.

### [18] Session counter keys are written with no TTL
**TRUE (code) · LATENT.**
```
app/services/quota_manager.py:58
58: redis.call('SET', KEYS[1], new_value)
```
Only `reset()` (`:187-195`) deletes them. The writers are `check_and_increment` / `check_batch`, both
unwired, so no `quota:{session}:{type}` key is ever created in production today — which is why the
per-session usage panel (`api/quota.py:188`) reads zeros.
**Fix:** `redis.call('SET', KEYS[1], new_value, 'EX', tonumber(ARGV[3]))`.

### [19] Local fallback counts rejected requests and never prunes; N× limit in multi-process
**TRUE for the gateway path, FALSE for the session path · LIVE.**
```
app/services/quota_manager.py:374-379
374:         if rows_increment > 0:
375:             rows_key = self._gateway_key(app_id, "rows")
376:             new_rows = self._local_gateway_counters.get(rows_key, 0) + rows_increment
377:             self._local_gateway_counters[rows_key] = new_rows
378:             if rows_limit > 0 and new_rows > rows_limit:
379:                 return False, f"daily row quota exceeded ({new_rows}/{rows_limit})"
```
`[rt]` gateway local path: rejected request keeps `rows=5`. The session-level local fallback does
**not** have this bug — `:149-150 if allowed: self._local_counters[key] = new_value` — `[rt]`
usage stayed 80 after two rejected 80-unit requests. Pruning: `_local_gateway_counters` keys embed the
date (`:277-280`) and are never removed (only `reset()` clears session counters) → slow growth.
Multi-process: 2 replicas (`k8s/cds-app.yaml:9`) each count separately, but only when Redis is absent
(main.py wires it at `:58` and logs a failure at `:60`).
**Fix:** roll back on overage at `:377-379`; drop non-current-day keys on write.

### [20] A transient Redis error permanently moves usage into process memory
**TRUE · LIVE (gateway path).**
```
app/services/quota_manager.py:354-362
354:         except Exception as e:
355:             logger.warning(f"[quota] Gateway quota check failed: {e}")
356:             return self._check_gateway_quota_local(
```
After Redis recovers, nothing reconciles `_local_gateway_counters` → the Redis counter under-reports
and the app gets more than its daily quota. Session path (`:142-143`) has the same shape but is unwired.
**Fix:** distinguish "no Redis configured" from "call failed": re-raise the latter, or count the
degradation as a metric/alert rather than silently switching ledgers.

### [21] `reset()` can abort after deleting the counters, leaving the session unlimited
**TRUE (code) · LATENT (no production caller passes `contract_limits`).**
```
app/services/quota_manager.py:187-208
187:         for qt in QuotaType:            # ← counters already deleted
…
201:                 QuotaType.DP_EPSILON: int(float(contract_limits.get("dp_epsilon_budget", 10)) * 10000),
```
`[rt]` `reset(sid, contract_limits={"dp_epsilon_budget": "abc"})` → `ValueError: could not convert
string to float: 'abc'`, and the previously configured ROWS limit afterwards is `0` (= unlimited,
per [14]). Callers pass no limits: `api/sandbox_sessions.py:368`, `api/connectors.py:433`,
`contract_fulfillment.py:122`.
**Fix:** parse all limits into `limit_map` before the delete loop, with per-key fallback + warning.

### [22] `set_limits` swallows the Redis write failure
**TRUE · LIVE (impact today is display-level).**
```
app/services/quota_manager.py:89-94
89:             self._local_limits[key] = int(limit)
90:             if self._redis:
91:                 try:
92:                     await self._redis.set(key, limit, ex=86400 * 7)  # 7 days TTL
93:                 except Exception as e:
94:                     logger.warning(f"Failed to set quota limit for {qt.value}: {e}")
```
The method returns normally, and `get_limit` then asks Redis (reachable, key absent) → `0` at `:102`
without falling back to the `_local_limits` value it just stored. LIVE callers:
`api/sandbox_sessions.py:372`, `api/connectors.py:463`. The bypass it would create needs
`check_and_increment` — unwired — so today `api/quota.py` simply reports "no limit" for a limited session.
**Fix:** collect failures and raise; or make `get_limit` fall back to `_local_limits` on a Redis miss.

### [23] `get_usage` swallows Redis errors with no log
**TRUE · LIVE (read-only path).**
```
app/services/quota_manager.py:165-172
165:         if self._redis:
166:             try:
167:                 val = await self._redis.get(key)
168:                 return int(val) if val else 0
169:             except Exception:
170:                 pass
171:
172:         return self._local_counters.get(key, 0)
```
Caller: `api/quota.py:188`. A Redis outage is indistinguishable from zero usage on the console.
**Fix:** `logger.warning("get_usage failed: %s", e)`.

### [24] Eager f-string logging
**TRUE · LIVE, style.** `:94`, `:143`, `:355` all pass pre-formatted f-strings.
**Fix:** `logger.warning("Redis quota check failed, falling back to local: %s", e)`.

## field_mask.py — LATENT (tests-only; production masking is `output_security.py`)

### [25] HASH mode is an unsalted, unkeyed digest of low-entropy PII
**TRUE (code) · LATENT.** New consequence beyond the documented hash-fallback note: the
irreversibility claim itself.
```
app/services/field_mask.py:117-120
117:     def _hash(self, value: str) -> str:
118:         """SM3 hash of the value (GB/T 32905)."""
119:         from app.services.crypto_service import crypto_service
120:         return crypto_service.sm3_hash(value.encode())
```
`[rt]` `_hash("13800138000") == "ee5e7b1c…"` — one deterministic digest, no salt, no key, so a phone
number / ID number is dictionary-recoverable and joinable across datasets.
**Fix:** keyed hash (`HMAC-SM3` with a tenant key from KMS) or `SM3(salt ‖ value)` with a stored salt.

### [26] GENERALIZE fails open everywhere
**TRUE · LATENT — the most consequential item in this module.**
```
app/services/field_mask.py:142        return value          # unknown/typo'd generalize_type
app/services/field_mask.py:146-149    except (ValueError, TypeError): return value
app/services/field_mask.py:162-167    except (ValueError, IndexError): continue … return value
app/services/field_mask.py:175-180    region miss → return value
```
`[rt]` `generalize_type="date_mnth"` (typo) on `"1990-01-02"` → value returned unchanged;
`_generalize_date_month("1990年1月2日")` → unchanged; `_generalize_region("Atlantis")` → unchanged;
and `mask_row` still reports `rules_applied=["d"]` (`[rt]`), so the caller cannot tell masking failed.
**Fix:** fail closed (REDACT) plus `logger.warning` on any generalization that did not apply.

### [27] `bucket_size` unvalidated
**TRUE (corrected) · LATENT.** Default is 100, not 0 — but explicit values crash.
```
app/services/field_mask.py:193-195
193:         bucket_size = params.get("bucket_size", 100)
194:         bucket = int(num // bucket_size) * bucket_size
195:         return f"[{bucket}, {bucket + bucket_size})"
```
`[rt]` `{bucket_size: 0}` → `ZeroDivisionError: float floor division by zero`; `None` → `TypeError:
unsupported operand type(s) for //: 'float' and 'NoneType'`; `"100"` → same `TypeError` for `'float'
and 'str'`; `50.0` → label `"[250.0, 300.0)"`. The `try` at `:188` covers only `float(value)`.
**Fix:** `bucket_size = float(params.get("bucket_size", 100) or 100)` with a positive-number guard.

### [28] Rule whose field is absent is skipped silently
**TRUE · LATENT.**
```
app/services/field_mask.py:91-93
91:         for rule in rules:
92:             if rule.field_name not in masked:
93:                 continue
```
No log, and `rules_applied` simply omits it — a renamed column means raw PII ships. The module's
`logger` (`:16`) is never used (see [33]).
**Fix:** `logger.warning("mask rule %s: field %r absent from row", rule.field_name, …)`, and fail
closed if `rule.required` is set.

### [29] Unconditional `str(value)` breaks types and hash determinism
**TRUE · LATENT.**
```
app/services/field_mask.py:98
98:             masked[rule.field_name] = self._apply_rule(str(value), rule)
```
`[rt]` HASH of int `25` → `d676db86…`, of float `25.0` → `eceb2093…` (same logical value, different
digest ⇒ the "deterministic for joins" property at `:6` fails); a `datetime` reaches generalization as
its `str()` form.
**Fix:** normalise the input (canonical number/date formatting) instead of bare `str()`.

### [30] Date-slice heuristic `len(fmt.replace('%',' ').strip())+5` is fragile / "off by one"
**FALSE as stated · LATENT.** The arithmetic in the claim is wrong and no supported format mis-slices.
```
app/services/field_mask.py:160
160:                 dt = datetime.strptime(value[:len(fmt.replace('%', ' ').strip()) + 5], fmt)
```
`[rt]` slice lengths: `%Y-%m-%d`→12, `%Y/%m/%d`→12, `%Y-%m-%dT%H:%M:%S`→**21** (claim said 18),
`%Y-%m-%d %H:%M:%S`→21 — all ≥ the needed prefix, and `"2020-01-02T03:04:05.123456"` still yields
`"2020-01"`. The fragility is real in the abstract, but the concrete failure is not — and when a
format does miss, the outcome is [26]'s fail-open, not a wrong slice.
**Fix (optional):** `dateutil`-style explicit parser or `datetime.fromisoformat` first.

### [31] `keep_chars` unvalidated — negative value is near-no-op masking
**TRUE · LATENT.**
```
app/services/field_mask.py:203-206
203:         keep = params.get("keep_chars", 3)
204:         if len(value) <= keep:
205:             return value
206:         return value[:keep] + "***"
```
`[rt]` `keep_chars=-1` on `"13800138000"` → `"1380013800***"` (10 of 11 characters kept);
`keep_chars="3"` → `TypeError: '<=' not supported between instances of 'int' and 'str'`;
`None` → same `TypeError`. `keep_chars=0` → `"***"` (fine).
**Fix:** `keep = int(params.get("keep_chars", 3)); keep = max(0, min(keep, len(value)))`.

### [32] Age bucketing: `60+` stops at 120, `inf` crashes, negatives/NaN mishandled
**TRUE · LATENT.**
```
app/services/field_mask.py:63    (60, 120, "60+"),
app/services/field_mask.py:146-154
146:         try:
147:             age = int(float(value))
148:         except (ValueError, TypeError):
149:             return value
…
151:         for low, high, label in self.AGE_RANGES:
152:             if low <= age < high:
153:                 return label
154:         return "unknown"
```
`[rt]` `"120"` → `"unknown"` (should be `60+`), `"130"` → `"unknown"`, `"-5"` → `"unknown"`;
`"inf"` → `OverflowError` (not in the `except` tuple) so it aborts the batch; `"nan"` → `ValueError`
→ returns the original value.
**Fix:** make the last bucket open-ended, clamp negatives, add `OverflowError` to the `except`.

### [33] Dead `import re` and an unused `logger`
**TRUE · LATENT.** `:10 import re`, `:16 logger = logging.getLogger(__name__)`; grep for `re.` and
`logger.` in the file returns nothing. Precisely the branches that fail open ([26], [28]) are silent.
**Fix:** delete `re`; add warnings as specified in [26]/[28].

### [34] Shallow copy: nested containers are never masked and alias the caller's row
**TRUE · LATENT.**
```
app/services/field_mask.py:88
88:         masked = dict(row)
```
`[rt]` rule on `"user.ssn"` against `{"profile": {"ssn": "110101…"}}` leaves the inner value intact
(path-style names are not supported); and mutating `masked_row["profile"]` mutates the caller's
original dict (probe observed the source value change), so a downstream redaction writes through.
**Fix:** recurse into `dict`/`list` values (or reject container columns with REDACT), and copy them.

### [35] `_redact` ignores `value`; placeholder default duplicated
**TRUE · LATENT.**
```
app/services/field_mask.py:122-124
122:     def _redact(self, value: str, placeholder: str = "[REDACTED]") -> str:
124:         return placeholder
```
`[rt]` `MaskRule(...).placeholder == '[REDACTED]' == _redact.__defaults__[0]` — two independent
defaults for the same constant.
**Fix:** module-level `DEFAULT_PLACEHOLDER = "[REDACTED]"`, referenced by both; drop the unused arg
or use it.

## alert_engine.py — LATENT (rule engine never runs in production; see reachability map)

### [36] `evaluate()` re-fires the same alerts forever (no dedup/cooldown)
**TRUE · LATENT.**
```
app/services/alert_engine.py:107-133
107:     def evaluate(self) -> list[Alert]:
111:             List of newly triggered alerts (not previously fired)
…
132:         self._fired_alerts.extend(new_alerts)
133:         return new_alerts
```
Nothing filters against `_fired_alerts`, and rule state is never consumed. `[rt]` one
`record_dp_budget` snapshot → `evaluate()` twice → `dp_budget_exhaustion` both times,
`len(get_fired_alerts()) == 2`.
**Fix:** return only alerts whose dedup key is not already in `_fired_alerts` within a cooldown; or
hand dedup to `alert_center.build_dedup_key` (`alert_center.py:30-44`), which already implements it.

### [37] `_check_off_hours` tests the wall clock, not the event timestamp
**TRUE · LATENT.**
```
app/services/alert_engine.py:247-257
247:         now = datetime.now(timezone.utc)
248:         hour = now.hour
…
256:         recent_cutoff = now - timedelta(minutes=5)
257:         recent_queries = [(ts, uid) for ts, uid in self._query_events if ts >= recent_cutoff]
```
`ts.hour` is never read; the event `ts` is used only for the 5-minute recency filter, and the alert
message reports `now` (`:270`). Both false-positive (query at 21:59, evaluated 22:01) and
false-negative (02:00 query evaluated at 07:00) follow.
**Fix:** `hour = ts.hour` per event.

### [38] `record_dp_budget` discards its timestamp; snapshots never expire
**TRUE · LATENT.**
```
app/services/alert_engine.py:100-105   snapshot written with datetime.now(...)
…
172:         for session_id, (_, remaining, total, user_id) in self._dp_budget_snapshots.items():
```
The stored timestamp is destructured to `_` and never used, `config.window_minutes` is not consulted,
and only `clear_events()` (`:284-288`) drops snapshots. `[rt]` a single low snapshot produces an alert
on every `evaluate()` call indefinitely.
**Fix:** skip snapshots older than the window (or evict on session end).

### [39] Unlocked mutable state on a module singleton
**TRUE (code) · LATENT.**
```
app/services/alert_engine.py:75-78
75:         self._rejection_events: list[tuple[datetime, str, str | None]] = []
76:         self._query_events: list[tuple[datetime, str | None]] = []
77:         self._dp_budget_snapshots: dict[str, tuple[datetime, float, float, str | None]] = {}
78:         self._fired_alerts: list[Alert] = []
```
`evaluate` iterates `self._configs.items()` (`:115`) and `_dp_budget_snapshots.items()` (`:172`) while
`configure` (`:82`) rebinds and `record_*` append. No concurrent caller exists today (no production
importer), so no `RuntimeError: dictionary changed size during iteration` is reachable.
**Fix:** a `threading.Lock` around record/evaluate when the engine is wired.

### [40] Event and fired-alert lists grow without bound
**TRUE · LATENT.** `:86` / `:90` append; only `clear_events()` (`:284`) and `clear_fired()` (`:282`)
remove, and `get_fired_alerts()` (`:276-278`) copies the whole list each call. Every `_check_*` scans
the full list (`:142`, `:257`).
**Fix:** prune to the configured window on each `record_*` (deque with maxlen).

### [41] `_check_off_hours` ignores `config.window_minutes` and `config.threshold`
**TRUE · LATENT.**
```
app/services/alert_engine.py:256
256:         recent_cutoff = now - timedelta(minutes=5)
```
Hardcoded 5 minutes; `config.threshold` (default 1, `:65`) is never read in that function — both
knobs of the rule are dead. Contrast `_check_consecutive_rejection:141` and `_check_anomaly_burst:221`
which do use `config.window_minutes`.
**Fix:** `timedelta(minutes=config.window_minutes or 5)` and gate on `len(recent_queries) >= config.threshold`.

### [42] `logger` defined and never used
**TRUE · LATENT.** `:15 logger = logging.getLogger(__name__)`; no `logger.` call in the file.
**Fix:** log rule hits, or remove.

## cross_space_policy.py — LATENT (zero importers outside its own test)

### [43] `export_result` missing from `OPERATION_TRUST_MAP` → default rule's VERIFIED gate bypassed
**TRUE · LATENT.**
```
app/services/cross_space_policy.py:60-66
60: OPERATION_TRUST_MAP: dict[str, TrustLevel] = {
…
63:     "read_data": TrustLevel.VERIFIED,
…
66: }                      # ← no "export_result"
app/services/cross_space_policy.py:159
159:         required_trust = OPERATION_TRUST_MAP.get(operation, TrustLevel.BASIC)
```
`[rt]` `"export_result" in OPERATION_TRUST_MAP → False`; a BASIC trust listing `export_result` at
sandbox `L2` → `{'action': 'allow', 'reason': 'allowed'}`; at `L1` → denied
(`requires L2 sandbox`). So only the trust half of rule `verified-data` (`:99-105`) is bypassed; the
sandbox floor still applies.
**Fix:** add `"export_result": TrustLevel.VERIFIED`, and make an unmapped operation default to DENY.

### [44] `min_trust_level` and `require_dp_budget` are dead rule fields
**TRUE · LATENT.** Declared once and never read: `:52  min_trust_level: TrustLevel = TrustLevel.BASIC`,
`:55  require_dp_budget: bool = False`. Grep for either name in the file returns only those lines.
`[rt]` `evaluate(trust=FULL, "train_model", sandbox_level="L3")` with no `dp_budget_remaining` →
`allow` (the DP check at `:178` is `if dp_budget_remaining is not None and …`).
**Harm if wired:** rules look like policy but decide nothing; a caller that forgets the optional
argument silently skips the DP gate the module docstring advertises (`:5-6`).
**Fix:** enforce `rule.min_trust_level` / `rule.require_dp_budget` inside `evaluate`, and deny when a
rule requires a budget the caller did not supply.

### [45] `expires_at` is never checked
**TRUE · LATENT.**
```
app/services/cross_space_policy.py:143
143:         if trust.status != FederationStatus.ACTIVE:
```
`FederationTrust.expires_at: datetime | None = None` (dataclass in `federation_connector.py`).
`[rt]` a trust with `expires_at = now - 1 day`, status ACTIVE, FULL level → `allow`.
**Fix:** `if trust.expires_at is not None and now >= trust.expires_at: → DENY (reason TRUST_SUSPENDED)`.

### [46] Priority semantics inverted; typo'd sandbox level fails open
**TRUE (both) · LATENT.**
```
app/services/cross_space_policy.py:193 vs :201
193:         for rule in sorted(self._rules, key=lambda r: r.priority, reverse=True):
…
201:         self._rules.sort(key=lambda r: r.priority)
app/services/cross_space_policy.py:170
170:         if SANDBOX_LEVEL_ORDER.get(sandbox_level, 0) < SANDBOX_LEVEL_ORDER.get(required_sandbox, 0):
```
`add_rule` sorts ascending (first match = smallest priority) while `_get_min_sandbox_level` takes
descending order. `[rt]` adding `priority=1, min_sandbox_level="L3"` for `read_data` left
`_get_min_sandbox_level("read_data") == "L2"` — the stricter custom rule is ignored. `[rt]` a rule with
`min_sandbox_level="l4"` (typo) makes `_get_min_sandbox_level` return `"l4"`, and
`SANDBOX_LEVEL_ORDER.get("l4", 0) == 0` → a `L0` request is `allow`ed.
**Fix:** one ordering, and validate `min_sandbox_level` against `SANDBOX_LEVEL_ORDER` on insert
(unknown ⇒ deny).

### [47] Singleton rule list mutated without lock or duplicate-id check
**TRUE · LATENT.**
```
app/services/cross_space_policy.py:198-201
198:     def add_rule(self, rule: CrossSpacePolicyRule):
200:         self._rules.append(rule)
201:         self._rules.sort(key=lambda r: r.priority)
```
`[rt]` adding a second rule with `rule_id="default-read"` yields two rules with that id
(`['Default read access', 'dup']`). No synchronisation while `_get_min_sandbox_level` iterates.
**Fix:** validate uniqueness and replace the list atomically (`self._rules = sorted(self._rules + [rule], …)`).

### [48] `evaluate_trust_score` returns a different `TrustLevel` vocabulary
**TRUE · LATENT.**
```
app/services/cross_space_policy.py:224
224:         return score.level.value, score.score
```
It returns `trust_evaluator.TrustLevel` (`:18` imports it as `EvalTrustLevel`), not the federation
vocabulary the engine gates on (`federation_connector.py:56-59`: none/basic/verified/full).
`[rt]` `evaluate_trust_score() → ("silver", 50)` — `"silver"` is not a federation trust level, so
feeding it into `FederationTrust.trust_level`/`OPERATION_TRUST_MAP` always misses and lands on the
default BASIC branch (`:159`).
**Fix:** map explicitly (`platinum/gold → FULL`, `silver → VERIFIED`, `new → BASIC`).

### [49] Dead `EvalTrustLevel`, `logger`, `LOG_AND_ALLOW`
**TRUE · LATENT.** `[rt]` grep: `EvalTrustLevel` appears only at `:18`; `logger` only at its
definition; `LOG_AND_ALLOW` only at `:26` — no code path returns it, so nothing is ever "logged and
allowed", and DENY/ALLOW decisions are never logged.
**Fix:** remove, or emit the audit log the enum promises.

### [50] PENDING / SUSPENDED / REVOKED all reported as `TRUST_SUSPENDED`
**TRUE · LATENT.**
```
app/services/cross_space_policy.py:144-148
146:                 reason=PolicyReason.TRUST_SUSPENDED,
147:                 message=f"Trust is {trust.status.value}",
```
`[rt]` PENDING → `reason: trust_suspended, message: "Trust is pending"`; REVOKED → `reason:
trust_suspended, message: "Trust is revoked"`. The machine-readable reason mislabels a revoked
relationship as suspended in audit trails (the message is correct).
**Fix:** add `TRUST_NOT_ACTIVE` (or map per status).

### [51] `trust_order` rebuilt on every call
**TRUE · LATENT, micro-optimisation.** `:160` constructs the dict inside `evaluate`. Not a hot path
today (no callers). **Fix:** module-level constant next to `SANDBOX_LEVEL_ORDER`.

## contract_fulfillment.py — LIVE

### [52] No idempotency or row lock in `fulfill_contract`
**TRUE (narrow) · LIVE.**
```
app/services/contract_fulfillment.py:50-52
50:         result = await db.execute(select(Contract).where(Contract.id == contract_id))
51:         contract = result.scalar_one_or_none()
…
52:         if contract.status != ContractStatus.ACTIVE.value:
```
No `with_for_update()`; per product it unconditionally creates a session + container + key (`:109-141`).
The practical window is two **concurrent** activations (`api/contracts.py:196` rejects a second
sequential activation because the status is already `active`).
**Harm:** duplicate sessions/containers/distributed keys for the same `(contract_id, product_id)`;
extra keys stay alive until a lifecycle sweep reclaims them.
**Fix:** `select(Contract).where(...).with_for_update()` and a
`SandboxSession.contract_id == … AND data_product_id == … AND status IN active` existence check
before provisioning.

### [53] Exceptions after container provision leak the container and hang the session
**TRUE · LIVE.** The `not distributed_key` branch cleans up properly (`:153-171`: destroy key,
terminate container, mark FAILED); the *exception* paths do not:
```
app/services/contract_fulfillment.py:141
141:         key_result = kms_service.generate_session_key(str(session.id))
…
180:             wrapped_payload=kms_service.export_wrapped(key_result["key_id"]),
181:             sm2_encrypted_payload=kms_service.export_sm2_ciphertext(key_result["key_id"]),
```
If any of `generate_session_key` / `distribute_key` / `attestation_from_provision` /
`export_wrapped` / `export_sm2_ciphertext` raises, the exception bubbles to the per-product catch
(`:61-64`), which only records `errors` — the container keeps running, `session.status` stays at
whatever provision returned, and the key exists with no `KeyMetadata` row.
**Caller chain:** `api/contracts.py:215` (fulfilment failure itself is swallowed at `:221-227`,
logged as `contract.fulfill_failed`).
**Harm:** orphan containers + sessions that no contract-side cleanup reaches; keys without metadata
escape the gap B3 crypto-erase path (`session_lifecycle.py:109-118` finds nothing to clear).
**Fix:** wrap `:141-186` in `try/except` mirroring `:153-171` (terminate container, destroy key,
`status=FAILED`, re-raise).

### [54] `sandbox_mode not in allowed_sandbox_modes` — substring-match risk
**FALSE as stated; the real defect is a different one · LIVE.**
```
app/services/contract_fulfillment.py:231-233
231:         if contract.allowed_sandbox_modes:
232:             if session.sandbox_mode not in contract.allowed_sandbox_modes:
233:                 return {"allowed": False, …}
```
The column is JSON (`app/models/contract.py:85  allowed_sandbox_modes: Mapped[list | None] =
mapped_column(JSON)`) and the only API write path validates a `list[str]` of canonical modes
(`app/schemas/contract.py:33-46`), so the comma-string case cannot be produced through the API.
What *is* wrong: this raw comparison bypasses the canonical alias helper used everywhere else —
`api/sandbox_sessions.py:168-174 _mode_allowed_by_contract` (alias set + `.strip().lower()`), which is
also what gated session creation (`:265`) and `api/connectors.py:73` use a raw `not in` too. A session
whose mode is an execution-profile alias accepted at creation can be denied here, and a
whitespace-padded contract entry (`[" query"]`) denies legitimate modes.
**Harm:** false denials of `check_contract_constraints` (callers: `api/sandbox_tasks.py:76/111/146`),
not an allow-list bypass.
**Fix:** reuse `_mode_allowed_by_contract` in both `contract_fulfillment.py:232` and
`api/connectors.py:73`.

### [55] Blocking synchronous calls inside `async def`
**TRUE · LIVE.**
```
app/services/contract_fulfillment.py:126
126:         provision_result = runtime.provision(
…
141:         key_result = kms_service.generate_session_key(str(session.id))
148:         distributed_key = kms_service.distribute_key(
```
None is awaited; the codebase's own convention for these same primitives is
`await asyncio.to_thread(...)` (`session_lifecycle.py:79` `runtime.terminate`,
`:108 kms_service.destroy_key`; `api/sandbox_sessions.py:507` provision).
**Harm:** per-product container bring-up (seconds) plus KMS/SM2 work blocks the event loop, so a
contract activation with N products stalls every request in that worker.
**Fix:** `await asyncio.to_thread(...)` around `provision` and the four KMS calls.

### [56] Dead `else "L3"` branch; first element taken without validation
**TRUE · LIVE (consequence corrected).**
```
app/services/contract_fulfillment.py:102-103
102:         allowed_levels = (contract.allowed_sandbox_levels or "L3").split(",")
103:         sandbox_level = allowed_levels[0].strip() if allowed_levels else "L3"
```
`str.split(",")` never returns `[]`, so the `else` is dead. The substantive part: no membership/strictness
choice, and an unrecognised token is *silently* given **L3** resource limits —
`sandbox_manager.py:55-57  return RESOURCE_LIMITS.get(sandbox_level, RESOURCE_LIMITS[SandboxLevel.L3.value])`
— while the bogus string is still persisted as `session.sandbox_level` (`:111`) and passed to
`runtime.provision(level=…)` (`:128`). Also `api/contracts.py` schema defaults `allowed_sandbox_levels =
"l2c"` (`app/schemas/contract.py:21`), so the first element is frequently `l2c` — valid
(`SandboxLevel.L2C = "l2c"`) but only because of the enum's lowercase spelling; `"L2C"`/`"l3"`/`"l2"`
would not match.
**Fix:** canonicalise against `SandboxLevel` and pick the strictest allowed level; reject unknown
values instead of defaulting to L3 limits.

### [57] `max_duration_hours * 3600` can raise `TypeError` on NULL
**FALSE · n/a.**
```
app/models/contract.py:87
87:     max_duration_hours: Mapped[int] = mapped_column(Integer, default=24)
```
`Mapped[int]` (non-Optional) makes the column NOT NULL, confirmed by the generated schema:
`alembic/versions/0000_baseline_all_tables.py:305  sa.Column('max_duration_hours', sa.Integer(),
nullable=False)`, so a NULL row cannot exist (ORM or raw). Zero/negative is blocked at creation
(`app/schemas/contract.py:92-96  "max_duration_hours must be between 1 and 720"`). The
`or 24` guards elsewhere (`api/sandbox_sessions.py:270`, `api/connectors.py:467`) are defensive only.
**Fix:** none needed.

### [58] Batch termination has no per-session error isolation
**TRUE · LIVE.**
```
app/services/contract_fulfillment.py:254-258
254:         terminated = 0
255:         for session in sessions:
256:             if await terminate_session(session, db, reason=f"contract_terminated:{contract_id}"):
257:                 terminated += 1
258:         await db.flush()
```
`terminate_session` can raise despite returning bool: `SessionStatus(session.status)`
(`session_lifecycle.py:62`) on an unknown status value, and `await
asyncio.to_thread(kms_service.destroy_key, …)` (`:106-108`) which is not wrapped — unlike the
container/snapshot steps around it (`:77-81`, `:87-103`). One bad session aborts the loop, so the
remaining sessions keep containers/keys and `contract.terminate_sessions` is never audited (`:261-266`).
**Caller chain:** `api/contracts.py:245`, `contract_service.py:213-214`.
**Fix:** `for session in sessions: try: … except Exception: logger.exception(...)` and keep counting.

### [59] Dead imports
**TRUE · LIVE file, cosmetic.** `:22 from datetime import datetime, timedelta, timezone` (no other
occurrence), `:24 from sqlalchemy import select, func` (`func.` never used), `:29 … SandboxLevel`
(the only occurrence). **Fix:** remove the three names.

### [60] Eager f-string logging
**TRUE · LIVE, style.** `:63  logger.error(f"Failed to provision session for product {product_id}: {e}")`.
**Fix:** `logger.error("Failed to provision session for product %s", product_id, exc_info=True)`.

---

## Score

| file | claims | TRUE·LIVE | TRUE·LATENT | FALSE |
|---|---|---|---|---|
| merkle_service.py | 5 | 1 (dead code) | 4 | 0 |
| dp_budget.py | 7 | 4 | 1 | 2 |
| quota_manager.py | 12 | 5 | 7 | 0 |
| field_mask.py | 11 | 0 | 10 | 1 |
| alert_engine.py | 7 | 0 | 7 | 0 |
| cross_space_policy.py | 9 | 0 | 9 | 0 |
| contract_fulfillment.py | 9 | 6 | 0 | 3 |
| **Total** | **60** | **16** | **38** | **6** |

Highest-value LIVE items: `dp_budget` never-invalidated per-process cache vs 2 API replicas ([10]) and
cache/DB divergence after rollback ([8]); gateway counters incremented with no rollback ([15]) and
silent ledger switch on transient Redis errors ([20]); contract fulfillment's post-provision exception
cleanup gap ([53]) and event-loop-blocking external calls ([55]).
