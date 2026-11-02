# .github/ — CI workflows (scored 10: 3 workflows / 477 LOC; the 40-entry gate list here is machine-parsed by 3 consumers and 31 files reference this path)

This file also covers the sibling `ci/` (4 tracked files / 181 LOC — the budget script, gitleaks config, baseline json, known-failures ledger): one CI domain, and `ci/` alone scores too low to earn its own file.

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Anything about what must be green | `workflows/ci.yml` (236 L) — `push` to main + PRs; jobs `backend` / `migration-pg` / `frontend` / `secrets`; workflow-level `permissions: contents: read`; concurrency `cancel-in-progress` |
| The gate list | ci.yml "Gate tests (must be green)" step — 40 test files inline, the single source of truth; `ci/known-failures.md:11` echoes it as `当前 **40** 项` and `tests/test_ci_workflow_policy.py` locks both |
| Type-aware schema check | `migration-pg` job vs `postgres:16` service (the SQLite lane must run `ALEMBIC_COMPARE_TYPES=0`, UUID→NUMERIC affinity) |
| Frontend lane | node 20, `npm ci` → `npm test` → `npm run build` → `npm run lint` (hard gate since 2026-10-30, 0 waivers), cache keyed on `cds-frontend/package-lock.json` |
| Node install smoke | `workflows/cube-node-smoke.yml` (136 L) — `workflow_dispatch` ONLY on `[self-hosted, linux, kvm]`; `bash -n` syntax gate runs on any runner; mints `/tmp/cds-smoke-tls` (`umask 077`, deleted in `if: always()`); `node_ip` input goes to `$GITHUB_STEP_SUMMARY` |
| Upstream contract drift | `workflows/cube-upstream-watch.yml` (105 L) — cron Mon 09:00 UTC + dispatch; exit 0 clean / 1 contract drift (block, human review) / 2 non-contract (info) / 3 error |
| Failure-budget gate | `ci/check_full_suite_budget.py` (94 L, stdlib only): baseline established → `failures+errors > budget` = exit 1 and a missing `report.xml` is red; baseline NOT established (`ci/full-suite-baseline.json` `established=false`) → count + `::warning::`, exit 0 — never a fabricated baseline |
| Secret-scan policy | `ci/gitleaks.toml` — `[extend] useDefault=true` + allowlist with `regexTarget="secret"` (matches the VALUE only, never path/rule) |

## CONVENTIONS
- Two tiers: the 40-file gate list must be green (blocks merge); the full suite runs `continue-on-error: true` with `--junitxml=report.xml`, then the budget step, then `upload-artifact` (`if: ${{ !cancelled() && hashFiles('report.xml') != '' }}`).
- CI runs python **3.11**; the production image is 3.12 and the dev venv 3.14 — a green GitHub run is not the authoritative product number. Authoritative full-suite rows come from the remote py3.12 + PG16 lane (`scripts/remote-harness/`), which parses this same ci.yml for its gate list (`source_lane.sh`).
- torch must end as a CPU build: a same-version `--force-reinstall --no-deps` step after `pip install -r requirements.txt`, plus a guard asserting `torch.version.cuda is None` (`requirements.txt` `torch>=2.0.0` can otherwise pull a ~2 GB CUDA wheel).
- The secrets job downloads gitleaks 8.30.1 by version **and** sha256 and runs the binary; `GITHUB_TOKEN` is never handed to third-party code (this replaced `gitleaks-action@v2`).
- Every `uses:` today is first-party `actions/*@vN`; `test_third_party_actions_pinned_to_full_commit_sha` therefore passes vacuously — it only bites when a third-party action is added, at which point it needs a full commit SHA.

## ANTI-PATTERNS (each enforced by `tests/test_ci_workflow_policy.py`)
- ci.yml must carry exactly **one** "Gate tests" step, in line-continuation shape (the remote lane parses `name: Gate tests` + the trailing-backslash block); the listed files must exist; the count must equal the `当前 **N** 项` line in `ci/known-failures.md` — change both in one commit.
- The full-suite step may not end in a bare `|| true` nor carry `if: always()`; the budget-script step and the artifact step must stay wired.
- No job may inherit the default write-scoped `GITHUB_TOKEN`, no `write-all` grant anywhere, and no `${{ secrets.* }}` may be interpolated into a step.
- Never raise `pytest-timeout` to mask a hang, never let the `ci/known-failures.md` baseline grow, never delete an `xfail` to go green.
- Never add a gitleaks allowlist value without written proof it is not live (the 3 current entries came from the 2026-10-18 ops review).
- cube-upstream-watch must stay fail-closed: a clone/checkout failure fails the run ("a RED schedule is the monitor working"), the pin is never auto-pushed on drift, and keep `CUBE_UPSTREAM_REPO` validated (`^https://`, `GIT_ALLOW_PROTOCOL=https`, URL after `--`) with step outputs passed through env, never interpolated.
- `ci/cube-upstream-check-baseline-2026-10-13.log` is untracked local residue, not a baseline the tooling reads.

## COMMANDS
```bash
grep -oE 'tests/test_[a-z0-9_]*\.py' .github/workflows/ci.yml | sort -u | wc -l   # 41 = 40 gate + the --ignore target
python -m pytest -q tests/test_ci_workflow_policy.py                # after ANY ci.yml or ci/ edit
python -m pytest -q $(grep -oE 'tests/test_[a-z0-9_]*\.py' .github/workflows/ci.yml | sort -u)
python -m pytest tests/ -q --ignore=tests/test_e2e_full_lifecycle.py --junitxml=report.xml
python ci/check_full_suite_budget.py report.xml                     # exit 1 only once established
```
