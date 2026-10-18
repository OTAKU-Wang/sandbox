# ocr finding verification — `app/services/rls_engine.py`, `app/services/pg_rls_manager.py`

36 claims (20 rls_engine / 16 pg_rls_manager). Read-only pass; `git status --porcelain` verified empty before and after.

**Headline: 0 findings are LIVE.** `pg_rls_manager.py` has *zero* importers anywhere in the repo (not even a test). `rls_engine.py` has exactly one importer, `app/services/db_access_proxy.py:17`, and `db_access_proxy.py` itself has zero importers inside `app/` — only `tests/test_db_access_proxy.py` and `tests/test_sprint4_services.py` import it. So every TRUE finding is **LATENT ("only tests import it", via an equally unwired proxy)**; there is no `SANDBOX_LEGACY_ENABLED`-style gate involved — the modules are simply never imported by the app. This matches the repo's known unwired-service pattern and the repo's own docs: `docs/spec-implementation-analysis.md:494` "PostgreSQL-in-TEE 完整架构 … ❌ 未实现 … 无任何 PG-in-TEE 代码" and `docs/ai-sandbox-gap-review-20260906.md:28` "分级相关代码只在 `rls_engine.py`/`clickhouse_schema.py` 内部使用".

## Reachability map

| Entry point | Chain | Live? |
|---|---|---|
| (none) | — → `app/services/pg_rls_manager.py` | **No.** AST reverse-import closure over all of `app/`: 0 importers; repo-wide grep (`app/ sdk/ cds-volume-plugin/ alembic/ tests/ deploy/ scripts/ k8s/ helm/`) hits only `pg_rls_manager.py` itself + `specs/SS-gap-analysis.md:182` (FG-006 "PG-in-TEE/生产密态数据库后端未落地"). No test imports it either. |
| `pytest tests/test_db_access_proxy.py`, `pytest tests/test_sprint4_services.py` | test → `db_access_proxy.py:17` → `rls_engine.evaluate_sql` / `inject_rls_where` (`db_access_proxy.py:120,127`) | **test-only** |
| `pytest tests/test_rls_engine.py`, `tests/test_sprint4_services.py::test_rls_*` | test → `rls_engine` directly | **test-only** |
| `app/api/*.py` (45 routers), `app/main.py` lifespan, task workers | — | **No.** `main.py` and every router: `app.main` reaches neither module (AST closure: `app.main in closure == False`). |

Verification command (AST reverse-import closure, `C:/Users/清彧/AppData/Local/Temp/ocr-cds/rev_imports.py`, run with `.venv-gate` py3.12):

```
TARGET app.services.rls_engine       direct: ['app.services.db_access_proxy']  transitive: ['app.services.db_access_proxy']  app/api: NO  main.py: False
TARGET app.services.pg_rls_manager   direct: NONE                              transitive: NONE                              app/api: NO  main.py: False
TARGET app.services.db_access_proxy  direct: NONE                              transitive: NONE                              app/api: NO  main.py: False
```

Baseline (informational, local gate venv — not the authoritative remote lane): `pytest -q tests/test_rls_engine.py tests/test_db_access_proxy.py tests/test_sprint4_services.py` → **50 passed**.

## Verdict summary

| # | idx | file | sev | one-line | verdict |
|---|---|---|---|---|---|
| 1 | 461 | pg | critical | FORCE RLS + `TO <session_role>`-only policy locks out every other role | TRUE · latent |
| 2 | 462 | pg | critical | `session_id` unencoded into DSN `options` → GUC injection | TRUE (escalation part overstated) · latent |
| 3 | 463 | pg | critical | `CREATE ROLE` without password; DSN user rewrite silently no-ops | TRUE (+ extra colon) · latent |
| 4 | 464 | pg | high | role-name sanitize+truncate → collisions / shared `sandbox_default` | TRUE · latent |
| 5 | 465 | pg | high | interpolated `SET LOCAL cds.session_id`; no `set_config` | TRUE · latent |
| 6 | 466 | pg | high | tracking row deleted even when `DROP ROLE` failed | TRUE · latent |
| 7 | 1325 | pg | medium | `REVOKE … IN SCHEMA public` hardcoded; cleanup from process dict | TRUE · latent |
| 8 | 1326 | pg | medium | `admin_dsn` stored, never used | TRUE · latent |
| 9 | 1327 | pg | medium | `row_security` GUC used as RLS-support probe + permanent negative cache | TRUE (degradation is disclosed) · latent |
| 10 | 1328 | pg | medium | policy-setup failure swallowed; `RLSStatus.error` never set; not idempotent | TRUE · latent |
| 11 | 1329 | pg | medium | grants recorded only if in-memory info exists | TRUE (truncation sub-claim FALSE) · latent |
| 12 | 1330 | pg | medium | singleton dict + check-then-act around `CREATE ROLE` | TRUE (impact overstated) · latent |
| 13 | 1331 | pg | medium | `grant_table_access(role=…)` accepts any role | TRUE as code fact · latent |
| 14 | 1834 | pg | low | `DELETE` silently skipped yet method returns `True` | TRUE · latent |
| 15 | 1835 | pg | low | f-string logging | TRUE (style) · latent |
| 16 | 1836 | pg | low | existence-check failure → non-idempotent `CREATE ROLE` error | TRUE · latent |
| 17 | 510 | eng | critical | region policy returns `None` (no filter) when config/context incomplete | TRUE · latent |
| 18 | 511 | eng | critical | `max_allowed_level` never caps anything | TRUE (partly test-pinned) · latent |
| 19 | 512 | eng | high | `*_column` from policy conditions interpolated as identifiers | TRUE · latent |
| 20 | 513 | eng | high | `_escape` only doubles quotes → "unreliable" | **FALSE** as stated (PG-only, `standard_conforming_strings=on`) |
| 21 | 514 | eng | high | `rfind(" WHERE ")` lands in the innermost subquery | TRUE · latent |
| 22 | 515 | eng | high | `strip()` ≠ whitespace normalisation → double `WHERE` | TRUE (LIMIT sub-claim FALSE) · latent |
| 23 | 516 | eng | high | `user_clearance` interpolated unvalidated in `inject_filter` | TRUE · latent |
| 24 | 517 | eng | high | row-level region check doesn't require `row.region == user.region` | TRUE · latent |
| 25 | 518 | eng | high | `user_clearance >= max_level` inverts the ceiling | TRUE · latent |
| 26 | 519 | eng | high | unknown `policy_type` → allow | TRUE · latent |
| 27 | 1396 | eng | medium | `find()` on GROUP BY/ORDER BY/LIMIT hits subqueries; no HAVING | TRUE · latent |
| 28 | 1397 | eng | medium | `_inject_where` omits parentheses → `AND`/`OR` precedence | TRUE (worse than claimed) · latent |
| 29 | 1398 | eng | medium | unknown clearance → `security_level IN ()` syntax error | TRUE · latent |
| 30 | 1399 | eng | medium | `evaluate_sql(table_name=…)` parameter unused | TRUE · latent |
| 31 | 1400 | eng | medium | re-import of `datetime`; naive local hour; cross-midnight window | TRUE (cross-midnight = fail-closed) · latent |
| 32 | 1401 | eng | medium | singleton `_policies` dict unsynchronised | TRUE as code fact, unreachable in this app's model · latent |
| 33 | 1866 | eng | low | `_time_condition` ignores `context["request_time"]` | TRUE · latent |
| 34 | 1867 | eng | low | f-string logging | TRUE (style) · latent |
| 35 | 1868 | eng | low | `import re` unused | TRUE · latent |
| 36 | 1869 | eng | low | `SensitivityLevel.__members__.values()`; no column validation | TRUE (maintainability only) · latent |

