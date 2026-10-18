# Verification report — `app/models/*.py` claim batch (37 claims)

Repo: `D:/workspace/sandbox` @ `452ffc6` (branch `feat/ss-18-dev-loop`). Every `file:line` below was read in this session;
nothing in the repo was modified.

## Verdict key

| Verdict | Meaning used here |
|---|---|
| **TRUE·LIVE** | Real defect, and a live `app/` insert/query path reaches it — state concretely what breaks. |
| **TRUE·LATENT** | Real observation (dead code, missing constraint, hygiene gap) but **no reachable runtime effect today**: either no code path produces/writes the case, or it is a convention item with zero data impact. Proven by grep. |
| **FALSE** | The claim's factual premise or stated consequence is wrong; the refuting line is quoted. |

Ignored as intentional (per the brief): additive-only linear alembic chain, model-vs-`0000` baseline deltas,
`text("false")` boolean server defaults, `verify_schema_parity.py` as the parity gate.

Two repo-wide facts that sink several claims, established up front:

1. **The parity gate compares table names and column names only** — `scripts/verify_schema_parity.py:5-6`
   ("compares table names and per-table column names"), `:22-27` (the map is `{col["name"]}`), and there is no
   type/default/index comparison in the file. So "autogenerate/parity will nag about this" arguments need a
   second mechanism.
2. **`alembic/env.py` never sets `compare_server_default`** — `alembic/env.py:48-53` and `:60-62` pass only
   `target_metadata` + `compare_type=COMPARE_TYPES`. Alembic autogenerate/`alembic check` therefore **ignore
   `server_default` drift entirely** (they do compare nullability, types when enabled, indexes and constraints).

## Migration chain (asked for)

```
$ git log --oneline -1 -- alembic/versions | cat
9f95cd1 feat(backend): SS-18/19 流通编排后端与 Jupyter 开发环（含 CR 修复）
```

`alembic/versions/` (26 revisions, linear, all tracked — `git status --porcelain alembic/versions` is empty):

```
0000_baseline_all_tables   0009_trained_models      0018_owner_fks           0024_bundle_review_columns
0001_p0_security_hardening 0010_rag_generative      0019_flow_orchestration  0025_notebook_data_resource_ids
0002_session_usability     0011_chain_anchoring_re  0020_webhook_detached_h  0026_notebook_description
0003_idle_policy           0012_user_mfa            0021_delivery_bundle_own
0004_session_operations    0013_volume_keys         0022_compute_run_params
0005_node_operations       0014_cube_templates      0023_task_fields
0006_task_queue            0015_cube_egress_dedup
0007_shared_volumes        0016_sandbox_tee_policy
0008_mpc_persistence       0017_api_keys
```

The chain is now at **0026**, not the 0021 that the docs cite. Revisions newer than 0021 add only:
`0022` → `compute_runs.params` / `compute_runs.resource_override`; `0023` → five `notebooks` columns +
`ix_notebooks_task_no` unique; `0024` → `delivery_bundles.t5_review_status/watermark_status/row_count`;
`0025`/`0026` → `notebooks.data_resource_ids` / `notebooks.description`.
**No recent revision adds any column, index or constraint that these claims say is missing**, and none of them
removes one. The claims that ask for a *new* revision (11, 12, 13, 27, 31, 32, 34, 35, 37) are asking for
something that genuinely does not exist in the chain.

## Reachability map

Writer/reader paths outside `app/models/` (grep over `app/`, `scripts/`, `deploy/`).

