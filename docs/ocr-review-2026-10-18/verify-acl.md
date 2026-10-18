# Verification: field_acl.py / encrypted_db.py static-review findings

- Repo: `D:/workspace/sandbox` @ branch `feat/ss-11-productization`
- Verifier: read-only code audit + in-process behavioural probe (no DB, no API, no repo mutation)
- Date: 2026-10-18
- Input: `ocr-findings.json` filtered to `app/services/field_acl.py` + `app/services/encrypted_db.py` → **23 findings** (the 13 named in the brief = severity high/critical; 10 more at medium/low are adjudicated in §4)
- Method rule applied: finding text is never evidence; every claim checked against the file, then callers grepped, then the behaviour executed where it was cheap to execute.

## 0. Decisive reachability fact (applies to every finding below)

**Both modules are orphaned code. Neither has a single production caller.**

Greps (whole repo, all file types, `tool.grep`, respect `.gitignore`):

| Pattern | Matches |
|---|---|
| `FieldACLService\|field_acl_service` | `app/services/field_acl.py:30,163` (self) + `tests/test_field_acl.py:4,9,12` — nothing else |
| `EncryptedDatabase\|encrypted_db` | `app/services/encrypted_db.py:28,50,54,250` (self) + `tests/test_encrypted_db.py:3,9` + `tests/test_e2e_flows.py:995,997,1007,1010` — nothing else |
| `filter_fields\|apply_masks\|build_sql_projection\|validate_aggregate_query` | definitions in `field_acl.py:54,93,131,142` + `tests/test_field_acl.py` only |
| `create_masked_view\|load_table(` | in `encrypted_db.py` + tests; the production hits are a **different class**: `app/api/sandbox_db.py:163` → `app/services/secure_duckdb.py:241` |
| non-`.py` refs to either module | only prose in `docs/` and `specs/` (design intent, never an import) |
| dynamic import of either | none. `app/services/retention_janitor.py:70` `__import__(module_name, …)` resolves ORM **models** from a retention plan, never `app.services.field_acl` / `app.services.encrypted_db` |

No router imports either module, so no `include_router` entry, no scope group and no role can reach them: they are not mounted anywhere in `app/main.py:322-377`.

The live equivalents are:

| Concern | Dead module (in scope) | Live code path |
|---|---|---|
| field-level ACL from `PolicyBundle.field_acl` | `app/services/field_acl.py` | `app/services/policy_evaluator.py:244-266` `_evaluate_field_acl` — **also dead**: `from app.services.policy_evaluator` appears only in `tests/test_policy_evaluator.py:6`. The live DuckDB gate uses `app/services/policy_compiler.py:463-493` (`secure_duckdb.py:136-143`), which checks level/operation/duration/DP-epsilon and **never looks at fields** |
| DuckDB masked views | `app/services/encrypted_db.py` | `app/api/sandbox_db.py` (mounted `app/main.py:340`, prefix `/api/v1/sandbox-db`, `Depends(require_group("pipeline"))`) → `app/services/secure_duckdb.py::SecureDuckDBEngine`, real `CREATE OR REPLACE VIEW` at `secure_duckdb.py:252,281`, identifiers scrubbed by `_sanitize_identifier` (`secure_duckdb.py:405-407`, `re.sub(r"[^a-zA-Z0-9_]", "_", name)`) |

CI does not protect either module: neither `tests/test_field_acl.py` nor `tests/test_encrypted_db.py` is in the must-be-green gate list (`.github/workflows/ci.yml:54-91`); they only run in the informational full-suite tier.

**Consequence for verdicts:** per the brief's rule, "no live caller" ⇒ **LATENT**. Where I write LATENT below, read it as "the code property is real (and mostly reproduced by execution) but nothing in the shipped request path can reach it." Severity is given twice: *current* (with zero callers) and *if wired* (the day someone imports the module).

## 1. Probe used as evidence

