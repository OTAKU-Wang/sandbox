# app/

**Score 16** (296 py files / 88,445 LOC in 8 subpackages; `app.main:app` referenced from 29 other files). FastAPI backend of CDS (密态沙箱) — GM/T crypto surface (SM2/SM3/SM4/TLCP), uvicorn :8000. This file = boot wiring + package-wide rules only; every subdirectory's own detail lives in its AGENTS.md.

## STRUCTURE
```
app/                        # counts measured 2026-11-02
├── main.py      # 721 L — the ONLY wiring point: 46 router mounts, 8 middlewares, 6 background loops
├── core/        # 15 modules / 3,111 L — Settings, engine+session, authN, W2 errors, telemetry (see core/)
├── api/         # 48 modules / 21,231 L — route modules; prefixes exist ONLY in main.py (see api/)
├── services/    # 120 modules / 56,040 L — flat, zero subpackages: runtimes, pipelines, KMS, output policy (see services/)
├── models/      # 45 modules / 3,070 L — one entity per file, 103 classes (see models/)
├── schemas/     # 12 modules / 1,213 L — pydantic wire models (see schemas/)
├── utils/       # 4 modules / 769 L — crypto honesty gates + attribution watermarking (see utils/)
├── _stubs/      # 33 generated CubeSandbox gRPC files / 2,290 L — never hand-edit (see _stubs/)
├── templates/   # 6 Jinja pages for legacy /admin/* — NOT the product UI (that is cds-frontend/)
└── static/      # css + js served under /static through `_KeyBlockedStatic` (main.py)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Subsystem boot / shutdown order | `main.py:230` `lifespan` | one try/except per subsystem; 6 `create_task` loops — duckdb sweeper 226, TTL cleanup 322, session cleanup 378, container reconcile 399, compute scheduler 408, webhook dispatcher 419 — each behind `TESTING` ± a feature flag |
| Router mounts + SS-12 gates | `main.py:614-672` | 40 `require_group("<g>")`, 6 `require_human`, 2 prefix-less absolute mounts (`session_stream`, `jupyter_proxy`: no scope gate by design, so machine keys cannot reach them); `open_api` at `/open/v1`, `cube_egress` at `/internal/audit` |
| Default-deny for machine keys | `main.py:509` `Ss12KeyDefaultDenyMiddleware` | raw ASGI on purpose — an app-level `HTTPBearer` dependency broke WS handshakes; delegates to `scopes.key_unmapped_deny` so guard and routes parse credentials identically (A65/A67) |
| Human-only `/static` | `main.py:679` `_KeyBlockedStatic` | same predicate (`effective_key_credential`): a human JWT + leftover key header must stay a human subject |
| Legacy admin pages | `main.py:713` `_register_admin_pages(app)`, called at L721 | ONE ASGI app (there is no second app); needs `ADMIN_PAGES_ENABLED`, which prod boot rejects |
| Boot-time honesty | `main.py` `hsm_backend_disclosure` 162, `dp_budget_backend_wiring` 124, `jwt_key_source` 82, `durable_queue_wiring` 62 → `app.state` (241/245) + WARNING logs | these underscore-adjacent helpers are machine-consumed: renaming them breaks `test_ocr_a75_boot_wiring_honesty`, `a89_jwt_boot_disclosure`, `a94*`, `a192d` |
| create_all gate | `main.py:53` `_create_all_allowed` | `DEBUG` ∨ `TESTING=1` ∨ `"sqlite" in DATABASE_URL`; production = Alembic only |
| Anything one level down | `core/`, `api/`, `services/`, `models/`, `schemas/`, `utils/`, `_stubs/` AGENTS.md | capability truth = `services/capability_matrix.py`; W2 error contract + the 18 prod gates = `core/`; queue semantics = `models/task_queue.py` |

## CONVENTIONS
- Middleware add-order is load-bearing (W1/A43): `main.py` adds `Ss12KeyDefaultDeny` (573) → `Metrics` (581) → `RequestIDContext` (591) → `RequestID` (592). Starlette makes the LAST-added outermost, so RequestID is outermost and `RequestIDContext` sits just inside it. `setup_security()` then adds CORS, SecurityHeaders, RateLimit and RequestSizeLimit (8 middlewares total).
- SS-12 authorization is applied at MOUNT time, not per route: each `include_router` carries `Depends(require_group("<group>"))` (or `require_human` for auth/users/api-keys/internal/static). A new prefix must land in `core/scopes.py` `GROUP_PREFIXES`, else key subjects get 403 `API_KEY_SCOPE_DENIED` before routing.
- Model registration: `main.py` imports 32 of the 45 model modules across 10 lines; the rest reach `Base.metadata` transitively via `app.api`/`app.services`, and `refresh_session` registers through the `app/models/__init__.py` barrel. `scripts/verify_schema_parity.py` — not the import list — is the real guard.
- `create_all` is gated by `_create_all_allowed`; `alembic/env.py` imports `app.main`, so autogenerate sees whatever this import set reaches, no more.
- `__init__.py` files are empty and there is no re-export layer — import by full path (`app.services.foo`). Three documented exceptions: `app/models/__init__.py` (RefreshSession-only barrel), `app/utils/office_watermark.py` (`__all__`), `app/_stubs/**/__init__.py` (generated docstrings).
- `settings = get_settings()` sits at `main.py:507`, AFTER the `lifespan` definition that uses it — correct only because lifespan runs post-import.
- Spec tags in comments (W1–W16, N3–N10, T1–T12, Gap A/B/E, G-###, SS-10…SS-19, PZ-##, MR-A##/MR-B##, A###, CB-##, D-e2e-##, "Round NN") point at ledgers in `docs/` + `specs/` — read the entry before restructuring tagged code.
- Mixed Chinese/English in docstrings, log lines and user-facing error details (e.g. 429 quota messages); async SQLAlchemy 2.0 throughout.

## ANTI-PATTERNS (THIS PROJECT)
- Never reorder the middlewares, never move the SS-12 default-deny check back into a route/app-level dependency (it breaks WS handshakes), and never re-implement its header parser a second time (A65/A67 misroute humans vs keys).
- Don't "fix" the `settings`-after-`lifespan` ordering in `main.py`.
- Never hand-edit or delete anything under `app/_stubs/` (including its `__init__.py`): `tests/test_cube_upstream_check.py` hashes the tree against `_VERSION.json.stub_hash`. Regenerate via `scripts/gen-cube-stubs.sh`.
- Legacy `/admin/*` Jinja pages carry NO authentication: off by default, rejected at startup in production — never enable in prod, and never treat `templates/` + `static/` as the product UI (the React admin lives in `cds-frontend/`).
- Never add a router mount without a scope group, and never add a scope for the human-only groups.
- Crypto/watermark honesty rules live in `utils/AGENTS.md` — read it before touching `app/utils`.

## COMMANDS
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000   # /health and /metrics are at ROOT, not under /api/v1
python -c "import app.main"                        # import smoke test after any app/ change
python -m compileall -q app                        # CI gate (prod image is py3.12; local venv may be newer)
docker compose up                                   # postgres/redis/clickhouse/minio/opa/vault + cds-api
python -m pytest -q tests/<file>.py                # asyncio_mode=auto, timeout=600
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" .venv/bin/python -m alembic upgrade head \
  && ALEMBIC_COMPARE_TYPES=0 .venv/bin/python -m alembic check   # the venv alembic, not PATH's (protobuf VersionError)
python scripts/verify_schema_parity.py             # CI gate: Alembic vs create_all must match
```