Runtime evidence below comes from `probe_rls.py` / `probe_pgrls.py` / `probe_fixes.py` under `C:/Users/清彧/AppData/Local/Temp/ocr-cds/`, executed with `D:/workspace/sandbox/.venv-gate/Scripts/python.exe` (py3.12.10) and `PYTHONPATH=D:/workspace/sandbox`. Nothing marked CLOSED in `app/services/AGENTS.md` or `docs/` covers either module; the honest-degradation/simulation rules do not apply here (no `is_simulation` path, no simulation ladder in either file).

---

# app/services/pg_rls_manager.py

## 1 · idx 461 · critical/security — `FORCE ROW LEVEL SECURITY` + a policy scoped to one session role denies every other role

**Verdict: TRUE · LATENT** (module has zero importers).

Code read:

```
pg_rls_manager.py:216  await db.execute(text(f'ALTER TABLE "{safe_table}" ENABLE ROW LEVEL SECURITY'))
pg_rls_manager.py:221  await db.execute(text(f'ALTER TABLE "{safe_table}" FORCE ROW LEVEL SECURITY'))
pg_rls_manager.py:231      f'CREATE POLICY "{safe_policy}" ON "{safe_table}" '
pg_rls_manager.py:232      f"FOR ALL "
pg_rls_manager.py:233      f'TO "{safe_role}" '
pg_rls_manager.py:234      f"USING (session_id = current_setting('cds.session_id')::text) "
```

Analysis: PostgreSQL combines permissive policies **per role**; once any policy exists on the table, a role for which no policy applies sees zero rows (SELECT/UPDATE/DELETE) and fails `WITH CHECK` on INSERT. `setup_rls_policy` emits exactly one policy, `TO "sandbox_<sid>"`, and turns on `FORCE`, which pulls the **table owner** (the app role) under RLS too. So after the first call for one session: the app role and every other session role lose access to that table. Aggravating factor found while reading: `current_setting('cds.session_id')` is the 1-arg form, so the policy raises `unrecognized configuration parameter` on any connection where the GUC was never set — the repo's own spec uses the missing-tolerant form (`specs/ss-09-structured-db-storage.md:288`, `current_setting('sandbox.allowed_regions', true)`). Not executed against a live PG (no PG instance in this environment); the semantics are PostgreSQL's documented default-deny.

Minimal fix: add a management policy (`CREATE POLICY <t>_admin ON <t> TO <app_role> USING (true) WITH CHECK (true)`) whenever FORCE is used, or make the session policy `TO PUBLIC USING (session_id = coalesce(current_setting('cds.session_id', true), session_id))`; never `FORCE` a table the application itself reads.

## 2 · idx 462 · critical/security — raw `session_id` interpolated into DSN `options`

**Verdict: TRUE · LATENT** (the privilege-escalation half of the claim is overstated).

```
pg_rls_manager.py:424  separator = "&" if "?" in dsn else "?"
pg_rls_manager.py:425  dsn += f"{separator}options=-c%20cds.session_id%3D{session_id}"
```

Probe:

```
session_id='x y=1 -c row_security=off'
 -> postgresql+asyncpg://sandbox_xy1crow_::S3cret@db:5432/cds?options=-c%20cds.session_id%3Dx y=1 -c row_security=off
parse_qsl -> [('options', '-c cds.session_id=x y=1 -c row_security=off')]
```

The `options` value terminates at the injected space, so a further `-c name=value` is appended. Consequence is *not* "connect as postgres": `session_authorization`/`role` require the connecting role to be a member of the target role, so that specific escalation fails. What does work is a user-settable GUC such as `row_security=off` — `FORCE ROW LEVEL SECURITY` binds only the table owner, so a non-owner session role with `row_security=off` skips the policies entirely. Also true as stated: a `session_id` containing `&`, `%` or a space corrupts the DSN for everyone.

Minimal fix: drop DSN-carried context; after connect run `SELECT set_config('cds.session_id', $1, false)` (parameterised). If the DSN path must stay, `urllib.parse.quote(session_id, safe='')` and reject `session_id` not matching `^[A-Za-z0-9_-]+$`.