`C:/Users/清彧/AppData/Local/Temp/ocr-cds/probe_acl.py` and `probe_acl2.py`, run with the repo venv (`Python 3.12.10`, `duckdb 1.5.5`, `gmssl` present — matches the production image's minor version). Pure in-process calls of `EncryptedDatabase` / `FieldACLService`; nothing else touched. Quoted probe lines below are verbatim stdout.

---

## 2. `app/services/encrypted_db.py` — 6 named findings

### B1. Masked view is not an enforcement boundary — base table readable (JSON idx 0, claimed *critical*)

**Verdict: TRUE (code property, reproduced) — LATENT (no caller). Current severity: none (dead). If wired: High/Critical.**

Evidence — routing depends only on whether a view name appears in the SQL text (`encrypted_db.py:125-131`):

```python
125        view_name = self._detect_view_query(sql)
126        if view_name and view_name in self._views:
127            result = self._query_masked_view(view_name, sql)
128        elif self._conn:
129            result = self._query_duckdb(sql)
```

`_query_duckdb` (`:136-141`) runs the SQL verbatim on the same connection that holds the **unmasked** base tables (`load_table` `:85`), so masking is advisory. Reproduced:

```
SELECT * FROM users            -> detect None  ; RAW SSN LEAKED: True
SELECT u.name, u.ssn FROM users u JOIN users_safe s ON u.name = s.name
                               -> detect users_safe ; rows [['Alice','110101199001011234'], ['Bob','110101199001011234']]
SELECT ssn FROM users WHERE name IN (SELECT name FROM users_safe)  -> raw leak: True
```

The last two are the strongest form of the claim: the query *does* mention the masked view, so the code believes it is on the masked path, rewrites only the token `users_safe` (see B3) and leaves the raw base table joined in — plaintext `ssn` returned with no error.

Reachability: **none.** `EncryptedDatabase` is constructed only in `tests/test_encrypted_db.py:9` and `tests/test_e2e_flows.py:997,1010`. No endpoint exists, therefore no business user and no sandbox can reach it.

Answer to brief item 3 (does sandbox SQL reach this?): **No.** Untrusted SQL enters at `POST /api/v1/sandbox-db/query` (`app/api/sandbox_db.py:126-138`, `Depends(get_current_user)` → any authenticated user who owns the session per `_authorize_session_engine` `app/api/sandbox_db.py:33-56`; machine keys need scope `pipeline:*`, `app/core/scopes.py` `GROUP_PREFIXES` → `/api/v1/sandbox-db`), and lands in `SecureDuckDBEngine.execute_query` (`secure_duckdb.py:284-309`), which executes it verbatim (`self._conn.execute(sql)`, `:309`) after a statement-prefix denylist (`:298`). That chain never calls `encrypted_db.query`/`_query_masked_view`/`_detect_view_query`.

Impact: as shipped, zero. The risk is resurrection risk — the module looks like a finished, tested security component (it has green tests) and would silently under-mask the first time someone wires it to a sandbox SQL surface.

Minimal fix: make masking a DB object instead of a text heuristic — register only masked **views** in the connection (`CREATE VIEW` like `secure_duckdb.py:281`), keep base tables in a separate attached database/alias that the query connection cannot name, and reject SQL that references the raw alias.

### B2. View detection is a case-insensitive substring match (idx 1, *high*)

**Verdict: TRUE (reproduced) — LATENT. Current: none. If wired: High.**

```python
221    def _detect_view_query(self, sql: str) -> str | None:
223        sql_lower = sql.strip().lower()
224        for view_name in self._views:
225            if view_name.lower() in sql_lower:
226                return view_name
```

Two failure modes, both executed:

1. false hit inside a literal — `SELECT name FROM users WHERE note = 'see users_safe_x'` → `detect -> users_safe`; the statement is silently rerouted to the masked path against the wrong object (returned 0 rows instead of the intended rows — a wrong answer, not an error);
2. prefix-colliding view names + dict insertion order — with `users_safe` and `users_safe_v2` both registered, `SELECT name, ssn FROM users_safe_v2` → `detect -> users_safe`; the rewrite then targets the non-existent `_masked_users_safe_v2`, DuckDB errors, and the except-branch (B4) returns rows masked by **the other view's** rules:

```
DuckDB masked query failed, returning raw masked data: Catalog Error: Table with name _masked_users_safe_v2 does not exist!
masked_columns reported: ['ssn']
rows: [['Alice','55b3ade0…','see users_safe_x'], ['Bob','55b3ade0…','plain']]
```

i.e. the caller asked for the view whose rule REDACTs `name` and got plaintext names back, in an OK-looking result, with extra columns it never selected.

Reachability: none (same chain as B1). Minimal fix: parse the statement and match the referenced relation exactly (or drop detection entirely per B1's fix); never substring-match identifiers in raw SQL text.

### B3. `str.replace` rewrites the SQL everywhere, including literals (idx 2, *high*)

**Verdict: TRUE (reproduced) — LATENT. Current: none. If wired: High.**

```python
182                # Rewrite query to use temp table
183                rewritten = sql.replace(view_name, temp_name)
184                cur = self._conn.execute(rewritten)
```

No tokenizer, no word boundary, no quoting-awareness; replaces **all** occurrences. Reproduced:

```
rewritten -> SELECT name, ssn FROM _masked_users_safe WHERE name = '_masked_users_safe'
row_count 0 rows []      (control without the predicate: row_count 2)
```

A user value that happens to equal the view name is silently rewritten, so the query returns the wrong rows with no diagnostic. It is also the mechanism that makes B2's prefix collision blow up (wrong temp name), and — per B1 — it rewrites only the view token, leaving every other relation in the statement pointing at raw data.

Reachability: none. Minimal fix: bind the masked data as a real named relation (a DuckDB `VIEW`/CTE alias) and execute the user's SQL unchanged, or at minimum substitute a parsed relation reference.

### B4. Masked-query failure degrades to "return everything" (idx 3, *high*)

**Verdict: TRUE as over-return + swallowed error; the wording "returns full unmasked rows" is imprecise — masking still applies to the columns the view rules cover. — LATENT. Current: none. If wired: Medium.**

```python
194            except Exception as e:
195                logger.warning(f"DuckDB masked query failed, returning raw masked data: {e}")
196
197        # Fallback: return all masked rows
198        return QueryResult(
199            columns=columns,          # ALL source-table columns
200            rows=masked_rows,         # ALL rows
```

Any DuckDB error (bad column, bad function, missing temp table) is converted into a successful result that ignores `WHERE`, projection, `LIMIT` and aggregation. Reproduced with a deliberate binder error:

```
sql = "SELECT name, nonexistent_col FROM users_safe"
except-branch row_count: 2 cols ['name', 'ssn', 'note'] masked ['ssn']
```

`note` — never requested, and outside the mask rules — comes back in the clear, and an invalid query is reported as success. So the honest characterisation is *scope widening + error suppression*, not "masking was skipped".

Reachability: none. Minimal fix: `raise` on the DuckDB error instead of falling through to the whole-table return (project the caller's column list and row predicate in the fallback, or refuse).

### B5. Table/column identifiers interpolated into DDL (idx 4, *high*)

**Verdict: LATENT, and the exploit claim is FALSE as tested. Current: none. Realistic severity: Low.**

```python
84            col_defs = ", ".join(f'"{c}" VARCHAR' for c in columns)
85            self._conn.execute(f'CREATE OR REPLACE TABLE "{name}" ({col_defs})')
```

The claim's premise is right (identifiers are wrapped in `"` but an embedded `"` is not doubled) — the live module is stricter (`secure_duckdb.py:405-407` strips every non-`[A-Za-z0-9_]`). But the claimed outcome (statement injection) did not materialise on `duckdb 1.5.5`; all three payloads fail closed with a parse error:

```
load_table('t", "pwned VARCHAR', …)  -> ParserException: syntax error at or near ","
load_table("t2", ['a", "pwned VARCHAR']) -> ParserException: syntax error at or near "VARCHAR"
load_table("t3", ['z"; CREATE TABLE pwned(i INT); --']) -> ParserException: syntax error at or near ";"
pwned query: Catalog Error / ParserException — injected table never created
```

So the residual weakness is malformed-DDL → exception (noise/DoS at worst), not arbitrary SQL. Combined with zero callers and a caller contract that would supply developer-chosen names, this is a hardening item, not a high. Minimal fix: apply the existing `_sanitize_identifier` (or double the quotes) in `load_table` / `_query_masked_view`.

### B6. Fallback mode returns raw rows (idx 5, *high*)

**Verdict: TRUE (reproduced) — LATENT, and doubly unreachable. Current: none. If wired: Medium.**

```python
206    def _query_fallback(self, sql: str):
210        for name, data in self._tables.items():
211            if f"from {name}" in sql_lower:
212                return QueryResult(columns=data["columns"], rows=data["rows"],
213                                    … masked_columns=[], …)
```

Reproduced with `HAS_DUCKDB` forced false:

```
fallback columns ['name', 'ssn'] rows [['Alice', '110101199001011234']] masked []
raw leak: True (projection 'name' ignored)
```

Reachability is blocked twice over: (a) no callers at all; (b) `duckdb>=1.0.0` is a hard dependency (`requirements.txt:49-50`), so `HAS_DUCKDB` is true in any image built from requirements — the branch only opens if duckdb is deliberately uninstalled, or after `close()` sets `self._conn = None` (`:242-246`) and `query()` falls through `:128-131`. The module-level singleton at `:250` also means an ImportError-time `logger.warning` (`:28`) is the only disclosure, consistent with the project's honest-degradation rule but not with fail-closed. Minimal fix: raise instead of returning unmasked rows in `_query_fallback` (only masked views should be answerable without DuckDB).

---

## 3. `app/services/field_acl.py` — 7 named findings

All seven share one reachability answer, stated once: `FieldACLService` is instantiated at `field_acl.py:163` and referenced by nothing but `tests/test_field_acl.py`. No router, service, runner template or SDK surface imports it (`grep field_acl` repo-wide: the other hits are `app/models/policy_bundle.py:28` for the JSON column, `app/services/policy_evaluator.py` which re-implements the logic in its own private method, and `tests/test_p0_security_gaps.py:291` which only greps this file's text in the SM3-replacement check). So: **every field_acl finding is LATENT; every one of them is a real code property.**

### A1. `parse_rules` trusts the policy dict (idx 6, *high*)

**Verdict: TRUE — LATENT. If wired: Medium (crash / policy bypass by omission).**

```python
46        for field_name, config in acl_config.items():
47            rules[field_name] = FieldRule(
48                read=config.get("read", False),
```

Executed: `parse_rules({"ssn": None})` → `AttributeError: 'NoneType' object has no attribute 'get'`; same via `filter_fields(["ssn"], {"ssn": None})`. A YAML author writing `ssn:` with no body therefore turns a *deny* rule into a 500 (or, if a caller swallows exceptions, into an invisible gap). No type check on `read`/`aggregate_only`, and `mask_pattern` is accepted unvalidated (see A4/A5, and C17 for the fallback branch). The identical fragility exists in the sibling dead twin `policy_evaluator.py:259-262` (`rule.get("read", False)`).
Minimal fix: validate the bundle shape at load (`isinstance(config, dict)`, coerce/raise) before parsing, and treat a malformed rule as deny.

### A2. Fail-open default (empty config and unknown field both allow) (idx 7, *high*)

**Verdict: TRUE as written — and it is deliberate, test-pinned design. LATENT. If wired: High (default-allow on unknown columns).**

```python
58        if not acl_config:
59            return FieldACLResult(allowed_fields=requested_fields, …)
…
74            if rule is None:
75                # No specific rule → allow
76                allowed.append(field_name)
```

Executed: `filter_fields(["a", "id_card"], {})` → `allowed: ['a', 'id_card']`. The risk named in the finding is exactly right for a masking component: a renamed or newly added sensitive column (`id_card`, `phone`) is visible until someone edits the bundle. Counterweight, stated honestly: the same semantics are asserted by tests (`tests/test_field_acl.py:12-16`, and the twin `tests/test_policy_evaluator.py:150 test_no_field_acl_allows_all`), so this is a policy decision recorded in code, not an oversight — and changing it needs a spec call (deny-by-default for a data-space product is the defensible choice, matching `policies/system.rego`'s `default allow = false`).
Minimal fix: invert to deny-on-unknown for sensitive-classified fields, or require an explicit `default: allow|deny` key in the bundle.

### A3. `aggregate_only` fields are also returned in `allowed_fields` (idx 8, *high*)

**Verdict: TRUE — LATENT. If wired: High.**

```python
77            elif rule.read:
78                allowed.append(field_name)
…
81                if rule.aggregate_only:
82                    aggregate_only.append(field_name)
```

and `build_sql_projection` hands both lists straight out (`:139-140`), so the aggregate-only column stays in the SELECT list. Executed:

```
allowed: ['name', 'income']  aggregate_only: ['income']
build_sql_projection: (['name', 'income'], ['income'])
```

`income` in `allowed_fields` is what a caller would interpolate into a projection; `aggregate_only_fields` is a second, ignorable return value. Pinned by `tests/test_field_acl.py:103-108` (`assert "income" in select_fields`), so the test currently locks in the unsafe shape.
Minimal fix: keep aggregate-only fields out of `allowed_fields` (own bucket), and have `build_sql_projection` emit aggregates only as `agg(col)` expressions.

### A4. `prefix:N` parsed with no validation or range check (idx 9, *high*)

**Verdict: TRUE, with one detail corrected (`prefix:` raises `ValueError`, not `IndexError`) — LATENT. If wired: High (negative N defeats the mask).**

```python
123        elif pattern.startswith("prefix:"):
124            n = int(pattern.split(":")[1])
125            return str_val[:n] + "***"
```

Executed:

```
'prefix:3'   -> '138***'
'prefix:'    -> RAISES ValueError: invalid literal for int() with base 10: ''
'prefix:abc' -> RAISES ValueError: invalid literal for int() with base 10: 'abc'
'prefix:-1'  -> '1381234567***'      <-- 10 of 11 digits survive: mask defeated
'prefix:99'  -> '13812345678***'     <-- full value disclosed
```

Two distinct problems, both real: a config typo aborts the whole result set (uncaught `ValueError` out of `apply_masks`), and a negative `N` turns the mask inside out. Note the finding predicted `IndexError` for `prefix:` — `split(":")[1]` yields `""`, so the type is `ValueError`; the crash class and the impact stand.
Minimal fix: `n = int(…)` inside `try`, reject `n < 1` or `n >` a sane cap, and fall back to full redaction with a logged warning.

### A5. `mask_pattern == "sha256"` implemented as truncated SM3 (idx 10, *high*)

**Verdict: TRUE (reproduced, including the environment-dependent algorithm) — LATENT. If wired: High for low-entropy identifiers.**

```python
120        elif pattern == "sha256":
121            from app.services.crypto_service import crypto_service
122            return crypto_service.sm3_hash(str_val.encode())[:16]
```

Confirmed by execution: the docstring/`FieldRule` comment (`:20`) advertises `"sha256"`; `crypto_service.sm3_hash` returns a 64-char digest (`crypto_service.sm3_hash('x') = b9e036c07be7c1df36f69e63504da93b…`), and the mask keeps `[:16]` — **64 bits** of output, so `len == 16` (asserted by `tests/test_field_acl.py:72-76`). Which algorithm actually ran depends on the deployment: with `gmssl` present here it is genuine SM3; without it `crypto_service.sm3_hash` delegates to the gated SHA-256 fallback (`app/services/crypto_service.py:109-122`, `app/core/config.py:211 SM3_FALLBACK_ALLOWED: bool = True`, prod-refused at `:469-474`) — so an operator reading "sha256" in the bundle may get SM3 or SHA-256 depending on the image, and gets neither of the 256 bits the name implies. The bigger practical issue the finding under-states: it is an **unsalted** digest, so an 18-digit ID or 11-digit phone is recovered by enumeration regardless of truncation.
Minimal fix: rename the pattern to what it does (`sm3-128` style) or use a keyed/kemask construction (HMAC with a per-bundle key) and stop truncating below the value's entropy.

### A6. Aggregate-only validation keyed on one global boolean (idx 11, *high*)

**Verdict: TRUE — LATENT. If wired: High.**

```python
150        if not result.aggregate_only_fields:
151            return []
152
153        if has_aggregation:
154            return []
```

`has_aggregation` is a caller-supplied flag with no per-field binding: one `COUNT(*)` anywhere whitelists every aggregate-only column, which is precisely the "add a `COUNT(*)` to the SELECT list and read the plaintext" bypass the finding describes. Executed: `validate_aggregate_query(["income"], {"income": {"read": True, "aggregate_only": True}}, True)` → `[]` (no violation). Nothing in the repo computes that flag — the only caller is `tests/test_field_acl.py:111-118`.
Minimal fix: validate per field — require each aggregate-only column to appear wrapped in an aggregate function (and absent from a bare projection), instead of trusting a boolean.

### A7. Returned field list is a SQL fragment with no identifier validation (idx 12, *high*)

**Verdict: TRUE as a code property — LATENT (nothing interpolates it). If wired: High (SQL injection).**

```python
131    def build_sql_projection(self, requested_fields: list[str], acl_config) -> tuple[list[str], list[str]]:
139        result = self.filter_fields(requested_fields, acl_config)
140        return result.allowed_fields, result.aggregate_only_fields
```

With an empty ACL everything passes through verbatim (A2), so the "safe" projection list is just the caller's strings: executed, `build_sql_projection(['x" FROM users; DROP TABLE t--'], {})` → `(['x" FROM users; DROP TABLE t--'], [])`. No allow-list against the table schema, no quoting, no `_sanitize_identifier` equivalent. Because the return type is "list of field names to put after SELECT", the intended usage is exactly the f-string join that becomes injection. Mitigating fact: **no caller exists**, so nothing interpolates it today, and the two ACL outputs (`allowed_fields`) are consumed only by tests.
Minimal fix: intersect `requested_fields` with the known column set and emit double-quoted, schema-validated identifiers (mirroring `secure_duckdb.py:405-407`).

---

## 4. Remaining in-scope findings (same two files, medium/low) — quick adjudication

All inherit §0: **zero production callers ⇒ LATENT.**

| # | Claim (JSON idx) | Verdict | Evidence | Note |
|---|---|---|---|---|
| C13 | `_masked_*` temp name built from the view name; persisted, not `TEMPORARY` (13) | TRUE / LATENT | `encrypted_db.py:177-181` `CREATE OR REPLACE TABLE "_masked_{view}"` | Masked snapshot lingers on the connection, contradicting the header comment "masking is applied at query time, not stored" (`:12`); also the name is user-influenced (see B5) |
| C14 | Process-global singleton + fixed temp-table name → cross-call interference (14) | TRUE / LATENT | `:250` `encrypted_db = EncryptedDatabase()` (opens a DuckDB connection at import); `:178` fixed `_masked_{view}` | Two concurrent `query()` calls on one instance race on the same temp table; nothing else would leak across because no one shares the instance today |
| C15 | `columns`/`rows` stored by reference; Python snapshot and DuckDB copy diverge (15) | TRUE / LATENT | `:80` `self._tables[name] = {"columns": columns, "rows": rows}` | Masked views read the Python copy (`:161-162`), DuckDB queries read the table copy — caller mutation silently changes one but not the other |
| C16 | `cur.description` is `None` for DDL/DML → `TypeError` (16) | TRUE / LATENT | `:140`, `:185` iterate `cur.description` unguarded | `_query_duckdb` has no statement-type gate (unlike the live path's denylist, `secure_duckdb.py:298`) |
| C17 | Unknown `mask_pattern` returned verbatim as the value (17) | TRUE (reproduced) / LATENT | `field_acl.py:128-129` `else: return pattern`; probe: `'SHA256' -> 'SHA256'`, `'Prefix:3' -> 'Prefix:3'`, `'MASK' -> 'MASK'` | Silent: replaces data with attacker/config-controlled text and logs nothing (`logger` never called, C22). Data destroyed rather than leaked |
| C18 | Shallow `dict(row)` + masks applied only to requested∩row keys (18) | TRUE / LATENT | `:102` `masked_row = dict(row)`; `:104` `if field_name in masked_row` | Nested JSON values shared with the caller's object; a `SELECT *` result carries unmasked columns that were never requested |
| C19 | Missing source table returns empty result silently (19) | TRUE / LATENT | `:158-159` | "no rows" indistinguishable from a mis-wired view |
| C20 | Unused imports `hashlib`, `field`, `datetime`, `timezone` (20) | TRUE / LATENT | `encrypted_db.py:14-17`; no reference anywhere in the file | Cosmetic |
| C21 | `MaskMode` imported but unused; `import time` inside `query()` (21) | PARTLY TRUE / LATENT | `:19` — `MaskMode` appears only in the docstring (`:56`), so "unused in code" is right; `import time` at `:121` | Style only |
| C22 | Unused `re`/`field`, `logger` never called in `field_acl.py` (22) | TRUE on all three counts — LATENT | `grep "re\\.\|field(" app/services/field_acl.py` → no matches (exit 1), so `import re` (`:6`) and `dataclasses.field` (`:8`) are dead; `logger` (`:10`) has zero call sites | The substantive half: a security module logs nothing — no record of a field being denied, of an illegal mask pattern, or of the A1/A4 parse crashes |

---

## 5. Out-of-scope observation the verification forced (worth a separate review item)

The dead module and the live module encode the *same* security assumption — **a masked view is not a boundary, because the raw table stays addressable in the same connection** — but the live path actually has callers:

- `POST /api/v1/sandbox-db/query` accepts arbitrary SQL from any authenticated session owner (`app/api/sandbox_db.py:126-138`), and `SecureDuckDBEngine.execute_query` only rejects statements *starting* with a denylisted keyword (`secure_duckdb.py:298-301`) before `self._conn.execute(sql)` (`:309`).
- `create_masked_view` builds a real DuckDB view over a table that remains in the same schema (`secure_duckdb.py:281`), and `_check_policy` (via `policy_compiler.py:463-493`) evaluates level/operation/duration/DP only — `field_acl` bundles are never consulted for field masking in this path.

So `SELECT * FROM <base_table>` over the same `session_id` returns unmasked rows on the live endpoint. That is not one of the 13 claims and I did not verify it by execution here (it needs the API + DuckDB engine), so treat it as a lead, not a finding.

## 6. Score and assumptions

- **TRUE (code property confirmed, mostly by execution): 13/13 named findings** — with three corrections to the finding text: B4's rows stay masked for rule-covered columns (the defect is over-return + swallowed error), B5's injection payload does not inject on duckdb 1.5.5 (fails closed with a parse error), A4's missing-digit case raises `ValueError`, not `IndexError`.
- **LATENT: 13/13** — neither module is reachable from any endpoint, service, runner or the SDK; both are shadowed by live implementations (`policy_compiler.PolicyEvaluator`, `secure_duckdb.SecureDuckDBEngine`), and their tests sit outside the CI gate list.
- **FALSE: 0/13 outright**; 3 findings partially false as worded (B4, B5, A4 — details above). Appendix: 10/10 TRUE code properties except C21's `import time` point (style-only, not a defect).
- Assumption 1: per the brief, LATENT is assigned from caller absence, and severity is reported both *current* and *if wired* rather than collapsed to one number.
- Assumption 2: probe results from the local venv are treated as evidence about these two self-contained modules only (pure functions over DuckDB/string parsing); the project rule that the authoritative *suite* runs remotely is untouched — no suite was run and nothing in the repo was modified.
