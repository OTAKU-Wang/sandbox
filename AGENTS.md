# PROJECT KNOWLEDGE BASE

**Generated:** 2026-11-02
**Commit:** 41ceb30
**Branch:** feat/ss-11-productization

## OVERVIEW
CDS = Confidential Data Sandbox (密态沙箱) — confidential data-circulation infrastructure for trusted data spaces: FastAPI (Python 3.12) backend, React 19 + Vite 8 admin frontend, standalone Python SDK, GM/T national crypto (SM2/SM3/SM4/TLCP), and a Go+C encrypted-volume plugin; deployed via docker compose, k3s manifests, and a Helm chart.

## STRUCTURE
```
sandbox/
├── app/                # FastAPI backend :8000; main.py 721 L boot wiring; core/ api/ services/ models/ schemas/ utils/ _stubs/ templates/ static/
├── cds-frontend/       # React 19 + Vite admin console (dev :3000 proxies /api → :8000); 255 ts/tsx under src/ (48k LOC)
│   └── node_modules/   # vendored deps — DO NOT EDIT
├── cds-volume-plugin/  # Go 1.25 + C `cds-encrypted` volume plugin; unix socket ↔ cube_runtime_adapter
│   └── tongsuo-build/  # second vendored Tongsuo — DO NOT EDIT
├── deploy/             # cube-node host bring-up + e2e-remote / local deploy harnesses
├── sdk/                # cds_sdk, standalone sync httpx REST client (/api/v1)
├── alembic/            # migrations, single linear additive-only chain (35 revisions, tip 0034)
├── tests/              # flat 497 test_*.py + conftest / ~95k LOC; two-tier CI (locked 40-file gate + budgeted full suite)
├── k8s/                # k3s manifest path (deploy.sh applies 10 of 13; NodePort 30080/30081)
├── helm/               # cds chart (helm/cds), production path (ns cds-system)
├── scripts/            # verify_schema_parity, clickhouse-init.sql, install-k3s, remote-harness/ lanes
├── docs/               # error-codes.md contract, ocr-review A### ledgers, mock-remediation, handoffs
├── specs/              # SS-01..SS-19 design contracts, 52 files / 30.8k LOC (no ss-14, no ss-17)
├── contracts/          # AuditRegistry.sol (Solidity 0.8, SM3 Merkle-root anchoring)
├── policies/           # OPA system.rego (5 LOC, default-deny anchor)
├── ci/                 # known-failures.md + full-suite budget baseline
└── Tongsuo/            # submodule Tongsuo 8.5 @ baa1bb2b, 27.7k files — DO NOT EDIT, never count as first-party
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Backend boot / middleware | app/main.py + app/AGENTS.md | 721 L, 47 `include_router`, ONE ASGI app (legacy /admin registers on the same app behind ADMIN_PAGES_ENABLED); 8 middlewares in load-bearing order; /health /metrics at root, not /api/v1 |
| Config / errors / authz deps / crypto honesty | app/core/ + app/utils/ (their AGENTS.md) | config.py 748 L / 163 Settings fields / 18 prod raises; errors.py = W2; scopes.py GROUP_PREFIXES machine authz, unmapped prefix = 403; utils SM4Cipher = labelled AES-256-GCM, sm3 fallback fail-closed |
| Security & sandbox core | app/services/ (its AGENTS.md) | flat 120 modules / 56k LOC; the 11-row domain table there is the layer's only navigation |
| Sandbox runtime selection | app/services/ runtimes, cube_runtime_adapter | prefixes `l0- tee- fc- bwrap- k8s- l2c-`; legacy adapters only behind SANDBOX_LEGACY_ENABLED (prod REJECTS it), l2c only with CUBE_DATAPLANE_ENABLED |
| API routers / AuthN split | app/api/ (its AGENTS.md) | 47 route modules / 21.2k L / 317 handlers; prefixes set only at mount; users of get_current_user 24, require_roles 22, get_business_user 13, none 5 |
| ORM + wire contracts | app/models/ + app/schemas/ (their AGENTS.md) | 44 modules / 105 classes (RefreshSession barrel); 55 pydantic models with zero-trust edge refusals; schemas/kms.py = Go wire contract |
| gRPC boundary | app/_stubs/ (its AGENTS.md) | cube/ hash-locked vs `_VERSION.json`; cdsvolume is the Python twin of the plugin proto — hand-authored docstrings, no hash guard |
| SDK entry | sdk/ (its AGENTS.md) | cds_sdk/client.py 1314 L; CDSClient 103 public methods (~40 SS-15 flow ops), CDSApiError; retries 429/503 only, 410 stops retry |
| Frontend API boundary | cds-frontend/src/services/ (its AGENTS.md) | 28 modules behind one axios client (api.ts); flow.ts 2422 L = the whole SS-15 surface |
| Frontend pages / chrome / roles | src/pages/, src/components/, src/utils/ (their AGENTS.md) | 42 lazy pages / 48 routes / 16 domain dirs; ROLE_GROUPS (utils/roles.ts) = route-authz truth; hotspot SessionDetail.tsx 1645 L |
| Test harness | tests/ (its AGENTS.md) | flat; conftest.py 651 L pins TESTING + 14 setdefault env vars BEFORE app.main import; one StaticPool SQLite; fake cubelet raises on any unstubbed RPC |
| Volume plugin | cds-volume-plugin/ (its AGENTS.md) | 12 prod .go / 3.1k L + 32 _test.go; NOT in ci.yml — `make test` is its only automated door |
| KMS wire contract | cds-volume-plugin/kms.go ↔ app/schemas/kms.py | served at app/api/kms.py:438+; tests/test_kms_volume_keys.py pins Go field names + Cipher.Name() strings |
| API error contract | docs/error-codes.md | 58 codes; four-way lock API↔FE enums↔SDK↔doc (test_capability_matrix_consistency) |
| Deploy config | root compose ×4, k8s/, helm/, deploy/{e2e-remote,local} (k8s/, helm/, deploy/ AGENTS.md) | seven machine-pinned copies (test_deploy_manifest_gates); node bring-up = deploy/cube-node/ (its AGENTS.md) |
| CI gates / budget | .github/workflows/ci.yml + ci/ (.github/AGENTS.md) | 40-file gate list machine-locked — cite ci.yml, never restate; full suite = continue-on-error + check_full_suite_budget.py |
| Migrations | alembic/ (its AGENTS.md) | ids >32 chars must sort after 0030 (PG VARCHAR(32) trap); parity script compares NAMES only — types/indexes via `alembic check` + PG16 lane |
| Ops scripts / test lanes | scripts/ (its AGENTS.md) | scripts/remote-harness/{sync_tree,source_lane,fe_lane}.sh = hardware gates ONLY (2026-10-19 ruling); api_dryrun_smoke.py read-only, exit 1 on RED |
| Freshest state / ledgers | docs/ (its AGENTS.md): docs/todo-plan-2026-10-30.md → docs/agent-handoff-2026-10-19.md; docs/ocr-review-2026-10-18/ (its AGENTS.md) | open work = specs/ss-18 §8 + ss-19 §10; A1..A319 defect ledger; mock registry = docs/mock-remediation-plan.md (MR-A1..A15) |
| Design contracts | specs/ (its AGENTS.md) | 52 files SS-01..SS-19; scripts/ cite ss-10 by SECTION NUMBER — never renumber |

## CODE MAP
No LSP index exists in this workspace; entries come from the writer digests and ref counts are grep-based (distinct importer files), not LSP-counted.

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| app.main:app | FastAPI app | app/main.py | alembic env, conftest, ~25 tests | boot wiring; prod forbids create_all; its `_`-prefixed helpers are de-facto public API pinned by boot-honesty tests |
| Settings / get_settings() | class / lru_cached factory | app/core/config.py | 153 files | widest fan-in in backend; 5 test modules cache_clear |
| database.get_db / CDSError | dep / exceptions | app/core/database.py, errors.py | get_db 49 app / 55 app+tests; CDSError 34 app / 62 app+tests | one async engine, request-scoped tx; W2 refusal contract (410 stops client retry, 503 Retry-After) |
| get_current_user / require_group | FastAPI deps | app/core/deps.py, scopes.py | 30 files / 46 mount sites | whole authN surface + SS-12 machine keys; unmapped prefix = default-deny 403 |
| audit_service.log | single funnel | app/services/audit_service.py | 68 files (54 non-test); 147+27 call sites in 33 api routers | SM-signed events → ClickHouse + PG; log_standalone survives get_db rollback |
| sandbox_runtime | facade / registry | app/services/sandbox_runtime.py (3389 L) | 39 files | adapter registry + SceneRuntime factory; SS-13 no-degrade 400 RUNTIME_UNAVAILABLE |
| sm3_hash / SM4Cipher | crypto | app/services/crypto_service.py, app/utils/crypto.py | sm3_hash 46 app / 72 app+tests; SM4Cipher 13 app / 22 app+tests | honesty gates; SM3 exists in 5 modules — gateway_service.py:82 is the only UNGATED SHA-256 fallback (open defect) |
| pinned_cube_version() | fn | app/services/cube_runtime_adapter.py | cube tests | reads `app/_stubs/cube/_VERSION.json` (9b75d339 / go-sdk-v0.7.3) |
| CDSClient / CDSApiError | exports | sdk/cds_sdk/client.py | 26 test modules import cds_sdk | `__all__`; retries only 429/503, Retry-After clamped [0,60]s; 410 SESSION_TERMINATED terminal (W2) |
| api / flowApi / ROLE_GROUPS | FE core | cds-frontend/src/services/api.ts, flow.ts, utils/roles.ts | 69 / 40 / 12 files | single HTTP client (401 single-flight refresh); SS-15 surface; 18 route-gate groups |
| client / db_session fixtures | pytest | tests/conftest.py | 205 / 171 files | app-under-test + the one shared SQLite connection (why everything stays on one loop) |
| KMSClient / CryptoPosture | Go | cds-volume-plugin/kms.go, main.go | 100 files reference the plugin | HTTP DEK custody (kms.py wire twin); machine-readable GM disclosure at boot |
| mpc_service | service | app/services/ | API | Shamir key custodianship, NOT MPC computation |

## CONVENTIONS
- Env config: pydantic BaseSettings, `env_prefix="CDS_"`, `.env`; `get_settings()` is lru_cached — 5 test modules call `cache_clear`, prod-posture tests instead monkeypatch the cached `Settings` and `delenv` TESTING/PYTEST_CURRENT_TEST.
- A new `CDS_*` key must land in five places: app/core/config.py, .env.example, both root composes, helm/cds/values.yaml, tests/test_config.py.
- API errors follow the W2 contract {code, message, detail, request_id}; `SESSION_TERMINATED`=410 must stop client retries (consumed by cds_sdk); HTTPException never appears in app/services.
- Alembic chain is linear and additive-only; never pin the tip in docs (0034 as of 2026-11-02); ids >32 chars may only sort after 0030 (which carries the PG VARCHAR(128) ALTER); the 0000 baseline omits columns added by 0001/0002; booleans use `text("false")`.
- `tests/conftest.py` (651 L) sets TESTING plus 14 `os.environ.setdefault` pins BEFORE importing app.main; never import the app ahead of conftest.
- KMS wire fields are a cross-language contract: `app/schemas/kms.py` and `cds-volume-plugin/kms.go` must move together (test_kms_volume_keys.py pins Go field names + Cipher.Name() strings).
- Cross-language byte parity: FE `contractSignPayload` ≡ `crypto_service.contract_sign_data` ≡ SDK `sign_data`, shared vectors in test_ocr_a255_xlang_parity — change all three together.
- Mixed Chinese/English docstrings and comments (国密) — keep the mix; spec tags (W/N/T/PZ/MR-A/FG/A###/SS-##) reference ledgers in docs/ (A### = docs/ocr-review-2026-10-18/).
- Frontend lockfile is package-lock.json — npm, never `bun install`.

## ANTI-PATTERNS (THIS PROJECT)
- Never restate "tests must run remotely": the 2026-10-19 user ruling (docs/agent-handoff-2026-10-19.md §1) makes LOCAL authoritative (local venv py3.14 + SQLite/fakeredis daily, or local py3.12 image + real PG16); the remote lanes are for hardware gates only. CI is py3.11, prod py3.12 — green CI never substitutes for a local authoritative run.
- Never edit alembic history; never run `create_all` in production (branch in app/main.py).
- Never present simulated paths as defects or delete them: tee_simulator, gpu_tee_simulator, firecracker sim mode, test_data_generator are intentional features, surfaced at boot (T12).
- Never fake TEE/GPU/HSM/chain hardware (禁伪造): disclose via `ALLOW_SIMULATION` / `is_simulation` / `crypto_posture` — honest-degradation 铁律, machine-enforced (boot raises + fail-closed crypto).
- Never mask CI failures by raising pytest-timeout; `ci/known-failures.md` baseline must not grow; never add bare `|| true` — full suite uses `continue-on-error` + ci/check_full_suite_budget.py.
- Never purge audit logs or Merkle leaves (retention 0 = forever; export-and-archive instead).
- Never degrade isolation instead of refusing: per SS-13, an L2' request with no MicroVM (±TEE) returns 400 `RUNTIME_UNAVAILABLE` — never falls back to process/bwrap isolation.
- Never hand-edit `app/_stubs/cube/` (stub_hash gate); never regenerate `app/_stubs/cdsvolume` losing its hand-authored contract docstrings — no protoc target exists, regen is manual.
- Never quote a hardcoded gate-file count — parse ci.yml (test_ci_workflow_policy locks 40 files, 0 test_ocr_* among them).
- Never assume `mpc_service` does cross-party ciphertext compute — it is Shamir key custodianship only.

## UNIQUE STYLES
- Seven deploy config copies, machine-pinned by test_deploy_manifest_gates: the 4 root docker-compose variants, k8s/, helm/, plus `deploy/e2e-remote/remote_deploy.sh` (scripted `docker run` mirroring root compose 1:1; `deploy/local/local_deploy.sh` is its twin) — any infra change lands in up to 7 files.
- Two vendored do-not-edit crypto trees: `Tongsuo/` (submodule) and `cds-volume-plugin/tongsuo-build/`.
- Only ci.yml runs on push/PR; cube-node-smoke.yml and cube-upstream-watch.yml are dispatch/cron-only — tests/test_cube_node_* are the ONLY automatic gate over deploy/cube-node/.
- tests/ is a leaf (nothing imports it) yet is the only automatic gate over cds-volume-plugin/, deploy/cube-node/, helm/k8s manifests and the FE↔API path contract.
- CubeSandbox compute nodes never enter `cds-system`: k8s/ and helm/ must not grow a Cube workload — nodes are bare-metal bring-ups from deploy/cube-node/ (runbook 红线 2).
- Canonical ClickHouse schema is scripts/clickhouse-init.sql (MergeTree, 7-year TTL); the k8s/configmaps.yaml copy is now parity-gated (former drift closed).
- OPA anchor: policies/system.rego (`package cds.system`, `default allow = false`) is a static anchor ONLY — runtime policy is pushed by the app; OPA data API is unauthenticated (A284), so 8181 stays loopback-only.
- On-chain anchoring: contracts/AuditRegistry.sol (Solidity 0.8) stores SM3 Merkle roots with an anchorer allowlist; no solc in CI — invariants are source-regex-locked (test_ocr_a195), so a Solidity refactor must keep the exact shapes.

## COMMANDS
```bash
# backend (no Makefile; CI py3.11, image py3.12, local venv py3.14)
pip install -r requirements.txt && pip install -e ./sdk
uvicorn app.main:app                # :8000; Dockerfile.api: python:3.12-slim, 2-stage
python -c "import app.main" && python -m compileall -q app tests alembic   # import smoke + CI compile gate
python scripts/verify_schema_parity.py   # NAMES-only parity gate; type/index drift = alembic check + PG16 lane
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" .venv/bin/python -m alembic upgrade head && ALEMBIC_COMPARE_TYPES=0 .venv/bin/python -m alembic check
# 必须走 .venv 里的 alembic：PATH 上的 ~/.local/bin/alembic 用系统 python（protobuf 7.35.0），与仓内
# gencode 7.35.1 冲突，会在 import app._stubs 时抛 Protobuf VersionError 假失败。
python -m pytest                    # authoritative = LOCAL (2026-10-19): strip proxy vars in the test subprocess ONLY;
                                    # parallel pytest runs need one SQLite file each; 8 files mark `slow`
