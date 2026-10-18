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
## alembic/versions/0019_flow_orchestration.py

### [E2-67] workloads.test_run_id has no FK although the docstring promises "保留真实外键" — **TRUE·LATENT**

`alembic/versions/0019_flow_orchestration.py:9-10` vs `:127`
```
9	workloads.test_run_id 指向后建的 workload_test_runs，故先建表再 add_column，
10	保持线性迁移且保留真实外键。
...
127	    op.add_column("workloads", sa.Column("test_run_id", sa.UUID(), nullable=True))
```
The docstring/code mismatch is real and the ordering argument in the claim holds: `workload_test_runs` is created at lines 110-125, i.e. **before** the `add_column` at line 127, so an FK was technically available right there. The stated harm is refuted by the gate itself: `app/services/workflow_service.py:227-233` loads the run and raises `WorkloadGateError(GATE_TEST_NOT_PASSED)` when `select(WorkloadTestRun).where(id == workload.test_run_id)` finds nothing — a dangling id fails closed, it does not pass silently. So: missing constraint + a docstring that overstates what the migration did; no live integrity failure.

### [E2-68] Global UNIQUE(event_id) contradicts the one-row-per-(event × subscription) fan-out model — **TRUE·LATENT**

`alembic/versions/0019_flow_orchestration.py:220` + `:234`
```
220	        sa.Column("event_id", sa.String(length=64), nullable=False),
234	        sa.UniqueConstraint("event_id", name="uq_webhook_deliveries_event_id"),
```
Diagnosis confirmed at the writer: `app/services/delivery_service.py:110` inserts one row per matching subscription with a fresh `event_id=uuid.uuid4().hex`, and that id is what receivers see (`EVENT_ID_HEADER` at `app/services/delivery_service.py:122`) — so today `event_id` really is a delivery id, and every receiver dedupes on a value that is unique per delivery. Nothing breaks now (the claim says so too); the constraint is what blocks the obvious next step (a deterministic per-event id for cross-subscription idempotency) — the second subscription's insert would violate `uq_webhook_deliveries_event_id`. Latent design trap; `(subscription_id, event_id)` is the semantically matching key.

### [E2-69] Many new FK columns have no supporting index — **TRUE·LATENT**

Schema facts all verified against `Base.metadata` (the chain end-state):
- `workloads` fk: contract_id, parent_id, published_by, source_notebook_id, source_task_id, user_id — idx: only `(contract_id,env,status)`, `(status,user_id)`, `user_id`
- `compute_runs` fk: workload_id, task_id, requested_by, session_id (+indexed contract_id) — idx: `contract_id`, `(contract_id,status)`, `(created_at,status)`
- `notebooks` fk: parent_id, session_id (unindexed) — idx: `contract_id`, `(status,user_id)`, `user_id`, `task_no(u)`
- `workload_test_runs` fk: triggered_by, session_id (unindexed) — idx: `workload_id`
- `api_endpoints` fk: workload_id (unindexed) — idx: `owner_id`, `contract_id`, `(contract_id,status)`
PostgreSQL really does not index FKs automatically. But the delete-side cost the claim predicts needs someone to delete or re-key a parent row, and none of these parents is ever deleted: `grep -rn "delete(Notebook|delete(Workload|delete(Contract|delete(User|delete(SandboxTask" app/` returns nothing, and no read path filters on the unindexed columns (only `WebhookDelivery.subscription_id` queries exist, at `app/api/deliveries.py:681,708,760`, and that column is indexed twice — 0019:237-238). Latent: worth adding when the first workload-retention or per-workload listing lands.

## alembic/versions/0020_webhook_detached_history.py

### [E2-70] downgrade re-imposes NOT NULL that upgrade-产生的 data violates — **TRUE·LATENT**

