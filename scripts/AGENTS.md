# scripts/

**Score 8 — distinct domain: build / codegen / ops tooling.** 16 top-level files (2,391 L; 17 files / 2,447 L counting this file) + `remote-harness/` (3 shell, 401 L); no package, no shared library — each entry point is invoked by CI, a compose mount, a k8s manifest, or a human at a terminal. 24 code files call into this tree (grep `scripts/` over `.github/workflows/`, the root composes, `k8s/`, `app/`, `tests/`: 3 workflows + 2 compose + 5 k8s manifests + 3 `app/` modules + 11 test modules).

## WHY THIS FILE
These scripts carry the repo's *supply-chain honesty*: upstream pins, sha verification, and fail-closed refusal to substitute "whatever version is at hand". That discipline is invisible from the Python tree and is the thing most often broken by a convenient edit. It is also the only subtree whose *text* is under test: `tests/test_ops_scripts_gates.py` (404 L) runs `bash -n` over every shell file here and asserts 33 source-text invariants (33 `def test_*` functions, 2 parametrized): locks, atomic writes, `${VAR:?}` required-vars, sentinel exit codes, installer URL allowlists. Rename, delete, or restructure a script and that gate reddens — `test_all_shell_scripts_present` holds the roster.

## WHERE TO LOOK
| Task | Script | Notes |
|------|--------|-------|
| CI schema gate | `verify_schema_parity.py` (87 L) | W5 gate in `ci.yml`: alembic chain vs `create_all` must agree, exit 1 on drift. Compares table/column **names** only |
| Regenerate Cube stubs | `gen-cube-stubs.sh` (343 L) | `git archive <pin>` from a read-only mirror, protoc, then sed-repath imports → `app/_stubs/`; refuses (exit 3) on dirty upstream / missing pin / no `grpc_tools`; needs bash ≥ 4 (`mapfile`) |
| Upstream drift check | `cube-upstream-check.sh` (162 L) | exit `0` clean · `1` **contract** paths moved (block & review) · `2` non-contract drift (auto-follow safe) · `3` error; watch list = `cube-contract-paths.txt` (24 L: 12 comment header lines + 12 pathspecs) |
| Cube bundle pin | `cube-bundle-manifest.json` | SS-10 CB-02 owns it; `gen-cube-stubs.sh` reads the pin from here before `_VERSION.json` |
| Build L2' scenario image | `build-cube-sandbox-image.sh` (89 L) | bakes the CubeEgress root CA (`CDS_CUBE_EGRESS_CA` / arg) — missing CA file is a hard fail, never a build without egress trust |
| Build template rootfs | `cube-rootfs-convert.sh` (168 L) | `docker export → mkfs.ext4 → e2fsck → sha256`; 红线 2: runs on the independent build machine, never on a compute node; `flock` on the out-dir, refuses `rm -rf` on live mounts |
| Vendored GM crypto | `prepare_tongsuo.sh` (163 L) | 406 MB Tongsuo source is not in git (only a pinned gitlink). Priority: `TONGSUO_TARBALL` (sha256-checked) → `TONGSUO_GIT_URL` → submodule; **all three failing = fail-closed** |
| k3s/k3d deploy | `install-k3s.sh` (248 L) | `CDS_K3S_MODE=k3s\|k3d`, China-mirror defaults, installer-URL allowlist; reads the namespace out of `k8s/namespace.yaml` instead of re-hardcoding it |
| kubectl wrapper | `k3d-kubectl.sh` (20 L) | kubectl inside the k3d server container |
| Legacy-k8s smoke | `e2e-k8s.sh` (177 L) | provisions a k8s session through the public API, waits for pod Ready, execs, checks the python-client stream + PVC (the deprecated k8s path); `CDS_E2E_USER/PASS` have no built-in values |
| Read-only API dry run | `api_dryrun_smoke.py` (232 L) | real lifespan + SQLite/`create_all`, calls every GET, counts (never calls) writes; RED = 5xx or missing W2 envelope → exit 1; artifact `.gate-scratch/dryrun_api.json` |
| Audit store / vault / chain init | `clickhouse-init.sql` (245), `vault-init.sh` (222), `fisco-init.sh` (123), `init-db.sql` (9) | `clickhouse-init.sql` is the **canonical** `cds_audit.audit_events` schema; `fisco-init.sh` deploys `contracts/AuditRegistry.sol` (`umask 077` before any key material) |
| **Authoritative test lanes** | `remote-harness/source_lane.sh` (168) · `sync_tree.sh` (162) · `fe_lane.sh` (71) | the py3.12 + real-PG16 backend lane, the work-tree sync, and the node-20 frontend lane — see CONVENTIONS; this is where real test numbers come from |

