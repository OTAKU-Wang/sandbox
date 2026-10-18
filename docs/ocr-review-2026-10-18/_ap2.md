## alembic/versions/0019_flow_orchestration.py

### [E2-67] workloads.test_run_id has no FK, contradicting the revision's own "保留真实外键" note — **TRUE·LATENT**

`alembic/versions/0019_flow_orchestration.py:127`
```
127	    op.add_column("workloads", sa.Column("test_run_id", sa.UUID(), nullable=True))
```
with the docstring rationale at `:9-10` — "workloads.test_run_id 指向后建的 workload_test_runs，故先建表再 add_column，保持线性迁移且保留真实外键". No `create_foreign_key` follows it and no later revision adds one; the ordering argument is also moot, since `workload_test_runs` is created at `:105` **before** the add_column. So the docstring-vs-code mismatch is real, and the model is consistent with the chain (`app/models/workload.py:55` has no `ForeignKey`) so there is no autogenerate drift. The claimed consequence is refuted, though: a dangling `test_run_id` fails **closed** in the publish gate — `run is None or not run.passed → WorkloadGateError(GATE_TEST_NOT_PASSED)` (`app/services/workflow_service.py:230-233`), preceded by `if not workload.test_run_id: raise GATE_TEST_REQUIRED` (`:227-228`) plus an `entrypoint_hash` equality check (`:235-240`). Nothing even produces a dangling value: `WorkloadTestRun` rows are never deleted (the complete `db.delete(` inventory in `app/` is cube templates, shared volumes/attachments, data products, data resources, webhook subscriptions, api endpoints, network policies) and `run_test` clears the pointer before re-testing (`workflow_service.py:142`). Real fix = one FK or one corrected docstring sentence.

### [E2-68] uq_webhook_deliveries_event_id is global although the row model is per-subscription — **TRUE·LATENT**

`alembic/versions/0019_flow_orchestration.py` (`uq_webhook_deliveries_event_id`), mirrored by `app/models/webhook_delivery.py:34` (`unique=True`). The claim reads the current semantics correctly: both writers mint a fresh id per subscription — `WebhookDelivery(..., event_id=uuid.uuid4().hex, ...)` in `dispatch_event` (`app/services/delivery_service.py:110`) and again for the manual test delivery (`app/api/deliveries.py:784`) — and the id is echoed to the receiver as the `EVENT_ID_HEADER` (`delivery_service.py:122`). So today the constraint never fires and no duplicate-id path exists; the cost is purely that `event_id` is a delivery id, and a future deterministic per-event id (fan-out dedup, receiver-side idempotency) would be rejected on the second subscription. Latent design note; `(subscription_id, event_id)` is the semantically matching key, and retries reuse the same row either way.

### [E2-69] 0019's many new FK columns are mostly unindexed — **TRUE·LATENT**

Facts verified: 0019 declares FKs at `:62-65` (notebooks), `:94-99` (workloads), `:121-123` (workload_test_runs), `:152-156` (compute_runs), `:184-186` (api_endpoints) but indexes only `user_id`/`contract_id`/`workload_id(test_runs)` and the composite/lookup indexes at `:67-69`, `:101-103`, `:125`, `:158-160`, `:188-190`; the models agree (`app/models/workload.py:27-33`, `app/models/compute_run.py:30-35`, `app/models/api_endpoint.py:21-26`, `app/models/workload_test_run.py:24`), so nothing is drift. But the cost mechanism the claim names — "PostgreSQL must seq-scan the child table when the parent row is deleted or its PK updated" — never fires, because no parent of those columns is ever deleted or re-keyed: the exhaustive `db.delete(` inventory in `app/` is `cube_template_service.py:813,848,857`, `shared_volumes.py:71,84,85`, `data_products.py:274`, `data_resources.py:288`, `deliveries.py:682,939`, `network_policy.py:245` — no contract, session, user, notebook, workload or sandbox_task deletion, and versioning/"retire" are status updates (`workflow_service.publish` flips `is_latest`, `0019` `parent_id` chain), not deletes. The three examples in the claim are child-side deletes or updates, which need no child index. The one genuine (small) gap found is a read, not the FK: `select(ApiEndpoint).where(ApiEndpoint.workload_id == workload.id, …)` (`app/api/workloads.py:221`) has no index — on a table of published-endpoint rows. Worth indexing for hygiene; no live FK-maintenance pain.
## alembic/versions/0020_webhook_detached_history.py

### [E2-70] downgrade restores NOT NULL on a column the app knowingly nulls — **TRUE·LATENT**