`alembic/versions/0020_webhook_detached_history.py:28-33`
```
28	def downgrade() -> None:
29	    if op.get_bind().dialect.name == "postgresql":
30	        op.alter_column("webhook_deliveries", "subscription_id", existing_type=sa.UUID(), nullable=False)
```
Everything the claim needs is real: the app nulls child rows on subscription delete (`app/api/deliveries.py:681`: `update(WebhookDelivery).where(subscription_id == ...).values(subscription_id=None)`, exactly as this file's docstring line 4-5 prescribes), so one deleted subscription is enough to make `downgrade` fail with `column "subscription_id" contains null values`. Of all the `downgrade` defects in this batch this is the only one that is *data-certain* rather than environment-conditional — still LATENT because no workflow or deploy script ever runs `alembic downgrade` (`ci.yml:42`, `scripts/verify_schema_parity.py:42`), and on PG the failure is a clean transactional rollback. The suggested `raise` with an operator-facing message is the cheap right fix.

### [E2-71] The SQLite batch_alter_table branch can leave the table half-rebuilt — **FALSE**

`alembic/versions/0020_webhook_detached_history.py:31-33`. The first half (the copy fails on `NOT NULL constraint failed`) is right, and identical to E2-70; the distinguishing claim — "此时原表已被 DROP，索引与 FK 关系处于半重建状态 … 需人工介入" — is not how this executes. SQLite DDL is transactional, and `batch_alter_table`'s new-table/copy/drop/rename sequence runs inside the transaction alembic opens around the revision (`alembic/env.py` `do_run_migrations` → `connection.begin_transaction()`), so the failing INSERT SELECT rolls the whole batch back and `webhook_deliveries` plus its three indexes survive untouched. That is also why the file bothers to branch by dialect at all. The `if_exists`-style pre-check suggestion is a nicety; the corruption risk it is meant to prevent does not exist.

### [E2-72] Detached history loses its subscription context and the 0019 FK is still RESTRICT — **TRUE·LIVE**

`alembic/versions/0020_webhook_detached_history.py:20-25` only relaxes the constraint; nothing records what the row used to belong to, while `app/api/deliveries.py:681` actively produces those rows on every `DELETE` of a subscription. Consequence today: `webhook_deliveries` rows with `subscription_id IS NULL` cannot be attributed (the URL/name/owner live only on `webhook_subscriptions`, which is hard-deleted), and no query, view or janitor looks at that population. That part happens for real, so I mark it live — but I push back on the second half: the RESTRICT-again scenario ("置空逻辑被旁路 → ForeignKeyViolation") is a hypothetical about a hand-run `DELETE FROM webhook_subscriptions`, and the nulling is deliberately dialect-independent for exactly that reason (docstring lines 4-5). A `subscription_url` snapshot column or an orphan view is the fix; nothing in the running system errors.

## alembic/versions/0021_delivery_bundle_ownership.py

### [E2-73] delivery_bundles.size_bytes is sa.Integer() → >2 GiB bundle fails the insert — **TRUE·LIVE**

`alembic/versions/0021_delivery_bundle_ownership.py:29`
```
29	        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
```
Live write with no cap in front of it: `app/api/deliveries.py:282-293` builds the whole bundle in memory, uploads it, then inserts `size_bytes=len(payload)` — the byte length of an arbitrary dataset export, bounded only by whatever the caller is willing to zip. On PG a bundle over 2,147,483,647 bytes raises `numeric value out of range` and, worse, the failure lands *after* `storage_service.upload(...)` (line 284), so the object is already in MinIO while the ownership row does not exist — leaving an undownloadable orphan object and a 500. SQLite (CI/dev) never range-checks, so the suite stays green: the exact PG-drift class 0018's docstring warns about. Fix is `BigInteger` here + `app/models/delivery_bundle.py`, plus a size guard before upload; and note the row-forensics wrinkle: any orphan object from a failed insert needs a janitor.

### [E2-74] Three FKs with no explicit ON DELETE — **TRUE·LATENT**

`alembic/versions/0021_delivery_bundle_ownership.py:32-34`
```
32	        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
33	        sa.ForeignKeyConstraint(["session_id"], ["sandbox_sessions.id"]),
34	        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
```
Correct that these are NO ACTION/RESTRICT on PG and unenforced on SQLite/CI, and correct that this table's own history (E2-74's 0020 comparison) shows the repo already decided delivery history outlives its parent. But the blocking scenario requires deleting a contract, session or user, and none of those is deleted anywhere in `app/` (same grep as E2-69). Latent; when contract purge is designed, this FK is the one that will surface first, so writing the decision down now is cheap.

## alembic/versions/0022_compute_run_params.py

### [E2-75] NULL and explicit {} are indistinguishable, defeating the reproducibility goal — **FALSE**

`alembic/versions/0022_compute_run_params.py:21-22` (both columns `nullable=True`, no `server_default`) — the DDL half is true, the inference is not. I tested it against this repo's own SQLAlchemy with a JSON column:
```
('empty', '{}',   params IS NULL = 0)     # explicit {}  -> stored '{}'
('none',  'null', params IS NULL = 0)     # explicit None -> stored JSON 'null'
('omitted', None, params IS NULL = 1)     # never set     -> SQL NULL
```
So case 3 (a user submitting `{}`) is stored as a JSON object and is cleanly distinguishable from a NULL pre-0022 row; `{**(run.resource_override or {})}` (`app/services/compute_scheduler.py:175`) and `if not params` (`app/services/compute_scheduler.py:74-81`) treat them the same at *execution* time, which is the correct outcome for an empty parameter set. What the claim missed is a real but different defect: because SQLAlchemy's JSON type defaults to `none_as_null=False`, the scheduler path's explicit `params=None` (`app/services/compute_scheduler.py:268,321-322`) is persisted as JSON `null`, not SQL NULL — so "not recorded" exists in two physical representations, and `ComputeRun.params.is_(None)` silently misses the JSON-`null` rows. That is the ambiguity worth reporting (and it applies to every JSON column written with an explicit `None` in this repo, not just these two).

### [E2-76] downgrade drop_column destroys recorded run inputs — **TRUE·LATENT**

`alembic/versions/0022_compute_run_params.py:25-27`. Accurate, and the audit-discipline argument is fair given these columns exist to make runs reproducible (docstring lines 4-5). Unreachable: no `alembic downgrade` in any script/CI path, consistent with every other downgrade finding in this batch.

## alembic/versions/0023_task_fields.py

### [E2-77] Non-concurrent UNIQUE INDEX on "大表 notebooks" inside the migration transaction — **TRUE·LATENT**

