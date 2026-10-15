# tests/

**Score 12 — distinct domain: test harness with its own env contract.** 175 flat pytest files (~35k LOC). `conftest.py` (180 L) is the fixture root every module consumes.

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Fixtures & env gates | `conftest.py` — `client` (httpx ASGITransport + get_db override), `db_session`, `setup_db` (autouse create/drop per test), `auth_headers`/`operator_headers`/`admin_headers`, `make_user` factory, `publish_product`, `create_user_with_role` |
| Sandbox cluster (~3,100 L) | `test_sandbox_runtime` (850, runs REAL bwrap — may fail on hosts without it; CI treats L0/bwrap/cgroup failures as environmental), `test_sandbox_security_hardening` (773), `test_sandbox_sessions`, `test_shared_volumes` |
| E2E flows | `test_e2e_flows` (1091, in-process ASGI), `test_e2e_sandbox` |
| Gap ledgers | `test_p0_8_9_10` (10 xfail), `test_p0_security_gaps`, `test_sprint8_integration` — `[GAP]/[FIXED]` classes, existence assertions by design |
| SDK coverage | `test_sdk_client`, `test_error_contract`, `test_pagination`, `test_async_operations` (httpx.MockTransport, no server) |

## CONVENTIONS
- `asyncio_mode = "auto"` (pyproject) yet explicit `@pytest.mark.asyncio` is redundant-but-ubiquitous — follow the local file's style.
- In-memory SQLite + `StaticPool`; conftest patches `app.core.database.engine`/`async_session` and sets `app.router.lifespan_context = None`.
- Env is pinned BEFORE app import: `TESTING=1` plus four RELAXED fail-closed switches (`CDS_KMS_REQUIRE_ATTESTATION=false`, `CDS_DEV_SANDBOX_REQUIRE_CONTRACT=false`, `CDS_K8S_USE_PYTHON_CLIENT=false`, `CDS_TRAINING_REQUIRE_TORCH=false`). The suite baseline deliberately runs dev-relaxed; ENFORCEMENT is covered by dedicated tests that monkeypatch the cached `Settings` (then `get_settings.cache_clear()`).
- Redis-backed logic is faked with `fakeredis[lua]` (lua extra needed for EVAL); monkeypatch module-private targets like `app.services.k8s_sandbox._kubectl`, `app.core.redis._redis_client`.
- CI is two-tier: a fixed ~30-file **gate list that MUST be green**, then the full suite `pytest tests/ -q --ignore=tests/test_e2e_full_lifecycle.py || true` is informational with a `ci/known-failures.md` baseline — the failure count must not grow; on timeout, find the hang, never raise the limit to mask it.

## ANTI-PATTERNS (THIS PROJECT)
- DO NOT "fix" simulation fallbacks they exercise — `tee_simulator`, `gpu_tee_simulator`, firecracker simulation mode, KMS software quotes, training proxy mode are design; `test_training_fail_closed` asserts a simulated result never masquerades as real.
- `test_p0_security_gaps` greps app source text (SCMP_ACT_KILL, bloom_filter, whitelist tokens) — brittle by design; it breaks on refactors of `audit_service`, `secure_duckdb`, `firecracker_runtime`, `sandbox_security`.
- Live-URL E2E (`e2e_full_test.py`, `test_e2e_full_lifecycle.py`) hits a hardcoded deployed base URL with `time.sleep` 429 backoff and hardcoded creds — manual-only, excluded from CI; don't add files in this style.
- `test_sm2_crypto.py` is misnamed: it tests SM4/SM3 (`SM4Cipher`/`sm3_hash`), no SM2.
- conftest's session-scoped `event_loop` fixture is a deprecated pytest-asyncio pattern; `test_p05_code_scanning` calls `asyncio.get_event_loop().run_until_complete` — both are known legacy, do not propagate.
- Duplicated helpers are local on purpose: `_mk_session`, `_run_runner`, `_mk_contract`, fake redis builders are re-declared per file; no shared util module exists.
- Windows portability: tests importing POSIX-only `resource` (via `sandbox_security`/`session_files`) carry `sys.platform` skip guards — keep them when copying those tests.

## COMMANDS
```bash
python -m pytest -q tests/test_rag_service.py        # asyncio auto, 600s timeout guard
python -m pytest tests/ -q -m "not slow"             # skip migration-chain / schema-parity time
```