## 3 · idx 463 · critical/bug — passwordless role + regex username rewrite

**Verdict: TRUE · LATENT.** One extra defect found.

```
pg_rls_manager.py:158  f'CREATE ROLE "{safe_role}" WITH LOGIN '     # no PASSWORD clause anywhere in the file
pg_rls_manager.py:416  dsn = re.sub(
pg_rls_manager.py:417      r"://([^:@]+)(:[^@]*)?@",
pg_rls_manager.py:418      f"://{role_name}:\\2@",
pg_rls_manager.py:419      dsn,
pg_rls_manager.py:420      count=1,
pg_rls_manager.py:421  )
```

Probe:

```
base with user   : postgresql+asyncpg://sandbox_sessabc1::S3cret@db:5432/cds?options=...
base w/o user    : postgresql+asyncpg://db:5432/cds?options=...    -> username NOT replaced, still the app DSN
urlparse('...://sandbox_sessabc1::S3cret@db:5432/cds') -> user='sandbox_sessabc1' pass=':S3cret'
```

(a) `CREATE ROLE` never sets a password and nothing later does, so any scram/md5 connection with the reused password fails; (b) when the base DSN carries no username the regex does not match and the function returns the **app-role** DSN — the caller cannot tell isolation was not applied; (c) new: capture group 2 already includes the `:` (`':S3cret'`), and the replacement adds another, so the produced password is `:S3cret` — the session DSN is malformed even when a password exists.

Minimal fix: `role_password = secrets.token_urlsafe(32)`, `CREATE ROLE … PASSWORD %(pw)s` (bound parameter), return the password/credential handle to the caller; rebuild the URL with `sqlalchemy.engine.make_url(base).set(username=…, password=…).render_as_string(hide_password=False)` and raise if the username was absent instead of returning the base DSN.

## 4 · idx 464 · high/security — role-name collisions and a shared `sandbox_default`

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:81  clean = re.sub(r"[^a-zA-Z0-9_]", "", session_id)
pg_rls_manager.py:82  return clean[:8] if clean else "default"
pg_rls_manager.py:87  role = f"{self._ROLE_PREFIX}_{suffix}"
```

Probe:

```
'sess-abcdef123456' -> 'sandbox_sessabcd'
'sess-abcdef9999'   -> 'sandbox_sessabcd'      # same role for two sessions
'----' / ''         -> 'sandbox_default'       # every degenerate id shares one role
```

Shared role ⇒ shared RLS filter and shared `GRANT`s (cross-session read), and `drop_session_role` for one of them `DROP ROLE`s the shared principal out from under the others (`pg_rls_manager.py:371`).

Minimal fix: `suffix = hashlib.sha256(session_id.encode()).hexdigest()[:16]`; refuse `"default"` — raise on an empty/short `session_id`.

## 5 · idx 465 · high/security — interpolated `SET LOCAL cds.session_id`

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:457  safe_id = session_id.replace("'", "''")
pg_rls_manager.py:458  await db.execute(
pg_rls_manager.py:459      text(f"SET LOCAL cds.session_id = '{safe_id}'")
```

