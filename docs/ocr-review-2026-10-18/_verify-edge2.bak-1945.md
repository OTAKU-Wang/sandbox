<!--APPEND-->
### [E2-1] async engine not disposed on the exception path in run_async_migrations — **FALSE**

`alembic/env.py:67-74`
```
67	    connectable = async_engine_from_config(
68	        config.get_section(config.config_ini_section, {}),
69	        prefix="sqlalchemy.",
70	        poolclass=pool.NullPool,
71	    )
72	    async with connectable.connect() as connection:
73	        await connection.run_sync(do_run_migrations)
74	    await connectable.dispose()
```
The control-flow observation is right (no try/finally), but the claimed harm does not exist: the engine is built with `poolclass=pool.NullPool` (line 70), so it retains no connections — `connect()` failure leaves nothing open, and if `run_sync` raises, the `async with` on line 72 still closes that one connection. `dispose()` on a NullPool engine releases an empty pool, so there is no "unclosed transport" / "Task was destroyed but it is pending" outcome to reach. Cosmetic hygiene only, not a defect.

### [E2-2] run_migrations_online calls asyncio.run() with no running-loop guard — **TRUE·LATENT**

`alembic/env.py:77-85`
```
77	def run_migrations_online() -> None:
78	    """Run migrations in 'online' mode."""
79	    asyncio.run(run_async_migrations())
80	
81	
82	if context.is_offline_mode():
83	        ...
84	else:
85	    run_migrations_online()
```
Fact confirmed: no `asyncio.get_running_loop()` guard, and `env.py` is executed at `context.configure` time, so an in-process `command.upgrade(cfg)` from a running loop would die with "asyncio.run() cannot be called from a running event loop". Nothing in the repo does this: the only programmatic alembic use is `scripts/verify_schema_parity.py:42` (`subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"])` — a fresh process), and `.github/workflows/ci.yml:42` runs the CLI. Harm needs a future in-process caller.

### [E2-3] render_as_batch not enabled although CI runs the chain on SQLite — **TRUE·LATENT**

`alembic/env.py:59-62`
```
59	def do_run_migrations(connection: Connection) -> None:
60	    context.configure(connection=connection, target_metadata=target_metadata, compare_type=COMPARE_TYPES)
61	    with context.begin_transaction():
62	        context.run_migrations()
```
`render_as_batch` is indeed absent, and `.github/workflows/ci.yml:42` does run `alembic upgrade head` against SQLite. But no upgrade() in 0000→0026 issues a bare ALTER/DROP that SQLite cannot do: 0001–0026 upgrades are only `add_column`/`create_table`/`create_index`, and the one column-attribute change is explicitly dialect-branched (`0020_webhook_detached_history.py:21-25` uses `op.alter_column` only on the postgresql branch and `batch_alter_table` otherwise). So today the failure the claim predicts never fires — it is a missing future-proofing guard, not a live break.

### [E2-4] ALEMBIC_COMPARE_TYPES only honours the literal "1" — **FALSE**

`alembic/env.py:37-42`
```
37	# SQLite stores sa.UUID columns with NUMERIC affinity, so type comparison
...
42	COMPARE_TYPES = os.environ.get("ALEMBIC_COMPARE_TYPES", "1") == "1"
```
The claimed mechanism is inverted. `"false" == "1"` is False, so `ALEMBIC_COMPARE_TYPES=false` (or `True`, `0`, ``) yields `COMPARE_TYPES=False` — type comparison is turned **off**, it does not stay on, so the predicted "CI 写成 false 后 SQLite 上涌出大量类型误报" cannot happen. (The real, unclaimed footgun is the opposite: `true`/`yes`/`on` also disable it.) The residual point — no warning when comparison is off — is a logging nicety, and CI's `ALEMBIC_COMPARE_TYPES=0` (`ci.yml:45`) is deliberate and documented by the comment above the flag.

### [E2-5] config.set_main_option re-interpolates DATABASE_URL; a "%" in the password breaks migrations — **TRUE·LIVE**

`alembic/env.py:34-35`
```
34	settings = get_settings()
35	config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
```
Reproduced against this repo's alembic (`.venv-gate`): `set_main_option("sqlalchemy.url", "postgresql+asyncpg://u:p%40ss@h/db")` → `ValueError: invalid interpolation syntax in 'postgresql+asyncpg://u:p%40ss@h/db' at position 24`, and `Config.file_config._interpolation` is `BasicInterpolation`; the `.replace("%", "%%")` form round-trips correctly. One correction to the claim: it raises already in `set_main_option` (line 35), not later in `get_section`. Live entry point: every migration run goes through this line — CI (`ci.yml:42`), the `verify_schema_parity.py` CI gate, and the production deploy path — so a deployment whose DB password contains a percent-encoded character (extremely common) cannot run migrations at all. Loud failure, but a hard blocker.

### [E2-6] fileConfig(...) with default disable_existing_loggers=True runs after import app.main — **TRUE·LATENT**

`alembic/env.py:23` then `alembic/env.py:28-30`
```
23	import app.main  # noqa: F401,E402
...
28	config = context.config
29	if config.config_file_name is not None:
30	    fileConfig(config.config_file_name)
```
Ordering is exactly as claimed: `import app.main` (line 23) creates every `logging.getLogger(__name__)` in the app surface, then `fileConfig` (line 30) is called without `disable_existing_loggers=False`, so those loggers are disabled for the rest of the process. No migration script imports app code, and alembic runs as its own CLI process (CI + `verify_schema_parity.py:42`), so nothing user-visible is silenced today; the in-process-reuse scenario the claim leans on does not exist yet — same conditional as E2-2.
## alembic/versions/0000_baseline_all_tables.py

### [E2-7] app_credentials timestamp columns are timezone-naive while the code compares them against aware datetimes — **TRUE·LIVE**

