# alembic/ — schema migration chain (scored ~9, distinct domain: additive-only linear chain with its own async env and two CI parity gates)

22 revisions, linear chain `0000_baseline_all_tables → … → 0021_delivery_bundle_ownership`, one revision per file, naming `NNNN_snake_case_topic`. ~1,933 L total; baseline 789 L (30 `create_table`s), `0019_flow_orchestration` 282 L (SS-15), the rest 31–61 L each.

## STRUCTURE
```
alembic/
├── env.py       # async env; imports app.main so autogenerate metadata == create_all import set
└── versions/    # NNNN_topic.py, strictly linear — no branches
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Add a column/table | new `NNNN_topic.py` revision | never edit an existing one |
| Root of the schema | `versions/0000_baseline_all_tables.py` | frozen; deliberately OMITS columns added by 0001/0002 so the chain applies on an empty DB |
| SS-15 flow tables | `versions/0019_flow_orchestration.py` (+0020, 0021) | fully Chinese docstrings |
| Autogenerate wiring | `env.py` | the `import app.main` is load-bearing for the parity gate |

## CONVENTIONS
- Additive-only, hand-reviewed migrations; the chain must apply cleanly on an empty DB.
- Dev/test DBs bootstrapped via `create_all` get `alembic stamp head` (they are not migrated).
- Boolean server defaults MUST use `text("false")`/`text("true")` — `text("0")` is rejected by real PostgreSQL (D-e2e-08 lesson); the model layer (`app/models/`) is the other half of the parity contract.
- Composite index names in models (`ix_<table>_<cols>`) must match migration `create_index` names or `alembic check` reddens.

## ANTI-PATTERNS
- Never edit alembic history (including the 0000 baseline) to add columns — new revision instead.
- `ALEMBIC_COMPARE_TYPES=0` exists ONLY for the SQLite CI check (SQLite has no real types) — never use it to mask type drift on PostgreSQL.
- Never run `alembic upgrade` against production through `create_all` paths — prod forbids `create_all` (branch in `app/main.py`); prod requires Alembic + `verify_schema_parity.py`.

## COMMANDS
```bash
# CI parity gates (from repo root)
python scripts/verify_schema_parity.py                       # alembic vs create_all, exit 1 on drift — CI gate
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" alembic upgrade head && ALEMBIC_COMPARE_TYPES=0 alembic check
# real PostgreSQL validation (remote harness)
bash deploy/e2e-remote/pg_validate.sh                        # alembic head + create_all on real PG
# dev bootstrap
CDS_DATABASE_URL=... alembic stamp head                      # for create_all-built DBs
```