`SET` cannot take a bind parameter, so `set_config('cds.session_id', :sid, true)` is the correct form (PG accepts a bound value there). The scoping half is real: `SET LOCAL` lasts only for the enclosing transaction, and PG only *warns* ("SET LOCAL can only be used in transaction blocks") when there is none — combined with the 1-arg `current_setting('cds.session_id')` in the policy (see #1), a pool connection that lost the setting makes every query on the RLS'd table raise, or filter on nothing. Injection risk with `standard_conforming_strings=on` (PG default) is limited to a same-role context leak, not escape.

Minimal fix: `await db.execute(text("SELECT set_config('cds.session_id', :sid, true)"), {"sid": session_id})`, plus the `, true` missing-ok flag in the policy expression.

## 6 · idx 466 · high/bug — tracking row deleted even when cleanup failed

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:370  try:
pg_rls_manager.py:371      await db.execute(text(f'DROP ROLE IF EXISTS "{safe_role}"'))
pg_rls_manager.py:372  except Exception as e:
pg_rls_manager.py:373      errors.append(f"DROP ROLE failed: {e}")
pg_rls_manager.py:377  if session_id in self._active_roles:
pg_rls_manager.py:378      self._active_roles[session_id].active = False   # dead: object is dropped next line
pg_rls_manager.py:379      del self._active_roles[session_id]
```

`DROP ROLE` legitimately fails while the role owns objects or sessions are active; the in-memory record is removed regardless, so the retry path (`drop_session_role`) loses `info.tables_granted` and never issues `DROP POLICY` again — a residual role + orphan policies with no way to enumerate them from this class (see #7). `.active = False` before `del` is dead code.

Minimal fix: only pop when `not errors`; keep a `_pending_cleanup` list on failure and drive it from a periodic sweep that reconciles against `pg_roles`/`pg_policies`.

## 7 · idx 1325 · medium/security — `IN SCHEMA public` hardcoded; cleanup keyed on a process dict

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:363  text(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM \"{safe_role}\"")
pg_rls_manager.py:340  if info and info.tables_granted:
```

Grants on any other schema are never revoked (and the product's own spec uses non-`public` schemas — `specs/ss-09-structured-db-storage.md:284` `data_products.customers`), which also makes the `DROP ROLE` of #6 fail. Cleanup depends entirely on `self._active_roles`, so after a restart or on a second API replica the earlier roles/policies/grants are unreachable.

Minimal fix: revoke via `information_schema.table_privileges` (`grantee = role`) or `REVOKE … ON ALL TABLES IN SCHEMA <each schema>`, and enumerate policies from `pg_policies WHERE polname LIKE 'rls_sandbox_%'`.

## 8 · idx 1326 · medium/bug — `admin_dsn` is stored and never used

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:74  self._admin_dsn = admin_dsn      # only occurrence in the class (source count = 1)
pg_rls_manager.py:70  admin_dsn: Admin DSN for creating roles. If None, uses the
pg_rls_manager.py:71                     session's own connection (requires SUPERUSER or
pg_rls_manager.py:72                     CREATEROLE privilege on the app role).
```

Every privileged statement (`CREATE ROLE` :156, `ALTER TABLE … FORCE` :221, `CREATE POLICY` :229, `GRANT` :301, `DROP ROLE` :371) runs on the caller's business `db`. Either the business role has CREATEROLE/SUPERUSER (every session configures its own isolation with a maximally privileged connection) or role creation just fails.

Minimal fix: delete the parameter and document the CREATEROLE requirement, or open a dedicated `create_async_engine(self._admin_dsn)` and route privileged DDL through it.

## 9 · idx 1327 · medium/bug — `row_security` GUC used as the "RLS supported?" probe, cached forever

**Verdict: TRUE · LATENT**, with the disclosure caveat noted: this is **not** an honest-degradation violation (`logger.warning` at :111-114 plus `RLSStatus.fallback_mode` at :435/:442 disclose it), so `AGENTS.md`'s simulation-ladder exemption covers the *fallback*, not the *probe*.

```
pg_rls_manager.py:101  result = await db.execute(text("SELECT current_setting('row_security') AS rls"))
pg_rls_manager.py:105  self._rls_available = row is not None and row[0] == "on"
pg_rls_manager.py:106  except Exception as e:
pg_rls_manager.py:108      self._rls_available = False
pg_rls_manager.py:97   if self._rls_available is not None:
pg_rls_manager.py:98       return self._rls_available
```

`row_security` is a USERSET switch, not a capability indicator (RLS needs server ≥ 9.5 and is skipped for BYPASSRLS/superuser roles). A single transient connection error permanently pins `False` for the process lifetime, i.e. every later session silently runs without DB-layer isolation.

Minimal fix: probe `SHOW server_version_num` + `SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user`, cache only the positive result, and re-probe on failure.

## 10 · idx 1328 · medium/bug — setup failure swallowed, `RLSStatus.error` never populated, not idempotent

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:251  except Exception as e:
pg_rls_manager.py:252      logger.warning(
pg_rls_manager.py:253          f"RLS policy setup failed for {table_name}: {e}. "
pg_rls_manager.py:254          "Falling back to application-level filtering.")
pg_rls_manager.py:256      return False
pg_rls_manager.py:433  return RLSStatus(session_id=session_id, role_created=True,
pg_rls_manager.py:434      rls_enabled=bool(info.tables_granted), …, fallback_mode=self._rls_available is False)
```

Verified mechanically: the string `error=` occurs **0** times in the class, so `RLSStatus.error` is always `None` and `fallback_mode` is `False` unless the version probe failed — a caller that reads status sees a protected session. There is no `DROP POLICY IF EXISTS` before `CREATE POLICY` (contrast `:348` which uses it on the drop path), so a second call for the same session+table errors *after* `ENABLE`/`FORCE RLS` already committed → table left forced-RLS with the policy from call #1 in place and the return value saying "fallback".

Minimal fix: `DROP POLICY IF EXISTS` before create; on failure record `self._last_error[session_id]` and surface it through `RLSStatus.error` / `fallback_mode=True`.

## 11 · idx 1329 · medium/bug — grants tracked only when an info object already exists

**Verdict: TRUE (the identifier-truncation sub-claim is FALSE) · LATENT.**

```
pg_rls_manager.py:241  info = self._active_roles.get(session_id)
pg_rls_manager.py:242  if info:
pg_rls_manager.py:243      info.tables_granted.append(table_name)
pg_rls_manager.py:145  self._active_roles[session_id] = SessionRoleInfo(   # fresh object → tables_granted=[]
pg_rls_manager.py:167  self._active_roles[session_id] = SessionRoleInfo(   # ditto, overwrites any prior entry
```

`setup_rls_policy` does not require `create_session_role` to have run, so a policy can be created with nowhere to record it; a later `create_session_role` replaces the entry wholesale and loses anything already tracked. Orphan policies then never get `DROP POLICY`ed (#6/#7). The 63-byte truncation concern does not hold: PG truncates identifiers deterministically, and create (:231) and drop (:348) build the same over-long name, so they still agree.

Minimal fix: `info = self._active_roles.setdefault(session_id, SessionRoleInfo(role_name=role_name, session_id=session_id))` and make `create_session_role` merge instead of replace.

## 12 · idx 1330 · medium/other — shared singleton state, unsynchronised check-then-act

**Verdict: TRUE (impact overstated) · LATENT.**

```
pg_rls_manager.py:139  result = await db.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role_name})
pg_rls_manager.py:156  await db.execute(text(f'CREATE ROLE "{safe_role}" WITH LOGIN …'))
pg_rls_manager.py:473  pg_rls_manager = PGRLSManager()
```

There is an `await` between the existence check and the `CREATE ROLE`, so two coroutines can interleave; the outcome is a `duplicate_object` error wrapped into `RuntimeError` (:175) rather than corruption. Unbounded `_active_roles` growth requires sessions that never call `drop_session_role`.

Minimal fix: drop the pre-check and catch `DBAPIError` with `orig.sqlstate == '42710'` as success (fixes #16 at the same time); if a guard is wanted, `asyncio.Lock` on the create/drop pair.

## 13 · idx 1331 · medium/security — `grant_table_access` accepts any role name

**Verdict: TRUE as a code fact · LATENT** (no caller exists to pass hostile input).

```
pg_rls_manager.py:258  async def grant_table_access(self, role: str, table_name: str, permissions: list[str], db) -> bool:
pg_rls_manager.py:289  safe_role = role.replace('"', '""')
pg_rls_manager.py:303      f'GRANT {perm_upper} ON "{safe_table}" '
pg_rls_manager.py:304      f'TO "{safe_role}"'
```

Only quoting is applied; nothing ties `role` to the manager's own `sandbox_*` namespace, so the method is a general GRANT endpoint (DML-only, `:280` blocks DDL/GRANT/ALL). Quoting itself is correct here (identifier doubled inside `"`), so this is a missing-boundary problem, not injection.

Minimal fix: take `session_id` and derive `role = self._build_role_name(session_id)`, or assert `role.startswith(self._ROLE_PREFIX + "_")`.

## 14 · idx 1834 · low/bug — skipped permissions still return success

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:280  unsafe = {"CREATE", "DROP", "ALTER", "TRUNCATE", "GRANT", "ALL"}
pg_rls_manager.py:64   _SAFE_PERMISSIONS = {"SELECT", "INSERT", "UPDATE"}
pg_rls_manager.py:295  if perm_upper not in self._SAFE_PERMISSIONS:
pg_rls_manager.py:298      f"Skipping unsupported permission '{perm}' for role {role}")
pg_rls_manager.py:299      continue
pg_rls_manager.py:316  return True
```

`DELETE`/`REFERENCES`/`TRIGGER` are neither unsafe nor safe ⇒ silently dropped, and `granted` is only logged (:315). A caller passing `["SELECT","DELETE"]` gets `True` and no DELETE grant.

Minimal fix: `return set(granted) == requested` (or raise for unsupported permissions, mirroring the `unsafe` branch).

## 15 · idx 1835 · low/performance — f-string logging

**Verdict: TRUE · LATENT · style, not a defect.** All 17 `logger.*` calls in the file interpolate eagerly (`pg_rls_manager.py:107,144,150,170,174,315,367,374,389` …). This is the house style across `app/services/` (e.g. `rls_engine.py:183`, `db_access_proxy.py:91`); converting one module is noise, converting all is a separate chore. No action recommended inside this review.

## 16 · idx 1836 · low/bug — pre-check failure makes role creation non-idempotent

**Verdict: TRUE · LATENT.**

```
pg_rls_manager.py:149  except Exception as e:
pg_rls_manager.py:150      logger.warning(f"Role existence check failed: {e}")     # falls through
pg_rls_manager.py:173  except Exception as e:
pg_rls_manager.py:175      raise RuntimeError(f"Role creation failed for session {session_id}: {e}") from e
```

PG has no `CREATE ROLE IF NOT EXISTS`, so an existing role + a failed probe ⇒ `RuntimeError` and nothing recorded in `_active_roles` (which then breaks the tracking of #11/#6).

Minimal fix: catch `42710 duplicate_object` from `CREATE ROLE` and treat it as the success path.

---

# app/services/rls_engine.py

## 17 · idx 510 · critical/security — region policy fails open when config or context is incomplete

**Verdict: TRUE · LATENT.**

```
rls_engine.py:79   allowed = self.conditions.get("allowed_regions", [])
rls_engine.py:81   user_region = context.get("user_region")
rls_engine.py:83   if not allowed or not user_region:
rls_engine.py:84       return None
rls_engine.py:240  if condition:                       # None ⇒ contributes no predicate
rls_engine.py:245  if not conditions: return None
db_access_proxy.py:126  if rls_where:                  # None ⇒ SQL sent unmodified
```

Probe: `allowed=['cn-north','cn-east']`, `user_region=None` → `None`; `allowed=[]` → `None`. An enabled region policy with a missing/blank context therefore applies **no** region filter at all. `RLSContext.user_region` defaults to `None` (`rls_engine.py:163`), so the miss is one omitted field away. Note the module already knows the correct answer — the neighbouring branch returns `"FALSE"` (`:88`) and `evaluate_sql` short-circuits on it (`:241-242`).

Minimal fix: `if not allowed: return "FALSE"` and `if not user_region: return "FALSE"` (both are the fail-closed convention used elsewhere in the repo).

## 18 · idx 511 · critical/security — `max_allowed_level` never constrains anything

**Verdict: TRUE · LATENT.** Half of the current behaviour is pinned by a test, which is itself the bug.

```
rls_engine.py:134  max_level = self.conditions.get("max_allowed_level", "public")
rls_engine.py:135  user_clearance = context.get("user_clearance", "public")
rls_engine.py:137  max_rank = SensitivityLevel.clearance_rank(max_level)
rls_engine.py:138  user_rank = SensitivityLevel.clearance_rank(user_clearance)
rls_engine.py:140  if user_rank >= max_rank:
rls_engine.py:142      return None
rls_engine.py:148      if SensitivityLevel.clearance_rank(level.value) <= user_rank   # ceiling still ignored
rls_engine.py:41   return ranks.get(level, -1)
```

Probe with `max_allowed_level='public'`: `user_clearance='secret'` → `None` (no filter ⇒ SECRET rows readable); `max_allowed_level='Secret'` (typo, rank −1) → `None`. `clearance_rank` returns `-1` for anything unrecognised, so a config typo turns the policy off.

Test pinning: `tests/test_sprint4_services.py:243-246` — `# User with secret clearance — no filtering` / `assert where is None` with `max_allowed_level="confidential"`. So the ceiling-not-applied behaviour is asserted as intended; the finding stands as a design defect, but fixing it is a test-visible change. `validate_policy` would reject the `'Secret'` typo (`rls_engine.py:325-327`) yet nothing calls `validate_policy` outside tests.

Minimal fix (verified by probe against the pinned tests):

```
if max_rank < 0 or user_rank < 0: return "FALSE"
eff = min(user_rank, max_rank)                       # filter levels to eff, not user_rank
```

`tests/test_sprint4_services.py:246` must then change from `is None` to "filtered to ≤ confidential"; the other pinned assertions (`:249-252`, `test_rls_engine.py:88-110`) still pass.

## 19 · idx 512 · high/security — policy-supplied column names interpolated as identifiers

**Verdict: TRUE · LATENT.**

```
rls_engine.py:80  region_column = self.conditions.get("region_column", "region")
rls_engine.py:90  return f"{region_column} = '{self._escape(user_region)}'"
rls_engine.py:101 time_column = self.conditions.get("time_column", "created_at")
rls_engine.py:111 conditions.append(f"{time_column} <= '{cutoff.isoformat()}'")
rls_engine.py:133 sensitivity_column = self.conditions.get("sensitivity_column", "security_level")
rls_engine.py:151 return f"{sensitivity_column} IN ({in_clause})"
rls_engine.py:425 column = policy.get("column", "region")
```

Probe: `region_column="region = 'a' OR 1=1 --"` → `region = 'a' OR 1=1 -- = 'a'`. `validate_policy` accepts it (`→ []`, verified), so nothing on the registration path rejects a hostile column. Reachable today only from whoever calls `add_policy` — nothing in `app/` does.

Minimal fix: one `_ident()` helper — `re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", name)` else `raise ValueError` — used at :90, :111/:115/:119, :151, :427, :433 (`re` is already imported, see #35), and call it from `validate_policy`.

## 20 · idx 513 · high/security — "`_escape` is unreliable"

**Verdict: FALSE as stated · not reachable anyway.**

```
rls_engine.py:153  @staticmethod
rls_engine.py:154  def _escape(value: str) -> str:
rls_engine.py:156      return value.replace("'", "''")
```

The claimed exploit vectors do not exist in this product: the backend is PostgreSQL-only (`postgresql+asyncpg`), where `standard_conforming_strings` has been `on` by default since 9.1 — a backslash inside `'…'` is literal, so quote-doubling is complete, and MySQL/GBK multi-byte vectors are irrelevant to a PG-only stack. The real hole in the same f-string is the **identifier** position, which no value-escaping can close — that is #19, tracked separately. Adding a control-character/NUL rejection to `_escape` is worth one line but is hardening, not a defect fix.

## 21 · idx 514 · high/bug — `rfind(" WHERE ")` lands in the innermost subquery

**Verdict: TRUE · LATENT.** The docstring promises the opposite (`rls_engine.py:256` "Subqueries: inject into outermost SELECT").

```
rls_engine.py:269  # Find the last WHERE (outermost query)
rls_engine.py:270  where_idx = sql_upper.rfind(" WHERE ")
```

Probe:

```
SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.id = t.id)
 -> SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u WHERE (region = 'cn-north') AND u.id = t.id)
```

The RLS predicate is applied to the `EXISTS` subquery; the outer row set is unfiltered — a security bypass, not just wrong SQL. The comment is also self-contradictory (the last `WHERE` is the innermost). Text search additionally ignores string literals, quoted identifiers and comments.

Minimal fix: this file cannot do this correctly with string surgery — use `sqlglot`/`sqlparse` to locate the outermost `Select.where`, or (project-appropriate) stop rewriting free-form SQL and require callers to pass parameterised queries with the RLS predicate merged by the parser.

## 22 · idx 515 · high/bug — `strip()` does not normalise whitespace, so `WHERE` goes undetected

**Verdict: TRUE (the `LIMIT` sub-claim is FALSE) · LATENT.**

```
rls_engine.py:261  # Normalize whitespace
rls_engine.py:262  sql = sql.strip()
rls_engine.py:268  if " WHERE " in sql_upper:
rls_engine.py:298  else:
rls_engine.py:300      return f"{sql} WHERE ({rls_where})"
```

Probe: `"SELECT * FROM t\nWHERE x = 1"` → `"SELECT * FROM t\nWHERE x = 1 WHERE (region = 'cn-north')"` — invalid SQL. A trailing `;` fails the same way. `LIMIT` is handled (`:291-297` inserts before it), so that part of the claim is wrong. Blast radius is fail-closed rather than a bypass: `db_access_proxy.py:227-228` executes the rewritten text and lets the DB error propagate.

Minimal fix: `sql = " ".join(sql.split())` before the keyword scan, match keywords with `\bWHERE\b` (and strip a trailing `;`) — or fold into the AST rewrite of #21.

## 23 · idx 516 · high/security — `inject_filter` interpolates `user_clearance` unvalidated

**Verdict: TRUE · LATENT.**

```
rls_engine.py:416  user_clearance: int | None = None,
rls_engine.py:433  condition = f"{column} <= {user_clearance}"
```

Probe: `user_clearance="3 OR 1=1"` → `SELECT * FROM t WHERE sec <= 3 OR 1=1`; `column="region) OR 1=1 --"` → `SELECT * FROM t WHERE region) OR 1=1 -- = 'a'`. Annotations are not enforced at runtime and both values arrive from the caller. The claim's second point is also accurate: this simplified API models sensitivity as an integer while `RLSPolicy._sensitivity_condition` models it as the `public/internal/confidential/secret` enum — two incompatible models in one engine.

Minimal fix: `clearance = int(user_clearance)` inside `try/except (TypeError, ValueError) → raise ValueError`, plus the `_ident()` check from #19; then pick one sensitivity model for the module.

## 24 · idx 517 · high/security — row-level region check does not compare against the user's region

**Verdict: TRUE · LATENT.**

```
rls_engine.py:354  if user_region is None: return False
rls_engine.py:356  row_region = row.get("region", "")
rls_engine.py:357  return user_region in allowed_regions and row_region in allowed_regions
```

Probe: user `cn-north`, policy `allowed_regions=['cn-north','cn-east']`, row `region='cn-east'` → **True** (cross-region row readable). The SQL path is stricter (`rls_engine.py:90` emits `region = '<user_region>'`), so the two APIs disagree. `row.get("region", "")` treats a missing field as a value instead of a denial.

Minimal fix (probe-verified: keeps `test_rls_engine.py:16-38` and the composite tests green):

```
row_region = row.get("region")
if not row_region: return False
return user_region in allowed_regions and row_region == user_region
```

## 25 · idx 518 · high/bug — `user_clearance >= max_level` inverts the sensitivity ceiling

**Verdict: TRUE · LATENT.**

```
rls_engine.py:360  max_level = policy.get("max_level", 0)
rls_engine.py:361  row_sensitivity = row.get("sensitivity", 0)
rls_engine.py:364  return user_clearance >= row_sensitivity and user_clearance >= max_level
```

Probe: policy `max_level=3` — clearance 1 on a `sensitivity=1` row → **False** (a low-sensitivity row denied to a low-clearance user, i.e. the predicate gates the *user*, not the data); clearance 3 on a `sensitivity=3` row → **True** (the ceiling never limited anything). A row with no `sensitivity` key defaults to 0 and is allowed.

Minimal fix (probe-verified against `test_rls_engine.py:88-110` and `test_sprint4_services.py` sensitivity cases):

```
effective = min(int(user_clearance), int(policy.get("max_level", 0)))
return int(row.get("sensitivity", 0)) <= effective
```

## 26 · idx 519 · high/security — unknown `policy_type` allows access

**Verdict: TRUE · LATENT.**

```
rls_engine.py:366  elif policy_type == "time":
rls_engine.py:367      return self.evaluate_time_policy(policy)
rls_engine.py:369  return True
```

Probe: `{"type": "regio"}` → **True**; `{}` (no type) → **True**. With `evaluate_composite`'s AND loop (`rls_engine.py:406-409`) a typo'd policy silently contributes nothing. The SQL side has the same shape: a `policy_type` that matches none of the three branches falls through to `rls_engine.py:70  return None` = "no predicate", and `conditions` is typed only by annotation, so a policy deserialised from JSON can carry any string there.

Minimal fix: `logger.error("unknown RLS policy type %r", policy_type); return False`, and mirror it in `to_sql_condition` as `return "FALSE"`.

## 27 · idx 1396 · medium/bug — `find()` for GROUP BY / ORDER BY / LIMIT also hits subqueries; HAVING unhandled

**Verdict: TRUE · LATENT.**

```
rls_engine.py:277  elif " GROUP BY " in sql_upper:
rls_engine.py:278      idx = sql_upper.find(" GROUP BY ")
rls_engine.py:298  else:
rls_engine.py:300      return f"{sql} WHERE ({rls_where})"
```

Probe:

```
SELECT * FROM (SELECT a FROM t GROUP BY a) s ORDER BY a
 -> SELECT * FROM (SELECT a FROM t WHERE (region='n')  GROUP BY a) s ORDER BY a      # filtered inside the subquery only
SELECT count(*) FROM t HAVING count(*) > 1
 -> SELECT count(*) FROM t HAVING count(*) > 1 WHERE (region='n')                    # invalid SQL
```

The `elif` precedence itself (GROUP BY → ORDER BY → LIMIT) is correct for a flat query; the defect is positional matching plus the missing `HAVING` branch.

Minimal fix: same as #21 (AST), with `HAVING` handled; a depth-aware first keyword scan at bracket depth 0 is the minimum for a text rewrite.

## 28 · idx 1397 · medium/security — `_inject_where` drops the parentheses

**Verdict: TRUE — and worse than the tool stated (exploitable with today's generated conditions, no future `OR` needed) · LATENT.**

```
rls_engine.py:447  return sql[:insert_pos] + f"{condition} AND " + sql[insert_pos:]      # no parens
rls_engine.py:274  + f"({rls_where}) AND "                                               # sibling does wrap them
```

Probe: `RLSEngine._inject_where("SELECT * FROM t WHERE a = 1 OR b = 2", "sec <= 1")` → `SELECT * FROM t WHERE sec <= 1 AND a = 1 OR b = 2`, which binds as `(sec <= 1 AND a = 1) OR b = 2`: every row with `b = 2` bypasses the filter. The injected condition itself contains no `OR`; only the *existing* WHERE does. `_inject_where` also uses `find` (first WHERE) where `inject_rls_where` uses `rfind` — two different, both wrong, placement rules.

Minimal fix: `return sql[:insert_pos] + f"({condition}) AND " + sql[insert_pos:]` and delete the duplicate helper in favour of one parenthesised, parser-based injector.

## 29 · idx 1398 · medium/bug — unrecognised clearance produces `IN ()`

**Verdict: TRUE · LATENT**, and reachable with a plain default context.

```
rls_engine.py:41   return ranks.get(level, -1)
rls_engine.py:145  allowed_levels = [... if SensitivityLevel.clearance_rank(level.value) <= user_rank]
rls_engine.py:151  return f"{sensitivity_column} IN ({in_clause})"
rls_engine.py:164  user_clearance: str | None = None      # RLSContext default
```

Probe (`RLSContext(user_id='u1')`, i.e. `user_clearance=None`, one sensitivity policy registered): `evaluate_sql → '(security_level IN ())'`. Because `eval_context` always contains the key (`rls_engine.py:223`), `context.get("user_clearance", "public")` at :135 yields `None`, not `"public"`; `user_rank = -1` slips past the `>= max_rank` guard and yields an empty list → syntactically invalid SQL in every supported dialect.

Minimal fix: `if not allowed_levels: return "FALSE"` (and `user_clearance = context.get("user_clearance") or "public"`), covered by #18's `max_rank < 0 or user_rank < 0 → "FALSE"`.

## 30 · idx 1399 · medium/bug — `table_name` parameter is never read

**Verdict: TRUE · LATENT.**

```
rls_engine.py:206  table_name: str | None = None,
rls_engine.py:213  table_name: Optional table name for table-specific policies
```

The only two occurrences in the file are the signature and the docstring; the body filters only on `policy_ids` and `enabled` (`:229-235`), and `RLSPolicy` has no table field. Callers passing `table_name` get every registered policy AND-combined onto an unrelated table.

Minimal fix: add `table: str | None = None` to `RLSPolicy`, filter `if table_name and p.table and p.table != table_name: continue`, or drop the parameter.

## 31 · idx 1400 · medium/bug — shadowing `datetime` import and naive local hour in `evaluate_time_policy`

**Verdict: TRUE (the cross-midnight part is fail-closed, so availability not confidentiality) · LATENT.**

```
rls_engine.py:14  from datetime import datetime, timezone, timedelta      # module level
rls_engine.py:385  if current_hour is None:
rls_engine.py:386      from datetime import datetime                       # re-import, shadows module name
rls_engine.py:387      current_hour = datetime.now().hour                  # naive, server-local
rls_engine.py:106  now = datetime.now(timezone.utc)                        # SQL path is UTC
```

Two time bases in one engine, so an hour-window policy evaluated at 08:00 UTC on a UTC+8 host admits/denies at the wrong hours. Probe: a `start_hour=22, end_hour=6` window returns **False at both 23:00 and 03:00** — the wrap-around window can never be satisfied (denies everything rather than allowing everything).

Minimal fix: delete the in-function import, `current_hour = datetime.now(timezone.utc).hour`, and handle wrap: `return start <= h < end if start <= end else not (end <= h < start)`.

## 32 · idx 1401 · medium/concurrency — singleton `_policies` dict is unsynchronised

**Verdict: TRUE as a code fact; the described harms are unreachable in this app's execution model · LATENT.**

```
rls_engine.py:178  self._policies: dict[str, RLSPolicy] = {}
rls_engine.py:187  if policy_id in self._policies:
rls_engine.py:188      del self._policies[policy_id]
rls_engine.py:229  policies = list(self._policies.values())
rls_engine.py:467  rls_engine = RLSEngine()
```

`add_policy`/`remove_policy`/`evaluate_sql` contain no `await`, so under the app's asyncio event loop (one worker per process) no interleaving exists; `KeyError` from the check-then-act and `dictionary changed size during iteration` need a second thread *and* a runtime mutator, and nothing in `app/` calls `add_policy` today. Note this is the repo-wide singleton convention (`app/services/AGENTS.md`), not an anomaly of this module.

Minimal fix (only if policy registration ever moves to request time): `self._policies.pop(policy_id, None) is not None` — one atomic call, no lock needed for CPython dict reads.

## 33 · idx 1866 · low/bug — `_time_condition` ignores the evaluation context

**Verdict: TRUE · LATENT.**

```
rls_engine.py:106  now = datetime.now(timezone.utc)
rls_engine.py:225  "request_time": context.request_time,      # assembled, never read
rls_engine.py:167  session_id: str | None = None              # never placed into eval_context at all
```

`request_time` is built into `eval_context` and read by nothing; `session_id` isn't even passed through. Time-window evaluation is therefore not reproducible per request and not firable in tests.

Minimal fix: `now = context.get("request_time") or datetime.now(timezone.utc)`; add `"session_id": context.session_id` if any condition should key on it, otherwise delete the field.

## 34 · idx 1867 · low/performance — f-string logging

**Verdict: TRUE · LATENT · style.** One instance (`rls_engine.py:183`), same house-wide pattern as the rest of `app/services/`. See #15 — no action inside this review.

## 35 · idx 1868 · low/dead-code — `import re` is unused

**Verdict: TRUE · LATENT.**

```
rls_engine.py:11  import logging
rls_engine.py:12  import re
```

`rg '\bre\.'` over `app/services/rls_engine.py` → 0 matches (verified). Keep it if #19's `_ident()` lands, otherwise drop the line.

## 36 · idx 1869 · low/maintainability — `SensitivityLevel.__members__.values()` and unparsed column config

**Verdict: TRUE (maintainability only — no functional defect today) · LATENT.**

```
rls_engine.py:325  if max_level not in SensitivityLevel.__members__.values():
rls_engine.py:26   class SensitivityLevel(str, Enum):
```

`__members__.values()` yields enum members, and the `in` test works only because `SensitivityLevel` subclasses `str`; a switch to `StrEnum`/`IntEnum` or a name-vs-value mix silently disables the check. The claim's second half is confirmed by probe: `validate_policy` on a region policy whose `region_column` is `"region = 'a' OR 1=1 --"` returns `[]` — no validation of `region_column` / `time_column` / `sensitivity_column` / numeric age parameters. Note `validate_policy` itself has no production caller (tests only).

Minimal fix: `if max_level not in {m.value for m in SensitivityLevel}` and add the #19 identifier check plus `isinstance(int)` checks for `min_age_hours`/`max_age_hours`/`embargo_hours` to `validate_policy`.

---

## Cross-cutting recommendation

Do not schedule per-claim fixes as "production defects". The honest fix set is:

1. **Wire-or-flag decision first.** Neither module is imported by the app. `specs/SS-gap-analysis.md` FG-006 already records PG-in-TEE as not landed; `docs/spec-implementation-analysis.md:216` meanwhile says `db_access_proxy` "✅ 已实现", which reads as wired but is not. Align the doc, or delete `pg_rls_manager.py` + `db_access_proxy.py` (removing the unwired service is the pattern already applied to `chain_attestation.py` in `specs/sandbox-productization-round3-spec.md` N2).
2. If they are to be wired, the fail-open security core must land first: #17, #18, #24, #25, #26, #29 (deny by default), then the identifier/parameter handling #19/#23/#28, then the rewrite correctness #21/#22/#27 (needs a real SQL parser — text rewriting of free-form SQL cannot be made safe here).
3. `pg_rls_manager.py` should not be wired before #1, #3, #5 are fixed: as written it denies the application role access to the tables it protects and produces a session DSN that cannot authenticate.
4. `pg_rls_manager.py` has no test file at all; any change there ships untested unless a test is added alongside.