`alembic/versions/0023_task_fields.py:20-25`
```
20	    op.add_column("notebooks", sa.Column("task_no", sa.String(32), nullable=True))
25	    op.create_index("ix_notebooks_task_no", "notebooks", ["task_no"], unique=True)
```
The mechanism is described correctly (PG takes a write-blocking SHARE lock; it cannot be CONCURRENTLY inside the revision's transaction; `op.get_context().autocommit_block()` + `postgresql_concurrently=True` is the escape hatch). The premise is not: `notebooks` is created by 0019, dated 2026-10-15 — nine days before this revision — so there is no large table to lock and no deployment where this migration has ever blocked anything. It becomes true the moment notebooks history matters, which is precisely when a rollback of a unique index built CONCURRENTLY (INVALID index on duplicate `task_no`) becomes the bigger headache. Verdict latent-by-premise; the docstring note is the proportionate response.
## contracts/AuditRegistry.sol

> Group note (E2-78..E2-81, corroborated by the operator's own pre-check): the contract is 43 lines, has **no** access control, and **nothing in the running system talks to it**. A repo-wide search for `AuditRegistry` outside `contracts/` hits only documentation — `AGENTS.md:97,134`, `docs/mock-remediation-plan.md:187` ("有 FISCO 单节点 + AuditRegistry.sol 部署，但无代码连接"), `docs/ai-sandbox-gap-remediation-plan.md:231` — there is no deployment script, no ABI, no web3 client, and `app/services/blockchain_adapter.py:638-645` states outright that with no configured backend "no real/wired chain anchoring exists". So the harm in all four claims depends on a chain deployment that does not exist; note also that `AGENTS.md:134` already records the permissionless-write issue (against a stale function name, `anchorRoot`).

### [E2-78] anchorAudit is permissionless and first-writer-wins, so anyone can front-run or DoS a root — **TRUE·LATENT**

`contracts/AuditRegistry.sol:27-34`
```
27	    function anchorAudit(
28	        bytes32 merkleRoot,
29	        string memory metadata
30	    ) external {
31	        require(audits[merkleRoot] == 0, "AuditRegistry: already anchored");
33	        audits[merkleRoot] = block.timestamp;
```
Both mechanics are exactly as described: no modifier/signature check anywhere in the file, and the `== 0` sentinel makes the first writer permanent while blocking the legitimate anchorer forever. It is LATENT only because no verifier consumes this registry: `verifyAudit` (line 40-42) has no caller outside the file, and the app's real anchoring is the PG append-only log or a disclosed in-process simulation (`app/services/blockchain_adapter.py:640-645`). Whoever wires a chain must pick one of the two options the claim lays out — restrict the writer, or demote `verifyAudit` to an existence proof — because with the current code the "authoritative" reading is unsafe.

### [E2-79] No zero-root check, so bytes32(0) can be anchored and later "verified" — **TRUE·LATENT**

`contracts/AuditRegistry.sol:31` uses `audits[merkleRoot] == 0` as the not-anchored sentinel with no `require(merkleRoot != bytes32(0))` in front of it, so anchoring the all-zero root is legal and `verifyAudit(bytes32(0))` (line 40-42) then returns true. Whether that is exploitable depends entirely on whether an empty batch/empty leaf can hash to all zeros — nothing in `app/` computes a Merkle root into this contract today (see group note), and an SM3 tree over real leaves cannot produce a zero root. Latent; the one-line guard costs nothing and should go in with whatever change wires the chain.

### [E2-80] AuditAnchored does not record the anchorer's address — **TRUE·LATENT**

`contracts/AuditRegistry.sol:16-20`
```
16	    event AuditAnchored(
17	        bytes32 indexed merkle_root,
18	        uint256 timestamp,
19	        string metadata
20	    );
```
True: neither the event nor the `audits` mapping (line 11, `bytes32 => uint256`) keeps `msg.sender`, so on a permissionless write path an indexer cannot distinguish the platform's anchor from a front-run one — which is the only forensic recourse given E2-78. Reachable only after deployment (group note). This is the change I would pair with E2-78 whichever way the permission question is settled, since an `address indexed anchor` is free and later migrations cannot retroactively add it.

### [E2-81] Existence is encoded as block.timestamp, so a timestamp-0 chain would silently un-anchor writes — **TRUE·LATENT**

`contracts/AuditRegistry.sol:11` + `:33` + `:41` (`audits[merkleRoot] = block.timestamp` / `return audits[merkleRoot] != 0`). The coupling is real and the failure mode is correctly reasoned: on any chain where a block can carry timestamp 0, the write succeeds, costs gas, and `verifyAudit` still returns false, with the root re-anchorable. In practice no public chain mines timestamp 0 and the FISCO/AntChain paths this repo would target start from real clocks, so it is a test-environment/edge hazard; and as long as no chain is wired (group note) it cannot manifest.

## policies/system.rego

### [E2-82] health is an unconditional `true` constant — **TRUE·LATENT**

`policies/system.rego:1-5` — the whole file is
```
1	package cds.system
2	
3	default allow = false
4	
5	health = true
```
The claim's own verification holds up independently: `k8s/opa.yaml:29-33` probes `httpGet /health:8181` (OPA's built-in endpoint), and nothing anywhere evaluates `data.cds.system.health` — a repo-wide search for `cds.system` returns only `namespace: cds-system` strings in the k8s manifests. So the rule is inert; the hazard is a future reader treating it as evidence of health. Either a semantic comment or deletion (the claim's alternative) closes it.

### [E2-83] No positive allow rule and no rego test pinning the default-deny contract — **TRUE·LATENT**

`policies/` contains exactly `README.md` and `system.rego` (no `system_test.rego`, and `system.rego` is the only `.rego` file in the repo). `data.cds.system.allow` is therefore constantly false and has no evaluator: the runtime policies the platform actually pushes are generated per-bundle (`app/services/policy_compiler.py:62,450-452`, `app/services/policy_evaluator.py:78,116` → `opa_client.push_policy(package_path, bundle.rego_source)`), and the only rego-shape assertions live in `tests/test_policy.py:33` (`bundle.rego_source.count("allow {") == 1`) — which tests the generated bundle, not this anchor package. So there is no live deny/allow behaviour at stake today; the missing baseline test matters exactly when someone adds the first `allow if` to this package, because Rego unions same-named rules.

## sdk/cds_sdk/__init__.py
### [E2-84] pyproject says 0.3.0, `__version__` says 0.2.0 — **TRUE·LIVE** (already known)

`sdk/cds_sdk/__init__.py:22` (`__version__ = "0.2.0"`) vs `sdk/pyproject.toml:3` (`version = "0.3.0"`).
Confirmed, and per the task instructions this is the pre-existing known drift, not a new finding. Two clarifications for whoever fixes it: nothing in the SDK derives a User-Agent from `__version__` (no `User-Agent` header is set anywhere in `client.py`), so the observable damage today is limited to caller-side diagnostics that print `cds_sdk.__version__`; and deriving it via `importlib.metadata.version("cds-sdk")` needs the `PackageNotFoundError` fallback the claim mentions, because CI installs the SDK editable from `./sdk` and callers may vendor the package directory.

## sdk/cds_sdk/client.py

### [E2-85] `_request_with_headers` never checks the status code, and `list_sessions` writes into the body unguarded — **TRUE·LIVE**

`client.py:245-255`
```
245	    def _request_with_headers(self, method: str, path: str, **kwargs: Any) -> tuple[Any, dict]:
246	        retries = kwargs.pop("_retries", self._max_retries)
247	        resp = self._client.request(method, path, **kwargs)
248	        if retries > 0 and resp.status_code in (429, 503):
249	            time.sleep(_retry_after_seconds(resp))
250	            return self._request_with_headers(method, path, _retries=retries - 1, **kwargs)
251	        resp.headers
252	        body = resp.json() if resp.status_code != 204 and resp.content else None
253	        return (
254	            CDSResponse(body, dict(resp.headers)) if isinstance(body, dict) else body,
255	            dict(resp.headers),
```
Against `_request` (line 236: `if resp.status_code >= 400: raise _parse_error(...)`) the asymmetry is exactly as claimed: only 429/503 are special, so 401/403/404/500 come back as a value. `resp.json()` at line 252 is also unguarded, so an HTML/text error page raises `json.JSONDecodeError` out of the SDK. And the one caller, `list_sessions` line 331 `body["next_cursor"] = headers.get(...)`, throws `TypeError` when `body` is `None` (204/empty) or a `list`. The realistic entry point is a keyset listing against a gateway that answers 502 with HTML, or an empty 204 body. Fix all three in one place as the claim says — the `isinstance(body, dict)` guard in `list_sessions` alone leaves the status-code hole.

### [E2-86] `_parse_error` assumes the error body is a JSON object — **TRUE·LIVE**

`client.py:115-121`
```
118	    try:
119	        payload = resp.json()
120	        detail = payload.get("detail", payload)
121	    except ValueError:
```
`resp.json()` succeeding on `[ "boom" ]`, `"unauthorized"` or `123` means line 120 raises `AttributeError: 'list' object has no attribute 'get'` — outside the `except ValueError` net — and the real HTTP status is lost. This is on the error path of every request in the SDK (`_request:237`, `_request_with_headers` callers, `refresh_token:503`, `login:527`, `register:549`), and non-object error bodies are what non-FastAPI frontends emit: the repo's own deploy path is nginx/compose in front of uvicorn, plus the cube egress/attestation sidecars; a `502` HTML page is caught by line 121 but a JSON array from a proxy or a stubbed test double is not. Guard with `payload.get(...) if isinstance(payload, dict) else payload`.

### [E2-87] Identifiers are interpolated into request paths with no percent-encoding — **TRUE·LIVE**

`client.py:429-437`
```
429	    def download_file(self, session_id: str, filename: str) -> bytes:
430	        resp = self._client.get(f"/api/v1/sandbox-sessions/{session_id}/files/{filename}")
...
435	    def delete_file(self, session_id: str, filename: str) -> dict:
436	        return self._request("DELETE", f"/api/v1/sandbox-sessions/{session_id}/files/{filename}")
```
I measured what httpx actually does with these strings (`httpx.URL(base + "/api/v1/sandbox-sessions/SID/files/" + f)`):
```
'../../admin/users' -> /api/v1/sandbox-sessions/admin/users
'a?b=1'             -> path /…/files/a            query 'b=1'
'a#frag'            -> path /…/files/a            fragment 'frag'
'a/b.txt'           -> /api/v1/sandbox-sessions/SID/files/a/b.txt
```
So `?`/`#` silently truncate the resource name and `..` does climb out of the `/files/` segment — and this is reachable with legal names, because the server's own validator only forbids separators, control characters and over-long names (`app/services/session_files.py:41-50`), leaving `?`, `#`, spaces and `..`-bearing names admissible. Where I disagree with the claim is the severity framing: the request still carries the caller's own token and lands on a route that authorizes independently, so this is a wrong-request/404-class bug (plus a name-confusion hazard), not SSRF or privilege escalation — the caller cannot reach anything their own credential does not already allow. `urllib.parse.quote(filename, safe="")` plus rejecting `"/"`, `"\\"`, `"\x00"` client-side (mirroring the server's rule) is the right fix, and `stream.py` needs the same treatment (see E2-104).

### [E2-88] Task code and RAG query travel in the URL query string — **TRUE·LIVE**

`client.py:609-620`
```
609	        params: dict[str, Any] = {"session_id": session_id, "task_type": task_type}
610	        if code is not None:
611	            params["code"] = code
...
618	        if rag_query:
619	            params["rag_query"] = rag_query
620	        return self._request("POST", "/api/v1/sandbox-tasks", params=params)
```
Confirmed for `create_task`: the whole script body (or the natural-language RAG query) becomes a query parameter of a logged URL. Note the claim is wrong about `execute()` — that one already uses a JSON body (`client.py:361`, `json={"code": code, "language": language}`), as does the notebook exec path (`client.py:891`) — so the exposure is specific to `POST /api/v1/sandbox-tasks`. The backend really does accept it as query parameters, so this is a client/server contract, not a client-only bug; and the repo's own RAG design shows the safer pattern (query text is embedded into the encrypted task code rather than stored in the clear, `app/services/rag_service.py:578-580`). Access-log exposure plus the 4-8 KB gateway limit are both real consequences.

### [E2-89] Retries apply to non-idempotent POSTs — **TRUE·LIVE**

`client.py:228-243` — the retry predicate is `retries > 0 and resp.status_code in (429, 503)` with no method test, so every POST in the SDK (`create_session`, `execute`, `exec_command`, `create_snapshot`, `rollback_snapshot`, `terminate_session`, `trigger_compute_run`, `redeliver_webhook_delivery`, …) is replayed. The 429 case is harmless (rejected before the handler ran); the 503 case is the one the claim targets and it is genuinely unsafe when the 503 comes from a proxy after the upstream already committed. This is the repo's documented retry policy (429/503 via `Retry-After`, 410 deliberately terminal), so the fix is not "stop retrying" but "make retried POSTs idempotent": the backend already has an `idempotency_key` column on `compute_runs` (`alembic/versions/0019_flow_orchestration.py:137`) that the SDK does not populate, and a client-generated key for create/rollback would close the rest.

### [E2-90] Auth-path mutual exclusion is incomplete and CDSError is misused for construction errors — **FALSE**

`client.py:197-207`. The validation is actually complete: `>1` of token/api_key/app → error at 197-198; half an app pair (`app_id` without `app_secret`) → error at 200-204; nothing → error at 205-206; bad `Bearer ` prefix → error at 185-193. The claim's own worked example is the refutation — passing `api_key` **and** `app_id` (no `app_secret`) makes `has_app` true (line 200 tests `app_id or app_secret`), so `197` fires with "pass exactly one auth path", which is precisely the right diagnosis rather than a misleading one. The remaining points are style: `api_key` and JWT sharing one `Authorization: Bearer` header is documented as intentional at lines 148-154 (the server distinguishes them by the `cds_` prefix), and `raise CDSError(400, ...)` for a constructor misuse is a defensible choice — the SDK uses `CDSError.status_code` as a diagnostic tag throughout (`stream.py:60` uses 400 for empty code, 501 for a missing dependency), and these raise before any request exists, so no caller can mistake them for an HTTP outcome worth retrying.

### [E2-91] iter_sessions silently yields zero rows when auth fails — **TRUE·LIVE**

`client.py:343-353`
```
347	        while True:
348	            page = self.list_sessions(limit=page_size, cursor=cursor, **filters)
349	            for item in page.get("items", []):
350	                yield item
351	            cursor = page.get("next_cursor")
```
Combined with E2-85 this is the worst live SDK defect in the batch: a 401 (expired JWT) is *returned as a value*, `CDSResponse` is a dict, so `page.get("items", [])` finds nothing, `page.get("next_cursor")` returns `None`, and the loop ends after one iteration. A data-pipeline script paging through sessions with a stale token sees a successful, empty result set — silent data loss with no exception, no log, and no non-empty `next_cursor` to notice. (`refresh_token` exists at line 496 but nothing calls it automatically.) One `raise` in `_request_with_headers` fixes both this and E2-85/E2-92's silent-page hazards.

### [E2-92] Keyset mode is inferred from default page/page_size values — **TRUE·LATENT**

`client.py:320`
```
320	        if cursor is not None or (limit is not None and page == 1 and page_size == 20):
```
The heuristic is real: passing `limit` while leaving the defaults silently switches from `skip/limit` to `cursor/limit`, and a caller who wants offset pagination *with* a limit cannot express it. But the failure the claim predicts ("服务端对同时出现 cursor 与 skip 的行为变化 → 静默返回错误页") cannot occur: in keyset mode the client sends only `{"limit", "cursor?"}` (line 322-326) and never both, so the two modes are never mixed on the wire. It is an API-surface wart (an explicit `cursor=`/`mode=` would be better), not a reachable wrong-page bug.

### [E2-93] wait_for_status: timeout<=0 never issues a request, and one transient error aborts the wait — **TRUE·LIVE**

`client.py:380-401`
```
389	        while time.monotonic() < deadline:
...
394	            last = self.get_session(session_id)
...
400	            time.sleep(interval)
401	        raise TimeoutError(f"session did not reach {list(statuses)} within {timeout}s (last status: {last.get('status')})")
```
Both halves are confirmed and both are reachable through the documented API: `wait_for_status(sid, timeout=0)` (or a negative value) issues zero requests and reports `last status: None`, which is indistinguishable from "the server never reported a status"; and any single blip inside the loop — a 503 that exhausts its three retries (`_request:229-235`), a connection reset, a 429 storm — propagates out of `wait_for_status` even though the deadline has not passed, even though the very next poll would have succeeded. For a helper whose whole job is "wait until terminal", swallowing retryable errors until the deadline is the expected semantics.

### [E2-94] `_retries` kwargs pollution, dead `_access_token`, and `_set_tokens` clobbering auth — **TRUE·LATENT**

`client.py:229` / `:209,511` / `:509-513`. Itemised: (1) the pollution vector does not exist today — `_retries` is only ever injected by the two retry recursions (lines 235, 250), and no public method forwards `**kwargs` into `_request` (the only `**kwargs` in the file are the two classmethods at 517/537, which pass them to the constructor); it stays a latent trap for whoever adds a `**kwargs` passthrough. (2) `_access_token` really is dead state: written at line 209 and line 511, never read (the header is the source of truth), so the field should go or be used. (3) The api_key-clobbering scenario is unreachable: `_set_tokens` is called only from `refresh_token` (line 506), which first requires `self._refresh_token`, and that is set only by `login`/`register` (lines 534, 554) — i.e. token mode; an `api_key=`/`app_id=` client hits `CDSApiError(401, "no refresh token available")` at line 497-498 before touching the headers. The suggested guard in `refresh_token` is still a sane belt-and-braces addition.

### [E2-95] login/register forward **kwargs into the constructor, so `headers` can erase the fresh token — **TRUE·LATENT**

`client.py:517-535` (and the mirror at 537-555)
```
531	        client = cls(base_url, body["access_token"], **kwargs)
532	        client._refresh_token = body.get("refresh_token")
```
with `__init__` line 219 doing `headers={**auth_headers, **(headers or {})}` — a caller-supplied `headers` containing `Authorization` does win over the just-issued Bearer, and the client then 401s on every later call. The facts are right; what is needed is a caller who passes an `Authorization` header into `login()`, which nothing in this repo or the docstring examples does, so I keep it latent (the same footgun exists for a direct constructor call, where overriding is arguably intended). `body["access_token"]` at line 533 is likewise a bare subscript: a 200 from a non-conforming/older backend yields `KeyError: 'access_token'` instead of a `CDSApiError` that says which URL misbehaved. Both are one-line hardenings.
### [E2-96] wait_for_operation ignores other terminal states and assumes a dict body — **TRUE·LATENT**

`client.py:451-462`
```
455	        while True:
456	            op = self.get_operation(operation_id)
457	            if op.get("status") in ("succeeded", "failed"):
458	                return op
459	            if time.monotonic() >= deadline:
460	                raise TimeoutError(f"operation {operation_id} did not finish within {timeout}s")
```
The two facts are accurate, but the server's terminal vocabulary is currently identical to the client's: `app/services/session_operations.py:20` is `_TERMINAL_STATUSES = {"succeeded", "failed"}` (writers only ever set `pending`/`running`/`succeeded`/`failed`), so there is no `cancelled`/`timeout` status an operation can reach today, and the "poll until timeout, mask the result" scenario needs the backend to add one first. Same for the `None`/list body: it requires the non-dict response class described in E2-85/E2-86. Worth a shared `_TERMINAL_OPERATIONS` constant plus an `isinstance` guard when the operation surface grows; not a live defect. Note the asymmetry with `wait_for_status` (E2-93), which does have a live problem in the same style.

### [E2-97] download_file bypasses the retry wrapper — **TRUE·LIVE**

`client.py:429-433`
```
430	        resp = self._client.get(f"/api/v1/sandbox-sessions/{session_id}/files/{filename}")
431	        if resp.status_code >= 400:
432	            raise _parse_error(resp, f"/files/{filename}")
433	        return resp.content
```
One correction to the claim: the status check *is* there (line 431), so a 429/503 becomes a proper `CDSApiError`, not a silent success. What is genuinely inconsistent is that this call (and the dataset-bundle download at the end of the file) skips `self._request`, so the documented `max_retries`/`Retry-After` behaviour does not apply to the only SDK calls that transfer bulk payloads — precisely the calls most likely to meet a 503. `_request_bytes` reusing the retry loop while returning `resp.content` is the right shape, and it also inherits the `_parse_error` hardening from E2-86.

### [E2-98] `_compact` drops every None, so "clear this field" is unexpressible — **TRUE·LIVE**

`client.py:134-137`
```
134	def _compact(fields: dict[str, Any]) -> dict[str, Any]:
135	    """Drop None entries so partial updates never send explicit nulls."""
```
Used by the update surface (`client.py:860`, `:873`, `:1020`, `:1054`, `:1123`), which means the SDK cannot send `null` for any of those fields: `quota_total=None` on an OpenAPI endpoint, `session_id=None` on a notebook update, `auto_compute`/`contract_id` clearing on a flow config all silently become "leave unchanged". That is the documented intent of the helper (its docstring says so) but an undeclared limitation of every method that uses it — the caller gets 200 OK and a no-op. An explicit sentinel (`UNSET`) or a `clear=[...]` list is the honest fix; at minimum each affected method's docstring needs "None means unchanged".

### [E2-99] Truthiness gates drop legitimate zero/empty values — **TRUE·LIVE**

`client.py:609-620` (`if timeout_seconds:` right next to `if code is not None:` in the same block) and `create_session`'s `if resource_limits:` / `if idle_policy:`. Concretely: `create_task(..., timeout_seconds=0)` sends no `timeout_seconds` at all and the server default applies, so the value the caller passed is silently ignored — while `exec_command` (line 364-369) and `refresh` (line 376-378) in the same file use `is not None` and do transmit zero-like values. Whether `0` is meaningful per-parameter is a separate question, but two conventions in one module is how the caller's setting quietly disappears; pick `is not None` and reject the values the server would refuse.

### [E2-100] Long nested ternary and `str(None)` producing "None" — **FALSE**

`client.py:77`
```
77	        expected = detail["code"] if isinstance(detail, dict) and detail.get("code") else f"HTTP_{status_code}"
```
The condition is `isinstance(detail, dict) and detail.get("code")`, so a present-but-`None` (or empty-string) `code` is falsy and the expression falls back to `HTTP_{status_code}` — the `"None"` string the claim predicts cannot be produced. Only the style complaint (a dense one-liner) survives, and `expected_code` comparisons in the SDK's tests/callers depend on this fallback working, so no change is warranted here.

### [E2-101] upload_file treats any str as a filesystem path — **TRUE·LIVE**

`client.py:407-426`
```
417	        if isinstance(data, str):
418	            with open(data, "rb") as fh:
419	                payload = fh.read()
...
421	            payload = data.read()
```
`upload_file(sid, "notes.txt", "hello\n")` — the most natural way to write the call — raises `FileNotFoundError: [Errno 2] No such file or directory: 'hello\n'`, i.e. the file *content* is echoed back in the exception message (a log-hygiene problem for secrets that were meant to be uploaded), and a text-mode file object reaches line 421, returns `str`, and then fails inside httpx with an unrelated `TypeError` about bytes. Distinguishing "path" from "content" by an explicit `content=`/`path=` argument (or `pathlib.Path` for paths) removes both traps; at minimum catch `OSError` and re-raise a `CDSError` that does not quote the content.

### [E2-102] "this session is None" conflates missing session with unsupported level — **TRUE·LATENT**

`client.py:692-702`
```
692	        level = session.get("sandbox_level") if isinstance(session, dict) else None
693	        if level not in _stream.SUPPORTED_STREAM_LEVELS:
...
701	                    f"(this session is {level!r}); use execute() or poll logs() "
```
The message really does render `this session is None` for both "response unusable" and "field absent". It stays latent because the realistic failure already raises earlier: a nonexistent session makes `get_session` raise `CDSApiError(404)` at `client.py:303-304` long before line 692, and the endpoint's session payload always carries `sandbox_level`, so reaching the confusing text needs an empty-body 200 or a renamed field. Splitting the two branches is a two-line diagnostic improvement.

## sdk/cds_sdk/stream.py

### [E2-103] recv timeouts are treated as a clean close, so streams truncate silently — **TRUE·LIVE**

`stream.py:133-141` + `:163-168`
```
135	            try:
136	                raw = ws.recv()
137	            except Exception as exc:  # server closed / timeout
138	                if _is_closed_error(exc):
139	                    return
140	                raise CDSError(502, f"websocket recv failed: {exc}") from exc
...
163	def _is_closed_error(exc: Exception) -> bool:
164	    name = type(exc).__name__
165	    if name in ("WebSocketConnectionClosedException", "WebSocketTimeoutException"):
166	        return True
167	    return isinstance(exc, (ConnectionError, TimeoutError))
```
`WebSocketTimeoutException` (what websocket-client raises on a `recv` after `settimeout`, `stream.py:46-50`) is folded into "closed", so a 30-second quiet period on a still-running command ends the iterator with `return` — no terminal frame, no exception, and the caller cannot tell truncation from completion. This is not exotic: `python3 -c` jobs that compute for >`recv_timeout` (default 30.0, `stream.py:81`) emit nothing while they compute. The safety framing is the important part and I agree with it: the docstring promises "Iteration stops after a terminal frame … or when the server closes the connection" (`stream.py:103-105`) and that a `blocked` frame means T5 DLP killed the stream, so a `blocked` frame that arrives after a quiet gap is dropped exactly like a benign close — a fail-open reading of a fail-closed contract. Distinguish the two: raise `CDSError(504, ...)` on timeout, keep `return` only for `WebSocketConnectionClosedException`, and document that the stream may still be running.

### [E2-104] session_id and the one-time ticket are interpolated into the WebSocket URL unquoted — **TRUE·LIVE**

`stream.py:111-114`
```
111	    path = (
112	        f"/api/v1/sandbox-sessions/{session_id}/exec/stream"
113	        f"?ticket={ticket}"
114	    )
```
Two consequences, measured as in E2-87: a `session_id` containing `?`/`&` injects extra query parameters into the handshake URL (and `#` truncates the ticket), so the request can arrive with a different (session, ticket) pairing than the caller built — reachable only with a caller-supplied malformed `session_id`, since the server's own ids are UUIDs. The more practical half is that the 30-second single-use ticket rides in the query string, which is what access logs record; I looked for redaction and found none (no `ticket` handling in the request-logging middleware, and the mint endpoint is `app/api/auth.py:256-261` → `app/api/session_stream.py:49`). Single-use + 30 s TTL bounds the replay window, which is presumably why it was accepted, but the claim's ask — `quote(..., safe='')` on both segments, and a note in the docs about ticket-in-URL logging — is right, and a header/first-frame credential would remove the log exposure entirely if the W10 contract can absorb it.

### [E2-105] The WebSocket handshake has no timeout — **TRUE·LIVE**

`stream.py:46-50`
```
46	def _default_ws_open(url: str, timeout: float):
47	    ws = _import_websocket().WebSocket()
48	    ws.connect(url)
49	    ws.settimeout(timeout)
50	    return ws
```
`settimeout` only governs subsequent `recv`, so both the TCP connect and the HTTP upgrade run on the OS default. Against a host that accepts the socket and never completes the upgrade (or just drops SYNs), `stream_exec` blocks for the full platform TCP timeout — minutes on Windows — while `CDSClient(timeout=...)` (httpx) suggests the call is bounded by 120 s. `ws.connect(url, timeout=timeout, handshake_timeout=timeout)` is supported by websocket-client and makes the behaviour match the rest of the SDK.

### [E2-106] ws.send is not wrapped, breaking the "only CDSError escapes" contract — **TRUE·LIVE**

`stream.py:129-133`
```
129	        frame: dict[str, Any] = {"type": "start", "command": command}
130	        if timeout_seconds is not None:
131	            frame["timeout"] = int(timeout_seconds)
132	        ws.send(json.dumps(frame))
```
`connect` (line 122-126) and `recv` (line 135-140) both convert their failures into `CDSError`, but the very first `send` is bare: the server closes the socket as soon as the ticket is rejected/expired, so `WebSocketConnectionClosedException` (or a `socket.error` on a half-open pipe) escapes raw, and callers written against the documented `CDSError`/`CDSApiError` contract (client docstrings plus `stream_exec`'s own) get an unhandled websocket-client exception on the most likely failure path — a ticket that went stale between the mint at line 107 and the handshake. Wrap the send the same way as recv (502), and see E2-107 for the `int()` on line 131.

### [E2-107] int(timeout_seconds) raises a bare ValueError/TypeError — **TRUE·LIVE**

`stream.py:130-131` inside a `try/finally` that converts nothing: `stream_exec(client, sid, code, timeout_seconds="30s")` propagates `ValueError: invalid literal for int() with base 10: '30s'`, and `timeout_seconds=-5` or `0` is forwarded to the server unchanged (the server clamps, per the docstring at `stream.py:96-98`, so the failure surfaces late as a command killed immediately). Everything else in the module validates into `CDSError(400, ...)` — `_build_command` does exactly that for empty code at `stream.py:53-55` — so this is a consistency gap on the one parameter that changes program behaviour. Validate once (`int(...)` in a `try`, reject `<= 0`) and raise `CDSError(400, ...)`.
## Score

107 claims judged: **43 TRUE·LIVE / 57 TRUE·LATENT / 7 FALSE**. Read the LATENT count with the rule this task set:
the code fact holds but nothing in `app/` reaches it (write-only column, unwired table, simulated path, or an
upstream guard absorbs it) — LATENT is not "wrong", it is "true today, harmless today". Cluster note: the alembic
group is dominated by one repeated root cause — later revisions add a column and its read path but never the matching
index/CHECK, and several of those columns turned out to be write-only, which is why so many land LATENT instead of LIVE.
The two known SDK facts (`__version__` drift, 429/503-only retry policy) were judged, not re-litigated.

| file | claims | LIVE | LATENT | FALSE |
|---|---|---|---|---|
| `alembic/env.py (6)` | 6 | 1 | 3 | 2 |
| `alembic/versions/0000_baseline_all_tables.py (6)` | 6 | 6 | 0 | 0 |
| `alembic/versions/0001_p0_security_hardening.py (4)` | 4 | 2 | 2 | 0 |
| `alembic/versions/0002_session_usability.py (1)` | 1 | 1 | 0 | 0 |
| `alembic/versions/0003_idle_policy.py (2)` | 2 | 0 | 1 | 1 |
| `alembic/versions/0004_session_operations.py (6)` | 6 | 2 | 4 | 0 |
| `alembic/versions/0005_node_operations.py (2)` | 2 | 0 | 2 | 0 |
| `alembic/versions/0006_task_queue.py (5)` | 5 | 0 | 5 | 0 |
| `alembic/versions/0007_shared_volumes.py (4)` | 4 | 2 | 2 | 0 |
| `alembic/versions/0008_mpc_persistence.py (3)` | 3 | 2 | 1 | 0 |
| `alembic/versions/0009_trained_models.py (5)` | 5 | 3 | 2 | 0 |
| `alembic/versions/0010_rag_generative.py (2)` | 2 | 0 | 2 | 0 |
| `alembic/versions/0011_chain_anchoring_records.py (2)` | 2 | 0 | 2 | 0 |
| `alembic/versions/0013_volume_keys.py (2)` | 2 | 0 | 2 | 0 |
| `alembic/versions/0014_cube_templates.py (2)` | 2 | 0 | 2 | 0 |
| `alembic/versions/0015_cube_egress_dedup.py (5)` | 5 | 3 | 2 | 0 |
| `alembic/versions/0016_sandbox_tee_policy.py (4)` | 4 | 0 | 4 | 0 |
| `alembic/versions/0017_api_keys.py (2)` | 2 | 0 | 2 | 0 |
| `alembic/versions/0018_owner_fks.py (3)` | 3 | 2 | 1 | 0 |
| `alembic/versions/0019_flow_orchestration.py (3)` | 3 | 0 | 3 | 0 |
| `alembic/versions/0020_webhook_detached_history.py (3)` | 3 | 1 | 1 | 1 |
| `alembic/versions/0021_delivery_bundle_ownership.py (2)` | 2 | 1 | 1 | 0 |
| `alembic/versions/0022_compute_run_params.py (2)` | 2 | 0 | 1 | 1 |
| `alembic/versions/0023_task_fields.py (1)` | 1 | 0 | 1 | 0 |
| `contracts/AuditRegistry.sol (4)` | 4 | 0 | 4 | 0 |
| `policies/system.rego (2)` | 2 | 0 | 2 | 0 |
| `sdk/cds_sdk/__init__.py (1)` | 1 | 1 | 0 | 0 |
| `sdk/cds_sdk/client.py (18)` | 18 | 11 | 5 | 2 |
| `sdk/cds_sdk/stream.py (5)` | 5 | 5 | 0 | 0 |
| **total** | **107** | **43** | **57** | **7** |

### Fix first

1. **E2-103** — `sdk/cds_sdk/stream.py:137-141,163-168`: a 30 s quiet period is reported as a clean close, so a T5 DLP
   `blocked` frame that arrives after that gap is dropped exactly like a normal hang-up and the caller believes the output is
   complete — fail-open on a contract the module documents as fail-closed. Split timeout from close and raise `CDSError(504)`.
2. **E2-91 (+E2-85)** — `sdk/cds_sdk/client.py:245-255,343-353`: `_request_with_headers` has no `>= 400` check, so an expired token
   makes `iter_sessions` return an empty but successful-looking result set — silent data loss in any pipeline that pages sessions.
   One `raise _parse_error(resp, path)` in that helper closes both claims.
3. **E2-37** — `app/services/mpc_service.py:233-238,39-41,61-64`: `reconstruct_key` iterates the raw request list, so a duplicated
   `x` makes `_mod_inverse(0, p)` return 0 and Shamir recovery hands back a wrong key as a success. Dedup/validate `x` at the endpoint and
   add the missing `UNIQUE(key_id, share_index)` (`0008_mpc_persistence.py:32-34`).