## CONVENTIONS
- Every script signals with its exit code, never with a log line — and a caller must capture it (`cmd; rc=$?`), because `cmd | tail; echo OK` reports tail's status. Never pipe a checked command through `tail`/`grep` and then trust `$?`.
- Pins live in exactly one place per artifact (`_VERSION.json`, `cube-bundle-manifest.json`, the sha constants in `prepare_tongsuo.sh`); scripts read them, they never hard-code a second copy. `gen-cube-stubs.sh` resolution order: `CUBE_PIN` env > `cube-bundle-manifest.json` > `_VERSION.json` > built-in constant, and it prints which one won.
- Anything touching upstream or the build host is read-only first and refuses rather than repairs (dirty upstream worktree, non-git dir, or a failing `git status` all exit 3 — inability to verify is a failure, not a pass).
- `install-k3s.sh` / `e2e-k8s.sh` assume China-network mirrors; do not swap in an upstream URL for a "simpler" one. `install-k3s.sh` allowlists `rancher.cn` hosts — any other https needs `CDS_ALLOW_UNLISTED_INSTALLER=1`, plain http dies.
- **Shebangs are not uniform, deliberately:** 8 of the 13 shell files under `scripts/` are `#!/usr/bin/env bash` + `set -euo pipefail`. `vault-init.sh` is `/bin/sh` and must stay POSIX (it runs inside the vault container); `k3d-kubectl.sh` is `/bin/bash`; the three `remote-harness/` lanes are bash with `set -uo pipefail` — **no `-e`**, because they accumulate failures (`FAILS++`, per-step `STEP_OK|STEP_FAIL <name> rc=`, terminal non-zero exit). That split is what `test_fail_fast_or_accumulator_in_place` accepts; adding `set -e` to a lane or dropping it elsewhere both redden it.
- Env vars are the config surface (no config file): `CDS_K3S_MODE`, `CUBE_UPSTREAM_DIR`/`CUBE_MIRROR`/`CUBE_PIN`, `TONGSUO_TARBALL`/`TONGSUO_TARBALL_SHA256`/`TONGSUO_GIT_URL`, `CDS_CUBE_EGRESS_CA`, `CDS_API_URL`/`CDS_E2E_USER`/`CDS_E2E_PASS`/`CDS_E2E_TOKEN`, `FISCO_*`, `VAULT_ADDR`/`VAULT_TOKEN`, `CDS_LOCAL_ROOT`, `CDS_LANE_DB_URL`, `CDS_NODE_IMAGE`.
- `remote-harness/` contract, on top of exit codes: stdout sentinels `LANE_DONE` / `LANE_FAIL fails=N` / `FE_LANE_DONE rc=N` / `SYNC_ALL_OK` / `TREE_MD5_MATCH` / `SYNC_ALL_VERIFY_FAIL n=`; `flock -n` single-instance in all three (shared PG, LOGDIR, node_modules); hardcoded harness target `root@100.112.5.253 -p 22022`, `SRC=/root/cds-test`, venv must be Python 3.12; `mask_dsn()` scrubs DB passwords from anything shipped back.
- `clickhouse-init.sql` is pinned by `test_ops_scripts_gates.py`: every `cds_audit.*` table needs a TTL, monthly-partition tables need `ttl_only_drop_parts`, `audit_events` needs ≥5 skipping indexes, `chain_valid` must be `Nullable(Boolean)` (a `DEFAULT true` is fail-open), and the k8s 8-column drift must stay registered *inside the file*.
- Passive consumers mount these files rather than call them: `init-db.sql`, `clickhouse-init.sql`, `vault-init.sh` land in `docker-entrypoint-initdb.d/` / `:/vault/config/init.sh` from `docker-compose.middleware.yml` + `docker-compose.prod.yml`, and `clickhouse-init.sql` is copied into ConfigMap `clickhouse-init-sql`.

## ANTI-PATTERNS (THIS PROJECT)
- Never hand-edit generated output here (`app/_stubs/**`) instead of re-running the generator, and never relax a refusal guard to make a run succeed.
- `init-db.sql` and `k8s/configmaps.yaml` are **drifted copies** of the ClickHouse/PG schemas — `scripts/clickhouse-init.sql` (30+ columns) is the authority, the k8s copy is 8 columns. Fix the copy only together with the authority, or say so.
- `vault-init.sh` must stay POSIX `/bin/sh` — it runs inside the vault container (busybox ash, no bash substring expansion). `k3d-kubectl.sh` (`/bin/bash`) is the other non-`env bash` file; there is no third.
- `cube-upstream-check.sh` exit codes are a published protocol: `1` = contract drift (block + human review), `2` = non-contract drift (auto-follow safe). Spec §4 originally reserved `2` for "error" and CB-00 reassigned it — hard errors use `3`. Never reinterpret the numbers to make a caller simpler.
- `api_dryrun_smoke.py` is read-only by contract: every write method is counted, never called; the only real writes are `POST /auth/login` + `/auth/refresh`. Do not add a write "just this one" —巡检 triggers, janitors and session creation must not be reachable through a dry run.
- Lane scripts: never delete the `export PATH="/root/cds-test/venv/bin:…"` line (without it the whole lane silently degrades to system python), never let the CI-gate-list parse return an empty set (it must `STEP_FAIL gate_parse`), and never build `bash -c` argument lists from `ls`.

## COMMANDS
```bash
python -m pytest -q tests/test_ops_scripts_gates.py tests/test_prepare_tongsuo.py   # the static gates over this dir
bash -n scripts/install-k3s.sh k8s/deploy.sh                    # release-gate §4
CDS_K3S_MODE=k3s|k3d bash scripts/install-k3s.sh                ; bash scripts/k3d-kubectl.sh get pods -n cds-system
CUBE_UPSTREAM_DIR=<clone> scripts/cube-upstream-check.sh        # 0 clean · 1 contract drift · 2 info · 3 error
scripts/gen-cube-stubs.sh                                       # needs .venv python WITH grpcio-tools + protoc
bash deploy/e2e-remote/pg_validate.sh                           # the alembic/PG half of the schema gate
CDS_LOCAL_ROOT=$PWD bash scripts/remote-harness/sync_tree.sh    # ends TREE_MD5_MATCH + SYNC_ALL_OK
CDS_LANE_DB_URL=postgresql+asyncpg://… bash scripts/remote-harness/source_lane.sh fast|full
CDS_NODE_IMAGE=<node:20-bookworm-slim> bash scripts/remote-harness/fe_lane.sh   # FE_LANE_DONE rc=N
python scripts/api_dryrun_smoke.py                              # exit 1 if any RED
```
