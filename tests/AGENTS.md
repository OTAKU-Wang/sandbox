# tests/

**Score 15 — distinct domain: test harness with its own env contract.** 211 flat pytest modules (45,667 LOC), zero subpackages — every file is its own collection root and `tests/__init__.py` is empty, so this single file covers the whole directory. `conftest.py` (472 L) is the fixture root every module consumes.

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Fixtures & env gates | `conftest.py` — `client` (httpx ASGITransport + get_db override), `db_session`, `setup_db` (autouse create/drop per test), `auth_headers`/`operator_headers`/`admin_headers`, `make_user` factory, `publish_product` (uploads a real CSV so the "product must carry a data resource" check stays live instead of being bypassed), `create_user_with_role`, `l2c_api_env` (resets the `sandbox_manager` singleton so the adapter rebinds the fake client), autouse `seed_default_cube_node` + `_restore_legacy_gate_defaults` |
| Sandbox cluster (~2,400 L) | `test_sandbox_runtime` (849, runs REAL bwrap — may fail on hosts without it; CI treats L0/bwrap/cgroup failures as environmental), `test_sandbox_security_hardening` (772), `test_sandbox_sessions` (584), `test_shared_volumes` (174) |
| E2E flows | `test_e2e_flows` (1098, in-process ASGI, 34 async tests, seeds `SandboxNode` directly), `test_ss15_flow_api` (608) + `test_ss15_e2e_flow` (476) for the SS-15 three-stage flow, `test_e2e_sandbox` |
| Gap ledgers | `test_p0_8_9_10` (11 xfail/skip), `test_p0_security_gaps`, `test_p05_code_scanning`, `test_sprint8_integration` — `[GAP]/[FIXED]` classes, existence assertions by design |
| SDK coverage | `test_sdk_client`, `test_pz11_sdk`, `test_ss15_sdk`, `test_error_contract`, `test_pagination`, `test_async_operations` — `sys.path.insert(0, REPO/"sdk")` + `httpx.MockTransport`, no server |
| Out-of-tree harness | `test_cds_volume_plugin` (`make local`, Go/C binary driven over `ctl` against an in-process MockKMS), `test_cube_node_tooling` (466 — runs `deploy/cube-node/preflight.sh`/`verify.sh` under a `CDS_CUBE_MOCK_ROOT` shim root), `test_cube_audit_tailer` + `test_ss13_node_tee_probe` (path-load `deploy/cube-node/*.py` with `importlib.util.spec_from_file_location`), `test_cube_template_dist` (fake gRPC built on real `app._stubs.cube` protobufs) |
| Fake cubelet / dataplane | conftest `TestCubeNodeClient` + `_install_test_cube_client()`: stubs only create/exec/update/destroy/list/get_storage_metrics and `__getattr__` raises on every other RPC; fake sandbox_id must be 32-hex (`f"{seq:032x}"`) because the real `l2c-<node>-<32hex>` right-cut parses it (D-e2e-19). conftest also patches `CubeRuntimeAdapter.prepare_session_volume` (SS-14 RT-07) |
| Upstream contract | `test_cube_upstream_check` — pins the upstream sha, the 15 `CubeboxMgr` RPC names and stub-regeneration idempotence; skips when the upstream clone or `.venv` is absent |
| Frontend | `frontend_test_plan.md` is a manual Chinese test plan (`TC-S15-01`…`TC-INT-04`) with no pytest counterpart — FE automation lives in `cds-frontend` (`npm test`); never treat it as a pytest source |