`alembic/versions/0020_webhook_detached_history.py:28-33`
```
28	def downgrade() -> None:
29	    if op.get_bind().dialect.name == "postgresql":
30	        op.alter_column("webhook_deliveries", "subscription_id", existing_type=sa.UUID(), nullable=False)
```
The upgrade is unconditional-nullable (`:20-25`) precisely because `delete_webhook` nulls the children before deleting the subscription — `update(WebhookDelivery).where(WebhookDelivery.subscription_id == subscription_id).values(subscription_id=None)` (`app/api/deliveries.py:681`, then `db.delete(sub)` at `:682`) — so a live `DELETE /api/v1/deliveries/webhooks/{id}` guarantees NULL rows over time and `ALTER … SET NOT NULL` would then abort with `contains null values`. This is the only downgrade-family claim whose precondition is actually produced by application traffic. Still LATENT by the same rule as E2-15/E2-25/E2-61/E2-66: nothing in the repo or any deploy path runs `alembic downgrade` (only `upgrade head`: `scripts/verify_schema_parity.py:42`, `.github/workflows/ci.yml:42`), and AGENTS.md pins the chain as additive-only. Fix is the claim's option 2 — guard/orphan-clear plus a "not reversible" docstring line.

### [E2-71] the SQLite batch_alter_table downgrade branch is worse (half-rebuilt table) — **TRUE·LATENT**

`alembic/versions/0020_webhook_detached_history.py:31-33`. Same precondition and same non-reachability as E2-70. One correction to the severity argument: SQLite has fully transactional DDL and `batch_alter_table` runs its copy/rename/recreate-indexes sequence inside alembic's single migration transaction (`alembic/env.py:61-62` `with context.begin_transaction(): context.run_migrations()`), so a `NOT NULL constraint failed` during the copy aborts the transaction — it does not leave "原表已 DROP，索引与 FK 处于半重建状态" for a human to clean up. The pre-flight count check the claim suggests is still the right ergonomic fix (a readable error beats a DDL error), and it belongs in the same one downgrade-hardening pass as E2-25/E2-32/E2-61/E2-66/E2-76.

### [E2-72] upgrade relaxes the FK but leaves detached history untraceable — **TRUE·LATENT**

`alembic/versions/0020_webhook_detached_history.py:20-25`. Confirmed on the data side: `webhook_deliveries` keeps `payload`/`event_type`/`event_id` only (`app/models/webhook_delivery.py`), so once `subscription_id` is NULL the url/name/owner context exists nowhere else — the audit log records the deleted subscription's url (`app/api/deliveries.py:683`), which is the only surviving trace. The FK-RESTRICT-bypass scenario (an ops script running `DELETE FROM webhook_subscriptions` directly) is real but out-of-band; the 0019 FK is indeed `sa.ForeignKeyConstraint(["subscription_id"], ["webhook_subscriptions.id"])` with no `ondelete`. A docstring sentence on the orphan policy (`WHERE subscription_id IS NULL`) is the proportionate fix; no code path breaks today.
## alembic/versions/0021_delivery_bundle_ownership.py

### [E2-73] delivery_bundles.size_bytes is sa.Integer() — **TRUE·LATENT**

`alembic/versions/0021_delivery_bundle_ownership.py:29` with ORM mirror `app/models/delivery_bundle.py:29` (`Integer`) ⇒ PG `integer`, ceiling 2,147,483,647, and CI/dev SQLite would not notice — the failure mode the claim predicts is correctly described. It is unreachable today because the only writer cannot get there: the dataset bundler aborts at `if total > MAX_BUNDLE_BYTES: raise CDSError("交付包超过 512MB 上限…", code="BUNDLE_TOO_LARGE", http_status=413)` (`app/api/deliveries.py:247-248`, `MAX_BUNDLE_BYTES = 512 * 1024 * 1024` at `:40`), and `size_bytes=len(payload)` is the *compressed* zip length (`:282`, `:292`), i.e. ≤ the 512 MiB pre-compression guard. So the ceiling sits ~4× below the overflow point, and the finding is the same insurance policy as E2-40/E2-46: `BigInteger()` in a new revision plus the model change, ideally bundled with the other two `size_bytes` columns rather than claimed as a high-severity live bug.

### [E2-74] the three new FKs declare no ON DELETE policy — **TRUE·LATENT**