`alembic/versions/0000_baseline_all_tables.py:376-379`
```
376	    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
377	    sa.Column('expires_at', sa.DateTime(), nullable=True),
378	    sa.Column('last_used_at', sa.DateTime(), nullable=True),
379	    sa.Column('revoked_at', sa.DateTime(), nullable=True),
```
`app/models/app_credential.py:49-52` matches (`DateTime` without `timezone=True`), so this is not model↔chain drift — the whole column family is naive while every other table in the baseline uses `DateTime(timezone=True)`. Consumer: `app/services/gateway_service.py:119` `if cred.expires_at and cred.expires_at < datetime.now(timezone.utc):` and line 134 `cred.last_used_at = datetime.now(timezone.utc)`. `app/api/gateway.py:114-116` always sets `expires_at` (default `expires_hours=720`), so any credential created through `POST /api/v1/gateway/credentials` reaches that comparison on the next request with a freshly loaded row. Reproduced with this repo's venv against SQLite (`Temp/ocr-cds/scratch_tz.py`): read-back is `datetime.datetime(2026,10,18,12,36,24,595326) tzinfo: None` and the comparison raises `TypeError: can't compare offset-naive and offset-aware datetimes`; PG returns naive for `timestamp without time zone` too. Tests miss it because `tests/test_contract_gateway.py:226-240` inserts a credential with `expires_at` left NULL, and `app/services/cube_egress.py:145` only selects `expires_at IS NULL` rows. Gateway routes `POST /api/v1/gateway/{contract_id}/query|access|metering|status` (`app/api/gateway.py:216,309,344,410`) are the live entry points. Fix = new ALTER revision + model change, as the claim says.

### [E2-8] certificates.serial_number gets both a UNIQUE constraint and a separate unique index — **TRUE·LIVE**

`alembic/versions/0000_baseline_all_tables.py:265-267`
```
265	    sa.UniqueConstraint('serial_number')
266	    )
267	    op.create_index('ix_certificates_serial', 'certificates', ['serial_number'], unique=True)
```
ORM side reproduces it, so it is not drift but a real duplicate: `app/models/certificate.py:38` `Index("ix_certificates_serial", "serial_number", unique=True)` plus `app/models/certificate.py:47` `serial_number: ... unique=True`. On PG that is two btree indexes over the same column (`certificates_serial_number_key` + `ix_certificates_serial`), maintained on every insert; low churn (certificate issue/renew), so the cost is storage/write only — real, cheap to remove, must be removed on both sides or `alembic check` regenerates it.

### [E2-9] connector_sessions.connector_id has two identical non-unique indexes — **TRUE·LIVE**

`alembic/versions/0000_baseline_all_tables.py:539-540`
```
539	    op.create_index('ix_connector_sessions_connector', 'connector_sessions', ['connector_id'], unique=False)
540	    op.create_index(op.f('ix_connector_sessions_connector_id'), 'connector_sessions', ['connector_id'], unique=False)
```
Same in the ORM: `app/models/connector.py:66` `Index("ix_connector_sessions_connector", "connector_id")` and `app/models/connector.py:71` `connector_id ... index=True`. Note `verify_schema_parity.py:26-28` only compares table→column-name sets, so neither the parity gate nor `alembic check` (which would flag only unmatched extras) keeps this from reappearing — the claim's "fix the model too" instruction is the right one. Genuine, write-path-only.

### [E2-10] merkle_leaves.batch_id has two identical non-unique indexes on a high-write audit table — **TRUE·LIVE**

`alembic/versions/0000_baseline_all_tables.py:661-662`
```
661	    op.create_index('ix_merkle_leaves_batch', 'merkle_leaves', ['batch_id'], unique=False)
662	    op.create_index(op.f('ix_merkle_leaves_batch_id'), 'merkle_leaves', ['batch_id'], unique=False)
```
ORM mirror: `app/models/merkle_leaf.py:24` `Index("ix_merkle_leaves_batch", "batch_id")` + `app/models/merkle_leaf.py:29` `batch_id ... index=True`. Merkle leaves are appended for every audit event in an anchoring batch, so the redundant index is pure write amplification on the hottest audit-side table. Same both sides ⇒ no parity break, real defect.

### [E2-11] field_visibility_configs.product_id: UNIQUE constraint plus a second unique index — **TRUE·LIVE**

`alembic/versions/0000_baseline_all_tables.py:471-473`
```
471	    sa.UniqueConstraint('product_id')
472	    )
473	    op.create_index('ix_field_visibility_product', 'field_visibility_configs', ['product_id'], unique=True)
```
ORM mirror: `app/models/field_exposure.py:72` `Index("ix_field_visibility_product", "product_id", unique=True)` + `app/models/field_exposure.py:76` `product_id ... unique=True`. Two unique indexes on one column that is also the FK and the lookup key (`gateway_service.py:310`-ish reads by product_id) — one suffices. Same class as E2-8, lower volume.

### [E2-12] baseline docstring still describes a two-revision chain — **TRUE·LIVE**

`alembic/versions/0000_baseline_all_tables.py:7-11`
```
7	This is the FIRST migration in the chain: on a fresh (production) database
8	`alembic upgrade head` builds every table, then 0001/0002 re-add their
9	additive columns. Columns introduced by 0001_p0_security_hardening and
10	0002_session_usability are deliberately ABSENT here so the chain applies
11	cleanly on an empty database:
```
The chain now runs to `0026_notebook_description.py` (`ls alembic/versions`), and later revisions add columns to the very tables the baseline creates — `0003` (`idle_policy`, `auto_resume`), `0005` (`scheduling_disabled`, `health_state`), `0016:24-32` (`tee_policy`, `trust_root`), `0019:38-40` (`env`, `workload_id`). So the enumeration is stale in a file whose stated purpose is to explain what `upgrade head` produces; the "deliberately omits 0001/0002 columns" rule itself stays correct. Documentation-only, but it sits on the path every deploy/CI run (`ci.yml:42`) reads when reasoning about schema alignment, so it is worth correcting.
## alembic/versions/0001_p0_security_hardening.py

### [E2-13] 0001 docstring claims it is "safe to run against a database already created by create_all" — **TRUE·LIVE**

`alembic/versions/0001_p0_security_hardening.py:11-13` and `:23-24`
```
11	is the first migration; it only ADDS new nullable columns for the P0 gap
12	remediation — it is safe to run against a database already created by
13	create_all, and against an empty database it is harmless.
...
23	For databases bootstrapped with create_all, run `alembic stamp head` after
24	upgrading so the chain tracks the applied state.
```
Both statements are wrong. Dumping `Base.metadata` (the exact schema `create_all` produces) shows every column this revision adds is already there: `contracts: valid_until=Y, purpose=Y, purpose_scope=Y`, `data_resources: field_classifications=Y`, `data_encryption_keys: wrapped_payload=Y, sm2_encrypted_payload=Y`, `policy_bundles: revoked_at=Y`. So `op.add_column` (line 38+) targets an existing column with no existence guard anywhere in this chain, and `SandboxSession`-style tables created by `create_all` also collide with 0000's own `create_table` calls. Such a database cannot reach the end of the chain at all — the only order that works is the opposite of line 23-24: stamp first, never upgrade. This is the docstring every operator reads before running the one command CI also runs (`ci.yml:42`), so the misleading text is on a live path — and the same wrong sentence appears in `0000:19-20` and `0003:12` (see E2-17).

### [E2-14] Security columns added nullable with no backfill, so pre-existing rows keep the pre-hardening behaviour — **TRUE·LATENT**

`alembic/versions/0001_p0_security_hardening.py:38-49` adds `valid_until`, `purpose`, `purpose_scope`, `sandbox_tasks.purpose`, `data_resources.field_classifications`, the two `data_encryption_keys` payload columns and `policy_bundles.revoked_at` — all nullable, no `server_default`, no data step.
The consumer behaves exactly as the claim predicts: `app/services/contract_service.py:261-262` filters `Contract.valid_until.isnot(None), Contract.valid_until < now` for the A1 auto-terminate sweep, and `valid_until` is only derived at signing time (`app/services/contract_service.py:159-164` → `_apply_valid_until_from_terms`, line 278-291), so an ACTIVE contract that predates the column is never swept. The sweep itself is wired (`app/services/session_lifecycle.py:271`, started from `app/main.py:128`). What keeps this LATENT: a chain-built database (the only production path — `0000` on an empty DB, per its own docstring) never contains rows created before the column, and `create_all` databases are stamped rather than upgraded, where the app also writes the columns. So the harm needs a pre-0001 legacy database upgraded in place. Recommendation stands as an accepted-risk note or a backfill revision.

### [E2-15] downgrade() drops key material and revocation evidence irreversibly — **TRUE·LATENT**

`alembic/versions/0001_p0_security_hardening.py:52-59`
```
52	def downgrade() -> None:
53	    op.drop_column("policy_bundles", "revoked_at")
54	    op.drop_column("data_encryption_keys", "sm2_encrypted_payload")
55	    op.drop_column("data_encryption_keys", "wrapped_payload")
```
Facts confirmed; `wrapped_payload` is the B3 restart-recovery blob (losing it makes existing DEKs unrecoverable) and `revoked_at` is the revocation-compliance evidence. But nothing in the repo downgrades: the only alembic invocation is `upgrade head` (`scripts/verify_schema_parity.py:42`, `.github/workflows/ci.yml:42`), and the repo's own rule is "never edit alembic history / additive-only". Every revision in the chain has the same property (`0000:666+` drops every table), so this is a chain-wide convention question, not a 0001-specific live defect.

### [E2-16] "sandbox_sessions.contract_index — not needed; contract_id already indexed" is factually wrong — **TRUE·LIVE**

`alembic/versions/0001_p0_security_hardening.py:20`
```
20	- sandbox_sessions.contract_index — not needed; contract_id already indexed
```
The baseline creates exactly five indexes on `sandbox_sessions` (`0000:495-499`: `created`, `data_product_id`, `product_status`, `user_id`, `user_status`) — none on `contract_id` — and `app/models/sandbox_session.py` declares no index for it either. There is a live reader: `app/services/contract_fulfillment.py:248` `SandboxSession.contract_id == str(contract_id)`, so that lookup is a sequential scan on PG. The comment is wrong today and actively discourages someone from fixing the exact query that exists.

## alembic/versions/0002_session_usability.py

### [E2-17] "Additive-only, safe against a create_all database" is false for this migration too — **TRUE·LIVE**

`alembic/versions/0002_session_usability.py:7`
```
7	Additive-only, safe against a create_all database:
```
`extended_seconds` (line 27) and `pre_pause_status` (line 31) exist in `app/models/sandbox_session.py`, and the `Base.metadata` dump confirms `sandbox_sessions: extended_seconds=Y, pre_pause_status=Y` — so a `create_all` database already has both columns and `op.add_column` (which has no existence guard anywhere in this chain) hits an existing column. (No `alembic upgrade` was executed for this review, per instructions; the conclusion is derived from the ORM schema, which is what `create_all` emits.) As with E2-13 the database cannot even get that far without stamping, and the repo convention (AGENTS.md + `0000:19-20`) is `create_all` + `alembic stamp head`. Live only in the sense that it is the instruction text an operator follows before a failed production upgrade — the fix is the same one-sentence correction.

## alembic/versions/0003_idle_policy.py

### [E2-18] idle_policy is an unconstrained String although the behaviour is a two-value switch — **TRUE·LATENT**

`alembic/versions/0003_idle_policy.py:29`
```
29	    op.add_column("sandbox_sessions", sa.Column("idle_policy", sa.String(length=16), nullable=True))
```
Confirmed: no CHECK/`sa.Enum`; the single validator is `app/schemas/sandbox_session.py:62-66` (`must be 'kill' or 'pause'`), applied on `SandboxSessionCreate`, and `app/api/sandbox_sessions.py:357` is the only writer (`app/api/dev_sandbox.py:133-134` explicitly rejects `pause`). The consumer does degrade silently as claimed — `app/services/session_lifecycle.py:207` `policy = session.idle_policy or settings.SESSION_DEFAULT_IDLE_POLICY` and the pause branch is an equality test, so any other string behaves like `kill`. Because every write path in `app/` goes through the validated schema (no PATCH/endpoint writes this column), a dirty value requires a non-API writer → latent hardening; the missing `VALID_IDLE_POLICIES` constant is a fair point.

### [E2-19] Prefer sa.false() over text("false"), and record that the in-place rewrite leaves DEFAULT 0 behind — **FALSE**

`alembic/versions/0003_idle_policy.py:7-10` and `:32`
```
7	NOTE (SS-14 e2e D-e2e-08): auto_resume server_default text("0") → text("false") —
8	PostgreSQL rejects an integer default on a boolean column, so on PG this
9	migration could never have applied (no deployed PG schema carries the old
10	literal); SQLite tolerated 0, which is why it survived. See 0005's note.
```
Both halves are already handled: `server_default=sa.text("false")` is this repo's stated convention (project rule: booleans use `text("false")`, not native bool defaults), and the note the claim asks to add — that no environment carries the old literal — is verbatim lines 9-10, with the same disclosure repeated in `0005:12-17` and `0007:7-10`. `sa.false()` would render the identical PG DDL (`DEFAULT false`); the SQLite `DEFAULT 0` vs `DEFAULT false` difference it mentions is semantically equal and this repo deliberately accepts it. Style preference, not a defect.

## alembic/versions/0004_session_operations.py

### [E2-20] session_operations.session_id has no FK to sandbox_sessions — **TRUE·LATENT**

`alembic/versions/0004_session_operations.py:21`
```
21	        sa.Column("session_id", sa.UUID(), nullable=False),
```
No FK, no cascade — fact confirmed, and the claim is right that a `create_table` could carry the FK on SQLite too (`0007:40-41` and `0008:38` do exactly that). What makes it latent: sessions are never hard-deleted, so no orphan rows can be produced by the app — the only `db.delete(...)` calls in `app/` are for data products, data resources, webhook subscriptions, api endpoints, network policies, cube templates and shared volumes; `SandboxSession` rows only change status (terminate/destroy). So the stated harm (records outliving a deleted session) has no producer today; the constraint is still worth adding as a cheap guard.

### [E2-21] Nothing at the schema level prevents two concurrent exclusive operations — **TRUE·LIVE**

`alembic/versions/0004_session_operations.py:18-32` (no unique/partial index) plus the service:
`app/services/session_operations.py:39-49`
```
42	    result = await db.execute(
43	        select(SessionOperation).where(
44	            SessionOperation.session_id == session_id,
45	            SessionOperation.op_type == op_type,
46	            SessionOperation.status.in_(["pending", "running"]),
```
then `app/services/session_operations.py:70-76` inserts if that SELECT came back empty. There is no lock on either side: the endpoint loads the session without `FOR UPDATE` (`app/api/sandbox_sessions.py:1631-1641`) and `grep -rn with_for_update app/` returns exactly one hit in the whole app — `app/models/task_queue.py:94`. Two concurrent `POST /{session_id}/snapshots/{snapshot_id}/rollback?async=true` requests (`app/api/sandbox_sessions.py:2188-2214`) both pass the check and both insert, and both background runners then execute `ss.rollback` on the same workspace. The 503 OPERATION_LOCKED contract advertised at line 2199-2200 is therefore best-effort; a partial unique index is the only cross-replica guard. This is the strongest live finding in the alembic group.

### [E2-22] updated_at is maintained only by the ORM — **TRUE·LATENT**

`alembic/versions/0004_session_operations.py:28` (DEFAULT only, no trigger) vs `app/models/session_operation.py:32` (`onupdate=func.now()`). The staleness is exposed to clients — `app/services/session_operations.py:149` puts `updated_at` in the polling payload. But the claim's Core-bypass vector does not exist for this table: `grep -rn "update(SessionOperation" app/` finds nothing; every status/progress write goes through the ORM object (`_execute`, `complete`). Latent-only.

### [E2-23] progress/status/op_type have no value-range constraints — **TRUE·LATENT**

`alembic/versions/0004_session_operations.py:22-24`. Facts right: `progress` is semantically 0..1 (service writes 0.0/0.9/1.0) and is returned verbatim to poll clients (`session_operations.py:145`). All in-app writers are literal constants inside `session_operations.py`, so a malformed value needs a non-ORM writer → latent hardening, same class as E2-18/E2-29.

### [E2-24] ix_session_operations_session_id duplicates the leading column of the composite index — **TRUE·LIVE**

`alembic/versions/0004_session_operations.py:31-32`
```
31	    op.create_index("ix_session_operations_session_id", "session_operations", ["session_id"], unique=False)
32	    op.create_index("ix_session_operations_session_created", "session_operations", ["session_id", "created_at"], unique=False)
```
ORM mirror confirms it is intentional-looking duplication rather than drift: `app/models/session_operation.py:19` `Index("ix_session_operations_session_created", ...)` + `app/models/session_operation.py:22` `session_id ... index=True`. The single-column index is never useful (every service query filters session_id and the composite serves it), so it is pure write cost per operation row. Fix must remove `index=True` in the model too, exactly as the claim says.

### [E2-25] downgrade drops indexes before the table and fails if either is missing — **TRUE·LATENT**

`alembic/versions/0004_session_operations.py:35-38`
```
35	def downgrade() -> None:
36	    op.drop_index("ix_session_operations_session_created", table_name="session_operations")
37	    op.drop_index("ix_session_operations_session_id", table_name="session_operations")
38	    op.drop_table("session_operations")
```
Correct that `DROP TABLE` already removes the indexes and that a missing index name aborts the (single-transaction) PG rollback. No production path downgrades — the repo only ever runs `upgrade head` (`ci.yml:42`, `verify_schema_parity.py:42`) — so the fragility is unreachable today; it is the same class as E2-66.

## alembic/versions/0005_node_operations.py

### [E2-26] The documented health_state domain omits "offline", which the code writes — **TRUE·LATENT**

`alembic/versions/0005_node_operations.py:10`
```
10	- sandbox_nodes.health_state         (unknown | healthy | stale | isolated)
```
`app/api/sandbox_nodes.py:266` writes `node.health_state = "offline"` (the other writers use healthy/stale/isolated/unknown at lines 116/137/163/184/217, and `app/services/cube_node_client.py:440,446` write healthy/stale). Because the column has no CHECK (`0005:31`), nothing breaks today — the harm is the future CHECK/validation built from this docstring rejecting a value the code writes on the stale-node recovery path. Documentation defect against a live column; real but conditional.

### [E2-27] downgrade drops both columns while the app still reads and writes them — **TRUE·LATENT**

`alembic/versions/0005_node_operations.py:34-36`. The columns are live on both sides (`app/models/sandbox_node.py:69-70` `scheduling_disabled`/`health_state`; the writers listed in E2-26), so a downgrade without a matching app rollback breaks the node list/isolate endpoints. True as a statement about the migration, unreachable as a defect: no deploy or CI path runs `alembic downgrade`. Same conclusion as E2-15/E2-25 — the fix is a "do not downgrade without the matching app version" note, not a code change.
## alembic/versions/0006_task_queue.py

> Group note (applies to E2-28..E2-32): the durable DB queue has **no producer and no consumer in `app/`**. `grep -rn "TaskQueueItem" app/ sdk/` only hits `app/models/task_queue.py` itself, plus `app/main.py:36` (`from app.models import task_queue as task_queue_models  # W16: durable queue table`) whose only purpose is table registration. The queue that actually runs in production is the Redis/in-memory one: `app/services/task_queue.py:1-24`, imported by `app/services/task_pipeline.py:23` (`self._queue.enqueue(...)` at line 111) and started from `app/main.py:93-94` (`task_pipeline.start()`). The only caller of `claim_next/enqueue/fail/requeue_stale` in the whole repo is `tests/test_durable_task_queue.py:4`. So the table is dead schema and every runtime harm in this group needs a future wiring.

### [E2-28] ix_task_queue_status duplicates the composite's leading column, run_after is unindexed, and nothing archives done/failed rows — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:34-36`
```
34	    op.create_index("ix_task_queue_task_type", "task_queue", ["task_type"])
35	    op.create_index("ix_task_queue_status", "task_queue", ["status"])
36	    op.create_index("ix_task_queue_status_type_created", "task_queue", ["status", "task_type", "created_at"])
```
Every structural fact checks out — `Base.metadata` for `task_queue` lists exactly `[created_at,status,task_type], [status], [task_type]`: `status` alone is a prefix duplicate, `run_after` is in no index, and the hot predicate `status='pending' AND (run_after IS NULL OR run_after <= now) ORDER BY created_at` (`app/models/task_queue.py:84-89`) can indeed use only the leading `status` column of the composite and must still sort. LATENT only because nothing enqueues or claims against this table (group note above) — the write amplification and the degrading scan start the day it is wired.

### [E2-29] No CHECK on status, and no consistency rule between status and claimed_by/claimed_at — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:24-28` (no constraints) vs `app/models/task_queue.py:29-36`
```
29	    # pending | claimed | done | failed
30	    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
```
The stuck-row mechanism is real: `requeue_stale` recovers a claim only through `TaskQueueItem.claimed_at.is_not(None)` (`app/models/task_queue.py:141-145`), so a `status='claimed'` row with `claimed_at IS NULL` is invisible both to `claim_next` (filters `status == "pending"`, line 85) and to the reaper — permanently stuck. `complete()` also leaves stale `claimed_by/claimed_at` on `done` rows (line 115-117). All three writers that could produce such a row are inside that same module, and none of them is called from `app/`, so the poison state is unreachable today.

### [E2-30] attempts / max_attempts have no range constraints — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:27-28`
```
27	        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
28	        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
```
Correct that the retry decision is the bare comparison `if item.attempts >= item.max_attempts` (`app/models/task_queue.py:124`), so `max_attempts=0` converts the first failure into a dead letter. The only in-repo writer passes `max_attempts` from the enqueue default; no API or service path can set it, hence latent (same class as E2-29).

### [E2-31] updated_at is ORM-maintained only; SQL-side writers silently stale it — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:32` gives `updated_at` a `DEFAULT CURRENT_TIMESTAMP` and nothing else, while `app/models/task_queue.py:40` carries `onupdate=func.now()`. The asymmetry is exactly as described; there is no SQL-side writer (and no stall-detection consumer of this column anywhere in `app/`), so it stays a documentation/hardening item until the queue is wired.

### [E2-32] downgrade drops the whole queue — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:39-43` — accurate, but the claim's premise "队列写入方分散在多个服务中" is false for this table: the writers are the five helpers in `app/models/task_queue.py` and nothing else (group note). No `alembic downgrade` runs in CI or any deploy script (`ci.yml:42`, `scripts/verify_schema_parity.py:42` both use `upgrade head`), so this is the same docstring-note item as E2-15/E2-25/E2-27.

## alembic/versions/0007_shared_volumes.py

### [E2-33] attachments.volume_id has a CASCADE FK but no index led by volume_id — **TRUE·LIVE**

`alembic/versions/0007_shared_volumes.py:40-41` + `:44` + `:46`
```
40	        sa.Column("volume_id", postgresql.UUID(as_uuid=True),
41	                  sa.ForeignKey("shared_volumes.id", ondelete="CASCADE"), nullable=False),
44	        sa.UniqueConstraint("session_id", "volume_id", name="uq_volume_attachment"),
46	    op.create_index("ix_volume_attachments_session", "shared_volume_attachments", ["session_id"])
```
`Base.metadata` for `shared_volume_attachments` confirms the only structures are `[session_id]` (index) and `(session_id, volume_id)` (unique) — nothing led by `volume_id`, so the claim's index analysis is right. Both named consequences are reachable: `app/services/shared_volumes.py:80-82` selects attachments `where volume_id == volume.id` and then `app/services/shared_volumes.py:85` does `db.delete(volume)` (PG re-checks the FK against the child table by `volume_id`), reached by `DELETE /api/v1/shared-volumes/{volume_id}` (`app/api/shared_volumes.py:138-147`). Live but low-impact: `shared_volume_attachments` is one row per attach, i.e. tiny. The ORM also lacks the index (`app/models/shared_volume.py`), so a fix must move both, as the claim notes.

### [E2-34] shared_volumes.owner_id has no FK to users.id — **TRUE·LATENT**

`alembic/versions/0007_shared_volumes.py:27`
```
27	        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
```
The baseline comparison is fair (`0000` does put FKs on the analogous ownership columns, and `Base.metadata` shows `contracts`/`data_products`/`certificates` all carry `users.id` FKs while `shared_volumes` has only the `(owner_id,name)` unique). But the orphan-on-user-delete harm cannot occur: there is no user-deletion code anywhere in `app/` (`grep "delete(User"` → no hits), so no code path can produce the ownerless volume. Latent referential-integrity gap worth closing at creation time; the claim's SQLite caveat about later ALTERs matches `0018`'s own note.

### [E2-35] attachments.session_id has no FK (and no CASCADE) — **TRUE·LATENT**

`alembic/versions/0007_shared_volumes.py:39`
```
39	        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
```
True and asymmetric with `volume_id` two lines below. The orphan harm needs a session delete or per-session purge; neither exists — sessions change status only, and no `db.delete(SandboxSession)` appears in `app/`. The residual effect the claim itself concedes (`uq_volume_attachment` occupying the pair) therefore never materialises. Same verdict class as E2-20/E2-34.

### [E2-36] ix_shared_volumes_owner_id is redundant with uq_shared_volume_owner_name — **TRUE·LIVE**

`alembic/versions/0007_shared_volumes.py:31` + `:34`
```
31	        sa.UniqueConstraint("owner_id", "name", name="uq_shared_volume_owner_name"),
34	    op.create_index("ix_shared_volumes_owner_id", "shared_volumes", ["owner_id"])
```
`Base.metadata` shows both structures coexisting, and `app/models/shared_volume.py:24` keeps `owner_id ... index=True`, i.e. the duplication is intentional ORM parity rather than drift. The redundant index costs a write on every volume create — real, trivial at this table's scale. The claim's caveat is also right that `alembic check`/parity would flag a one-sided removal (and note that this repo's parity checker only compares table/column names, so it would not even see the index — see the Score note).

## alembic/versions/0008_mpc_persistence.py

### [E2-37] mpc_key_shares lacks UNIQUE(key_id, share_index); duplicate x coordinates make Shamir recovery return a wrong key silently — **TRUE·LIVE**

`alembic/versions/0008_mpc_persistence.py:32-34` + `:40`
```
32	        sa.Column("share_id", sa.String(length=128), primary_key=True, nullable=False),
33	        sa.Column("key_id", sa.String(length=64), nullable=False),
34	        sa.Column("share_index", sa.Integer(), nullable=False),
40	    op.create_index("ix_mpc_shares_key", "mpc_key_shares", ["key_id"])
```
The schema fact is exactly as claimed (`Base.metadata`: `key_id` index non-unique, no unique on `(key_id, share_index)`), and the arithmetic consequence is right: `_mod_inverse(0, p)` is `pow(0, p-2, p)` = 0 (`app/services/mpc_service.py:39-41`), so `lagrange_coef` becomes 0 instead of raising (`app/services/mpc_service.py:61-64`) and a wrong integer comes back as a success. I could not reach it through the DB the way the claim phrases it — `split_key` writes `share_id = f"{key_id}-s{i}"` (`app/services/mpc_service.py:178,191-197`) so a duplicate `share_index` needs a non-ORM writer — but the same silent-wrong-key outcome is live one level up: `POST /api/v1/mpc/reconstruct` takes `share_ids: list[str] = Field(..., min_length=2)` with no dedupe (`app/api/mpc.py:67,134-142`), and `reconstruct_key` guards with `len(found) >= threshold` (dict keyed by share_id, `app/services/mpc_service.py:227-230`) yet then builds `points` by iterating the **raw request list** (`app/services/mpc_service.py:233-238). A request like `["k-s1","k-s1","k-s2"]` with threshold=2 passes both guards and feeds two identical x coordinates into the interpolator. Fix at both layers: dedupe in `reconstruct_key` (one line) plus the `UNIQUE(key_id, share_index)` the claim asks for.

### [E2-38] No CHECK on threshold/total_shares/status — **TRUE·LATENT**

`alembic/versions/0008_mpc_persistence.py:22-24`. The app-side guard exists and is the only writer: `app/services/mpc_service.py:204,214` writes `status="active"`, rotate writes `rotated` (line 296), destroy writes `destroyed` (line 323), and reconstruct rejects any non-active key at line 221-222; the numeric bounds live in `MPCService.split_key` and the Pydantic request. So `threshold > total_shares` rows require a SQL-side insert → latent hardening; the `status` typo scenario likewise (a typo'd value simply fails closed at line 221).

### [E2-39] ix_mpc_keys_status is unused — **TRUE·LIVE**

`alembic/versions/0008_mpc_persistence.py:28`. Confirmed no query filters on it: `grep -n "status" app/services/mpc_service.py` shows only Python-side comparisons after a PK load (`221`, `291`, `320`), `_load_key`/`_load_shares` key on `key_id`, and `list_keys` is `select(MPCKeyRecord).order_by(MPCKeyRecord.created_at)` with no WHERE (`app/services/mpc_service.py:260-261`). The index is therefore dead weight on every rotate/destroy. Cost is negligible but the claim is factually correct, and its alternative (`(status, created_at)`) is what a future "list active keys" endpoint would need.

## alembic/versions/0009_trained_models.py

### [E2-40] trained_models.size_bytes is sa.Integer() → 2.1 GB ceiling, INSERT fails — **TRUE·LIVE**

`alembic/versions/0009_trained_models.py:28`
```
28	        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
```
The column is written straight from the uploaded artifact's byte count on the model-registration path, so a checkpoint above 2,147,483,647 bytes raises `integer out of range` on PG. Unlike the 0010 twin (E2-46), there is **no** size cap in front of this write: `app/services/rag_generative_service.py:22` caps RAG bundles at 256 MiB, but I found no equivalent constant on the trained-model registration path. `BigInteger` is the right call; the `input_tokens`/`output_rows` half of the claim is speculative — `app/services/inference_service.py:164-172` writes per-request values and `update_usage_on_completion` sets (not accumulates) `output_rows` (line 178-182), so 32-bit is fine there.

### [E2-41] ix_trained_models_product duplicates 0018's ix_trained_models_product_id — **TRUE·LIVE**

`alembic/versions/0009_trained_models.py:34` + `alembic/versions/0018_owner_fks.py:30`
```
34	    op.create_index("ix_trained_models_product", "trained_models", ["product_id"])
30	    op.create_index("ix_trained_models_product_id", "trained_models", ["product_id"])   # 0018
```
Confirmed end-state from `Base.metadata`: `trained_models` indexes = `[product_id], [product_id], [status]` — two identical B-trees. The claim is also right that `0018`'s docstring justification ("the chain never created this index") is false: line 34 above is the proof, in the same repository. Every trained-model insert pays for both. Deleting the 0009 one is safe (ORM `app/models/trained_model.py` keeps `index=True`, which matches 0018's name).

### [E2-42] inference_usage.model_id / user_id have no FKs — **TRUE·LATENT**

`alembic/versions/0009_trained_models.py:37-51` (no `ForeignKeyConstraint`; `Base.metadata` shows zero FKs on `inference_usage`). The metering rows are written for every inference call (`app/services/inference_service.py:164-173`), so the table is live — but the mis-attribution harm needs the referenced row to disappear or be rewritten, and `trained_models` is never deleted: the only `db.delete()` targets in `app/` are data products, data resources, webhook subscriptions, api endpoints, network policies, cube templates and shared volumes; models are revoked via `status`/`revoked_at`. Latent.

### [E2-43] status has no value domain and double-truth with revoked_at — **TRUE·LATENT**

`alembic/versions/0009_trained_models.py:29`. All in-app writers use literal constants, and the availability check reads one field (`app/services/inference_service.py` model-prepare path plus `_load_model` at line 110), so a contradictory `(status, revoked_at)` pair again needs a non-ORM writer. Latent hardening; the single-source-of-truth point is worth a docstring even without the CHECK.

### [E2-44] ix_inference_usage_model should be (model_id, created_at); trained_models list filters are under-indexed — **TRUE·LIVE**

`alembic/versions/0009_trained_models.py:34-35` + `:48` and the readers
```
116	        query = select(TrainedModel).order_by(TrainedModel.created_at.desc())   # inference_service.list_models
118	            query = query.where(TrainedModel.owner_id == owner_id)
120	            query = query.where(TrainedModel.product_id == product_id)
```
Live today, unlike E2-42: `usage_stats` filters `model_id`/`contract_id` over an append-only metering table whose only indexes are `(model_id)` and `(contract_id)` — no `created_at` — so windowed aggregation sorts after a single-column filter, and `list_models` sorts by `created_at DESC` with no composite to serve it. Note the claim's second half is slightly off-target: `owner_id` on `trained_models` has **no index at all** (`Base.metadata`: `product_id, product_id, status`), so the owner-scoped listing is the worse query, not the missing `(product_id,status)` pair (no query filters status).

## alembic/versions/0010_rag_generative.py

### [E2-45] sandbox_tasks.rag_generative_model_id is String(64) while rag_generative_models.model_id is a UUID PK — **TRUE·LATENT**

`alembic/versions/0010_rag_generative.py:20` vs `:24`
```
20	    op.add_column("sandbox_tasks", sa.Column("rag_generative_model_id", sa.String(length=64), nullable=True))
24	        sa.Column("model_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
```
The type mismatch is real and mirrored in the ORM (`app/models/sandbox_task.py:78` `String(64)`, `app/models/rag_generative_model.py` UUID). But each claimed consequence is refuted at the call sites: nothing compares the two columns in SQL (the only consumer converts in Python — `app/services/task_pipeline.py:358` `uuid.UUID(str(task.payload[...]))`, and `app/services/rag_generative_service.py:81-82` queries the UUID PK), so no `operator does not exist` error is possible; a malformed value raises `ValueError` inside the guarded block at `app/services/task_pipeline.py:352-363`, which logs and leaves `rag_generative = None` → the runner fails closed, no 500; and the value is only ever embedded into the generated runner via `repr()` (`app/services/rag_service.py:598`), so no injection either. What survives is the structural half: no FK, no format check at write time (`app/api/sandbox_tasks.py:73,167` stores the form field verbatim), dangling id after a model row is deleted — real but unreachable as a failure today.

### [E2-46] rag_generative_models.size_bytes is sa.Integer() — **TRUE·LATENT**

`alembic/versions/0010_rag_generative.py:29`. Fact correct; the app rejects oversized bundles before the INSERT (`app/services/rag_generative_service.py:22,37-38`: `_MAX_BUNDLE_BYTES = 256 * 1024 * 1024`), so the 2 GiB ceiling is 8x above the enforced limit and cannot be hit through the API. The claim states this guard itself; the residual is a latent trap if the cap is raised — worth changing together with E2-40 in one revision.
## alembic/versions/0011_chain_anchoring_records.py

### [E2-47] No CHECK guaranteeing "confirmed ⇒ on-chain evidence exists" — **TRUE·LATENT**

`alembic/versions/0011_chain_anchoring_records.py:22-27`
```
24	        sa.Column("tx_hash", sa.String(length=128), nullable=True),
25	        sa.Column("block_height", sa.Integer(), nullable=True),
26	        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
27	        sa.Column("receipt", sa.JSON(), nullable=True),
```
The DDL really admits `(confirmed=true, tx_hash=NULL, receipt=NULL)`, but the only writer cannot produce it: `app/services/blockchain_adapter.py:76-101` always builds a `receipt` payload (line 98 — that is what `_load_chain_records` re-hydrates from, line 118-132), and every backend that sets `confirmed=True` also sets `tx_hash` (e.g. the PG append-only path, `app/services/blockchain_adapter.py:500-507`). So the "无凭据记录被当已确认" row needs a non-writer. Useful nuance for whoever implements the fix: the constraint is cheap precisely because live data already satisfies it — but it must be `confirmed ⇒ tx_hash IS NOT NULL AND receipt IS NOT NULL` only if no future backend may confirm asynchronously.

### [E2-48] block_height is sa.Integer(); chain heights are u64 — **TRUE·LATENT**

`alembic/versions/0011_chain_anchoring_records.py:25` (`block_height`) and `app/models/chain_anchoring.py` (same `Integer`). Nothing in this repo writes a large height: the pg-log backend leaves `block_number` unset (`app/services/blockchain_adapter.py:500-507` passes only `tx_hash=chain_hash`, `confirmed=True`) and the fisco/ant adapters are disclosed in-process simulations (`app/services/blockchain_adapter.py:640-645`, which states outright "no real/wired chain anchoring exists" when no real backend is configured). The overflow only bites the day a real chain adapter is wired — the claim's own "don't edit 0011 in place, add an ALTER revision" caveat is the correct handling.

## alembic/versions/0013_volume_keys.py

### [E2-49] server_default="SM4-GCM" contradicts the feature's own anti-fabrication rule — **TRUE·LATENT**

`alembic/versions/0013_volume_keys.py:27`
```
27	        sa.Column("algorithm", sa.String(length=32), nullable=False, server_default="SM4-GCM"),
```
The conflict the claim describes is real and documented on both sides: `app/api/kms.py:31-45` `_volume_cipher_label` says the persisted record "must carry what the plugin declared — never a hardcoded SM4-GCM claim for a volume that was sealed with AES. Undeclared is recorded as `unknown`", and the API always writes the label explicitly (`app/api/kms.py:302` → `algorithm=algorithm, key_length=len(dek)` at line 313-314). So the DDL default can only fire for a writer that skips the column — a SQL backfill or hand-repair — which is exactly the case the project's 禁伪造 rule cares about. Latent (no in-app path hits it), but it is the wrong default for a compliance column and costs nothing to change to no-default + `nullable=False` on a fresh chain.

### [E2-50] key_length / status carry no CHECK; "active with no wrapped_key" is a legal row — **TRUE·LATENT**

`alembic/versions/0013_volume_keys.py:28-30`. Each claimed invariant is guarded in the one writer: length is validated against config before insert (`app/api/kms.py:295-298`, `DEK must be {settings.CUBE_DEK_LENGTH} bytes`), distribute refuses anything that is not active or has no key material (`app/api/kms.py:353`), and revoke is idempotent-by-status (`app/api/kms.py:386`). A mismatched `(algorithm, key_length)` or `'Active'` therefore needs a SQL-side write → latent hardening; the `status='active' AND wrapped_key IS NULL` CHECK the claim proposes is the one worth having, since `kms.py:353` shows the code already treats that state as impossible.

## alembic/versions/0014_cube_templates.py

### [E2-51] updated_at is not refreshed by the database — **TRUE·LATENT**

`alembic/versions/0014_cube_templates.py:35` and `:49` (both tables: `server_default=CURRENT_TIMESTAMP`, no trigger). As with E2-22/E2-31 the ORM is the only updater, and the impact is smaller than the claim assumes: no code in the template pipeline reads `updated_at` — `grep -n updated_at app/services/cube_template_service.py` returns nothing — so the GC/retry logic cannot be misled; only a human reading the row during an incident would be. Note also that this table is genuinely where the distribution state machine lives (`_TEMPLATE_TRANSITIONS`/`_NODE_TRANSITIONS`), which is why the docstring note is the right fix rather than a trigger.

### [E2-52] status has no CHECK and the state machine treats an unknown source state as terminal — **TRUE·LATENT**

`alembic/versions/0014_cube_templates.py:30` + `app/services/cube_template_service.py:123-132`
```
123	def _check_transition(kind: str, src: str, dst: str) -> None:
124	    table = _TEMPLATE_TRANSITIONS if kind == "template" else _NODE_TRANSITIONS
125	    allowed = table.get(src, set())
126	    if dst not in allowed:
```
Confirmed both halves: an out-of-domain `src` yields an empty allow-set, i.e. that row can never transition again (every attempt 400s with `VALIDATION_ERROR`), and `ready_node_ids` filters on `status == READY` (`app/services/cube_template_service.py:444-451`), so a dirty node row silently removes a schedulable node. But all writers go through the guarded setters (`_check_transition("template", ...)` at line 489 and `("node", ...)` at line 494) with values from the `CubeTemplateNodeStatus` enum, so no in-app path writes `ready`/`ERROR`. Latent; the CHECK is a cheap add since both tables are created in this one revision.

## alembic/versions/0015_cube_egress_dedup.py

### [E2-53] ix_cube_egress_dedup_node duplicates the unique constraint's leading column — **TRUE·LIVE**

`alembic/versions/0015_cube_egress_dedup.py:30-32`
```
30	        sa.UniqueConstraint("node_id", "file_name", "offset", "line_no", name="uq_cube_egress_dedup_key"),
32	    op.create_index("ix_cube_egress_dedup_node", "cube_egress_dedup", ["node_id"])
```
The unique constraint is led by `node_id`, so every node-prefixed lookup is served by it; the extra single-column index is pure write cost on the hottest insert path in the repo (one dedup row per egress JSONL line, `app/services/cube_egress_audit.py:290-294`). `created_at`, the column an eventual janitor would filter on, is unindexed — see E2-55.

### [E2-54] check-then-act dedup with one commit and no IntegrityError handling — **TRUE·LIVE**

`app/services/cube_egress_audit.py:273-309`
```
277	        existing = await db.execute(
278	            select(CubeEgressDedup.id).where(
...
285	        if existing.first() is not None:
286	            duplicates += 1
...
290	        db.add(CubeEgressDedup(node_id=node_id, file_name=file_name, offset=offset, line_no=line_no))
...
309	    await db.commit()
```
The endpoint (`app/api/cube_egress.py:31-35`, service-token guarded via `verify_cube_audit_token`) calls this straight through and nothing in `app/api/cube_egress.py` catches `IntegrityError`. Two overlapping pushes of the same tail window (node restart while the old tailer is still draining, or a retry racing the original) both pass the SELECT, and the loser's commit raises on `uq_cube_egress_dedup_key` — aborting the transaction, so that batch's `audit_logs` rows written by `audit_service.log` in the same loop are lost too and the caller gets a 500 instead of the documented `{"duplicates": m}`. (Self-duplication *inside* one batch is actually caught, because the session autoflushes before the next SELECT.) The proposed `ON CONFLICT DO NOTHING` / per-row SAVEPOINT is the right fix; the node side treats 5xx as retryable, so the practical symptom is repeated churn rather than permanent loss.

### [E2-55] cube_egress_dedup is not in the retention plan and has no TTL — **TRUE·LIVE**

`app/services/retention_janitor.py:48-50`
```
48	        ("audit_logs", "app.models.audit_log", "AuditLog", settings.AUDIT_LOG_RETENTION_DAYS),
49	        ("alert_records", "app.models.alert", "AlertRecord", settings.ALERT_RETENTION_DAYS),
50	        ("merkle_leaves", "app.models.merkle_leaf", "MerkleLeaf", settings.MERKLE_LEAF_RETENTION_DAYS),
```
Exactly the three tables the claim names; the janitor iterates only that plan (line 65), so the dedup table grows one row per egress line forever. The claim's caveat is also the right one: any window must stay ≥ the node's maximum re-push window, otherwise a re-delivered line would be re-ingested as a new audit event — and note that `audit_logs` itself is never purged (retention=0 means forever, project rule), so this table's growth is the only unbounded one in the egress path.

### [E2-56] id has no database-side default — **TRUE·LATENT**

`alembic/versions/0015_cube_egress_dedup.py:24` — no `server_default`, while `app/models/cube_egress_dedup.py` supplies `default=uuid.uuid4` client-side. This is the repo-wide convention for new tables in this chain (0011:21, 0013:23, 0017:29 all do the same), so singling out 0015 is arbitrary; a non-ORM insert would indeed hit NOT NULL, and there is no non-ORM insert (the only writer is `cube_egress_audit.py:290-294`). Worth one docstring sentence at most.

### [E2-57] offset / line_no have no non-negative CHECK — **TRUE·LATENT**

`alembic/versions/0015_cube_egress_dedup.py:27-28` vs `app/api/cube_egress.py:19-21`
```
20	    offset: int = Field(ge=0)
21	    line_no: int = Field(ge=1)
```
The claim is accurate including its own concession that the live path is clean; the sole writer is that schema-checked endpoint, so a negative key requires SQL-side access. Latent hardening.

## alembic/versions/0016_sandbox_tee_policy.py

### [E2-58] nullable + server_default creates a third state (NULL) distinct from 'none' — **TRUE·LATENT**

`alembic/versions/0016_sandbox_tee_policy.py:25-32`
```
27	        sa.Column("tee_policy", sa.String(length=32), nullable=True, server_default="tee_if_available"),
31	        sa.Column("trust_root", sa.String(length=16), nullable=True, server_default="none"),
```
The three-state hazard exists on paper, and the direction of failure it describes would indeed be the dangerous one (`trust_root != "none"` reading NULL as "has a trust root"). Two facts keep it latent: every write path assigns a concrete string — `app/api/sandbox_sessions.py:359-360` sets `tee_policy=body.tee_policy, trust_root="none"` at insert and line 433 overwrites with `session.trust_root = _tee if isinstance(_tee, str) else "none"` — and **no consumer reads either column from the DB**: the only TEE decision compares the request body (`app/api/sandbox_sessions.py:409` `require_tee = body.tee_policy == "tee_required"`), and the compliance report derives its own `trust_root` from the attestation service (`app/services/compliance_report.py:48,53`), not from sessions. So today these columns are write-only disclosure fields; the claim's "either make it NOT NULL or drop the default" is worth settling *before* a reader appears (the 0024-style "leave NULL = unrecorded" convention it points at is real — `alembic/versions/0024_bundle_review_columns.py:5`).

### [E2-59] No CHECK on two security-relevant string columns — **TRUE·LATENT**

`alembic/versions/0016_sandbox_tee_policy.py:27,31`; domains are documented in the docstring (`:4-8`) and enforced by Pydantic (`app/schemas/sandbox_session.py:76-80`, `Invalid tee_policy: ... Must be one of VALID_TEE_POLICIES`). The silent-downgrade scenario requires a value that got past that validator, and the enforcement point that exists reads the validated body, not the column (E2-58). Latent; and note the constraint's real value is future-proofing, since the claim's premise "下游按 == tee_required 做强校验" is not yet true of the column.

### [E2-60] server_default backfills historical sessions with a policy they never expressed — **TRUE·LATENT**

`alembic/versions/0016_sandbox_tee_policy.py:27,31` against `alembic/versions/0024_bundle_review_columns.py:5` ("只加列、不回填，存量包保持 NULL 并由前端显示「未记录」，绝不反推"). The inconsistency with the repo's own newer convention is real. Two mitigating facts: the sessions table is append-only in production terms (a chain-built PG database is upgraded before any data exists — 0016 lands before the SS-13 cutover traffic), and neither column feeds the compliance report today (E2-58), so no historical *audit* artefact is falsified. Doc/convention item, not a defect with a live effect.

### [E2-61] downgrade drops the TEE policy and trust root columns irreversibly — **TRUE·LATENT**

`alembic/versions/0016_sandbox_tee_policy.py:35-37`. Accurate; unreachable (no `alembic downgrade` in any script or workflow — same finding as E2-15/E2-25/E2-27/E2-32). The suggested one-line comment in `downgrade()` is the whole fix.

## alembic/versions/0017_api_keys.py

### [E2-62] issued_by FK has no explicit ON DELETE, and rotate moves the issuer to the rotating admin — **TRUE·LATENT**

`alembic/versions/0017_api_keys.py:37` + `:46`
```
37	        sa.Column("issued_by", sa.UUID(), nullable=False),
46	        sa.ForeignKeyConstraint(["issued_by"], ["users.id"]),
```
The FK half is latent for the now-familiar reason: nothing in `app/` deletes users, so RESTRICT never fires. On the second half I disagree with the framing — `app/api/api_keys.py:229-231` writes `status="active", issued_by=admin.id` for the **new** key row while the old row keeps its original `issued_by` and is flipped to `revoked` (line 237). Each row recording who caused *that* key to exist is the correct forensic model; "签发人随轮转漂移" would only be a defect if rotation rewrote the historical row, which it does not. Fix is a one-word `ondelete="RESTRICT"` plus a comment.

### [E2-63] status has no domain CHECK; a dirty value makes a credential silently unusable — **TRUE·LATENT**

`alembic/versions/0017_api_keys.py:35`; ORM-side guards: PATCH accepts only `Literal["active","disabled"]` (`app/api/api_keys.py:51`), create/rotate write `"active"` (lines 141, 229), revoke writes `"revoked"` (line 237), and auth fails closed on anything else (`app/core/deps.py:91` `if key.status != "active"` → denied with `reason=key.status`). So an invalid value is a self-inflicted outage of one key, not an auth bypass — and producing it requires a SQL-side write. Latent, worth the CHECK given this is the machine-credential table.

## alembic/versions/0018_owner_fks.py

### [E2-64] Plain CREATE INDEX inside the migration transaction blocks writes on trained_models — **TRUE·LIVE**

`alembic/versions/0018_owner_fks.py:29-30`
```
29	def upgrade() -> None:
30	    op.create_index("ix_trained_models_product_id", "trained_models", ["product_id"])
```
On PostgreSQL this is a locking `CREATE INDEX` executed inside the migration transaction, and unlike most revisions in this chain this one is guaranteed to run on real PG deployments (that is its stated purpose, docstring lines 14-17). Impact is bounded in practice — `trained_models` holds one row per registered model, so the SHARE-lock window is milliseconds — but the claim's direction is right for a table that could grow. One correction to the suggested remedy: `postgresql_concurrently=True` cannot simply be added, because CONCURRENTLY refuses to run inside a transaction; it needs `with op.get_context().autocommit_block():` (and then the matching concurrent drop in `downgrade`).

### [E2-65] Unused `import sqlalchemy as sa` — **TRUE·LIVE**

`alembic/versions/0018_owner_fks.py:20` — `grep -n "sa\." alembic/versions/0018_owner_fks.py` returns zero hits, and the body is only the two `op.*` calls at lines 29-34. Dead import; cosmetic.

### [E2-66] downgrade assumes the index exists — **TRUE·LATENT**

`alembic/versions/0018_owner_fks.py:33-34`. Same class as E2-25/E2-32/E2-61: no `alembic downgrade` runs anywhere in the repo, so the missing `if_exists=True` cannot bite. Bundle all four of these into one "downgrade hardening" pass if anyone ever cares about reversibility.
## alembic/versions/0006_task_queue.py

### [E2-28] ix_task_queue_status duplicates the composite index's leading column and no index serves the claim query — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:35-36`
```
35	    op.create_index("ix_task_queue_status", "task_queue", ["status"])
36	    op.create_index("ix_task_queue_status_type_created", "task_queue", ["status", "task_type", "created_at"])
```
The ORM mirrors both (`app/models/task_queue.py:22` composite + `:30` `status ... index=True`), so the duplication is real, not drift, and the read-path analysis is right: `claim_next` filters `status == "pending" AND (run_after IS NULL OR run_after <= now)` and sorts by `created_at` (`app/models/task_queue.py:82-92`); `run_after` is in no index, and with `task_types` unset the composite's middle `task_type` column forces a sort. What keeps it latent: nothing in `app/` ever writes this table. `enqueue/claim_next/complete/fail/requeue_stale` are called only from `tests/test_durable_task_queue.py` (a leaf); `app/main.py:36` imports the module only to register the table, and the queue that actually runs is the Redis/in-memory one (`app/services/task_queue.py`, driven by `task_pipeline`, started at `app/main.py:93-94`). Zero writes ⇒ no write amplification and no claim query to degrade. The fix is right, and wiring the worker the header advertises ("multi-replica durable queue") is what would promote it to LIVE.

### [E2-29] status has no CHECK and no status↔claimed_at invariant; a claimed row without claimed_at is unrecoverable — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:24-28` (no `CheckConstraint` anywhere in the file). The arithmetic of the claimed harm checks out: `requeue_stale` requires `claimed_at.is_not(None)` (`app/models/task_queue.py:143`), so a `status='claimed'` row with NULL `claimed_at` is invisible to the sweeper and unreachable for claimers (`claim_next` only takes `status == "pending"`, line 85) — permanently stuck. But no producer exists: the only writer that sets `claimed` is the conditional UPDATE at `app/models/task_queue.py:101-105`, which sets `claimed_at=now` in the same statement, and `fail()`/`requeue_stale()` clear `claimed_by`/`claimed_at` as a pair with `status="pending"` (`:127-129`, `:149-151`). Needs a non-ORM writer to occur ⇒ schema-hardening (the same `VALID_*`-constant point as E2-18/E2-23).

### [E2-30] attempts/max_attempts have no range CHECK — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:27-28`
```
27	        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
28	        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
```
Facts right, and `fail()` does decide dead-lettering with `if item.attempts >= item.max_attempts` (`app/models/task_queue.py:124`), so `max_attempts <= 0` would mean "fail on first error". Latent twice over: the table has no in-app writer at all (see E2-28), and even `enqueue(max_attempts=…)` (`:53`) is never called with a caller-supplied value from any route — `max_attempts` in `app/api/deliveries.py` belongs to a different table (`app/models/webhook_delivery.py:39`).

### [E2-31] updated_at is maintained only by the ORM — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:32` declares DEFAULT only; the ORM adds `onupdate=func.now()` (`app/models/task_queue.py:40`). Two corrections to the claim: (a) the in-app Core-style claim UPDATE (`app/models/task_queue.py:101-105`) is an ORM-enabled `update(TaskQueueItem)`, and `func.now()` as a SQL-expression `onupdate` is emitted into its SET clause, so the queue's own bypass-looking write does maintain the column; (b) no consumer reads it — nothing in `app/` selects `task_queue.updated_at`, and staleness detection uses `claimed_at` (`:144`), so the "运维看板/卡死检测误判" premise has no reader today. Combined with the no-writer finding (E2-28), latent documentation/trigger nicety.

### [E2-32] downgrade() drops the queue table with all pending work — **TRUE·LATENT**

`alembic/versions/0006_task_queue.py:39-43` — `drop_index` ×3 then `drop_table`, i.e. pending/claimed rows and `last_error` evidence are gone. True statement, unreachable defect: the repo only ever runs `alembic upgrade head` (`scripts/verify_schema_parity.py:42`, `.github/workflows/ci.yml:42`) and AGENTS.md forbids editing/downgrading history. Same conclusion as E2-15/E2-25/E2-27 — a docstring note is the whole fix, and the table currently holds no application traffic anyway.
## alembic/versions/0007_shared_volumes.py

### [E2-33] CASCADE FK on shared_volume_attachments.volume_id has no index — **TRUE·LIVE**

`alembic/versions/0007_shared_volumes.py:40-41` (FK with `ondelete="CASCADE"`), `:44` `uq_volume_attachment (session_id, volume_id)` (led by `session_id`), `:46` the only other index is on `session_id`. ORM agrees (`app/models/shared_volume.py:35-44`) ⇒ no drift, the gap is real on both sides, and the contrast the claim draws is fair: `0008_mpc_persistence.py:38-40` does index its CASCADE child column. Live traffic on all three cost paths: every attach INSERT fires PG's parent-existence trigger, which must scan `shared_volume_attachments` by `volume_id` with no usable index (`app/services/shared_volumes.py:54` via `app/api/shared_volumes.py:104`); `GET /api/v1/shared-volumes` counts attachments with `volume_id.in_(…)` (`app/api/shared_volumes.py:73-76`); `delete_volume()` selects attachments by `volume_id` twice then relies on the cascade (`app/services/shared_volumes.py:78-85`), reached from `DELETE /{volume_id}` (`app/api/shared_volumes.py:138`). Router mounted at `app/main.py:373`. Fix must add the index on both sides (model + new revision), exactly as the claim says.

### [E2-34] shared_volumes.owner_id has no FK to users — **TRUE·LATENT**

`alembic/versions/0007_shared_volumes.py:27` — plain UUID, no `ForeignKeyConstraint`; the ORM matches (`app/models/shared_volume.py:24`), and no later revision adds one (`grep -rn shared_volume alembic/versions/` hits only 0007). The baseline does FK comparable ownership columns, so the inconsistency is real. Latent because no orphans can be produced: `grep -rn 'delete(User\|db.delete(user' app/` returns nothing — users are never deleted in `app/`. Also note the repo's documented stance that FKs the chain cannot retro-fit are aligned on the ORM side instead (`0018_owner_fks.py:13-17`), so "record the intent in the docstring" is the realistic half of this fix.

### [E2-35] shared_volume_attachments.session_id has no FK — **TRUE·LATENT**

`alembic/versions/0007_shared_volumes.py:39` — no FK, unlike `volume_id` two lines below (`:40-41`) and unlike baseline session-FK children (`audit_logs`, `pipeline_tasks`, `sandbox_tasks`). Same reachability finding as E2-20/E2-34: `SandboxSession` rows are never hard-deleted (the only `db.delete()` calls in `app/` cover data products/resources, webhooks, endpoints, network policies, cube templates and these volumes), so no orphan attachments rows can be produced, and `resolve_binds`/`resolve_mounts` filter by `session_id` (`app/services/shared_volumes.py:90-112`) so a hypothetical orphan is never mounted. Cheap guard, not a live break.

### [E2-36] ix_shared_volumes_owner_id duplicates the leading column of uq_shared_volume_owner_name — **TRUE·LIVE**

`alembic/versions/0007_shared_volumes.py:31` `UniqueConstraint("owner_id","name")` + `:34` single-column index on `owner_id`; ORM mirrors both (`app/models/shared_volume.py:20` and `:24` `index=True`), so it is a genuine duplicate rather than drift, and the claim's caveat is confirmed: the index must go from the model too or `alembic check` re-adds it. The unique index does serve every `WHERE owner_id = ?` lookup — including the only such query, `app/api/shared_volumes.py:67` — so the extra index is write-only cost. Low volume (volume create/delete is admin-ish traffic), so this is the mildest of the duplicate-index family (E2-8/9/10/24).
## alembic/versions/0008_mpc_persistence.py

### [E2-37] mpc_key_shares lacks UNIQUE(key_id, share_index); a duplicate x-coordinate silently reconstructs the wrong key — **TRUE·LATENT**

`alembic/versions/0008_mpc_persistence.py:32-40` — PK is `share_id` only; `key_id` gets a non-unique index. The silent-failure chain is exactly as described: `reconstruct_key` builds `points.append((rec.share_index, y))` from caller-supplied ids (`app/services/mpc_service.py:233-238`, reached from `POST /api/v1/mpc/keys/reconstruct`, `app/api/mpc.py:142`), and duplicate x makes `denominator` 0 (`:61`), `_mod_inverse(0, p) = pow(0, p-2, p) = 0` (`:39-41`) — no exception, wrong secret (`:63-66`). What blocks it: the only writer assigns `share_id=f"{key_id}-s{i}"` and `share_index=i` one-to-one (`app/services/mpc_service.py:176-194`), and `rotate_key` mints a **new** `key_id` and crypto-erases the old rows (`delete(...)` at `:300`) rather than re-inserting shares under the old key — so no reachable write produces two rows with the same `(key_id, share_index)`. Needs an out-of-band insert/restore; for a Shamir escrow table that invariant belongs in the DDL anyway, and the ORM `__table_args__` (`app/models/mpc_key.py:36`) must move with it.

### [E2-38] threshold/total_shares/status have no CHECK constraints — **TRUE·LATENT**

`alembic/versions/0008_mpc_persistence.py:22-24`. Validation exists only in the service: `threshold < 2`, `total_shares < threshold`, `total_shares > 255` (`app/services/mpc_service.py:157-161`) plus the Pydantic bounds on the split request, so an out-of-band row with `threshold > total_shares` would be persisted `active` and only fail at reconstruct (`len(found) < key_rec.threshold`, `:229`). No such writer exists (single insert path, `:176-206`), and the status vocabulary is written by three in-app literals only (`status="active"` `:204/:214`, `"rotated"` `:296`, `"destroyed"` `:323`) — all inside String(16). Latent hardening, same class as E2-29/E2-30.

### [E2-39] ix_mpc_keys_status serves no query — **TRUE·LIVE**

`alembic/versions/0008_mpc_persistence.py:28`; ORM declares it too (`app/models/mpc_key.py:21`), so no drift. The claim's survey is accurate: every read is keyed by `key_id`/`share_id` (`_load_key`, `get_share` `app/services/mpc_service.py:250-258`) and `list_keys` has no status filter at all (`:261` `select(MPCKeyRecord).order_by(MPCKeyRecord.created_at)`), while `status` *is* written on rotate and destroy (`:296`, `:323`) — so the index is maintained and never read. Real, tiny cost: `mpc_keys` is one row per admin `POST /api/v1/mpc/keys/split`, so this is write-cost hygiene, not a performance defect. Removing it means dropping it from `__table_args__` as well.
## alembic/versions/0009_trained_models.py

### [E2-40] trained_models.size_bytes is a 4-byte INTEGER — **TRUE·LATENT**

`alembic/versions/0009_trained_models.py:28` and ORM mirror `app/models/trained_model.py:34` (`Integer`) ⇒ on PG the column is `integer`, ceiling 2,147,483,647. But the only writer is `inference_service.register_model`, which rejects anything above `_MAX_MODEL_BYTES = 256 * 1024 * 1024` (`app/services/inference_service.py:36`, `:63-64`) before storing `size_bytes=len(artifact_bytes)` (`:80`), and `POST /api/v1/inference/models` additionally sits behind the global 10 MB `RequestSizeLimitMiddleware` installed at `app/core/security.py:220` via `app/main.py:303`. The claim's other trigger — batch-accumulated `input_tokens`/`output_rows` — also does not exist: `update_usage_on_completion` assigns (`values(output_rows=output_rows, …)`, `:179-181`), it does not add. So the overflow needs the cap to be raised (the natural next step for ONNX checkpoints) — a `BigInteger()` in a new revision is cheap insurance, but nothing fails today.

### [E2-41] 0018 creates a second index on trained_models.product_id, and its docstring premise is false — **TRUE·LIVE**

`alembic/versions/0009_trained_models.py:35`
```
35	    op.create_index("ix_trained_models_product", "trained_models", ["product_id"])
```
against `alembic/versions/0018_owner_fks.py:30` `op.create_index("ix_trained_models_product_id", "trained_models", ["product_id"])`. No revision between them drops the 0009 index (`grep -rn trained_models alembic/versions/` → only 0009, 0010 (chain header) and 0018), so a chain-built PG database ends with two identical btree indexes. The ORM declares only the 0009 spelling (`app/models/trained_model.py:22` `Index("ix_trained_models_product", …)`) plus `index=True` on the column (`:29`) — which is why `alembic check` saw the *missing* index in the first place and the fix duplicated it instead of renaming. And 0018's stated premise "the ORM carried index=True on trained_models.product_id that the chain never created" (`0018_owner_fks.py:9-10`) is factually wrong: 0009 created it. Fix on both sides: keep one index name in the chain (a new revision dropping `ix_trained_models_product`) and drop the redundant `Index(...)` from `__table_args__`.

### [E2-42] inference_usage.model_id/user_id have no FK — **TRUE·LATENT**

`alembic/versions/0009_trained_models.py:40-42` — no FK on `model_id`, `user_id` (or on `trained_models.owner_id/product_id`, `:22-23`), while the same chain does declare create-time FKs (`0007:40-41`, `0008:38`). The orphan-producing action does not exist: `grep -rn 'delete(' app/services/inference_service.py app/api/inference.py` returns nothing — models are revoked (`status='revoked'` + `revoked_at`, `:102-104`), never deleted, and users are never deleted either. Usage rows are appended per invoke (`:164-172`) and read back only through `usage_stats` (`:185-194`), which never joins `trained_models`, so nothing silently drops rows today. Latent guard + the E2-34 docstring caveat.

### [E2-43] status unconstrained and duplicated by revoked_at as the revocation signal — **TRUE·LATENT**

`alembic/versions/0009_trained_models.py:29,32`. Both halves verified: the sole revocation writer sets the pair together (`app/services/inference_service.py:102-104`, `if model.status == "registered": status="revoked"; revoked_at=now`), and the enforcement side reads **only** `status` — `if not model or model.status != "registered"` (`:311`) and `app/api/inference.py:128` — so `revoked_at` is display/reporting only (`app/api/inference.py:223`). A typo'd status therefore would indeed bypass the gate, but no in-app write can produce one (literals only) ⇒ latent hardening; the "single authoritative source" note is worth a line in the model docstring (`app/models/trained_model.py:3-5` currently says registered → revoked, which matches).

### [E2-44] inference_usage index should be (model_id, created_at) — **TRUE·LIVE**

`alembic/versions/0009_trained_models.py:50` single-column; ORM mirror `app/models/trained_model.py:44`. The query the claim describes exists verbatim: `usage_stats` = `select(InferenceUsage).order_by(InferenceUsage.created_at.desc())` + optional `model_id`/`contract_id` filters (`app/services/inference_service.py:185-194`), called from `GET /api/v1/inference/metering` (`app/api/inference.py:226-239`, which loops once per owned model). Since `inference_usage` gets a row per invoke, that ORDER BY is an unindexed sort over the model's slice — a real (if modest) live cost, and the composite keeps the model_id-only capability. One correction: the claim's second half has no matching query — `list_models` filters `owner_id`/`product_id` only, never `status` (`:113-121`), so `(product_id, status)` would be speculative.
## alembic/versions/0010_rag_generative.py

### [E2-45] sandbox_tasks.rag_generative_model_id is String(64) while rag_generative_models.model_id is UUID — **TRUE·LATENT**

`alembic/versions/0010_rag_generative.py:20` vs `:24`; the ORM matches the chain on both sides (`app/models/sandbox_task.py:78` `String(64)`, `app/models/rag_generative_model.py:22` UUID) so there is no parity break — the type divergence itself is real. Two of the three consequences, however, do not hold:

1. No query compares the two columns in SQL. The consumer converts in Python and passes a typed UUID to the ORM: `uuid.UUID(str(task.payload["rag_generative_model_id"]))` → `prepare_rag_generative_model(..., model_id: uuid.UUID)` (`app/services/task_pipeline.py:356-359`), so no `varchar = uuid` operator error is reachable.
2. The "invalid input syntax for type uuid → 500" path is caught: the conversion sits inside `try: … except Exception as e: logger.warning("[Pipeline] RAG generative materialization failed…")` (`app/services/task_pipeline.py:352-363`), so a malformed id degrades to a warning + no bundle in the workspace (fail-closed per the comment at `:344-345`), not a 500.

What is left is the genuine part: an unvalidated free-text column (`app/api/sandbox_tasks.py:118-123` only checks non-emptiness, never that the id parses or that the bundle exists) that can dangle, and no FK possible. Latent ⇒ aligning the type or validating at the API is the right call, but nothing breaks today.

### [E2-46] rag_generative_models.size_bytes is a 4-byte INTEGER — **TRUE·LATENT**

`alembic/versions/0010_rag_generative.py:29`, ORM mirror `app/models/rag_generative_model.py:27` (`Integer`) ⇒ PG `integer`. The claim states the guard correctly and the guard holds: the single writer rejects bundles above `_MAX_BUNDLE_BYTES = 256 * 1024 * 1024` before storing `size_bytes=len(artifact_bytes)` (`app/services/rag_generative_service.py:22`, `:37-38`, `:51`), and the route is behind the 10 MB request-cap middleware (`app/core/security.py:220`, installed at `app/main.py:303`). So it is the same insurance-policy finding as E2-40 — `BigInteger()` in a *new* revision (never editing 0010, which may already be applied), worth doing together with `trained_models.size_bytes`.