| table.column | written by | read by | verdict for the claim(s) on it |
|---|---|---|---|
| `api_endpoints.slug` | `api/deliveries.py:866,875` (lower-cased, 409 on collision) | `api/open_api.py:29` (route lookup `POST /open/v1/{slug}`), `deliveries.py:826,963`, `delivery_service.py:228-230`, `quota.py:256` | global unique is **required** → [1] FALSE |
| `api_endpoints.status` | `deliveries.py:879` (create), `:929` (PATCH) | `delivery_service.py:298` (admission gate), `flow_runtime.py:243-251`, `workloads.py:221-224`, `quota.py:325` | [2] FALSE |
| `api_endpoints.published_at` | `deliveries.py:880` — always set at create | `deliveries.py:831` (response dict) only; **no gate reads it** | [2] FALSE |
| `api_endpoints.timeout_seconds` | create/insert default only (absent from `schemas/flow.py:138-151`) | `delivery_service.py:344` (`spec.get(...) or endpoint.timeout_seconds`) | [3] FALSE |
| `api_endpoints.rate_limit_per_minute`, `.quota_total` | `deliveries.py:877-878` (create), `:923-926` (PATCH), bounded by Pydantic `ge=1 le=10000` / `ge=1` | `delivery_service.py:278` (`max(1, …)`), `:300` (quota gate) | [3] FALSE |
| `api_endpoints.calls_used`, `.last_called_at` | `delivery_service.py:390-391` read-modify-write + `flush` | `delivery_service.py:300` quota gate, `flow_runtime.py:260`, `quota.py`, `delivery_service.py:247` | [4] **TRUE·LIVE** |
| `blockchain_anchors.*` (all) | `services/blockchain_adapter.py:442-452` (ORM `session.add`, the only writer) | `:419-427` (last chain hash), `:468-483` (load by id), `:547-557` (integrity walk) — **never by `data_hash`** | [5] LATENT, [6] FALSE, [21] LATENT, [22] FALSE |
| `compute_runs.idempotency_key` | `compute_scheduler.py:285,300,317` | `:287` dedupe probe + DB unique `0019:151` | [7] FALSE, [8] LATENT |
| `compute_runs.status` | `:301` (`skipped_overlap`), `:318` (pending), `:129` / `:214-220` (execute) | `compute.py:47`, `flow_runtime.py`, `:291-293` overlap probe | [7] FALSE (`skipped_overlap` is produced live) |
| `compute_runs.output_files/.resource_usage/.params/.resource_override` | `:313-320` (insert), `:205`, `:207-211` (whole-object reassign) | `compute.py:47-50`, `:198` (`run.params`) | [9] LATENT |
| `delivery_bundles.size_bytes/.file_count/.row_count/.t5_review_status/.watermark_status` | `deliveries.py:286-297` (single writer, capped at `:247`) | `deliveries.py:362-365`, `flow_runtime.py:370` | [10] FALSE, [23] FALSE, [24] FALSE |
| `delivery_bundles.object_name` | `deliveries.py:283,290` (`delivery/{uuid4}.zip`) | `deliveries.py:404`, `:478` (download/preview) | [25] FALSE |
| `mpc_keys.threshold/.total_shares/.status` | `mpc_service.py:199-215` (validated at `:157-161`) | `:224-238` reconstruct, `:135-139`, `:261` list | [11] LATENT, [27] LATENT |
| `mpc_key_shares.share_index/.share_value` | `mpc_service.py:191-197` (`share_id = f"{key_id}-s{i}"` PK) | `:237-238` Lagrange, `:252`, `:333-336` | [12] FALSE, [13] LATENT |
| `rag_generative_models.vocab_size/.size_bytes` | `rag_generative_service.py:46-52` (cap `:37`, vocab ≥1 `:70-72`) | `:124` (`{model_id, vocab_size}` → runner), `:81-83` | [14] FALSE, [28] FALSE |
| `watermark_records.job_id` | `training.py:842-849` (check-then-act at `:819-823`) | `:820` dup probe, `:887` verify (`scalar_one_or_none`) | [16] **TRUE·LIVE** |
| `watermark_records.key_seed` | `training.py:825,848` (unbounded client value, `:488`) | `:904` verify metadata; echoed in response `:866` | [17] **TRUE·LIVE**, [33] LATENT |
| `watermark_records.owner_id/.model_id/.layer_positions/.ordered_positions` | `training.py:844-847` | `:893-895` expected bits, `:900-905` | [15] LATENT (dead import only) |
| `training_jobs.job_id` | `training.py:236` (`f"sft-{uuid4().hex[:12]}"`) | `training.py:809` (and the string join into `watermark_records.job_id`) | [30] FALSE (one unique index only) |
| `training_jobs.user_id/.status/.created_at` | `training.py:249-258`, `:261-262`, `:277-286`, `:391-392` | `training.py:322-331` (list, `ORDER BY created_at DESC`) | [31] **TRUE·LIVE**, [32] LATENT |
| `webhook_subscriptions.events` | `deliveries.py:621` | `delivery_service.py:98` (`is_not(None)`), `:103-105` | [19] FALSE (model and `0019:200` both NOT NULL) |
| `webhook_subscriptions.secret_enc` | `deliveries.py:622`, `:693` (`encrypt_task_code`, KMS envelope) | `delivery_service.py:120` (`decrypt_task_code`) | [20] LATENT |
| `webhook_subscriptions.user_id` | `deliveries.py:616` | `delivery_service.py:100`, `_load_subscription` | [35] **TRUE·LIVE** (redundant index) |
| `webhook_subscriptions.status` | create default `active`; auto-pause `delivery_service.py:161-163`; PATCH `deliveries.py:660+` | `delivery_service.py:97`, `:186` | feeds [18] |
| `webhook_deliveries.status/.next_retry_at/.attempt` | `delivery_service.py:110`, `:126-158`, `deliveries.py:764-767` | `delivery_service.py:171-175` (dispatch window), `deliveries.py:708-712` | [18] **TRUE·LIVE** (partly) |
| `webhook_deliveries.subscription_id` | `:110`, `deliveries.py:782`; detached to NULL on delete `deliveries.py:681` | `delivery_service.py:179`, `deliveries.py:708,760` | [34] **TRUE·LIVE** (redundant index) |
| `workload_test_runs.checks/.report/.passed/.status` | `workflow_service.py:139-140` (insert RUNNING), `:210-213` (whole-object rewrite) | publish gate `:230-240` (`run.passed`, `run.report["entrypoint_hash"]`) | [36] LATENT, [37] LATENT |

---

## Claims

### [1] `api_endpoints.slug` global unique should be per-contract — **FALSE**

`app/models/api_endpoint.py:30` `slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)`.
The claim's conditional ("若路由实际按合约维度解析") is resolved the other way: the call route is a **flat
global path** — `app/api/open_api.py:22` `@router.post("/{slug}")` mounted at `/open/v1`
(`app/main.py:357`), resolved by `select(ApiEndpoint).where(ApiEndpoint.slug == slug)`
(`app/api/open_api.py:29`). Global uniqueness is the correctness precondition of that route; per-contract
scoping would make `/open/v1/{slug}` ambiguous. `uq_api_endpoints_slug` mirrors it at
`alembic/versions/0019_flow_orchestration.py:183`. The case-collision half is also already handled:
`slug = body.slug.strip().lower()` (`app/api/deliveries.py:866`) and the generator restricts to
`[a-z0-9-]` (`app/services/delivery_service.py:263`).
*One-line fix:* none needed; optionally add the comment the claim itself offered — "slug 全局唯一是刻意的：
路由为 `/open/v1/{slug}` 扁平路径（open_api.py:22）".

### [2] `api_endpoints.status` defaults ENABLED while `published_at` is separate — **FALSE**

`app/models/api_endpoint.py:32` (default `enabled`) and `:43` (`published_at` nullable). The claimed state
"端点行一经创建（`published_at IS NULL`）就处于可被调用状态" is not producible: the single creator sets both in
the same constructor — `app/api/deliveries.py:879` `status=ApiEndpointStatus.ENABLED.value` and `:880`
`published_at=datetime.now(timezone.utc)` — and refuses creation at all unless a *published production*
workload exists (`deliveries.py:851-857`, `WORKLOAD_NOT_PUBLISHED` 412). So "enabled ⇒ published" holds by
construction; `published_at` is display metadata (only read at `deliveries.py:831`).
*One-line fix:* none; if the invariant should be machine-checked, add a comment on `api_endpoint.py:43`
stating it is written at create, never a gate.

### [3] No range constraints on `timeout_seconds` / `rate_limit_per_minute` / `quota_total` — **FALSE**

