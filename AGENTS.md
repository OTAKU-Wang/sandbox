# PROJECT KNOWLEDGE BASE

**Generated:** 2026-10-12
**Commit:** n/a (workspace is not a git repo)
**Branch:** n/a

## OVERVIEW
CDS = Confidential Data Sandbox (密态沙箱) — confidential data-circulation infrastructure for trusted data spaces: FastAPI (Python 3.12) backend, React 19 + Vite 8 admin frontend, standalone Python SDK, GM/T national crypto (SM2/SM3/SM4/TLCP), deployed via docker compose, k3s manifests, and a Helm chart.

## STRUCTURE
```
sandbox/
├── app/                # FastAPI backend; main.py boot wiring, :8000
├── cds-frontend/       # React 19 + Vite 8 admin console (dev :3000, proxies /api → :8000)
│   └── node_modules/   # vendored deps — DO NOT EDIT
├── sdk/                # cds_sdk, standalone sync httpx REST client (/api/v1)
├── alembic/            # migrations, single LINEAR chain 0000→0010, additive-only
├── tests/              # 175 pytest files, two-tier CI (gate list + informational full)
├── k8s/                # k3s manifest deploy path (deploy.sh, NodePort 30080/30081)
├── helm/               # cds chart (helm/cds), production path (ns cds-system)
├── scripts/            # verify_schema_parity.py, clickhouse-init.sql, install-k3s.sh
├── docs/               # error-codes.md contract, mock-remediation MR-A1..A15, retention
├── specs/              # SS-01..SS-09 design contracts
├── contracts/          # AuditRegistry.sol (Solidity 0.8, SM3 Merkle-root anchoring)
├── policies/           # OPA system.rego (default-deny anchor)
├── ci/                 # known-failures.md baseline
└── Tongsuo/            # vendored upstream Tongsuo 8.5 (OpenSSL fork, SM2/SM3/SM4, ~23k files) — DO NOT EDIT
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Backend boot / middleware / error contract | app/main.py | /health, /metrics at root (not /api/v1) |
| Security & sandbox core | app/services/ | 107 flat modules: runtimes, ML pipelines, GM crypto/KMS, output policy |
| API routers | app/api/ | 34 routers; prefixes only in main.py |
| Config / errors / deps | app/core/ | CDS_ env prefix, W2 error contract, prod safety gates |
| SDK entry | sdk/cds_sdk/client.py | 544 L; exports CDSClient, CDSError, CDSApiError |
| Frontend API boundary | cds-frontend/src/services/ | shared axios, single-flight 401 refresh |
| Frontend complexity concentration | cds-frontend/src/pages/SessionDetail.tsx | 1304 LOC, 26 lazy pages |
| API error contract | docs/error-codes.md | {code,message,detail,request_id}, 12 named codes |
| Agent handoff (env/lanes/pitfalls) | docs/agent-handoff-2026-10-16.md | read first on a new machine; open work = specs/ss-18-prototype-alignment.md §8 |
| Remote verification lanes | scripts/remote-harness/{sync_tree,source_lane,fe_lane}.sh | tests run on the remote harness (user rule); needs CDS_LANE_DB_URL; never drop the venv PATH export |
| Mock/simulation registry | docs/mock-remediation-plan.md | MR-A1..A15, honest-degradation rules |
| Deploy | root compose (4 variants), k8s/, helm/cds | five parallel config copies — see NOTES |
| CI gates | .github/workflows/ci.yml | gate list must be green; full suite informational |

## CODE MAP
No LSP index exists in this workspace; the entries below come from the init-deep digests (backend + platform) and were not reference-counted.

| Symbol | Type | Location | Refs | Role |
|--------|------|----------|------|------|
| app.main:app | FastAPI app | app/main.py | compose/k8s/helm entries | boot wiring; prod forbids create_all |
| CDSClient / CDSError / CDSApiError | exports | sdk/cds_sdk/client.py | all SDK consumers | `__all__`; retries only 429/410/503 via Retry-After |
| get_settings() | factory | app/core/config.py | backend-wide | lru_cached; tests must cache_clear |
| SM4Cipher | class | app/utils/crypto.py | crypto stack | AES-256-GCM labeled SM4 in dev/test |
| mpc_service | service | app/services/ | API | Shamir key custodianship, NOT MPC computation |
| SessionDetail.tsx | React page | cds-frontend/src/pages/ | route map | frontend complexity concentration |
| k8sOpsApi | module | cds-frontend/src/services/ | pages | the one axios deviation |

## CONVENTIONS
- Env config: pydantic BaseSettings, `env_prefix="CDS_"`, `.env`; `get_settings()` is lru_cached — tests must `cache_clear`.
- API errors follow the W2 contract {code, message, detail, request_id}; `SESSION_TERMINATED`=410 must stop client retries (consumed by cds_sdk).
- Alembic chain is linear and additive-only: 0000 baseline deliberately omits columns added by 0001/0002; new columns go in new revisions.
- Mixed Chinese/English docstrings, user-facing strings, and requirements.txt comments (国密) — keep the mix.
- Spec tags in code (W1–W16, N3–N10, T1–T12, Gap A/B/E, G-###, "Round 39/40/45") reference remediation docs in docs/.
- Frontend lockfile is package-lock.json — npm, not bun.

## ANTI-PATTERNS (THIS PROJECT)
- Never edit alembic history; never run `create_all` in production (branch in app/main.py).
- Never present simulated paths as defects or delete them: tee_simulator, gpu_tee_simulator, firecracker sim mode, test_data_generator are intentional features, surfaced at boot (T12).
- Never fake TEE/GPU/HSM/chain hardware (禁伪造): simulated paths must disclose via `ALLOW_SIMULATION` / `is_simulation` (项目铁律 honest degradation).
- Never mask CI failures by raising pytest-timeout; the `ci/known-failures.md` baseline "must not grow".
- Never purge audit logs or Merkle leaves (retention 0 = forever; export-and-archive instead).
- Never assume `mpc_service` does cross-party ciphertext compute — it is Shamir key custodianship only.

## UNIQUE STYLES
- Five parallel deploy paths carry their own config copies: root docker-compose.yml / .middleware.yml / .prod.yml / .blockchain.yml, k8s/, helm/ — any infra change lands in up to 5 files.
- Canonical ClickHouse schema is scripts/clickhouse-init.sql (`cds_audit.audit_events` ~40 cols, MergeTree, 7-year TTL); k8s/configmaps.yaml carries a drifted 8-col copy.
- OPA anchor: policies/system.rego (`package cds.system`, `default allow = false`); runtime policy is pushed by the app.
- On-chain anchoring: contracts/AuditRegistry.sol (Solidity 0.8) stores SM3 Merkle roots and rejects duplicates.

## COMMANDS
```bash
# backend (no Makefile)
pip install -r requirements.txt && pip install -e ./sdk
uvicorn app.main:app                # :8000; Dockerfile.api: python:3.12-slim, 2-stage
python -m pytest                    # asyncio_mode=auto, testpaths=tests, timeout=600, `slow` marker
python -m compileall -q app tests alembic
python scripts/verify_schema_parity.py   # alembic vs create_all parity, CI gate
CDS_DATABASE_URL="sqlite+aiosqlite:///./ci.db" alembic upgrade head && ALEMBIC_COMPARE_TYPES=0 alembic check

