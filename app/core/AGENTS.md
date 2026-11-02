# app/core/

**Score 12 — distinct domain: infra & config** (counts measured 2026-11-02). 15 modules + empty `__init__.py`, 3,111 LOC, ~70 public symbols. Highest fan-in in the repo by import-site count: `core.config` 153 files / 222 sites, `core.database` 123 / 164, `core.errors` 58 / 66, `core.deps` 46, `core.redis` 22.

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Every tunable + prod posture | `config.py` (748 L) | read FIRST: 163 `Settings` fields, `env_prefix="CDS_"`, `env_file=".env"`, `@lru_cache get_settings()`, `is_production()` |
| Production hard gates | `config.py::validate_security_config` (533-743) | 18 `raise ValueError`: seccomp off ("never retry without seccomp"), SM3→SHA-256 fallback armed (PZ-04/MR-A14), built-in default column DEK, MFA key falling back to the JWT secret (PZ-10), unsigned dev admin tokens, `ADMIN_PAGES_ENABLED` (W4), `SANDBOX_LEGACY_ENABLED` (SS-13), Cube dataplane without TLS CA / results dir / signing key, attestation with an empty measurement whitelist (A141), federation without mTLS, short/default JWT, unknown `RAG_EMBEDDING_BACKEND`. WARN-class issues get appended, never raised (`ALLOW_SIMULATION` T12, `WEBHOOK_ALLOW_PRIVATE_URLS`, janitor real-delete, default JWT) |
| JWT key / janitor validators | `config.py:488` `validate_jwt_security` (2 raises), `:510` `validate_janitor_config` | both return issue lists; the caller decides raise-vs-log — `main.jwt_key_source` mirrors the secret-resolution order |
| W2 error classes | `errors.py` (86 L) | `CDSError` base → `SessionNotFound`, `SessionTerminated` (410 — client MUST stop retrying), `SessionStateConflict`, `OperationLocked` (503+Retry-After), `RateLimited`, `ResourceExhausted`/`QuotaExceeded`, `RequestTimeout` |
| W2 wire format | `error_handlers.py` | `{code, message, detail, request_id}` + `X-Request-ID` on every error; also maps Starlette HTTP (`HTTP_<status>`) and pydantic (`VALIDATION_ERROR`) |
| AuthN + machine-key authz | `deps.py` (308 L) + `scopes.py` (405 L) | `resolve_auth_subject`/`get_auth_subject` split `cds_ak_` keys from human JWTs (`machine_key_from_request` reads `X-CDS-API-Key`); `get_current_user`, `get_business_user`, `require_roles`, `verify_service_token`, `verify_cube_audit_token`. scopes: `<group>:<verb>` grammar, longest-prefix `GROUP_PREFIXES`, `require_group`/`require_human`, 5-failure/15-min cooldown, per-key rate limit |
| DB engine, session, commit hooks | `database.py` (305 L) | `Base`/`engine`/`async_session`/`get_db`; `drain_post_commit_hooks` flushes `session.info[NETPOL_PENDING_KEY]` pushes AFTER the commit succeeds; `SyncDbBridge`/`sync_bridge_session` let sync workers reach the async engine |
| Security middleware + Redis degradation | `security.py` (423 L) | `setup_security` adds CORS + SecurityHeaders + RateLimit + RequestSizeLimit; `RateLimitMiddleware` = Redis 60 s fixed windows (`cds:rl:<scope>:<id>:<window>`) failing open to an in-process window; `classify_redis_failure`/`redis_degradation_state` change only the classification + throttled disclosure, never the fail-open answer |
| request_id plumbing | `telemetry.py` + `logging.py` | `RequestIDMiddleware` issues/propagates `X-Request-ID` and resolves route templates for metric labels; `RequestIDContextMiddleware` + `request_id_var` contextvar + `RequestIdFilter` put the id on every log record; `setup_logging(LOG_JSON)` |
| Cache / Redis / metrics | `cache.py`, `redis.py`, `metrics.py` | `cache_get/set/delete/clear/stats` on the `cache:` namespace with an in-process fallback and glob escaping (`_escape_glob`); `get_redis` with a 30 s reconnect cooldown + `redact_url`; 11 collectors incl. `AUDIT_WRITE_FAILURES` and `CUBE_EGRESS_LINES`, exposed by `render_metrics()` |
| Keyset pagination (W8) | `pagination.py` | `paginate_keyset` fixed to `created_at DESC, id DESC`; cursor = base64url `<created_at iso>|<id>` |
| Path safety | `path_security.py` | `sanitize_filename`, `safe_resolve_within`, `safe_join_object_name` → `PathTraversalError`; use for ANY user-supplied path (null bytes, `../`, symlink escapes) |
| Single-use auth challenges (B1) | `challenge_store.py` | `consume()` = Redis SETNX on `cds:challenge:consumed:<sha256>`, 300 s TTL; degrades to a bounded 4,096-entry local set with a throttled loud WARNING |