## CONVENTIONS
- `asyncio_mode = "auto"` (pyproject) yet explicit `@pytest.mark.asyncio` is redundant-but-ubiquitous — follow the local file's style.
- In-memory SQLite + `StaticPool`; conftest patches `app.core.database.engine`/`async_session` and sets `app.router.lifespan_context = None`.
- Env is pinned BEFORE app import: `TESTING=1` plus four RELAXED fail-closed switches (`CDS_KMS_REQUIRE_ATTESTATION=false`, `CDS_DEV_SANDBOX_REQUIRE_CONTRACT=false`, `CDS_K8S_USE_PYTHON_CLIENT=false`, `CDS_TRAINING_REQUIRE_TORCH=false`). The suite baseline deliberately runs dev-relaxed; ENFORCEMENT is covered by dedicated tests that monkeypatch the cached `Settings` (then `get_settings.cache_clear()`).
- Redis-backed logic is faked with `fakeredis[lua]` (lua extra needed for EVAL); monkeypatch module-private targets like `app.services.k8s_sandbox._kubectl`, `app.core.redis._redis_client`.
- CI is two-tier: a fixed **gate list of 34 files that MUST be green** (`.github/workflows/ci.yml`; `ci/known-failures.md` still says 24 — stale), then the full suite `pytest tests/ -q --ignore=tests/test_e2e_full_lifecycle.py || true` is informational with a `ci/known-failures.md` baseline — the failure count must not grow; on timeout, find the hang, never raise the limit to mask it.
- conftest pins 12 env vars with `os.environ.setdefault` BEFORE `from app.main import app`: `TESTING=1`, the four relaxed switches above, plus the SS-10/13/14 set (`CDS_LEGACY_L2_ENABLED`, `CDS_CUBE_DATAPLANE_ENABLED`, `CDS_SANDBOX_LEGACY_ENABLED`, `CDS_ALLOW_ADMIN_SELF_REGISTER`, `CDS_CUBE_NODE_TLS_CA`, `CDS_CUBE_TEMPLATE_SIGNING_KEY`, `CDS_CUBE_RESULTS_DIR`, `CDS_TRAINING_ALLOW_SIMULATION`) — each carries a comment naming its spec tag and where enforcement is tested. Edit order matters: the pins must precede the app import.
- Prod-mode tests do NOT `cache_clear`: they `monkeypatch.setattr(get_settings(), …)` the cached instance and `delenv` both `TESTING` and `PYTEST_CURRENT_TEST` so `validate_security_config()` believes it is in prod (`test_security_config_matrix`, `test_pz04_honesty_gates`, `test_pz01_sft_real`). Only `test_k8s_n7.py` calls `get_settings.cache_clear()`.
- Line 1 of a new module docstring carries its ledger tag (`SS-10 CB-0n`, `SS-12`/`SS-13`/`SS-15`, `PZ-0n`, `P0-*`, `MR-A*`, `N*`/`W*`/`Gap *`, `D-e2e-*`, `Round 39/40/45`) — grep the tag to find the spec. Docstrings and comments are mixed Chinese/English.
- Deferred imports dominate (~124 in-function `from app…` vs 67 module-level in one 43-file sample), so an import error surfaces per-test rather than at collection.
- Heavy-ML tests build tiny REAL models in-process (2-layer BERT + char WordPiece tokenizer, no network) or run the sandbox runner through `subprocess` for host-vs-sandbox parity; only 5 files use subprocess at all.
- Statistical assertions pin their RNG: `_seeded_rng` in `test_node_selector` — the weighted-preference thresholds sit inside the jitter band, so an unpinned assertion would pass by luck.
- Test users are uuid-suffixed `@example.com` (tolerates shared-DB ordering); a missing host tool (`openssl`, `git`, `torch`, `.venv`, upstream clone) is a conditional skip with a reason, never a hard failure.