`app/models/api_endpoint.py:36,38,39`. Every stated consequence is contradicted by the live code:
`app/schemas/flow.py:144` `rate_limit_per_minute: int = Field(60, ge=1, le=10000)`, `:145`
`quota_total: int | None = Field(None, ge=1)`, and the update schema repeats both bounds (`:150-151`) — so
`rate_limit_per_minute <= 0` / negative `quota_total` cannot be written through the API. `timeout_seconds`
is **not writable at all** (absent from `OpenApiEndpointCreate`/`Update`, `app/schemas/flow.py:138-151`), so
it is always the DDL default 60. And even a hand-written `rate_limit_per_minute = 0` row does **not** become
"全量放行": the limiter clamps — `app/services/delivery_service.py:278`
`if len(window) >= max(1, endpoint.rate_limit_per_minute)`. Absence of a DB CHECK matches the documented
convention (`app/models/AGENTS.md`: "no DB-level enum/CHECK").
*One-line fix:* none; the only defensible version is a comment recording the validated ranges.

### [4] `calls_used` is a per-call counter updated read-modify-write — **TRUE·LIVE**

`app/models/api_endpoint.py:39-40` (`quota_total`, `calls_used` alongside the config columns). Live path:
the row is loaded at `app/api/open_api.py:29`, the quota gate reads the in-memory copy at
`app/services/delivery_service.py:300`, then the row is re-written after a **multi-second sandbox execution**
at `delivery_service.py:390-391` — `endpoint.calls_used = (endpoint.calls_used or 0) + 1` + `flush`, and the
request commits at `app/core/database.py:39`. Two concurrent calls on the same endpoint both start from the
same value, so the second UPDATE overwrites the first: **lost update → quota under-count → quota over-use**,
with a race window as wide as the sandbox run. Fix: an atomic conditional statement, e.g.
`UPDATE api_endpoints SET calls_used = calls_used + 1, last_called_at = now() WHERE id = :id AND
(quota_total IS NULL OR calls_used < quota_total) RETURNING calls_used`, and treat 0 rows as `QUOTA_EXCEEDED`.
The `Integer`→`BigInteger` half is theoretical (2^31 calls on one endpoint). *Downgrade note:* the claim's
headline framing is a "performance/hot row" issue; the actual defect is the lost update.

### [5] `blockchain_anchors`: unused `JSON` import, `metadata_json` stored as `Text` — **TRUE·LATENT** (dead import only)

`app/models/blockchain_anchor.py:9` imports `JSON`; the AST scan over the file finds exactly two textual
occurrences — the import and the trailing comment at `:35` (`metadata_json: Mapped[str | None] =
mapped_column(Text)  # JSON metadata`), so `JSON` is dead. Nothing writes/reads the column except
`app/services/blockchain_adapter.py:442-452`. The claimed `ValueError/TypeError` on read does **not** happen:
the writer always `json.dumps` (`blockchain_adapter.py:449`) and the reader already has the None-safe wrapper
the claim asks for — `blockchain_adapter.py:473` `metadata = json.loads(row.metadata_json) if
row.metadata_json else {}`.
*One-line fix:* drop `JSON` from `app/models/blockchain_anchor.py:9` (keep `Text`).

### [6] `blockchain_anchors` lacks a uniqueness constraint and a `data_hash` index — **FALSE**

`app/models/blockchain_anchor.py:23-27` (the three indexes at `:24-26`: `ix_blockchain_anchors_backend/tx_hash/created`). Both asserted consequences fail against
the only reader set: `blockchain_adapter.py:419-421` (`backend` + `ORDER BY created_at DESC LIMIT 1`),
`:468` (by `id`), `:550-552` (`backend` + `ORDER BY created_at`) — **no query anywhere filters by
`data_hash`** (grep for `data_hash` outside `app/models` returns `blockchain_adapter.py` hash-computation
sites only, no `filter_by(backend=…, data_hash=…)`), so neither the "returns multiple rows" nor the
"full-table scan on `data_hash`" path exists. Uniqueness would be *wrong* here: each row is one hash-chain
entry with a fresh `uuid4` id (`:443`) and a `tx_hash` over `prev_hash:data_hash:anchor_id` (`:500-502`), so
re-anchoring identical data is a legitimate new link and the chain walk (`:557-566`) depends on all links.
*One-line fix:* none.

### [7] `compute_runs` needs a partial unique index to prevent overlapping runs — **FALSE**

