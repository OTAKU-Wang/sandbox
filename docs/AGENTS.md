# docs/ — remediation ledgers, the W2 error contract, June trackers, SS-14 e2e archive (scored 8: 26 files incl. archives; distinct domain — this is where "what was actually fixed" is recorded, and part of it is machine-consumed)

`specs/` says what should be built; `docs/` records what was done and which contract tests pin it. Two very different kinds of file live here — check which one you are reading before quoting it.

## WHERE TO LOOK — live contracts
| Task | File |
|------|------|
| API error envelope + code catalogue (~55 codes, `{code,message,detail,request_id}`) | `error-codes.md` (86) — parsed by `tests/test_capability_matrix_consistency.py`, one of four locked sources (API `capability_matrix.py` ↔ FE `enums.ts` ↔ SDK ↔ this doc) |
| Retention / janitor policy (audit logs + Merkle leaves = `0` = forever) | `retention.md` (29) |
| SS-10 policy package → `CubeNetworkConfig` proto field mapping | `cube-egress-mapping.md` (163) — file:line anchors, proto fields verified by introspecting `app/_stubs` |
| Freshest state of the repo + open defects | `cr-2026-10-15-review.md` (187) — §4 = live not-yet-fixed ledger, §5 = remote-verification evidence |
| Developer-portal quick index | `developer-sandbox-spec.md` (23) — mirror only; authority is `specs/data-sandbox-developer-portal-prd.md` + `specs/ss-15-flow-orchestration.md` |

## WHERE TO LOOK — execution plans with appended 执行记录
| Task | File |
|------|------|
| Mock/simulation registry (S1-S4 classes, MR-A1..A15) | `mock-remediation-plan.md` (472) — cited by `tests/test_pz04_honesty_gates.py` (MR-A9/A12/A13/A14) |
| CubeSandbox parity W1..W14 + non-alignment 不做项 | `cubesandbox-parity-plan.md` (788) |
| AI-sandbox gap T1..T12 over the A-F baseline | `ai-sandbox-gap-remediation-plan.md` (533), `ai-sandbox-gap-review-20260906.md` (133) |
| SS-14 remote e2e record (deploy → business loop → sandbox security → TDS) | `e2e-2026-10-13-remote/` (README index + 01..05 + `evidence-node/`) |

## ARCHAEOLOGY — never plan from these
`spec-implementation-analysis.md` (920), `implementation-tasks.md` (874), `implementation-tasks-v2.md` (756), `merged-task-plan.md` (194), `task-status-comparison.md` (117), `industry-comparison-analysis.md` (85): June-2026 snapshots (~55% / ~70% implemented) that re-tally the same P0/P1/P2 universe at different dates, with named owners and strikethrough status. The forward plan is `specs/ss-11-cds-productization.md`.

## CONVENTIONS
- Header blockquote metadata per doc (日期 / 基线 / 性质 / 范围, plus 前序 / 编号 for plans). Plan template: 基线复核 → P0/P1/P2 tasks with file:line anchors → 横切面 (开关矩阵 / Alembic 清单 / 测试计划 / 发布顺序 / 风险表) → 执行边界声明 → appended `Round NN 执行记录`.
- Round numbering is global across documents: R32-41 gap-remediation → R42-44 parity → R45-46 mock/productization → the 2026-10-15 CR. Reading order follows that chain.
- ID namespaces per document: A-F (gap review), T1-T12 (remediation), W1-W14 (parity), M-01..M-28 → MR-A1..A15 (mock); cross-refs use G-/FG-/PZ-/SS-/CB-.
- Code wins: 实施时若与现状冲突，以代码为准并回写本文件.

## ANTI-PATTERNS
- `error-codes.md` is a test input: changing the 级别能力 table or the code list can break `test_capability_matrix_consistency.py`. `SESSION_TERMINATED` (410) must stay non-retryable — `cds_sdk` stops retrying on it.
- Do not delete honest simulated paths. mock-remediation classes them: S1 synthetic-data features stay, S2 honest fallbacks stay but need a real prod backend, S3 hardware-gated simulation is allowed; only S4 (fake implementations, idle gates, orphans) is a defect to fix.
- `mock-remediation-plan.md` MR-A1 step 2 (`tongsuo enc -sm4-gcm` CLI) is 作废 — superseded by SS-10 CB-03's libtongsuo C helper; don't re-plan it.
- Never put a remote host address or any password in repo markdown (the SS-14 archive states this explicitly); those values live in `/root/cds.env` / `/tmp/cds-local.env`.
- `docs/e2e/01-deploy.md` (42) is a shorter near-duplicate of `e2e-2026-10-13-remote/01-deployment.md` (57) — read the archive copy.
- Where these documents disagree with the root `AGENTS.md`, they are usually the fresher side (alembic head, test-file count, "12 named codes" vs ~55, defect S4 which is already closed).
- Docs have no build/lint of their own; the code they describe is gated by:
```bash
python -m pytest tests/test_capability_matrix_consistency.py tests/test_pz04_honesty_gates.py -q
```
