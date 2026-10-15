# app/core/

**Score 12 — distinct domain: infra & config.** 14 modules, ~1.3k LOC. Highest single-module centrality in the repo: `core.database` has 62 importers, `core.deps` 30.

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Deployment behavior, all tunables | `config.py` (344 L) | read FIRST: ~120 `Settings` fields, `model_config = {"env_prefix": "CDS_", "env_file": ".env"}`, lru_cached `get_settings()` |
| Production safety gates | `config.py::validate_security_config` | RAISES at startup in prod for `SECCOMP_FALLBACK_ALLOWED`, `HSM_SOFTWARE_FALLBACK_ALLOWED`, `ADMIN_PAGES_ENABLED`, short/default JWT secret; WARNS for `ALLOW_SIMULATION` (T12) |
| Error contract (W2) | `errors.py` + `error_handlers.py` | `CDSError` base → `SessionNotFound`, `SessionStateConflict`, `RateLimited`, `QuotaExceeded`… mapped to `{code, message, detail, request_id}`, `Retry-After` on transient locks, 410 for terminal states |
| AuthN | `deps.py` | `get_current_user`, `require_roles(*allowed)` (HTTPBearer) |
| Keyset pagination (W8) | `pagination.py` | `encode_cursor`/`decode_cursor`/`paginate_keyset` (created_at + id, base64) |
| Path safety | `path_security.py` | `sanitize_filename`, `safe_resolve_within` — use for ANY user-supplied path |
| Security middleware | `security.py` | `RateLimitMiddleware` (Redis-backed fixed windows, fail-open on Redis outage), `SecurityHeadersMiddleware`, `RequestSizeLimitMiddleware` |
| Redis | `redis.py` | `get_redis`/`close_redis`, 30s reconnect cooldown |
| Caching | `cache.py` | `cache_get/set/delete` with `cache:` prefix, in-process fallback |
| DB engine & session | `database.py` | `engine`/`async_session`/`Base`/`get_db` — single source; services receive a caller-passed `AsyncSession`, never open their own |
| Session metrics | `metrics.py` | `render_metrics` + `record_session_transition`/`record_output_inspection`/`record_kms_distribution` counters |

## CONVENTIONS
- Config changes: env vars are `CDS_`-prefixed field names. Tests that `monkeypatch.setenv` MUST call `get_settings.cache_clear()` (lru_cached).
- Middleware classes are added in `main.py` — see parent for the load-bearing add-order (last-added = outermost).
- `cache.py`, `security.py` rate limiting, and metrics are all disabled under `TESTING=1` / `PYTEST_CURRENT_TEST` — do not "fix" tests that rely on that.
- Passwords: direct `bcrypt` (NOT passlib — passlib 1.7.4 is incompatible with bcrypt 5.0); 72-byte truncation is manual.
- Dev-fallback secrets in `auth_service` etc. are gated on `DEBUG`/`TESTING=1`/`PYTEST_CURRENT_TEST`; prod paths call `settings.validate_jwt_security()`.
- The W2 error body's `request_id` is stamped by `telemetry.py::RequestIDMiddleware` — keep the header name stable, error and metrics payloads both echo it.

## ANTI-PATTERNS (THIS PROJECT)
- `core.cache` in-process fallback and Redis fail-open rate limiting are deliberate availability choices (docs/error-codes.md) — not bugs.
- JSON-mode logging (`LOG_JSON`) is a no-op until enabled; `RequestIdFilter` must stay first in the logging chain.