# frontend
cd cds-frontend && npm run dev      # :3000, proxies /api → :8000
cd cds-frontend && npm run build    # tsc -b && vite build
cd cds-frontend && npm run lint     # and: npm test

# infra
docker compose up                   # postgres:16, redis:7, clickhouse 24.3, minio, opa, vault
helm dependency build ./helm/cds    # required before helm install
```

## NOTES
- Crypto honesty: SM2/SM3/SM4 (GM/T) is the design, but Tongsuo/ is vendored and NOT wired — backend SM4-GCM is actually AES-256-GCM and sm3() silently falls back to SHA-256 without gmssl. Frontend does SM2/SM3 client-side via sm-crypto.
- Prod compose knowingly ships software-simulation switches TRUE until real-hardware acceptance (FG-001/FG-002); the mock/simulation registry is docs/mock-remediation-plan.md (MR-A1..A15).
- Sandbox guest path contract has ONE source of truth: `GUEST_DATA_DIR=/sandbox/data` (readonly declaration), `GUEST_OUTPUT_DIR=/sandbox/output` (the only harvested dir), `GUEST_WORK_DIR=/scratch` (compat-harvested), `GUEST_EXEC_DIR=/results/.cds-exec` (never harvested) in `app/services/cube_runtime_adapter.py`. Declared volumes do not reach the guest on cold start (data is injected over the exec channel), so "readonly" means declaration + harvest exclusion, NOT hypervisor enforcement — never claim stronger. Changing a path must also re-pin the literal mount assertions in `tests/test_cube_runtime_adapter.py`.
- Known infra drift: postgres user `cds` vs `cds_admin`; `CDS_LOG_JSON` true (prod compose) vs "false" (helm); 8-col vs 40-col audit_events init SQL.
- Known defect S4: firecracker seccomp retry is not gated by `SECCOMP_FALLBACK_ALLOWED` (sandbox_runtime is) — inconsistent isolation posture.
- Committed dev artifacts: k8s/secrets.yaml (DEV-ONLY credentials — rotate in any real environment) and logs/cds-egress/access.jsonl (30 real session records).
- tests/test_e2e_full_lifecycle.py is permanently ignored in CI (hits a real cluster); dev/test DBs bootstrapped by create_all get `alembic stamp head`.
- Duplication: root product-design.md + CDS-product-design.md are parallel v1.0 product-design docs; docs/implementation-tasks.md / -v2.md / merged-task-plan.md are historical trackers, not live plans.
- Janitor runs dry-run until compliance review (docs/retention.md).