## ANTI-PATTERNS (THIS PROJECT)
- DO NOT "fix" simulation fallbacks they exercise — `tee_simulator`, `gpu_tee_simulator`, firecracker simulation mode, KMS software quotes, training proxy mode are design; `test_training_fail_closed` asserts a simulated result never masquerades as real.
- `test_p0_security_gaps` greps app source text (SCMP_ACT_KILL, bloom_filter, whitelist tokens) — brittle by design; it breaks on refactors of `audit_service`, `secure_duckdb`, `firecracker_runtime`, `sandbox_security`.
- Live-URL E2E (`e2e_full_test.py`, `test_e2e_full_lifecycle.py`) hits a hardcoded deployed base URL with `time.sleep` 429 backoff and hardcoded creds — manual-only, excluded from CI; don't add files in this style.
- `test_sm2_crypto.py` is misnamed: it tests SM4/SM3 (`SM4Cipher`/`sm3_hash`), no SM2.
- conftest's session-scoped `event_loop` fixture is a deprecated pytest-asyncio pattern; `test_p05_code_scanning` calls `asyncio.get_event_loop().run_until_complete` — both are known legacy, do not propagate.
- Duplicated helpers are local on purpose: `_mk_session`, `_run_runner`, `_mk_contract`, fake redis builders are re-declared per file; no shared util module exists.
- Windows portability: tests importing POSIX-only `resource` (via `sandbox_security`/`session_files`) carry `sys.platform` skip guards — keep them when copying those tests.
- `test_p0_8_9_10.py` is an xfail LEDGER, not a failing suite: its 11 entries name open gaps (KMS does no SM2 DEK encryption, `distribute_key` returns None and takes no attestation parameter, no `suspend_sessions_by_cert` / cert→DEK→session cascade). A failure there is a gap-closed signal — never delete the xfail to go green. It also asserts on `inspect.getsource(cert_svc._cascade_revoke)`, so refactoring that method body breaks it by design.
- Host-coupled tests must stay skip-not-fail: `test_cube_upstream_check.py` defaults `CUBE_UPSTREAM_DIR` to a personal path (`~/Documents/workspace/CubeSandbox`) and shells out to `git` + `scripts/cube-upstream-check.sh` / `gen-cube-stubs.sh` — do not promote that default to a required fixture path. `@pytest.mark.slow` sits on exactly 3 files (`test_migrations`, `test_pz10_mfa`, `test_pz03_chain_persistence`), the ones that run `alembic upgrade head` in a subprocess.
- SS-13 tests carry cross-file coupling: `SS13_TEST_CUBE_NODE_ID` is duplicated (`conftest.py:219`, `test_ss13_e2e.py:32`) and SS-13 files delete+re-add the autouse-seeded `SandboxNode` row in-test instead of declaring a fixture.
- Wire contracts pinned by tests, not renameable: `test_kms_volume_keys.py:17` — "Go client (kms.go) wire field names — machine-consumed; do not rename"; `test_pz13_capability_matrix.py` requires levels `L2` and `k8s` to report `deprecated: True` and `l2c` False (SS-13 single-path contract).
- `pytest.raises(Exception, match="blocked|forbidden|denied")` (`test_db_access_proxy`) breaks on any error-message rewording — new tests assert the W2 `code` instead.
- `test_pz01_sft_real.py` does `import torch` at module top AND `torch = pytest.importorskip("torch")` two lines later; the top-level import defeats the skip and makes torch a hard collection dep. Don't copy the pair.
- Process-global auth/rate-limit counters must be autouse-reset (`_clean_ss12_state`); SS-12 tests additionally assert an attacker cannot grow those caches.

## COMMANDS
```bash
python -m pytest -q tests/test_rag_service.py        # asyncio auto, 600s timeout guard
python -m pytest tests/ -q -m "not slow"             # skip migration-chain / schema-parity time
python -m pytest tests/ -q --ignore=tests/test_e2e_full_lifecycle.py || true   # informational tier
# Gate tier: the 34 files listed in .github/workflows/ci.yml must be green.
# Authoritative numbers come from the REMOTE py3.12 production-derived image harness,
# NOT this venv — see the 2026-10-15 row of ci/known-failures.md and
# docs/cr-2026-10-15-review.md §5 (local runs are iteration-only oracles).
```