## CONVENTIONS
- Config changes: env vars are `CDS_`-prefixed field names. `get_settings()` is `@lru_cache`d — prod-mode tests monkeypatch the cached `Settings` instance and `delenv` TESTING/`PYTEST_CURRENT_TEST`; only `tests/test_k8s_n7.py` and `tests/test_sandbox_runtime.py` call `get_settings.cache_clear()`. Match the file you are editing; do not mass-convert.
- `get_db` owns the transaction: `try: yield session` then commit / rollback+raise. Routers MUST NOT call `db.commit()` inside a handler — a mid-request commit on the request-scoped session is what produces `MissingGreenlet`/stale-row failures downstream. Isolate a single statement with a SAVEPOINT (`begin_nested`), never `session.rollback()` (it expires every ORM object in the session).
- Post-commit side effects ride on `session.info[NETPOL_PENDING_KEY]` and run in `drain_post_commit_hooks` after the commit succeeds — never push to infra from inside the transaction, and never `rollback()` to undo one failed push (each push is individually guarded).
- Middleware classes are added in `main.py` — see parent for the load-bearing add-order (last-added = outermost). The SS-12 default-deny gate lives in `main.py`, not here.
- `cache.py`, `security.py` rate limiting, and metrics are all disabled under `TESTING=1` / `PYTEST_CURRENT_TEST` — do not "fix" tests that rely on that.
- Metric labels are a hard red line (`metrics.py` docstring): never add an unbounded dimension (`session_id`, `contract_id`, user id) — cardinality explosion. HTTP labels come from route templates (`telemetry._resolve_route_template`).
- Passwords: direct `bcrypt` (NOT passlib — passlib 1.7.4 is incompatible with bcrypt 5.0); 72-byte truncation is manual.
- Dev-fallback secrets in `auth_service` etc. are gated on `DEBUG`/`TESTING=1`/`PYTEST_CURRENT_TEST`; prod paths call `settings.validate_jwt_security()`.
- The W2 error body's `request_id` is stamped by `telemetry.py::RequestIDMiddleware` and read back via the `request_id_var` contextvar — keep the header name stable, error and log payloads both echo it.
- An audit write that cannot be persisted must never be silently lost (`deps.py::_record_audit_write_failure` + `metrics.AUDIT_WRITE_FAILURES`); degradation here is disclosed, not swallowed.
- Bearer-ish material never lands in a Redis key raw — hash it first (`challenge_store._token_id` = sha256).

## ANTI-PATTERNS (THIS PROJECT)
- `core.cache` in-process fallback, Redis fail-open rate limiting, the in-process scope cooldown/rate-limit counters and `challenge_store`'s local fallback set are deliberate availability choices (docs/error-codes.md, scopes.py + challenge_store.py docstrings) — honestly approximate under multi-instance, never claimed global. Not bugs.
- `_unverified_jwt_sub` is audit identity ONLY — never auth and never a tenant filter.
- Never widen one of the 18 prod `raise`s into a WARN to make a deployment boot: they are the product's refusal contract (SS-13 single-path, PZ-04 hash honesty, PZ-10 MFA key separation, W4 legacy pages).
- JSON-mode logging (`LOG_JSON`) is a no-op until enabled; `RequestIdFilter` must stay first in the logging chain.
- Do not add a scope for the human-only groups (`auth`/`users`/`api-keys`/`internal`/`static`) — machine keys are always rejected there by `require_human`.
