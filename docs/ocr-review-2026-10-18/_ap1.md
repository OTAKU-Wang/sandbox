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
