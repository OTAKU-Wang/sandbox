# app/models/

**Score 34 — distinct domain: SQLAlchemy ORM layer.** 44 modules + `__init__.py` = 3,070 LOC / 105 classes (55 mapped tables, 48 `str, Enum` companions, plus `user.UserRole` and `watermark_record.EncryptedKeySeed(TypeDecorator, BigInteger)`), largest file 155 L — one entity per file, declarative only, no business logic (sole exception: `task_queue.py`). Import sites by module: `user` 124, `sandbox_session` 120, `contract` 85, `data_product` 66, `audit_log` 61, `sandbox_node`/`data_resource` 28, `kms` 24.

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Durable multi-replica queue | `task_queue.py` (155 L) | the one model module with behavior: `enqueue`/`claim_next`/`complete`/`fail`/`requeue_stale`; PG uses `FOR UPDATE SKIP LOCKED`, SQLite falls back to an optimistic conditional UPDATE |
| Refresh-token revocation | `refresh_session.py` | the only table registered through the package barrel instead of `main.py` (see CONVENTIONS); `alembic/versions/0032_*` added its columns, `0033_*` converged its webhook index |
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
- `__init__.py` is NOT empty: it is a 702-byte barrel that imports **only** `RefreshSession`, so that table is visible to `Base.metadata.create_all` and to alembic `env.py` transitively (any `app.models.*` import runs it, so there is no second edit site to drift). Its docstring says do not grow it — every other model stays on the `app/main.py` import convention.

## ANTI-PATTERNS (THIS PROJECT)
- Three overlapping task tables exist on purpose-historically (`task_queue`, `sandbox_task`, `pipeline_task`) — see parent before adding columns; do not add a fourth.
- Never add a plaintext key/secret column: KMS tables hold wrapped envelope blobs, `api_key` holds hash + prefix.
- Historical nullable columns stay NULL on purpose: `sandbox_session.env` (SS-15 test/production) leaves pre-SS-15 rows unlabelled and outside the environment gate — never "backfill" them in a migration. `compute_run.idempotency_key` is NULL for manual triggers precisely so the unique constraint (one run per scheduled instant) allows many manual runs.
- Schema changes are additive-only: new revision under `alembic/versions/`, never an edit to an existing one; `python scripts/verify_schema_parity.py` is the CI gate. The chain is LINEAR — head as of 2026-11-02 is `0034_integrity_patrol_columns` (`0032` refresh sessions, `0033` webhook index convergence, `0034` adds two `data_resources` columns matching `app/models/data_resource.py`).
- No table here holds plaintext key material, and `watermark_record.EncryptedKeySeed` is a `TypeDecorator` that encrypts on the way in — reuse it instead of adding another encrypted-column recipe.

## COMMANDS
```bash
# ALWAYS the venv alembic: PATH's ~/.local/bin/alembic runs system protobuf 7.35.0,
# which clashes with the 7.35.1 gencode in app/_stubs and dies on `import app.main`.
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" .venv/bin/python -m alembic revision --autogenerate -m "topic"  # env.py imports app.main for metadata
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" .venv/bin/python -m alembic upgrade head \
  && ALEMBIC_COMPARE_TYPES=0 .venv/bin/python -m alembic check   # SQLite migration lane
python scripts/verify_schema_parity.py            # alembic-chain vs create_all must match
```