`alembic/versions/0021_delivery_bundle_ownership.py:32-34` — plain `NO ACTION`/RESTRICT on `contract_id`, `session_id`, `created_by`, and unlike 0020's `subscription_id` there is no detach step anywhere in `app/` for these. The blockage is nonetheless unreachable: contracts, sandbox_sessions and users are never deleted (the exhaustive `db.delete(` inventory is the one cited under E2-69, and `delivery_bundles` rows are never deleted either), and `contract_id` is NOT NULL so `SET NULL` as suggested is not even applicable without a schema change. Same class as E2-62/E2-34/E2-35: worth one explicit `ondelete=` (or a docstring line recording "parents are never hard-deleted") rather than a live 500.
## alembic/versions/0022_compute_run_params.py

### [E2-75] NULL vs explicit `{}` in params/resource_override are indistinguishable — **FALSE**

`alembic/versions/0022_compute_run_params.py:21-22`. The central assertion — "三种事实在库里长得一模一样" — is refuted by the write path. The request model defaults to `None`, not `{}`: `params: dict | None = None` / `resource_override: dict | None = None` (`app/schemas/flow.py:85-87`), the endpoint forwards them verbatim (`app/api/compute.py:114-123`), and `claim_run` stores exactly what it received (`params=params, resource_override=resource_override`, `app/services/compute_scheduler.py:313-323`). So a pre-0022 row and a scheduled run are SQL `NULL`, while an operator who really submits `{}` is persisted as a JSON `{}` — the DB *does* keep the distinction, and replay reads it back faithfully. What is true is only the execution-side truthiness (`if not params: return entrypoint` at `:79-80`; `**(run.resource_override or {})` at `:175`), and that conflation is harmless rather than dangerous: "no parameters" and "empty parameter object" execute identically, and no override with an empty dict is by definition the same spec. The audit-fidelity premise ("事后无法判断当时传了什么") therefore does not hold; at most the migration could carry the `comment=` documentation the claim suggests.

### [E2-76] downgrade drops params/resource_override irreversibly — **TRUE·LATENT**

`alembic/versions/0022_compute_run_params.py:25-27`. Facts right — those two columns are the reproducibility record the docstring itself motivates (`:4-5`, "参数本体必须与运行行同存"), and dropping them cannot be undone by re-upgrading. Unreachable by the repo's own rules: no `alembic downgrade` in any deploy or CI path (`upgrade head` only — `scripts/verify_schema_parity.py:42`, `.github/workflows/ci.yml:42`), plus the additive-only rule. Same fix as the rest of this family (E2-25/E2-32/E2-61/E2-66/E2-70): a warning comment or an archival step in `downgrade()`.
## alembic/versions/0023_task_fields.py

### [E2-77] UNIQUE index built in-migration-transaction on notebooks blocks writes — **TRUE·LIVE**

`alembic/versions/0023_task_fields.py:19-25`
```
20	    op.add_column("notebooks", sa.Column("task_no", sa.String(32), nullable=True))
...
25	    op.create_index("ix_notebooks_task_no", "notebooks", ["task_no"], unique=True)
```
All five ADD COLUMNs plus the unique index run inside one transaction (`alembic/env.py:59-62`: `context.configure(...)` then `with context.begin_transaction(): context.run_migrations()`), with no `autocommit_block()` and no `postgresql_concurrently` anywhere in the file, so on PG this is a locking `CREATE UNIQUE INDEX` (SHARE lock + full scan/sort) that blocks writers on `notebooks` for its duration — the same finding shape as E2-64, and this one is worse because the unique build also has to sort. Impact today is bounded: `notebooks` is only created three revisions earlier (`0019_flow_orchestration.py:71`), so any chain-built database reaches 0023 with a days-old table; the `downgrade()` `drop_index` at `:29` is likewise blocking. The claim's remedy is correct as written (it does include the `autocommit_block()` wrapper), and it should be paired with the caveat it already states about INVALID leftover indexes on CONCURRENTLY failure — plus the note that a *unique* index built non-concurrently also hard-fails the whole migration if duplicate `task_no` values ever appear.
## contracts/AuditRegistry.sol

Group note: nothing in `app/`, `sdk/`, `deploy/`, `k8s/` or `helm/` deploys or calls this contract — `anchorAudit`/`verifyAudit` appear only in `docs/` and `specs/`, `requirements.txt` has no web3/solidity dependency, and live anchoring runs through `app/services/blockchain_adapter.py` (`PGAppendOnlyAdapter` is the default, `:612`; the FISCO/AntChain adapters compute local SM3 hash-chains and disclose themselves via `_backend_disclosure`, `app/api/audit.py:679`). That makes all four findings TRUE as code facts about a shipped-but-unwired artifact and LATENT as defects; they matter the day a real chain deployment appears.

### [E2-78] anchorAudit is permissionless and first-writer-wins — **TRUE·LATENT**

