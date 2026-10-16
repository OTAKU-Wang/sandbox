# app/

**Score 17** (282 files, 9 subdirs, ~70.8k LOC — highest reference density in repo). FastAPI backend entry of CDS (Confidential Data Sandbox) — GM/T national crypto (SM2/SM3/SM4), uvicorn on :8000.

## STRUCTURE
```
app/
├── main.py       # 401 L: 44 include_router calls, 8+ lifespan subsystems, 3 ASGI middlewares
├── core/         # infra: config, database, deps, scopes, errors, pagination (see core/)
├── api/          # 44 route modules, ~15.9k LOC — prefixes assigned ONLY in main.py (see api/)
├── services/     # 117 flat modules, ~45.7k LOC — sandbox runtimes, pipelines, crypto/KMS (see services/)
├── models/       # 43 ORM files + empty __init__.py / 101 classes, one entity per file (see models/)
├── schemas/      # 11 pydantic wire modules + __init__.py, 1,148 LOC (see schemas/)
├── utils/        # crypto.py (SM4Cipher, sm3_hash), watermark.py (zero-width/LSB stego)
├── _stubs/       # GENERATED pinned gRPC stubs — never hand-edit (see _stubs/)
├── templates/    # legacy Jinja admin pages — NOT the real UI (React lives in cds-frontend/)
└── static/       # 63-line fetch client + CSS for the legacy admin pages
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Boot wiring, subsystem order | main.py `lifespan` | security validate → Redis → create_all → pipeline → KMS TTL → key restore → cleanup loop → CDC → Merkle; each in its own try/except |
| Routers & prefixes | main.py `include_router` block | health/metrics at root; `open_api` at `/open/v1`; `cube_egress` at `/internal/audit`; `session_stream` WS absolute; `admin` on a separate app (main.py:398) |
| Machine-credential (API-key) authorization | core/scopes.py `require_group` | every router carries `Depends(require_group(...))` or `require_human`; unmapped path prefix = default-deny |
| Sandbox-level capability truth | services/capability_matrix.py | single authority for real/simulated/unsupported/planned; never duplicate the matrix |
| Error contract | core/errors.py + core/error_handlers.py | W2: `{code, message, detail, request_id}`, Retry-After, 410 for terminal states |
| Deployment gates | core/config.py `validate_security_config` | raises in prod for unsafe flags, warns for ALLOW_SIMULATION |
| Queue semantics | models/task_queue.py | `enqueue`/`claim_next`/`complete`/`requeue_stale` — business logic lives in the model module |

## CONVENTIONS
- Middleware order is load-bearing (W1): Starlette makes the LAST-added middleware outermost. `main.py` adds `Ss12KeyDefaultDenyMiddleware` first, `MetricsMiddleware` second, `RequestIDMiddleware` third (→ RequestID outermost) — do not reorder. The SS-12 gate is raw ASGI because an HTTPBearer dependency there broke WS handshakes.
- SS-12 authorization is applied at MOUNT time, not per route: `main.py` passes `Depends(require_group("<group>"))` (or `require_human` for auth/users/api-keys/internal/static) into `include_router`; a new prefix must be added to `core/scopes.py` `GROUP_PREFIXES` or key subjects get 403 on it.
- `main.py` imports every model module explicitly so `Base.metadata.create_all` sees all tables; a new model file MUST be added there.
- `create_all` runs only in DEBUG/TESTING/`sqlite`-in-DATABASE_URL; production requires Alembic (`alembic upgrade head` + `scripts/verify_schema_parity.py` gate). `alembic/env.py` imports `app.main` so autogenerate sees exactly this import set.
- All `__init__.py` are empty and there is no `__all__` — import modules by full path (`app.services.foo`), never via package re-export. Sole exception: `app/_stubs/cube/**/__init__.py` carry a "generated — do not edit" docstring.
- Async SQLAlchemy 2.0 only; `Base`/`get_db` come from `app.core.database`; models use `str, Enum` columns and PG JSONB dialect types.
- Spec-reference comments (W1–W16, N3–N10, T1–T12, Gap A/B/E, G-###, SS-10…SS-15, PZ-01…PZ-16, MR-A1…MR-A15, D-e2e-##, CB-00…CB-06, "Round 39/40") reference remediation docs in `docs/` + `specs/` — read them before restructuring flagged code.
- Mixed Chinese/English in docstrings and some user-facing HTTPException details (e.g. 429 quota messages).

## ANTI-PATTERNS (THIS PROJECT)
- `settings = get_settings()` is defined in main.py AFTER `lifespan` which uses it — works only because lifespan runs post-import; do not "fix" the ordering casually.
- `utils/crypto.py::SM4Cipher` is actually AES-256-GCM ("SM4-GCM equivalent for dev/test; production Tongsuo via system OpenSSL") — never present it as real SM4. `sm3_hash` falls back to SHA-256 when `gmssl` is missing; since PZ-04/MR-A14 that fallback is gated by `CDS_SM3_FALLBACK_ALLOWED` (fail-closed `RuntimeError` when false) plus one process-lifetime warning — the fallback is disclosed, but callers still cannot tell which hash ran.
- Never hand-edit or delete files under `app/_stubs/` (including its `__init__.py`): they are generated from a pinned upstream sha. Regenerate via `scripts/gen-cube-stubs.sh` only.
- Legacy `/admin/*` Jinja pages carry NO authentication: off by default, rejected at startup in production — never enable in prod.
- Three overlapping task tables: `models/task_queue.py` (durable queue), `models/sandbox_task.py`, `models/pipeline_task.py` — confirm which one a flow uses before adding columns.
- `app/templates` + `app/static` are dead weight for the real product; the React app in `cds-frontend/` is the admin UI.

## COMMANDS
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000   # health: GET /health, metrics: GET /metrics
python -m compileall -q app                        # CI gate (py3.12 image; local venv may be newer — annotations differ)
docker compose up                                   # postgres/redis/clickhouse/minio/opa/vault + cds-api
python -m pytest -q tests/<file>                   # asyncio_mode=auto, timeout=600
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" alembic upgrade head && ALEMBIC_COMPARE_TYPES=0 alembic check
python scripts/verify_schema_parity.py             # CI gate: Alembic vs create_all must match
```
