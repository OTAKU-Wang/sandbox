# app/

**Score 17** (195 files, 7 subdirs, highest reference density in repo). FastAPI backend entry of CDS (Confidential Data Sandbox) — GM/T national crypto (SM2/SM3/SM4), uvicorn on :8000.

## STRUCTURE
```
app/
├── main.py       # FastAPI app: 34 routers, ~8 lifespan subsystems, middleware order
├── core/         # infra: config, database, deps, errors, middleware, pagination (see core/)
├── api/          # 34 route modules, ~12.5k LOC — prefixes assigned ONLY in main.py (see api/)
├── services/     # 107 flat modules, ~39.5k LOC — sandbox runtimes, pipelines, crypto/KMS (see services/)
├── models/       # 30 ORM files, one entity per file, str-Enum status columns
├── schemas/      # 9 pydantic API modules (Create/Update/Response per domain)
├── utils/        # crypto.py (SM4Cipher, sm3_hash), watermark.py (zero-width/LSB stego)
├── templates/    # legacy Jinja admin pages — NOT the real UI (React lives in cds-frontend/)
└── static/       # 64-line fetch client + CSS for the legacy admin pages
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Boot wiring, subsystem order | main.py `lifespan` | quota restore → pipeline → KMS TTL → key restore → cleanup loop → CDC → Merkle; each in its own try/except |
| Routers & prefixes | main.py `include_router` block | health/metrics at root; `session_stream` WS has absolute paths; `admin` on a separate app |
| Error contract | core/errors.py + core/error_handlers.py | W2: `{code, message, detail, request_id}`, Retry-After, 410 for terminal states |
| Deployment gates | core/config.py `validate_security_config` | raises in prod for unsafe flags, warns for ALLOW_SIMULATION |
| Queue semantics | models/task_queue.py | `enqueue`/`claim_next`/`complete`/`requeue_stale` — business logic lives in the model module |

## CONVENTIONS
- Middleware order is load-bearing (W1): Starlette makes the LAST-added middleware outermost. `main.py` adds `MetricsMiddleware` first, `RequestIDMiddleware` second — do not reorder.
- `main.py` imports every model module explicitly so `Base.metadata.create_all` sees all tables; a new model file MUST be added there.
- `create_all` runs only in DEBUG/TESTING/sqlite; production requires Alembic (`alembic upgrade head` + `scripts/verify_schema_parity.py` gate).
- All `__init__.py` are empty and there is no `__all__` — import modules by full path (`app.services.foo`), never via package re-export.
- Async SQLAlchemy 2.0 only; `Base`/`get_db` come from `app.core.database`; models use `str, Enum` columns and PG JSONB dialect types.
- Spec-reference comments (W1–W16, N3–N10, T1–T12, Gap A/B/E, "Round 39/40", G-###) reference external remediation docs in `docs/` — read them before restructuring flagged code.
- Mixed Chinese/English in docstrings and some user-facing HTTPException details (e.g. 429 quota messages).

## ANTI-PATTERNS (THIS PROJECT)
- `settings = get_settings()` is defined in main.py AFTER `lifespan` which uses it — works only because lifespan runs post-import; do not "fix" the ordering casually.
- `utils/crypto.py::SM4Cipher` is actually AES-256-GCM ("SM4-GCM equivalent for dev/test; production Tongsuo via system OpenSSL") and `sm3_hash` silently falls back to SHA-256 when `gmssl` is missing — the API surface cannot tell which ran.
- Legacy `/admin/*` Jinja pages carry NO authentication: off by default, rejected at startup in production — never enable in prod.
- Three overlapping task tables: `models/task_queue.py` (durable queue), `models/sandbox_task.py`, `models/pipeline_task.py` — confirm which one a flow uses before adding columns.
- `app/templates` + `app/static` are dead weight for the real product; the React app in `cds-frontend/` is the admin UI.

## COMMANDS
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000   # health: GET /health, metrics: GET /metrics
docker compose up                                   # postgres/redis/clickhouse/minio/opa/vault + cds-api
python -m pytest -q tests/<file>                   # asyncio_mode=auto, timeout=600
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" alembic upgrade head && ALEMBIC_COMPARE_TYPES=0 alembic check
python scripts/verify_schema_parity.py             # CI gate: Alembic vs create_all must match
```
