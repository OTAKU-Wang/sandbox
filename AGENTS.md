# PROJECT KNOWLEDGE BASE

**Generated:** 2026-10-16
**Commit:** 5d5417e
**Branch:** feat/ss-11-productization

## OVERVIEW
CDS = Confidential Data Sandbox (密态沙箱) — confidential data-circulation infrastructure for trusted data spaces: FastAPI (Python 3.12) backend, React 19 + Vite 8 admin frontend, standalone Python SDK, GM/T national crypto (SM2/SM3/SM4/TLCP), and a Go+C encrypted-volume plugin; deployed via docker compose, k3s manifests, and a Helm chart.

## STRUCTURE
```
sandbox/
├── app/                # FastAPI backend; main.py boot wiring, :8000 (`admin` on a separate app)
├── cds-frontend/       # React 19 + Vite 8 admin console (dev :3000, proxies /api → :8000)
│   └── node_modules/   # vendored deps — DO NOT EDIT
├── cds-volume-plugin/  # NEW: Go 1.25 + C `cds-encrypted` volume plugin; unix socket ↔ cube_runtime_adapter
│   └── tongsuo-build/  # second vendored Tongsuo — DO NOT EDIT
├── deploy/             # NEW: cube-node host bring-up + e2e-remote / local deploy harnesses
├── sdk/                # cds_sdk, standalone sync httpx REST client (/api/v1)
├── alembic/            # migrations, single LINEAR chain 0000→0021, additive-only
├── tests/              # 211 .py / 208 collected modules, 45.7k LOC; two-tier CI (gate + informational)
├── k8s/                # k3s manifest deploy path (deploy.sh, NodePort 30080/30081)
├── helm/               # cds chart (helm/cds), production path (ns cds-system)
├── scripts/            # verify_schema_parity.py, clickhouse-init.sql, install-k3s.sh
├── docs/               # error-codes.md contract, mock-remediation MR-A1..A15, retention
├── specs/              # SS-01..SS-15 design contracts, 48 files / 29.3k LOC (no ss-14 doc: SS-14 = remote-e2e round)
├── contracts/          # AuditRegistry.sol (Solidity 0.8, SM3 Merkle-root anchoring)
├── policies/           # OPA system.rego (default-deny anchor)
├── ci/                 # known-failures.md baseline
└── Tongsuo/            # vendored upstream Tongsuo 8.5 (OpenSSL fork, SM2/SM3/SM4, ~23k files) — DO NOT EDIT
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Backend boot / error contract | app/main.py | 401 L, 44 `include_router`; /health, /metrics at root (not /api/v1) |
| Security & sandbox core | app/services/ | 117 flat modules / 45.7k LOC: runtimes, ML pipelines, GM crypto+KMS, output policy |
| Sandbox runtime selection | app/services/ runtimes, cube_runtime_adapter | prefixes `l0- tee- fc- bwrap- k8s- l2c-`; legacy adapters only behind SANDBOX_LEGACY_ENABLED (prod REJECTS it), l2c only with CUBE_DATAPLANE_ENABLED |
| API routers | app/api/ | 44 route modules; prefixes only in main.py |
| AuthN surface | app/api/ | 30 use get_current_user/require_roles, 11 use get_business_user, 5 use neither (health, metrics, admin, session_stream, cube_egress) |
| Config / errors / deps | app/core/ | config.py 571 L, ~152 Settings fields, 15 prod raises in validate_security_config |
| SDK entry | sdk/cds_sdk/client.py | 1132 L / 114 defs (SS-12 + SS-15 surfaces); CDSClient, CDSError, CDSApiError |
| Frontend API boundary | cds-frontend/src/services/ | 22 modules; flow.ts (1466 L) largest, imported by 21 pages; `rag` module is gone |
| Frontend complexity concentration | cds-frontend/src/pages/SessionDetail.tsx | 1613 LOC; 38 lazy pages in 16 domain folders |
| Volume-plugin ↔ KMS contract | cds-volume-plugin/kms.go ↔ app/schemas/kms.py | machine contract (plugin README §KMS 契约); no Python test covers it |
| API error contract | docs/error-codes.md | {code,message,detail,request_id}, ~55 codes; parsed by test_capability_matrix_consistency (API↔FE↔SDK↔doc lock) |
| Mock/simulation registry | docs/mock-remediation-plan.md | MR-A1..A15, honest-degradation rules |
| Freshest state + open-defect ledger | docs/cr-2026-10-15-review.md | 187 L; §5 = remote-only test rule |
| Deploy | root compose ×4, k8s/, helm/, deploy/e2e-remote, deploy/local | seven config copies — see UNIQUE STYLES |
| CI gates | .github/workflows/ci.yml | backend py3.11; 34-file gate list green; full suite informational |

## CODE MAP
No LSP index exists in this workspace; entries come from the writer digests and ref counts are grep-based, not LSP-counted.

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| app.main:app | FastAPI app | app/main.py | compose/k8s/helm entries | boot wiring; prod forbids create_all |
| core.database / core.config | modules | app/core/ | 105 / 101 importers | DB session factory + Settings (highest fan-in in backend) |
| audit_service.log | call | app/services/audit_service.py | 52 importers; 140 call sites in app/api | single audit funnel |
| get_settings() | factory | app/core/config.py | backend-wide | lru_cached; tests must cache_clear |
| SM4Cipher | class | app/utils/crypto.py | 37 importers | AES-256-GCM labeled SM4 in dev/test |
| pinned_cube_version() | fn | app/services/cube_runtime_adapter.py | cube tests | reads `app/_stubs/cube/_VERSION.json` (9b75d339 / go-sdk-v0.7.3) |
| CDSClient / CDSError / CDSApiError | exports | sdk/cds_sdk/client.py | all SDK consumers | `__all__`; retries only 429/503 via Retry-After — 410 SESSION_TERMINATED is terminal and deliberately NOT retried (W2); v0.3.0 adds the SS-15 flow surface (40 ops) |
| mpc_service | service | app/services/ | API | Shamir key custodianship, NOT MPC computation |
| flowApi / flowKeys | module | cds-frontend/src/services/flow.ts | 21 pages | SS-15 flow surface, ~35 hooks |
| k8sOpsApi | module | cds-frontend/src/services/ | pages | the one axios deviation |

## CONVENTIONS
- Env config: pydantic BaseSettings, `env_prefix="CDS_"`, `.env`; `get_settings()` is lru_cached — tests must `cache_clear`, but prod-mode tests instead monkeypatch the cached `Settings` and `delenv` TESTING/PYTEST_CURRENT_TEST (only test_k8s_n7.py uses cache_clear).
- API errors follow the W2 contract {code, message, detail, request_id}; `SESSION_TERMINATED`=410 must stop client retries (consumed by cds_sdk).
- Alembic chain is linear and additive-only (0000→0021; 0019–0021 are the SS-15 revisions): the 0000 baseline deliberately omits columns added by 0001/0002; new columns go in new revisions; booleans use `text("false")`, not native bool defaults.
- `tests/conftest.py` (472 L) pins 12 env vars via `os.environ.setdefault` BEFORE importing app.main; never import the app ahead of conftest.
- KMS wire fields are a cross-language contract: `app/schemas/kms.py` and `cds-volume-plugin/kms.go` must move together (test_kms_volume_keys.py pins the Go field names).
- Mixed Chinese/English docstrings, user-facing strings, and requirements.txt comments (国密) — keep the mix.
- Spec tags in code (W1–W16, N3–N10, T1–T12, PZ-##, MR-A##, FG-###, Gap A/B/E, G-###) reference ledgers in docs/ and specs/.
- Frontend lockfile is package-lock.json — npm, not bun.

## ANTI-PATTERNS (THIS PROJECT)
- Never run the authoritative suite locally: local venv is py3.14, CI backend is py3.11, the production image is py3.12. Gate and full-suite numbers come from the REMOTE py3.12 production-derived image + real PG16 (2026-10-15 baseline 2864 passed / 24 failed / 5 skipped; gate 231/0/2).
- Never edit alembic history; never run `create_all` in production (branch in app/main.py).
- Never present simulated paths as defects or delete them: tee_simulator, gpu_tee_simulator, firecracker sim mode, test_data_generator are intentional features, surfaced at boot (T12).
- Never fake TEE/GPU/HSM/chain hardware (禁伪造): simulated paths must disclose via `ALLOW_SIMULATION` / `is_simulation` (项目铁律 honest degradation).
- Never mask CI failures by raising pytest-timeout; the `ci/known-failures.md` baseline "must not grow".
- Never purge audit logs or Merkle leaves (retention 0 = forever; export-and-archive instead).
- Never degrade isolation instead of refusing: per SS-13, an L2' request with no MicroVM (±TEE) returns 400 `RUNTIME_UNAVAILABLE` — never falls back to process/bwrap isolation.
- Never hand-edit `app/_stubs/cube/`: tests/test_cube_upstream_check.py hashes the stub tree against `_VERSION.json.stub_hash`.
- Never assume `mpc_service` does cross-party ciphertext compute — it is Shamir key custodianship only.

## UNIQUE STYLES
- Seven deploy config copies, not five: the 4 root docker-compose variants, k8s/, helm/, plus `deploy/e2e-remote/remote_deploy.sh` (scripted `docker run` mirroring root compose 1:1) and `deploy/local/local_deploy.sh` — any infra change lands in up to 7 files.
- Two vendored do-not-edit crypto trees: `Tongsuo/` and `cds-volume-plugin/tongsuo-build/`.
- Only ci.yml runs on push/PR; cube-node-smoke.yml and cube-upstream-watch.yml are workflow_dispatch-only, so upstream-pin drift is human-review by design.
- Canonical ClickHouse schema is scripts/clickhouse-init.sql (`cds_audit.audit_events` ~40 cols, MergeTree, 7-year TTL); k8s/configmaps.yaml carries a drifted 8-col copy.
- OPA anchor: policies/system.rego (`package cds.system`, `default allow = false`); runtime policy is pushed by the app.
- On-chain anchoring: contracts/AuditRegistry.sol (Solidity 0.8) stores SM3 Merkle roots and rejects duplicates.

## COMMANDS
```bash
# backend (no Makefile)
pip install -r requirements.txt && pip install -e ./sdk   # CI installs the SDK editable
uvicorn app.main:app                # :8000; Dockerfile.api: python:3.12-slim, 2-stage
python -m compileall -q app tests alembic
python scripts/verify_schema_parity.py   # alembic vs create_all parity, CI gate
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" alembic upgrade head && ALEMBIC_COMPARE_TYPES=0 alembic check
python -m pytest                    # asyncio_mode=auto, testpaths=tests, timeout=600, 3 files mark `slow`
                                    # NOT authoritative locally — real numbers come from the remote py3.12 image
