# app/core/

**Score 14 — distinct domain: infra & config.** 15 modules + empty `__init__.py`, 2,295 LOC. Highest single-module centrality in the repo: `core.database` 105 importers, `core.config` 101, `core.deps` 43, `core.errors` 31.

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Deployment behavior, all tunables | `config.py` (571 L) | read FIRST: ~152 `Settings` fields, `model_config = {"env_prefix": "CDS_", "env_file": ".env"}`, lru_cached `get_settings()` |
| Production safety gates | `config.py::validate_security_config` | 15 prod `raise`s (160-line body) incl. `SECCOMP_/HSM_SOFTWARE_/SM3_FALLBACK_ALLOWED`, short/default JWT, `ADMIN_PAGES_ENABLED`, missing `COLUMN_ENCRYPTION_KEY`/`MFA_SECRET_ENCRYPTION_KEY`, `SANDBOX_LEGACY_ENABLED`, Cube dataplane without TLS CA / results dir / signing key, attestation without allowed MR measurements, federation without mTLS material; WARNS for `ALLOW_SIMULATION` (T12) |
| Error contract (W2) | `errors.py` + `error_handlers.py` | `CDSError` base → `SessionNotFound`, `SessionTerminated` (410 — client MUST stop retrying), `SessionStateConflict`, `OperationLocked` (503+Retry-After), `RateLimited`, `ResourceExhausted`, `QuotaExceeded`, `RequestTimeout`; body `{code, message, detail, request_id}`; `OperationNotSupported` maps to 501 |
| AuthN + credential scopes | `deps.py` (288 L) + `scopes.py` (337 L) | `get_current_user`, `get_business_user` (human-or-`cds_ak_` key), `require_roles`, `verify_service_token`, `verify_cube_audit_token`; `scopes.py` = scope grammar `<group>:<verb>`, longest-prefix `GROUP_PREFIXES` map, `require_group`/`require_human`, 5-failure/15-min cooldown, per-key rate limit |
| Keyset pagination (W8) | `pagination.py` | `encode_cursor`/`decode_cursor`/`paginate_keyset` (created_at + id, base64) |
| Path safety | `path_security.py` | `sanitize_filename`, `safe_resolve_within`, `safe_join_object_name` — use for ANY user-supplied path |
| Security middleware | `security.py` | `RateLimitMiddleware` (Redis fixed windows, fail-open on Redis outage), `SecurityHeadersMiddleware`, `RequestSizeLimitMiddleware`, `_unverified_jwt_sub` (audit-identity ONLY — never auth/tenant filter) |
| DB engine & session | `database.py` | `engine`/`async_session`/`Base`/`get_db` + `SyncDbBridge`/`sync_bridge_session` (sync workers → one throwaway async loop per call, because the engine/connections are async) |
| Redis / cache / metrics / logging | `redis.py`, `cache.py`, `metrics.py`, `logging.py`, `telemetry.py` | `get_redis`/`close_redis` (30s reconnect cooldown); `cache_get/set/delete/clear/stats` with `cache:` prefix + in-process fallback; `render_metrics` + transition/inspection/KMS counters; `RequestIdFilter` + `LOG_JSON` |

## CONVENTIONS
- Config changes: env vars are `CDS_`-prefixed field names. Tests that `monkeypatch.setenv` MUST call `get_settings.cache_clear()` (lru_cached).
- `get_db` owns the transaction: `try: yield session` then commit / rollback+raise. Routers MUST NOT call `db.commit()` inside a handler — a mid-request commit on the request-scoped session is what produces `MissingGreenlet`/stale-row failures downstream. Isolate a single statement with a SAVEPOINT (`begin_nested`), never `session.rollback()` (it expires every ORM object in the session).
- Middleware classes are added in `main.py` — see parent for the load-bearing add-order (last-added = outermost). The SS-12 default-deny gate lives in `main.py`, not here.
- `cache.py`, `security.py` rate limiting, and metrics are all disabled under `TESTING=1` / `PYTEST_CURRENT_TEST` — do not "fix" tests that rely on that.
- Metric labels are a hard red line (`metrics.py` docstring): never add an unbounded dimension (`session_id`, `contract_id`, user id) — cardinality explosion.
- Passwords: direct `bcrypt` (NOT passlib — passlib 1.7.4 is incompatible with bcrypt 5.0); 72-byte truncation is manual.
- Dev-fallback secrets in `auth_service` etc. are gated on `DEBUG`/`TESTING=1`/`PYTEST_CURRENT_TEST`; prod paths call `settings.validate_jwt_security()`.
- The W2 error body's `request_id` is stamped by `telemetry.py::RequestIDMiddleware` — keep the header name stable, error and metrics payloads both echo it.
- An audit write that cannot be persisted must never be silently lost (`deps.py::_record_audit_write_failure`); degradation here is disclosed, not swallowed.

## ANTI-PATTERNS (THIS PROJECT)
- `core.cache` in-process fallback, Redis fail-open rate limiting, and the in-process scope cooldown/rate-limit counters are deliberate availability choices (docs/error-codes.md, scopes.py docstring) — honestly approximate under multi-instance, never claimed global. Not bugs.
- JSON-mode logging (`LOG_JSON`) is a no-op until enabled; `RequestIdFilter` must stay first in the logging chain.
- Do not add a scope for the human-only groups (`auth`/`users`/`api-keys`/`internal`/`static`) — machine keys are always rejected there by `require_human`.
