# docs/ — remediation ledgers, the W2 error contract, OCR defect ledger, June trackers, e2e archives (scored 11: 419 tracked files / 89 `.md` — both counts include the 2 `AGENTS.md` this round added (`git ls-files docs` = 419) — / 6 subdirs / 32,251 md-LOC over those 89 (`git ls-files 'docs/*.md' | xargs wc -l`; 32,159 once you drop the 2 `AGENTS.md`) / 24,401 of that md-LOC sits in `ocr-review-2026-10-18/`, whose 324 non-md evidence files take the same dir to 106,583 LOC all-file; distinct domain — this is where "what was actually fixed" is recorded, and part of it is a test input)

`specs/` says what should be built; `docs/` records what was done and which contract tests pin it. Two very different kinds of file live here — check which one you are reading before quoting it. 23 top-level `.md` (22 content + this file) carry the live contracts and plans; the rest is archive (`.log`/`.txt`) plus the OCR round, which has its own `ocr-review-2026-10-18/AGENTS.md`.

## WHERE TO LOOK — live contracts
| Task | File |
|------|------|
| API error envelope + code catalogue (**58 codes in the code table**; counting rule: table rows whose first cell is a backticked ALL_CAPS token, `grep -cE '^\| *`[A-Z_]{4,}`' docs/error-codes.md` → 58 rows, 58 distinct, = 51 in the 错误码清单 table L13-70 + 7 appended under 级别能力 L97-103; that 清单 has 52 data rows, the 52nd being the `HTTP_{status}` catch-all; envelope `{code,message,detail,request_id}`) | `error-codes.md` (103) — parsed by `tests/test_capability_matrix_consistency.py`, one of four locked sources (API `capability_matrix.py` ↔ FE `enums.ts` ↔ SDK ↔ this doc); the 级别能力 table is the LAST section (L85→EOF), so codes appended at the end land inside it |
| Retention / janitor policy (audit logs + Merkle leaves = `0` = forever) | `retention.md` (80) — §「2026-10-30 白名单真删」 is the live part; `NEVER_DELETE_CATEGORIES` / `REAL_DELETE_WHITELIST` are code-side guards, config only picks a subset |
| SS-10 policy package → `CubeNetworkConfig` proto field mapping | `cube-egress-mapping.md` (163) — file:line anchors, proto fields verified by introspecting `app/_stubs` |
| How to run anything on THIS machine (env, lanes, pitfalls) | `agent-handoff-2026-10-19.md` (85) §1 — **本机为权威，远程只留给硬件门** (user override of the 10-15 远程-only rule); chain: `-10-16` (168) → `-10-18` (120) → `-10-19` |
| Open-defect ledger (what is NOT done) | `todo-plan-2026-10-30.md` (158) P0→P3 + 决策清单; wave plan `ocr-fix-plan-2026-10-19.md` (98); portal-round ledger `defect-ledger-2026-10-16-portal-e2e.md` (970) |
| Per-finding truth of the OCR round (A1..A319) | `ocr-review-2026-10-18/ocr-verified-findings.md` — cited by 62 test modules; see that dir's `AGENTS.md` |
| Freshest e2e record (authoritative oracle = local docker py3.12 image) | `e2e-2026-11-02-local-remote/04-defects-and-fixes.md` (D-e2e-## ids) |
| Older state snapshot + remote-lane evidence | `cr-2026-10-15-review.md` (187) — §4 live not-yet-fixed ledger, §5 the superseded remote-only rule |
| Developer-portal quick index | `developer-sandbox-spec.md` (23) — mirror only; authority is `specs/data-sandbox-developer-portal-prd.md` + `specs/ss-15-flow-orchestration.md` |
| FE styling tokens / prototype alignment | `frontend-style-baseline.md` (47) → `specs/ss-18-prototype-alignment.md` |

## WHERE TO LOOK — execution plans with appended 执行记录
| Task | File |
|------|------|
| Mock/simulation registry (S1-S4 classes, MR-A1..A15) | `mock-remediation-plan.md` (472) — cited by `tests/test_pz04_honesty_gates.py` (MR-A9/A12/A13/A14) |
| CubeSandbox parity W1..W14 + non-alignment 不做项 | `cubesandbox-parity-plan.md` (788) |
| AI-sandbox gap T1..T12 over the A-F baseline | `ai-sandbox-gap-remediation-plan.md` (533), `ai-sandbox-gap-review-20260906.md` (133) |
| SS-14 remote e2e record (deploy → business loop → sandbox security → TDS) | `e2e-2026-10-13-remote/` (README index + 01..05 + `evidence-node/`) |
| OCR wave 1 (A1..A319: ledger, fix queue, gate evidence) | `ocr-review-2026-10-18/` (380 files) — own AGENTS.md; `ocr-review-2026-10-20/` + `-20-partial/` are **empty** rounds (`findings=0`, json `[]`) |

## ARCHAEOLOGY — never plan from these
`spec-implementation-analysis.md` (920), `implementation-tasks.md` (874), `implementation-tasks-v2.md` (756), `merged-task-plan.md` (194), `task-status-comparison.md` (117), `industry-comparison-analysis.md` (85): June-2026 snapshots (~55% / ~70% implemented) that re-tally the same P0/P1/P2 universe at different dates, with named owners and strikethrough status. The forward plan is `specs/ss-11-cds-productization.md`.

## CONVENTIONS
- Header blockquote metadata per doc (日期 / 基线 / 性质 / 范围, plus 前序 / 编号 for plans). Plan template: 基线复核 → P0/P1/P2 tasks with file:line anchors → 横切面 (开关矩阵 / Alembic 清单 / 测试计划 / 发布顺序 / 风险表) → 执行边界声明 → appended `Round NN 执行记录`.
- Round numbering is global across documents: R32-41 gap-remediation → R42-44 parity → R45-46 mock/productization → the 2026-10-15 CR. Reading order follows that chain.
- ID namespaces per document: A-F (gap review), T1-T12 (remediation), W1-W14 (parity), M-01..M-28 → MR-A1..A15 (mock); cross-refs use G-/FG-/PZ-/SS-/CB-.
- Docs are append-only in practice: corrections go into a new 时效更新/§6 section, never by rewriting the older body; ledgers mark 未修 explicitly.
- Code wins: 实施时若与现状冲突，以代码为准并回写本文件.
- Verification口径 lives here, not in code comments: since 2026-10-19 the authoritative run is LOCAL (`.venv` py3.14 or the local py3.12 docker image + real PG16), remote lanes are for hardware gates only. Strip proxies only in the test child (`env -u http_proxy …`), never touch global network config; one SQLite file per parallel lane.

## ANTI-PATTERNS
- `error-codes.md` is a test input: changing the 级别能力 table or the code list can break `test_capability_matrix_consistency.py`. `SESSION_TERMINATED` (410) must stay non-retryable — `cds_sdk` stops retrying on it.
- Do not delete honest simulated paths. mock-remediation classes them: S1 synthetic-data features stay, S2 honest fallbacks stay but need a real prod backend, S3 hardware-gated simulation is allowed; only S4 (fake implementations, idle gates, orphans) is a defect to fix.
- `mock-remediation-plan.md` MR-A1 step 2 (`tongsuo enc -sm4-gcm` CLI) is 作废 — superseded by SS-10 CB-03's libtongsuo C helper; don't re-plan it.
- Never put a remote host address or any password in repo markdown; those values live in `/root/cds.env` / `/tmp/cds-local.env`. The rule is currently **broken** in tracked files (`defect-ledger-2026-10-16-portal-e2e.md:8`, `agent-handoff-2026-10-16.md:21,24-25`, `ai-sandbox-gap-remediation-plan.md:395,419`, `e2e/01-deploy.md:10`, and the root file literally named `root@100.112.5.253`) — copy the 10-13 archive's masking style (`100.112.5.x`), don't add to the leak.
- `docs/e2e/01-deploy.md` (42) is a shorter near-duplicate of `e2e-2026-10-13-remote/01-deployment.md` (57) — read the archive copy.
- `docs/` is copied into the runtime image (`Dockerfile.api` ships it beside `app/` + `alembic/`) and 113 tracked files outside `docs/` name a `docs/*.md` path (`git grep -lE 'docs/[A-Za-z0-9._/-]+\.md' -- . ':!docs' ':!*AGENTS.md'`; 57 of them under `tests/`): don't treat md edits as comment-only changes.
- Where these documents disagree with the root `AGENTS.md`, they are usually the fresher side (root now matches `0034` and the 58-code table; the other figure anyone quotes for that file is 63 = every distinct backticked ALL_CAPS token in it, 5 of them not codes — the constants `ALLOWED_TRANSITIONS`/`SUPPLY_DATA_LEVELS`, the env var `CDS_SANDBOX_LEGACY_ENABLED`, and 2 superseded names cited inline `GATE_NOT_TEST_ENV`/`SESSION_TERMINATED_OBSERVED`; `retention.md` grew, defect S4 closed) — but every line count in any doc rots within days: measure before quoting.
- Docs have no build/lint of their own; the code they describe is gated by:
```bash
python -m pytest tests/test_capability_matrix_consistency.py tests/test_pz04_honesty_gates.py -q
python -m pytest tests/test_janitor_real_delete.py tests/test_deploy_manifests.py -q   # retention.md
```