`contracts/AuditRegistry.sol:27-35`
```
27	    function anchorAudit(
28	        bytes32 merkleRoot,
29	        string memory metadata
30	    ) external {
31	        require(audits[merkleRoot] == 0, "AuditRegistry: already anchored");
```
No owner, no role, no signature, and `:31` makes the mapping permanently first-write-wins — the analysis is accurate, including both consequences (permanent DoS of the legitimate anchorer, and a wrong timestamp endorsement). Latent because there is no deployment path and, decisively, no downstream that treats presence as authority: the verify path is `blockchain_adapter.verify(anchor_id, merkle_root)` against locally persisted `AnchorRecord`s (`app/api/audit.py:430`, `app/services/audit_service.py:268-270`), never `verifyAudit`. The claim's own alternative (downgrade `verifyAudit` to an existence proof + record the anchorer) is the right pairing if the contract is ever wired up.

### [E2-79] no zero-root check; bytes32(0) can be anchored and then "verifies" — **TRUE·LATENT**

`contracts/AuditRegistry.sol:31,33,40-42` — `audits[bytes32(0)] = block.timestamp` is accepted and `verifyAudit(bytes32(0))` then returns true, so a downstream that computes an all-zero root for an empty batch gets a false positive. True for the contract, unreachable from this repo (see the group note; and no code path anchors an empty batch: `audit_service.py:268-270` anchors a computed Merkle root of real leaves). Note the claim's suggested snippet is self-contradictory as pasted — it contains both `require(audits[merkleRoot] != 0, …)` and `require(audits[merkleRoot] == 0, …)`; only the `merkleRoot != bytes32(0)` guard is meant.

### [E2-80] AuditAnchored records no anchorer address — **TRUE·LATENT**

`contracts/AuditRegistry.sol:16-20` (event has root/timestamp/metadata) and `:34` (`emit AuditAnchored(merkleRoot, block.timestamp, metadata)`); `audits` stores only a `uint256` timestamp (`:11`). So under the permissionless write path there is no on-chain way to tell a legitimate anchor from a front-run one — the observation is exact and it is precisely the mitigation E2-78 needs if access control is not adopted. Latent for the same reason: the contract is not deployed or read by anything in this repo.

### [E2-81] existence and timestamp share one slot, with 0 as the "absent" sentinel — **TRUE·LATENT**

`contracts/AuditRegistry.sol:33` `audits[merkleRoot] = block.timestamp;` combined with `:31`/`:41`'s `== 0` / `!= 0` tests. The reasoning is sound (a chain whose `block.timestamp` is 0 — `vm.roll(0)`, a bespoke private chain genesis — would let the same root be re-anchored and make `verifyAudit` return false for a write that succeeded), and the `+ 1` offset or a struct with an `exists` flag removes the dependency. It needs a chain that timestamps blocks at 0, which excludes every public chain, and the contract is unwired here ⇒ latent; worth folding into the same contract pass as E2-78/E2-79/E2-80.
## policies/system.rego

### [E2-82] `health = true` is an unconditional constant rule — **TRUE·LATENT**

`policies/system.rego:5` — a bare `health = true`, so `data.cds.system.health` is true for any input, exactly as claimed. The file *is* shipped to OPA (`docker-compose.yml:72` and `docker-compose.prod.yml:74` mount `./policies:/policies`), so anyone who queries that path gets a meaningless "healthy". Nothing in the repo consumes it: the client only evaluates per-contract packages (`app/services/opa_client.py:58-76` builds `/v1/data/{path}` from a `package_path` the caller supplies, and `app/services/policy_evaluator.py:109-132` pushes/queries `cds/policies/contract_<id>`), and the readiness probe is OPA's native endpoint — `k8s/opa.yaml:29-32` `httpGet: path: /health, port: 8181` — as the claim itself verified. Latent ⇒ the fix is the claim's own: delete the rule, or comment its semantic boundary before anyone wires it into an authorization or readiness decision.

### [E2-83] package has no positive rule and no Rego test pinning default-deny — **TRUE·LATENT**

`policies/system.rego:1-5` is the whole package (`package cds.system`, `default allow = false`, `health = true`), and it is the only `.rego` file in the repo (`find . -name '*.rego'` → `./policies/system.rego`), so no `system_test.rego` baseline exists. Two facts sharpen the finding rather than soften it: nothing in `app/` ever queries `cds/system/allow` (the evaluator targets `cds/policies/contract_*`, `app/services/policy_evaluator.py:116-132`), so this "anchor" gates nothing at runtime; and since OPA answers per-package, a future `allow if …` added here would union-open a path no test would catch. Latent (a missing test + decorative anchor), and the claim's suggested minimal test file is the cheap lock.
