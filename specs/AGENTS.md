# specs/ — design contracts, SS-01..SS-15 + product/deployment docs (scored 8: 47 docs + 2 HTML mocks, ~29k LOC, flat; referenced by backend, frontend, infra, tests and CI)

Chinese-first prose, English identifiers. These are the interfaces `app/` and `cds-frontend/` implement. Specs are claims — verify against code before trusting (several are stale).

## WHERE TO LOOK
| Question | File |
|----------|------|
| Master spec (TC609-6-2025-01 / GM/T aligned) | `tech-spec.md` (1137) |
| Deep tech detail: key hierarchy, schema, error codes 1xxx-9xxx | `detailed-tech-spec.md` + `detailed-tech-spec-supplement.md` (§11-24) |
| SS-01 KMS/identity (SM2 certs, DEK, attestation) | `ss-01-kms-identity.md` |
| SS-02 contract/policy (SandboxMode 6 core modes + `contract.policy_params`, OPA/Rego default-deny PDP) | `ss-02-contract-policy.md`, `policy-compiler-spec.md` |
| SS-03 scheduler / code pre-scan (FORBIDDEN_IMPORTS) | `SS-03.md`, `ss-03-task-scheduler.md` |
| SS-04 sandbox runtime (per-scene runtimes, L1/L2/L3, GPU-TEE, adapter contract) | `ss-04-sandbox-runtime.md` (1178), `sandbox-adapters-spec.md` (RuntimeAdapter ABC) |
| SS-05 output gateway (6 inspection stages, forbidden mimes) | `SS-05.md`, `ss-05-output-gateway.md`, `secure-output-lifecycle.md` |
| SS-06 audit + SS-08 interconnect (SM3 Merkle, chain anchoring) | `SS-06.md`, `ss-06-audit-ss-08-interconnect.md`, `blockchain-integration-spec.md` |
| SS-07 AI training (DP-SGD, memorization suite, checkpoints) | `ss-07-ai-training-pipeline.md` |
| SS-09 structured DB storage (SELECT-only rewriter rules) | `ss-09-structured-db-storage.md`, `structured-data-storage-security.md` (v2: retires the per-session temp PostgreSQL) |
| Anti-exfiltration (脱库) threat model + SQL guards, advanced scenes, model/output lifecycle | `data-security-spec.md` (910), `advanced-scenarios-spec.md`, `secure-output-lifecycle.md` |
| SS-10 Cube dataplane connector (零 diff CubeSandbox 节点数据面直驱, L2' MicroVM, CB-01..11) | `ss-10-cube-dataplane-connector.md` |
| SS-11 productization master plan PZ-01..21 (honesty gates, capability matrix, `SimulationBadge`) | `ss-11-cds-productization.md` (409) — current forward plan; §九 执行记录 is the ledger of what "done" means |
| SS-12 platform agent API keys (`cds_ak_` + 43 base64url, scope `<group>:<verb>`, keys denied on human-only routes) | `ss-12-agent-api-keys.md` — §十 pins per-lane write-sets + VERIFY gates |
| SS-13 runtime 归一: MicroVM single path, TEE optional (`sandbox_level=l2c` default, `tee_policy`, `CDS_SANDBOX_LEGACY_ENABLED`, `TEE_NODE_UNAVAILABLE`) | `ss-13-microvm-single-path.md` (161) — retires the L0/L1/L2/L3 fallback tiers `product-spec.md` §9 still specs |
| SS-15 数据流通闭环: catalog→contract→online IDE→test/prod sandbox→3 delivery channels (migrations 0019-0021) | `ss-15-flow-orchestration.md` (371, newest, untracked) — 16 numbered design decisions, densest file here |
| SS-14 — has NO spec file (it was the remote-e2e round) | record: `docs/e2e-2026-10-13-remote/` |
| Developer portal / workbench (v2.0 "AI Agent 开发手册": Deprecation Matrix, WBS, page IDs 2-1..4-3) | `data-sandbox-developer-portal-prd.md`, `developer-portal-implementation-plan.md`, `frontend-spec.md` v2.0, `prototype-v3.html` (1911) |
| DDD decomposition + full PG/ClickHouse schema | `ddd-implementation-spec.md` (1536) |
| Product / roles / frontend | `product-spec.md` v1.1, `frontend-spec.md` v2.0, `frontend-roles-design.md`, `frontend-implementation-guide.md` (stale pins), `prototype.html` / `prototype-v3.html` (visual mocks only) |
| Gap tracking G-001..G-141, FG-001..017, round records | `SS-gap-analysis.md` (1738: G-001..G-141 + FG + ~40 Round records), `productization-gap-analysis.md`, `sandbox-productization-round3-spec.md` (N1-N16; superseded as forward plan by `ss-11-cds-productization.md`) |
| Release Go/No-Go | `release-gate-checklist.md`, `product-security-release-review.md` (§6 禁止对外口径: forbidden external claims) |
| Deployment modes (compose / 243 K3s / helm) | `productization-deployment-runbook.md` (canonical), `deployment-spec.md` |

## CONVENTIONS
- A spec is a ledger, not just design: it ends with 执行记录/修复记录 appended per round with fixed columns (任务 / 状态 / 关键产出 / 新增测试与结果 / 回归结论 / 未实施项), backfilled from real runs — never write an anticipated number into one.
- Task-ID namespaces never collide (W-, N-, CB-, MR-, PZ-, T1-T12, G-/FG-, D-e2e-##, Round NN) and cross-references cite them instead of restating (引用不重述).
- 配置四同步: a new `CDS_*` key appears in `.env.example`, `docker-compose.yml`, `docker-compose.prod.yml`, `helm/cds/values.yaml` + `app/core/config.py` (+ `tests/test_config.py`) in the same change, fail-closed by default.
- Alembic stays linear and additive-only; after each revision run `scripts/verify_schema_parity.py` and `alembic check` on PG *and* SQLite. Never trust `--autogenerate`. (0016=SS-13, 0017=SS-12, 0019-0021=SS-15)
- Verification authority is the remote py3.12 harness, not this venv (user rule: 测试不在本地跑); local Python 3.14 runs are structurally blind to def-time annotation failures.
- Fail-closed is the default: default-deny PDP, quote-required verification, no silent placeholder returns.
- Honest degradation (项目铁律): simulated/fallback paths must `logger.warning` + disclose (`ALLOW_SIMULATION`, `is_simulation`); faking hardware (TEE/GPU/chain/HSM) is banned — 禁伪造.
- `CDS_*` env prefix everywhere; production must not rely on code defaults; `validate_security_config` surfaces every enabled fallback at startup.
- After implementation, code wins — update the spec to match (以代码为准并回写本文件).

## ANTI-PATTERNS / STALE SOURCES
- Four near-duplicate pairs drifted in **both** directions: `SS-03.md`/`ss-03-task-scheduler.md`, `SS-04.md`/`ss-04-sandbox-runtime.md` (lowercase adds a P1/P2/P3 delivery table, uppercase keeps the `exec()` + identifier-sanitisation hardening notes), `SS-05.md`/`ss-05-output-gateway.md` (lowercase has the 6-mode rule-set merge + safetensors/onnx), `SS-06.md`/`ss-06-…-interconnect.md`. Choose per topic; neither copy is canonical wholesale (for SS-06/SS-08 the 完整版 `ss-06-audit-ss-08-interconnect.md` is the fuller copy).
- `SS-06.md` is a hidden 3-document bundle (SS-06 + an SS-07 section + SS-08) — grepping SS-07/SS-08 finds it there too, so a patch can land in the wrong copy.
- `SS-gap-analysis.md` needs offset reads: §1 结论, §2 Active Gap, §2.1 FG table (FG-017 = Cube node bring-up), §3 设计裁剪, then ~40 round records.
- `ss-10` §1.4 铁律 binds execution agents: node dataplane zero diff; CDS is the only control plane; 禁伪造; 数据不出域; audit paths may never drop; upstream is the security baseline; zero local commits in the upstream repo. §7.8 bans any `enc`-class CLI (Tongsuo/OpenSSL) for AEAD.
- Never "complete" an FG (hardware/external-system acceptance) item in software, never re-implement a round3 §1.1 已产化基线 or §1.3 明确不做项 item (E2B SDK compat, envd in-VM agent, OCI template store, cross-node pause/resume), and never decide W6 key rotation — it is a 用户决策项.
- `prototype-v3.html` (1911) is untracked and is still only a visual mock; `distributed-sandbox-connector-spec.md` (Kong + Keycloak + Celery/RabbitMQ) and `frontend-implementation-guide.md` (zustand 4, TS 5.3, vite 5, crypto-js, WebCrypto-as-SM2) predate productization — the runbook and `cds-frontend/package.json` are the truth.
- Naming drift: uppercase `SS-NN.md` (legacy) vs lowercase `ss-NN-area.md` (current).
- `frontend-architecture-spec.md` is stale: says React 18/TS 5.3/zustand 4/WebCrypto-SM2; reality is React 19/TS ~6.0/zustand 5/sm-crypto (+Tailwind 4, @xterm, recharts).
- `detailed-tech-spec.md` v2.0 predates the productized repo (RabbitMQ/postgres:15) vs the runbook (Celery+Redis, postgres:16); `deployment-spec.md` and it define two divergent compose stacks.
- `phase2-iteration-plan.md` is superseded by `sandbox-productization-round3-spec.md`.
- `prototype.html` (2045 lines) is a visual mock with fake data — never a functional surface.