python -m pytest tests/test_capability_matrix_consistency.py tests/test_pz04_honesty_gates.py  # doc↔code gates

# frontend — CI job runs EXACTLY: npm ci → npm test → npm run build → npm run lint (lint is a hard gate)
cd cds-frontend && npm ci && npm test && npm run build && npm run lint   # build = tsc -b && vite build; tests sit outside the tsc graph
cd cds-frontend && npm run dev      # :3000, proxies /api → :8000 (needs ws: true; also proxies /open)

# volume plugin (Go 1.25 + C, needs out-of-tree Tongsuo build; not wired into ci.yml)
cd cds-volume-plugin && make local && make test   # bare `go test ./...` fails by design (GM negative paths)

# infra
docker compose up                   # postgres:16, redis:7, clickhouse 24.3, minio (pinned RELEASE), opa, vault
bash -n scripts/install-k3s.sh k8s/deploy.sh && CDS_K3S_MODE=k3s bash scripts/install-k3s.sh
helm dependency build ./helm/cds    # required before helm template/install
bash deploy/cube-node/preflight.sh  # then install.sh, then verify.sh --json (exit 0 = node acceptance)
```

## NOTES
- Crypto honesty: SM2/SM3/SM4 (GM/T) is the design, but Tongsuo/ is vendored and NOT wired into the backend — SM4-GCM is actually AES-256-GCM and sm3() falls back to SHA-256 (gated CDS_SM3_FALLBACK_ALLOWED since PZ-04/MR-A14). SM3 exists in 5 modules; gateway_service.py:82 is the only ungated, undisclosed fallback (open defect). The volume plugin DOES use Tongsuo: prod must set `CDS_SM4_AEAD_BACKEND=tongsuo` (default aes_fallback = disclosed degrade M-01). FE does SM2/SM3 client-side via sm-crypto.
- Honest degradation is machine-readable everywhere: 18 boot raises in core/config, fail-closed utils/crypto, plugin `crypto_posture=` boot line (deploy gates read it, never WARNING text), migration 0034 defaults integrity_status='uncheckable' not 'ok'.
- Sandbox guest path contract has ONE source: `GUEST_DATA_DIR` (readonly declaration) / `GUEST_OUTPUT_DIR` (only harvested) / `GUEST_WORK_DIR` (compat) / `GUEST_EXEC_DIR` (never) in `app/services/cube_runtime_adapter.py`; "readonly" = declaration + harvest exclusion, NOT hypervisor enforcement — never claim stronger; a path change re-pins literal mount assertions in tests/test_cube_runtime_adapter.py.
- Unpinned drift risks (no test catches either): FE `src/pages/Flow/directoryContract.ts` ↔ cube_runtime_adapter GUEST_* paths; FE `ApiKeys.tsx` SCOPE_GROUPS ↔ core/scopes.py GROUP_PREFIXES.
- Stale docs to distrust: never quote a hardcoded gate count (parse ci.yml); SDK version single-sourced from sdk/pyproject.toml (A78); specs/ has no ss-14 or ss-17 file; a frontend `rag` service is dead; `CDS-product-design.md` (v1.0) is deprecated by `product-design.md` (v3.0); root `.scratch-*`/`.gate-files-tonight.txt` are tracked junk; docs/cr-2026-10-15-review.md is two rounds stale; README claims ("zero mock code", "no frontend") are wrong.
- k8s/deploy.sh applies 10 of 13 — kafka.yaml/debezium.yaml (CDC) deliberately manual (a machine gate forbids re-adding them); it and the other deploy scripts `|| true` every wait and signal done with a trailing sentinel token (DEPLOY_DONE / LANE_DONE / …) — a printed "Deployed" ≠ healthy. MinIO pinned `RELEASE.2025-01-20T14-49-07Z` across 7 sites, `:latest` banned.
- Infra drift CLOSED 2026-10-30 (gated): PG user `cds`, `CDS_LOG_JSON="true"` across prod copies, k8s/secrets.yaml = `<REPLACE_ME_*>` template (a gate reds on old literals) — but git HISTORY still holds the old dev literals: rotate anywhere ever deployed from this branch; history rewrite = lead decision.
- AuditRegistry access control fixed 2026-10-20 (A195): `anchorAudit`/`verifyAudit`/`authorizedAnchors` — old `anchorRoot` gone; real-chain revert still HARDWARE-GATED (no solc/web3 locally), gate = source-regex assertions.
- Janitor: real-delete landed 2026-10-30 behind a hardcoded whitelist (alert_records / session_workspace_residue / dev_debug_artifacts only); audit_logs, merkle_leaves, delivery_bundles, data_resources refused at code level regardless of config; still `CDS_JANITOR_ENABLED="false"` in every deploy copy pending retention.md sign-off.
- Duplication / dead weight: docs/implementation-tasks.md / -v2.md / merged-task-plan.md are historical trackers, not live plans; 基础版数据沙箱原型/ (+ .zip) is superseded by specs/prototype-v3.html; app/templates + app/static are the legacy /admin surface but test-pinned (not deletable); FE src/utils/cn.ts + components/Common + public/icons.svg + README are dead scaffold; cds-volume-plugin bin/, tongsuo-build/, sm4gcm/sm4_gcm* are gitignored artifacts.
