# app/models/

**Score 14 — distinct domain: SQLAlchemy ORM layer.** 43 modules + empty `__init__.py` / 2,698 LOC / 101 classes (46 of them `str, Enum` companions), 158 importing files, avg 60 LOC — one entity per file, declarative only, no business logic (sole exception: `task_queue.py`).

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Durable multi-replica queue | `task_queue.py` (155 L) | the one model module with behavior: `enqueue`/`claim_next`/`complete`/`fail`/`requeue_stale`; PG uses `FOR UPDATE SKIP LOCKED`, SQLite falls back to an optimistic conditional UPDATE |
| Sandbox level/status enums | `sandbox_session.py` | `SandboxLevel` (`L0`/`L1`/`L2`/`L2C="l2c"`/`L3`/`K8S`) + `SessionStatus`; `l2c` is the SS-10/SS-13 Cube microVM level |
| DEK lifecycle | `kms.py` (140 L) | `DEKStatus`/`DistributionStatus`, `KeyStatus = DE…` alias merges the old `KeyMetadata.KeyStatus`; stores the envelope blob only — "key material is NEVER stored here" |
| Cube template registry | `cube_template.py` (122 L) | `cube_templates` (build metadata + C4 "HTTP 2xx = READY" probe) + `cube_template_nodes` (per-node distribution state, sha verification) |
| Contract terms + signature | `contract.py` (128 L) | the persisted side of the `schemas/contract.py` validators |
| SS-15 flow tables | `workload.py`, `workload_test_run.py`, `compute_run.py`, `notebook.py`, `api_endpoint.py`, `webhook.py`, `webhook_delivery.py`, `delivery_bundle.py` | dev→test→prod admission + delivery channels |
| Credential / identity | `api_key.py` (SS-12 `cds_ak_` keys), `app_credential.py`, `user_mfa.py`, `federation_trust.py`, `certificate.py` | key rows keep a hash/prefix, never the secret |

## CONVENTIONS
- `Base` and `get_db` come from `app.core.database` (import `from app.core.database import Base`); every PK is `UUID(as_uuid=True)` with a Python-side `uuid4` default (126 sites), timestamps use `func.now()`.
- Status/type columns are Python `class X(str, Enum)` written as `String` — no DB-level enum/CHECK; a new status value is a code change plus (if indexed) an index/migration, never an ALTER of an enum type.
- Named indexes use the `ix_<table>_<col…>` form (88 explicit `Index("ix_…")`, incl. composite status/type/created_at queues).
- PG-only constructs go through the dialect import (`sqlalchemy.dialects.postgresql import UUID/JSONB`); server-side boolean defaults are `sa_text("false")`/`sa_text("true")` (the only 3 `sa_text` uses in this dir) — `text("0")` is accepted by SQLite and rejected by real PostgreSQL (D-e2e-08).
- `__init__.py` is empty (0 bytes): there is no barrel, so a consumer imports `app.models.<name>`. A new model file must additionally be imported in `app/main.py` (see parent) or `create_all`/alembic autogenerate will not see the table.

## ANTI-PATTERNS (THIS PROJECT)
- Three overlapping task tables exist on purpose-historically (`task_queue`, `sandbox_task`, `pipeline_task`) — see parent before adding columns; do not add a fourth.
- Never add a plaintext key/secret column: KMS tables hold wrapped envelope blobs, `api_key` holds hash + prefix.
- Historical nullable columns stay NULL on purpose: `sandbox_session.env` (SS-15 test/production) leaves pre-SS-15 rows unlabelled and outside the environment gate — never "backfill" them in a migration. `compute_run.idempotency_key` is NULL for manual triggers precisely so the unique constraint (one run per scheduled instant) allows many manual runs.
- Schema changes are additive-only: new revision under `alembic/versions/`, never an edit to an existing one; `python scripts/verify_schema_parity.py` is the CI gate.

## COMMANDS
```bash
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" alembic revision --autogenerate -m "topic"   # env.py imports app.main for metadata
python scripts/verify_schema_parity.py            # alembic-chain vs create_all must match
```