python -m pytest tests/test_capability_matrix_consistency.py tests/test_pz04_honesty_gates.py  # doc↔code gates

# frontend
cd cds-frontend && npm run dev      # :3000, proxies /api → :8000
cd cds-frontend && npm run build    # tsc -b && vite build (all the CI frontend job runs)
cd cds-frontend && npm run lint     # and: npm test (vitest — NOT in CI)

# volume plugin (Go 1.25 + C, needs Tongsuo; not wired into ci.yml)
cd cds-volume-plugin && make local && make test

# infra
docker compose up                   # postgres:16, redis:7, clickhouse 24.3, minio, opa, vault
bash -n scripts/install-k3s.sh k8s/deploy.sh        # release-gate §4
CDS_K3S_MODE=k3s bash scripts/install-k3s.sh && bash scripts/e2e-k8s.sh
helm dependency build ./helm/cds    # required before helm template/install
bash deploy/cube-node/preflight.sh  # then install.sh, then verify.sh --json (exit 0 = node acceptance)
```

## NOTES
- Crypto honesty: SM2/SM3/SM4 (GM/T) is the design, but Tongsuo/ is vendored and NOT wired — backend SM4-GCM is actually AES-256-GCM and sm3() silently falls back to SHA-256 without gmssl (gated by CDS_SM3_FALLBACK_ALLOWED since PZ-04/MR-A14); SM3 is implemented 4× (utils/crypto, services/crypto_service, gateway_service, secure_duckdb). Frontend does SM2/SM3 client-side via sm-crypto.
- S4 is CLOSED: firecracker seccomp retry is gated by SECCOMP_FALLBACK_ALLOWED exactly like sandbox_runtime (the two digests cite MR-A13 and MR-A6 for it — reconcile against docs/mock-remediation-plan.md).
- Prod compose knowingly ships software-simulation switches TRUE until real-hardware acceptance; the FG gate series now runs to FG-017 (Cube node bring-up); registry = docs/mock-remediation-plan.md (MR-A1..A15).
- Stale docs to distrust: ci/known-failures.md still says the gate tier is 24 files (ci.yml lists 34); sdk/cds_sdk/__init__.py `__version__` is 0.2.0 vs pyproject 0.3.0; specs/ has no ss-14 file (SS-14 = the remote-e2e round, docs/e2e-2026-10-13-remote/); docs naming a frontend `rag` service are dead.
- k8s/deploy.sh applies 10 of 14 manifests — kafka.yaml / debezium.yaml (CDC) are manual. The helm chart has no MinIO template, and `minio/minio:latest` is unpinned in compose/k8s.
- contracts/AuditRegistry.sol `anchorRoot` has no access control (43 L — anyone may anchor a root).
- Known infra drift: postgres user `cds` vs `cds_admin`; `CDS_LOG_JSON` true (prod compose) vs "false" (helm); 8-col vs 40-col audit_events init SQL.
- Committed dev artifacts: k8s/secrets.yaml (DEV-ONLY credentials — rotate in any real environment) and logs/cds-egress/access.jsonl (30 real session records).
- tests/test_e2e_full_lifecycle.py is permanently ignored in CI (hits a real cluster); dev/test DBs bootstrapped by create_all get `alembic stamp head`.
- Duplication: root product-design.md + CDS-product-design.md are parallel v1.0 product-design docs; docs/implementation-tasks.md / -v2.md / merged-task-plan.md are historical trackers, not live plans; 基础版数据沙箱原型/ (+ .zip) is the external prototype baseline, superseded by specs/prototype-v3.html.
- tests/e2e_full_test.py is never collected (name misses `test_*.py`); nothing imports test modules — tests/ is a leaf, and it is the only automated gate on cds-volume-plugin/ and deploy/cube-node/ code.
- Janitor runs dry-run until compliance review (docs/retention.md).
