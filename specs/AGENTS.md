# specs/ — design contracts, SS-01..SS-09 + product/deployment docs (scored 8: 39 flat files, referenced by backend, frontend and infra)

Chinese-first prose, English identifiers. These are the interfaces `app/` and `cds-frontend/` implement. Specs are claims — verify against code before trusting (several are stale).

## WHERE TO LOOK
| Question | File |
|----------|------|
| Master spec (TC609-6-2025-01 / GM/T aligned) | `tech-spec.md` (1137) |
| Deep tech detail: key hierarchy, schema, error codes 1xxx-9xxx | `detailed-tech-spec.md` + `detailed-tech-spec-supplement.md` (§11-24) |
| SS-01 KMS/identity (SM2 certs, DEK, attestation) | `ss-01-kms-identity.md` |
| SS-02 contract/policy (SandboxMode 12 values, OPA/Rego default-deny PDP) | `ss-02-contract-policy.md`, `policy-compiler-spec.md` |
| SS-03 scheduler / code pre-scan (FORBIDDEN_IMPORTS) | `SS-03.md`, `ss-03-task-scheduler.md` |
| SS-04 sandbox runtime (per-scene runtimes, L1/L2/L3, GPU-TEE, adapter contract) | `ss-04-sandbox-runtime.md` (1178), `sandbox-adapters-spec.md` (RuntimeAdapter ABC) |
| SS-05 output gateway (6 inspection stages, forbidden mimes) | `SS-05.md`, `ss-05-output-gateway.md`, `secure-output-lifecycle.md` |
| SS-06 audit + SS-08 interconnect (SM3 Merkle, chain anchoring) | `SS-06.md`, `ss-06-audit-ss-08-interconnect.md`, `blockchain-integration-spec.md` |
| SS-07 AI training (DP-SGD, memorization suite, checkpoints) | `ss-07-ai-training-pipeline.md` |
| SS-09 structured DB storage (SELECT-only rewriter rules) | `ss-09-structured-db-storage.md`, `structured-data-storage-security.md` |
| SS-10 Cube dataplane connector (零 diff CubeSandbox 节点数据面直驱, L2' MicroVM, CB-01..11) | `ss-10-cube-dataplane-connector.md` |
| DDD decomposition + full PG/ClickHouse schema | `ddd-implementation-spec.md` (1536) |
| Product / roles / frontend | `product-spec.md`, `frontend-spec.md`, `frontend-roles-design.md`, `frontend-implementation-guide.md`, `prototype.html` (visual mock only) |
| Gap tracking G-001..G-141, FG-001..016, round records | `SS-gap-analysis.md` (1738), `productization-gap-analysis.md`, `sandbox-productization-round3-spec.md` (forward plan N1-N8) |
| Release Go/No-Go | `release-gate-checklist.md`, `product-security-release-review.md` (§6 禁止对外口径: forbidden external claims) |
| Deployment modes (compose / 243 K3s / helm) | `productization-deployment-runbook.md` (canonical), `deployment-spec.md` |

## CONVENTIONS
- Fail-closed is the default: default-deny PDP, quote-required verification, no silent placeholder returns.
- Honest degradation (项目铁律): simulated/fallback paths must `logger.warning` + disclose (`ALLOW_SIMULATION`, `is_simulation`); faking hardware (TEE/GPU/chain/HSM) is banned — 禁伪造.
- `CDS_*` env prefix everywhere; production must not rely on code defaults; `validate_security_config` surfaces every enabled fallback at startup.
- After implementation, code wins — update the spec to match (以代码为准并回写本文件).

## ANTI-PATTERNS / STALE SOURCES
- Two sources of truth for SS-06+SS-08: `SS-06.md` vs `ss-06-audit-ss-08-interconnect.md` (the 完整版) — prefer the full version.
- Naming drift: uppercase `SS-NN.md` (legacy) vs lowercase `ss-NN-area.md` (current).
- `frontend-architecture-spec.md` is stale: says React 18/TS 5.3/zustand 4/WebCrypto-SM2; reality is React 19/TS ~6.0/zustand 5/sm-crypto (+Tailwind 4, @xterm, recharts).
- `detailed-tech-spec.md` v2.0 predates the productized repo (RabbitMQ/postgres:15) vs the runbook (Celery+Redis, postgres:16); `deployment-spec.md` and it define two divergent compose stacks.
- `phase2-iteration-plan.md` is superseded by `sandbox-productization-round3-spec.md`.
- `prototype.html` (2045 lines) is a visual mock with fake data — never a functional surface.