`app/models/compute_run.py:29-32` (two plain indexes), `:43` (`idempotency_key … unique=True`, materialized
as `uq_compute_runs_idempotency_key` in `alembic/versions/0019_flow_orchestration.py:151`). The claimed
multi-replica double-insert is exactly what that constraint blocks: both replicas derive the same key from
the same `contract.next_compute_at` (`compute_scheduler.py:285`), and the loser's INSERT raises
`IntegrityError`, which is caught → `rollback` → `return None` (`compute_scheduler.py:319-324`); boot says so
verbatim — `app/main.py:141` "多副本下靠 idempotency_key 唯一约束抢占". `SKIPPED_OVERLAP` is not dead either:
it is inserted live at `compute_scheduler.py:296-305` whenever the previous run is still active on a
*different* instant (or a manual trigger collides with an active run). Residual, and not what the claim
describes: two simultaneous **manual** triggers both carry `idempotency_key = NULL`
(`app/api/compute.py:118`), so they can overlap — but NULL-for-manual is documented as intentional
(`app/models/AGENTS.md`: "`compute_run.idempotency_key` is NULL for manual triggers precisely so the unique
constraint … allows many manual runs").
*One-line fix:* none for the scheduler; if manual overlap must also be excluded, that is a product decision
about the documented manual-NULL design, not a model bug.

### [8] Nothing prevents `trigger='schedule'` with `idempotency_key IS NULL`; `attempt` retry would collide — **TRUE·LATENT**

`app/models/compute_run.py:39-43`: `trigger` and `idempotency_key` have no CHECK tying them, so such a row is
admittable. But no code path writes one: the scheduler always passes a non-null instant
(`compute_scheduler.py:349` with `next_compute_at` pre-filtered `is_not(None)` at `:341`), and the manual
route deliberately passes `scheduled_for=None` (`app/api/compute.py:114-118`). The `IntegrityError` handling
is explicit, not swallowed (`compute_scheduler.py:313-317` and `:319-324`). The retry-collision half is
hypothetical: `attempt` is never incremented anywhere in `app/` — the only touch outside the model is
`app/api/compute.py:45` (read for the response) — while the real per-attempt counter lives on
`WebhookDelivery.attempt` (`delivery_service.py:127`).
*One-line fix:* if desired, `CheckConstraint("trigger <> 'schedule' OR idempotency_key IS NOT NULL")` in a
new additive revision — defence against future writers only.

### [9] Four plain `JSON` columns silently drop in-place mutation — **TRUE·LATENT**

`app/models/compute_run.py:51,53,55,56` use bare `JSON`, so `MutableDict`/`MutableList` tracking is indeed
absent and an in-place edit would be lost. Grep shows no in-place mutation of these four columns: the only
writer assigns whole objects — `compute_scheduler.py:205` `run.output_files = files`,
`:207-211` `run.resource_usage = {...}`, and `params`/`resource_override` are set once at insert
(`:313-320`). (The in-place pattern does exist elsewhere, but on a different table:
`app/services/data_product_sandbox.py:258` `session.output_files.append(...)` on `sandbox_sessions`.)
*One-line fix:* `MutableList.as_mutable(JSON)` / `MutableDict.as_mutable(JSON)` on the four columns — a
hardening change; `verify_schema_parity.py` compares names only, so it stays green.

### [10] `delivery_bundles.size_bytes` is int4 → overflow — **FALSE**

`app/models/delivery_bundle.py:29`. There is exactly one writer (`app/api/deliveries.py:286-292`,
`size_bytes=len(payload)`), and it is gated at `deliveries.py:247`
`if total > MAX_BUNDLE_BYTES: raise` with `MAX_BUNDLE_BYTES = 512 * 1024 * 1024` (`deliveries.py:40`).
`payload` is the zip of those same entries, so `size_bytes ≤ 512 MiB` — ~4× below the 2^31−1 ceiling; no
insert can overflow today. The claim's own text concedes the 512 MB guard; what remains is "if someone raises
the constant", i.e. a future-proofing note, not a present defect.
*One-line fix:* none now; if the cap is ever raised, `BigInteger` + a new additive revision, in the same PR
as the constant.

### [11] `mpc_keys` has no numeric CHECK (`threshold ≥ 1`, `total_shares ≥ threshold`) — **TRUE·LATENT**

`app/models/mpc_key.py:27-28` (plain `Integer`, no CHECK; `0008_mpc_persistence.py:19-28` agrees). Real, but
unreachable via `app/`: the sole writer validates first — `app/services/mpc_service.py:157-161`
(`threshold < 2` → raise, `total_shares < threshold` → raise, `total_shares > 255` → raise) — and the claim's
specific worry about rotation is refuted: `rotate_key` does **not** re-persist the old row's numbers
directly, it re-enters `split_key` (`mpc_service.py:305-312`), so the same validation runs again. Only a raw
SQL write could produce an unrecoverable key, and no such writer exists in `app/`, `scripts/` or `deploy/`.
*One-line fix:* `CheckConstraint("threshold >= 1")` + `CheckConstraint("total_shares >= threshold")` in a new
revision, if DB-level defence-in-depth for Shamir parameters is wanted.

### [12] `mpc_key_shares` needs `UNIQUE(key_id, share_index)` or duplicate indices corrupt reconstruction — **FALSE**

`app/models/mpc_key.py:38` `share_id: Mapped[str] = mapped_column(String(128), primary_key=True)` — and the
only writer builds that PK as the pair the claim wants to constrain:
`app/services/mpc_service.py:178` `share_id = f"{key_id}-s{i}"`. A duplicate `(key_id, share_index)` is
therefore a **primary-key violation**, not a silent duplicate; the "Lagrange 分母为 0 / `_mod_inverse(0,p)`
返回 0" scenario cannot be reached through insert. The range part (`share_index ∈ [1,total_shares]`) is not
DB-enforced, but the same writer loops `for i in range(1, total_shares + 1)` (`mpc_service.py:176`) and
`rotate_key` crypto-erases the old shares before re-inserting (`mpc_service.py:299-300`), so index reuse
across generations is not possible either.
*One-line fix:* none (add a comment at `mpc_key.py:38` noting `share_id` encodes `(key_id, share_index)`).

### [13] `share_value` carries no cipher-algorithm / key-version marker — **TRUE·LATENT**

`app/models/mpc_key.py:43` `share_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)` — no
`share_cipher_alg` / `share_key_version`, and `algorithm` (`:26`) is the *business* key algorithm. The
format is fixed-offset and implicit: `mpc_service.py:107-109` `nonce + tag + ct`, read back as
`:115` `nonce, tag, ct = blob[:12], blob[12:28], blob[28:]`; KEK is
`sha256(AUDIT_ENCRYPTION_KEY or JWT_SECRET_KEY)` (`mpc_service.py:100-103`). So a KEK change would make all
historical shares undecryptable with no in-DB way to stage a re-encryption. Latent because nothing rotates
that KEK (no re-wrap/rotation job exists in `app/`), and because the failure is **fail-closed, not silent**:
GCM tag verification raises in `decrypt_gcm` rather than returning a wrong share.
*One-line fix:* add `share_cipher_alg` + `share_key_version` in a new additive revision (and document the
`nonce(12)||tag(16)||ct` layout) before any KEK-rotation runbook is written.

### [14] `rag_generative_models.size_bytes` is int4 → overflow — **FALSE**

`app/models/rag_generative_model.py:27`. Sole writer `app/services/rag_generative_service.py:46-51`
(`size_bytes=len(artifact_bytes)`) is capped at `:37-38` against `_MAX_BUNDLE_BYTES = 256 * 1024 * 1024`
(`:22`), which is 8× under the int4 ceiling, and the check runs *before* `db.add`. Grep for
`RAGGenerativeModel` shows no other insert path. The claim's trigger ("一旦调大 … 或其它写入路径") is a
future edit, not a present defect.
*One-line fix:* none now; bundle `BigInteger` + revision with any cap increase.

### [15] `watermark_records`: dead `ForeignKey` import, and no FK on `job_id`/`owner_id`/`model_id` — **TRUE·LATENT** (dead import)

`app/models/watermark_record.py:5` imports `ForeignKey`; the AST scan counts one occurrence in the whole
file → dead. The second half ("任何人都能用任意 `owner_id`/`model_id` 落一条权属记录") is wrong: the only
writer is `app/api/training.py:842-849`, behind `require_roles(UserRole.ADMIN, UserRole.OPERATOR)`
(`training.py:806`) **and** a job-ownership check (`training.py:813`). And a wrong/forged `owner_id`
does not make verification falsely *pass*: `verify_watermark` regenerates the expected bits from the stored
values (`training.py:894-896`) and compares against the weights, so a mismatched identity fails verification.
*One-line fix:* remove `ForeignKey` from `watermark_record.py:5`.

### [16] `watermark_records.job_id` non-unique → check-then-act race, then a permanent 500 — **TRUE·LIVE**

`app/models/watermark_record.py:19` `Index("ix_watermark_records_job_id", "job_id")` (non-unique;
`0000_baseline_all_tables.py:242` confirms). The race is exactly as described:
`app/api/training.py:819-823` (its `scalar_one_or_none()` is at `:822`) probes then 409s, and two
concurrent embeds both pass, both insert (`training.py:842-852`). After that the table poisons its own
readers, because both statements use `scalar_one_or_none()` — `training.py:822` (embed) and
`training.py:889` (verify) — so each raises
`MultipleResultsFound` → 500 on both endpoints, with no self-healing path (only a manual row delete).
*One-line fix:* `UniqueConstraint("job_id", name="uq_watermark_records_job_id")` in `__table_args__` plus a
new additive revision that de-dupes existing rows first, and map `IntegrityError` → 409 at `training.py:852`.

### [17] `key_seed` is int4 with no bound on the request field → 500 after the weights were already rewritten — **TRUE·LIVE**

`app/models/watermark_record.py:33` `key_seed: Mapped[int] = mapped_column(Integer, nullable=False)`
(→ PostgreSQL `INTEGER`, `0000_baseline_all_tables.py:237`). The request field has **no** range check:
`app/api/training.py:488` `key_seed: int | None = Field(None, description="PRNG seed (auto-generated if omitted)")`.
Ordering makes it damaging: `_save_model_checkpoint(checkpoint)` runs at `training.py:839`, the record insert
is flushed at `training.py:852` — so a seed > 2^31−1 (e.g. `10**12`) raises
`value out of range for type integer` **after** the checkpoint on disk has been watermarked, yielding a 500
with a modified model and no watermark row (verify then 404s). The auto-generated path is safe by
construction: `training.py:825` `random.randint(0, 2**31 - 1)`.
*One-line fix:* `BigInteger` + additive revision, and `Field(None, ge=0, le=2**31 - 1)` at `training.py:488`
(so the API rejects instead of 500-ing).

### [18] `webhook_deliveries` zombie `delivering` rows + paused subscriptions starving the dispatch window — **TRUE·LIVE** (second half only; first half FALSE)

Model: `app/models/webhook_delivery.py:37` (`status`) with no lease column.
**Refuted half** — the row is never *durably* left in `delivering`: `delivery_service.py:126-129` sets
`DELIVERING` and only `flush()`es, and there is no commit until the caller commits
(`delivery_service.py:205-206` in the loop, `app/core/database.py:39` per request). A crash, a
`CancelledError` (not caught by the `except Exception` at `:151`) or a mid-batch raise unwinds the
transaction, so PostgreSQL keeps the row at `pending`/`failed` and it is retried. Deleted subscriptions are
already terminalized: `delivery_service.py:182-185` sets `DEAD` ("订阅已删除").
**Real half** — a *paused* subscription's undelivered rows are never advanced:
`delivery_service.py:186-187` `if subscription.status != WebhookStatus.ACTIVE.value: continue` leaves them
`pending` with an already-expired `next_retry_at`, while the window is
`ORDER BY created_at ASC … LIMIT 20` (`:171-175`) and counts `attempted` only for rows it actually delivers
(`:188`). Auto-pause is a normal outcome after 10 consecutive failures (`:161-163`,
`MAX_CONSECUTIVE_FAILURES = 10` at `:33`). Once one paused subscription has ≥20 stale rows, every tick burns
the whole window on `continue`s, `attempted == 0`, and no log line (`:208` logs only when `attempted`) →
new events for healthy subscriptions stop being delivered until an operator re-activates the subscription.
*One-line fix:* in `dispatch_pending`, advance rows whose subscription is not ACTIVE to a terminal
`cancelled`/`expired` status (or exclude them in the WHERE clause) instead of `continue`.

### [19] `webhook_subscriptions.events` model says NOT NULL but the migration says nullable — **FALSE**

`app/models/webhook.py:40` `events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)` and the
migration is identical in nullability: `alembic/versions/0019_flow_orchestration.py:200`
`sa.Column("events", sa.JSON(), nullable=False)`. The `nullable=True` line the reviewer read is the one
directly above it — `0019:199` `sa.Column("secret_enc", sa.Text(), nullable=True)` — an off-by-one. The DB
does enforce NOT NULL, so no `events IS NULL` row can exist and the
`WebhookSubscription.events.is_not(None)` filter (`delivery_service.py:98`) can never silently exclude a
subscription. No server_default on either side either, so there is nothing to align.
*One-line fix:* none.

### [20] `secret_enc` comment names the wrong cipher, and a NULL secret fails open — **TRUE·LATENT**

`app/models/webhook.py:38` says "SM4-GCM 加密后的签名密钥（utils.crypto.SM4Cipher）", but both writers use the
KMS envelope helper: `app/api/deliveries.py:611,622` and `:689,693`
`encrypt_task_code(secret, scope=f"webhook-{sub.id}")`, whose output is
`app/services/task_code_security.py:18` `f"{_PREFIX}{key_id}:{encoded}"` (`kms:v1:`), and the read side is
`delivery_service.py:119` `decrypt_task_code(...)`. The "wrong path stays silent" note is also correct:
`task_code_security.py:25-26` returns any un-prefixed value verbatim ("accepting legacy plaintext values").
The fail-open (`delivery_service.py:119` `… else ""` → `:123` `if secret:` → no
`X-CDS-Signature-Sha256` header) requires a row with `secret_enc IS NULL`, and no writer produces one —
`create_webhook` (`deliveries.py:622`) and `rotate_webhook_secret` (`deliveries.py:693`) always set it;
`0019:199` leaves it nullable, so only an out-of-band write could.
*One-line fix:* correct the comment at `webhook.py:38` to "KMS 信封加密（`task_code_security.encrypt_task_code`，
`kms:v1:` 前缀）", and treat an empty secret as a delivery error rather than an unsigned send.

### [21] `blockchain_anchors.confirmed` has no `server_default` — **TRUE·LATENT**

`app/models/blockchain_anchor.py:34` `confirmed: Mapped[bool] = mapped_column(Boolean, default=False)` —
Python-side only, and the DDL agrees there is no default: `0000_baseline_all_tables.py:80`
`sa.Column('confirmed', sa.Boolean(), nullable=False)` (compare `:87` `created_at … server_default=sa.text('(CURRENT_TIMESTAMP)')`).
So a `bulk_insert`/`execute(text(...))` write would hit NOT NULL. Latent because the only writer is the ORM —
`blockchain_adapter.py:442-452` (`BlockchainAnchor(...)` with `confirmed=record.confirmed`) — and
`grep -rn "blockchain_anchors"` finds no raw-SQL writer in `app/`, `scripts/` or `deploy/`.
*One-line fix:* `server_default=sa_text("false")` — note the repo-specific spelling (`app/models/AGENTS.md`
"`sa_text("false")`/`sa_text("true")` … `text("0")` is rejected by real PostgreSQL`), not `sa.false()`.

### [22] `block_number` infers 32-bit `Integer` → cross-chain overflow — **FALSE**

`app/models/blockchain_anchor.py:33` `block_number: Mapped[int | None] = mapped_column()` does infer
`INTEGER` (`0000_baseline_all_tables.py:79`), but the claimed failure is unreachable: every persisted value
comes from the PG append-only adapter, which stores either NULL (`anchor()` never sets `block_number`, so the
`AnchorRecord` default `block_number: int | None = None` at `blockchain_adapter.py:41` flows through
`:447`) or `len(self._chain) + 1` from the in-memory backends (`:264`, `:344`) — a per-process counter, not a
chain height. `_persist_anchor` is called only from that adapter (`blockchain_adapter.py:513`); the real-chain
backends don't write this table at all. And even at a 1-second block interval, 2^31−1 is ~68 years of
blocks — no chain in `ChainBackend` gets near it.
*One-line fix:* none; `BigInteger` is harmless but fixes a value this column never holds.

### [23] `delivery_bundles` display columns: comment-only "enums", no CHECK/non-negative CHECKs — **FALSE** (restates the convention)

`app/models/delivery_bundle.py:34-36`. Two problems with the claim. (a) Adding `CheckConstraint`/PG enum for
status text is the project's stated anti-convention — `app/models/AGENTS.md`: "Status/type columns are Python
`class X(str, Enum)` written as `String` — no DB-level enum/CHECK". (b) The premise "两处「枚举」靠字符串
字面量各写一遍" is wrong: each value set is produced by exactly one helper in one module —
`app/api/deliveries.py:170-177` `_t5_review_status()` returns `passed_with_findings` / `partially_reviewed` /
`passed` (the literals at `deliveries.py:172-176` are *input* classifiers from the per-file review, a
different vocabulary), and `:180-186` `_watermark_status()` returns `not_requested` / `not_applied` /
`applied` / `partially_applied`, called once each (`deliveries.py:278-279`). `row_count` is likewise
computed once (`deliveries.py:155-167`, `:235-240`, `:280`) and stays NULL when unknown rather than being
coerced to 0 — deliberate, per the column comment at `delivery_bundle.py:36`. Nothing breaks at runtime.
*One-line fix:* none; if a shared enum is wanted, it is a `str, Enum` companion class per repo convention —
but the "只改一侧" risk the claim cites doesn't exist here.

### [24] `delivery_bundles` default drift vs `0021` will make autogenerate emit a harmful migration — **FALSE**

The drift itself is real: `app/models/delivery_bundle.py:29-30` have only Python `default=0` while
`0021_delivery_bundle_ownership.py:25-26` sets `server_default="0"`, and `delivery_bundle.py:37` uses
`server_default=func.now()` vs `0021:27` `sa.text("(CURRENT_TIMESTAMP)")`. Both asserted consequences are
wrong. (1) Neither gate sees `server_default` at all: `scripts/verify_schema_parity.py:5-6,22-27` compares
table names and **column names only**, and `alembic/env.py:48-53,60-62` never passes
`compare_server_default`, so autogenerate/`alembic check` will not propose dropping a default. (2) On a
migrated database the column *does* carry `DEFAULT 0`, so a bypass-ORM INSERT works fine — the model-side
omission changes nothing in the shipped schema.
*One-line fix:* none required; aligning the model (`default=0, server_default="0"`) is cosmetic consistency
with `webhook_delivery.py:38-39`, not a defect fix.

### [25] `delivery_bundles.object_name` should be unique (+ format CHECK) — **FALSE**

`app/models/delivery_bundle.py:28`. The privilege-escalation scenario needs two rows sharing one object
name, and no path can make one: the sole writer composes it from the row's own freshly minted UUID —
`app/api/deliveries.py:283` `object_name = f"delivery/{bundle_id}.zip"` with `bundle_id = uuid.uuid4()`
(`:212`) — and inserts at `:286-290`. The download/preview paths resolve the row by its own primary key
first and only then read its object name (`deliveries.py:398` `where(DeliveryBundle.id == bundle_id)` →
`:404` `object_name = owner.object_name or f"delivery/{bundle_id}.zip"`; same at `:472-478`), and
authorization runs on that row's contract before the fetch. "按 id A 下载到 B 的字节" requires a back-fill
writer that does not exist (grep: no other `object_name` write in `app/`).
*One-line fix:* none; `unique=True` is free but protects against a hypothetical writer.

### [26] `mpc_key.py` imports `uuid` and never uses it — **TRUE·LATENT** (hygiene)

`app/models/mpc_key.py:10` `import uuid`; the AST scan counts exactly one occurrence in the file, and both
string PKs are generated in the service: `app/services/mpc_service.py:178`
(`share_id = f"{key_id}-s{i}"`) and the `key_id` mint above it. Zero runtime effect.
*One-line fix:* delete line 10 of `app/models/mpc_key.py`.

### [27] `mpc_keys.status` has no `MPCKeyStatus` companion enum and no `destroyed_at` — **TRUE·LATENT** (hygiene)

`app/models/mpc_key.py:29` `status: Mapped[str] = mapped_column(String(16), …, default="active")` with bare
strings written at `app/services/mpc_service.py:204,214` (`"active"`), `:296` (`"rotated"`), `:322`
(`"destroyed"`), and read-compared at `:291-292`. `destroy_key` flips status only
(`mpc_service.py:315-325`) and the table has `rotated_at` but no `destroyed_at`
(`mpc_key.py:31`), so destruction time is unauditable — while `kms.py` keeps both. No runtime break:
a typo'd status would surface as a failed comparison, and this is not unique (46 of 101 model classes have
`str, Enum` companions).
*One-line fix:* add `class MPCKeyStatus(str, Enum)` and use `MPCKeyStatus.ACTIVE.value` at `:29`; add
`destroyed_at` in a new additive revision if destruction timing must be auditable.

### [28] `rag_generative_models` needs non-negative CHECKs and a `server_default` alignment — **FALSE**

`app/models/rag_generative_model.py:26-27` (`vocab_size`, `size_bytes`, Python `default=0` only) vs
`0010_rag_generative.py:28-29` (`server_default="0"`). The migration-noise argument is the same as claim 24
and is refuted by the same two lines: `scripts/verify_schema_parity.py:5-6,22-27` (names only) and
`alembic/env.py:48-53,60-62` (no `compare_server_default`) — autogenerate does not nag about server
defaults, and the migrated DB already has `DEFAULT 0`. The data-corruption half is unreachable through the
only writer: `rag_generative_service.py:70-72` raises when the vocab has no tokens, so `vocab_size ≥ 1`;
`size_bytes` comes from `len(artifact_bytes)` after a non-empty check at `:35-38` (never negative). CHECKs
on numeric columns are convention-legal here, but they guard values nothing in `app/` can produce.
*One-line fix:* none; optionally `CheckConstraint("vocab_size >= 1")` + `("size_bytes >= 0")` in a new
revision as pure defence-in-depth.

### [29] `training_job.py` imports `Integer` and never uses it — **TRUE·LATENT** (hygiene)

`app/models/training_job.py:6` — AST scan counts one occurrence; every column on the table is String /
DateTime / Text / JSON / UUID (`training_job.py:36-57`), so there is no int column. Zero runtime effect.
*One-line fix:* `from sqlalchemy import String, DateTime, Text, ForeignKey, func, JSON, Index`.

### [30] `job_id` has `unique=True` **and** `index=True`, so two identical indexes exist — **FALSE**

`app/models/training_job.py:36` `job_id: Mapped[str] = mapped_column(String(64), unique=True,
nullable=False, index=True)`. SQLAlchemy 2.0 does **not** create two objects for that combination — verified
empirically in this session with the repo's own SQLAlchemy 2.0.48 (`python` 3.12.10): a column declared
`unique=True, index=True` yields one `Index(name='ix_t_jobs_job_id', columns=['job_id'], unique=True)` and
**no** `UniqueConstraint`, while `unique=True` alone yields a `UniqueConstraint` and no index. The baseline
migration carries exactly that one object: `0000_baseline_all_tables.py:645`
`op.create_index(op.f('ix_training_jobs_job_id'), 'training_jobs', ['job_id'], unique=True)`, with no unique
constraint in the table's constraint list (`0000:642-644`). Worse, the suggested fix is actively harmful:
dropping `index=True` would swap the unique index for a `UNIQUE` constraint, which *is* a diff `alembic
check` reports (it compares constraints and indexes), reddening the CI gate the claim is trying to help.
*One-line fix:* none — keep the declaration as is.

### [31] `training_jobs` has no `created_at` index while the list endpoint sorts by it — **TRUE·LIVE**

`app/models/training_job.py:30-33` declares only `ix_training_jobs_status` and
`ix_training_jobs_user_status(user_id, status)` (`0000:646-647`). The live list query is
`app/api/training.py:322-331`: `where(user_id == …)` [+ optional `status`] then
`order_by(TrainingJob.created_at.desc()).offset(skip).limit(limit)`. The composite index serves the predicate
but not the ordering, so PostgreSQL must materialize and sort every row of that user on every page (plus the
companion `COUNT(*)` at `:323`). Sibling tables in the same directory do carry the time index —
`pipeline_task.py:37` `ix_pipeline_tasks_created` (`0000:594`) and `sandbox_session.py:50`
`ix_sandbox_sessions_created` (`0000:495`) — and this table is append-only with no retention cleanup.
Impact is bounded (per-user training-job counts are small), so severity is low, but the missing index and the
sort are both real and live.
*One-line fix:* add `Index("ix_training_jobs_created", "created_at")` (or extend to
`(user_id, status, created_at)`) plus a new additive revision.

### [32] No invariant between `status` and `started_at`/`completed_at`; no `updated_at` — **TRUE·LATENT**

`app/models/training_job.py:38,56-57`: nothing ties the terminal statuses to `completed_at` or `running` to
`started_at`, and `0000:625-641` confirms the table has no `updated_at` column (unlike
`compute_run.py:62`). Real gap, no reachable defect: the single writer always pairs them —
`training.py:261-262` (`running` + `started_at`), `:277-286` (`completed_at` then `completed`/`failed`),
`:391-392` (`cancelled` + `completed_at`) — and no other module writes these columns. The suggested
`CheckConstraint("completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at")` would not
actually catch the described inconsistency (a terminal status with NULL timestamps).
*One-line fix:* add `updated_at … onupdate=func.now()` in a new additive revision, and state the invariant
in a column comment; skip the CHECK unless timestamps are genuinely monotonic in all writers.

### [33] `key_seed` is plaintext secret material and is echoed back with `layer_positions` — **TRUE·LATENT**

`app/models/watermark_record.py:33` stores it unencrypted, and the embed response does return both the seed
and the full position map: `app/api/training.py:860-867` (`layer_positions=wm_result.metadata.layer_positions,
key_seed=key_seed`), with `key_seed: int` on the response model at `training.py:497`. That is a genuine
watermark-removal enabler for whoever receives it (positions + seed ⇒ locate and reset the LSBs). It is
latent rather than live because of who can receive it: embed requires
`require_roles(UserRole.ADMIN, UserRole.OPERATOR)` (`training.py:806`) **and** job ownership
(`training.py:813`) — that caller already holds and rewrote the checkpoint (`:839`), so the response adds
nothing secret. No other surface discloses it: verify re-loads `record.key_seed` server-side only
(`training.py:904`), and `WatermarkRecord` is read nowhere else in `app/`. So the claim's attacker model
("嵌入方 = 潜在的模型再分发者") does not match the gate.
*One-line fix:* drop `key_seed`/`layer_positions` from `WatermarkEmbedResponse` (`training.py:497,494`) —
the embedder needs neither to use the result; encryption of the column is unnecessary while the DB is the
only store and readers are privileged.

### [34] `webhook_deliveries.subscription_id`: single-column index duplicates the composite's prefix — **TRUE·LIVE** (minor)

`app/models/webhook_delivery.py:29-31` sets `index=True`, and `:24` declares
`ix_webhook_deliveries_sub_status(subscription_id, status)`; the DB really has both
(`0019:237` + `0019:238`). Both subscription-only queries — `deliveries.py:708`
(`where(subscription_id == …)`, status optional) and `deliveries.py:760` — are fully served by the composite,
so the extra B-tree is maintained on every INSERT/UPDATE of this append-heavy dispatch table
(one row per push attempt, plus `deliveries.py:681` detaches all of a deleted subscription's rows in one
UPDATE). Caveats the claim omits: (a) PostgreSQL may legitimately *prefer* the narrower single-column index
for a subscription-only filter (cheaper pages per row-lookup), so "never selected" is too strong; (b)
removing `index=True` from the model alone leaves the physical index in place and creates a real
`alembic check` diff (indexes are compared), so the change needs a paired `drop_index` revision.
*One-line fix:* drop `index=True` at `webhook_delivery.py:30` **and** add a revision with
`op.drop_index("ix_webhook_deliveries_subscription_id", "webhook_deliveries")`.

### [35] `webhook_subscriptions.user_id`: same prefix duplication — **TRUE·LIVE** (minor)

`app/models/webhook.py:33` `index=True` plus `:28` `Index("ix_webhook_subs_user_status", "user_id",
"status")`, materialized twice in the DB (`0019:211` + `0019:212`). Readers are
`delivery_service.py:100` (`where(user_id == …)` per event) and `_load_subscription`
(`deliveries.py:586`, by PK). Write amplification is far smaller than in [34] — subscriptions are created
rarely (`deliveries.py:615-624`) — so this is a storage/noise cleanup, not a hot-path fix. Same caveat: the
drop needs a revision.
*One-line fix:* drop `index=True` at `webhook.py:33` + `op.drop_index("ix_webhook_subscriptions_user_id",
"webhook_subscriptions")` in a new revision.

### [36] `workload_test_runs.checks`/`report` are plain `JSON`, so in-place edits vanish — **TRUE·LATENT**

`app/models/workload_test_run.py:31-32` use bare `JSON`, and these columns *are* the publish-gate credential
(`workflow_service.py:230-240` reads `run.passed` and `(run.report or {}).get("entrypoint_hash")`). The claim
itself notes the reason it has not bitten: the only writer assigns whole objects —
`workflow_service.py:139-140` inserts the RUNNING row, then `:210-212`
`run.checks = checks`, `run.report = report`, `run.passed = passed` (and `:213` status). Grep finds no
`run.checks.append` / `run.report[...] =` anywhere in `app/`.
*One-line fix:* `MutableList.as_mutable(JSON)` / `MutableDict.as_mutable(JSON)` at `:31-32` — a cheap guard;
the parity gate compares names only, so it stays green.

### [37] `workload_test_runs` is the only SS-15 flow table without `updated_at` — **TRUE·LATENT** (hygiene, with a factual slip)

`app/models/workload_test_run.py:38` ends at `created_at`; `0019_flow_orchestration.py:118` agrees there is
no `updated_at`, while the row *is* rewritten after creation (`workflow_service.py:139-140` insert RUNNING,
`:210-213` rewrite status/passed/checks/report/exit_code/error_message), so last-change time is
unrecoverable and "created-then-finished instantly" is indistinguishable from "stuck in RUNNING". The
supporting list is wrong in one entry: `webhook_deliveries` does **not** have `updated_at` either
(`webhook_delivery.py:48-49` are `created_at` and `delivered_at`). No data or query is affected today —
readers use `status`/`passed`/`finished_at`.
*One-line fix:* add `updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
server_default=func.now(), onupdate=func.now())` plus a new additive revision.

---

## Score

| file | claims | TRUE·LIVE | TRUE·LATENT | FALSE |
|---|---|---|---|---|
| `app/models/api_endpoint.py` | 4 | 1 ([4]) | 0 | 3 ([1],[2],[3]) |
| `app/models/blockchain_anchor.py` | 4 | 0 | 2 ([5],[21]) | 2 ([6],[22]) |
| `app/models/compute_run.py` | 3 | 0 | 2 ([8],[9]) | 1 ([7]) |
| `app/models/delivery_bundle.py` | 4 | 0 | 0 | 4 ([10],[23],[24],[25]) |
| `app/models/mpc_key.py` | 5 | 0 | 4 ([11],[13],[26],[27]) | 1 ([12]) |
| `app/models/rag_generative_model.py` | 2 | 0 | 0 | 2 ([14],[28]) |
| `app/models/training_job.py` | 4 | 1 ([31]) | 2 ([29],[32]) | 1 ([30]) |
| `app/models/watermark_record.py` | 4 | 2 ([16],[17]) | 2 ([15],[33]) | 0 |
| `app/models/webhook_delivery.py` | 2 | 2 ([18],[34]) | 0 | 0 |
| `app/models/webhook.py` | 3 | 1 ([35]) | 1 ([20]) | 1 ([19]) |
| `app/models/workload_test_run.py` | 2 | 0 | 2 ([36],[37]) | 0 |
| **Total** | **37** | **7** | **15** | **15** |

Note on [18]: counted once, as TRUE·LIVE — its scheduling-starvation half is live; its zombie-`delivering`
half is refuted in the claim body above.

### What is worth acting on

1. **[17]** unbounded `key_seed` (one-line Pydantic bound + `BigInteger`) — only claim that corrupts state
   on a normal 500.
2. **[16]** unique `watermark_records.job_id` — self-permanent 500 after a single race.
3. **[4]** atomic `calls_used` increment — quota over-use under concurrency, wide window.
4. **[18]** terminal status for deliveries whose subscription is not ACTIVE — delivery starvation.
5. Cheap hygiene, zero risk: **[5] [26] [29] [15]** dead imports; **[20]** wrong comment.
6. **[31]/[34]/[35]** index changes — each needs a paired additive/drop revision, model-only edits would
   create the drift they are meant to avoid.
