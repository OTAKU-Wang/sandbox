# scripts/

**Score 10 — distinct domain: build / codegen / ops tooling.** 15 files, 1,513 LOC; no package, no shared library — each entry point is invoked by CI, a workflow, or a human at a terminal.

## WHY THIS FILE
These scripts carry the repo's *supply-chain honesty*: upstream pins, sha verification, and fail-closed refusal to substitute "whatever version is at hand". That discipline is invisible from the Python tree and is the thing most often broken by a convenient edit.

## WHERE TO LOOK
| Task | Script | Notes |
|------|--------|-------|
| CI schema gate | `verify_schema_parity.py` (83 L) | W5 gate in `ci.yml`: alembic chain vs `create_all` must agree, exit 1 on drift |
| Regenerate Cube stubs | `gen-cube-stubs.sh` (278 L) | `git archive <pin>` from a read-only mirror, protoc, then sed-repath imports → `app/_stubs/`; refuses (exit 3) on dirty upstream / missing pin / no `grpc_tools` |
| Upstream drift check | `cube-upstream-check.sh` (132 L) | exit `0` clean · `1` **contract** paths moved (block & review) · `2` non-contract drift (auto-follow safe) · `3` error; watch list = `cube-contract-paths.txt` |
| Cube bundle pin | `cube-bundle-manifest.json` | SS-10 CB-02 owns it; `gen-cube-stubs.sh` reads the pin from here before `_VERSION.json` |
| Build L2' scenario image | `build-cube-sandbox-image.sh` (63 L) | bakes the CubeEgress root CA (`CDS_CUBE_EGRESS_CA` / arg) — missing CA file is a hard fail, never a build without egress trust |
| Build template rootfs | `cube-rootfs-convert.sh` (117 L) | `docker export → mkfs.ext4 → e2fsck → sha256`; 红线 2: runs on the independent build machine, never on a compute node |
| Vendored GM crypto | `prepare_tongsuo.sh` (79 L) | 406 MB Tongsuo source is not in git (only a pinned gitlink). Priority: `TONGSUO_TARBALL` (sha256-checked) → `TONGSUO_GIT_URL` → submodule; **all three failing = fail-closed** |
| k3s/k3d deploy | `install-k3s.sh` (151 L) | `CDS_K3S_MODE=k3s\|k3d`, China-mirror defaults; `k3d-kubectl.sh` is the kubectl-inside-server-container wrapper |
| Legacy-k8s smoke | `e2e-k8s.sh` (130 L) | provisions a k8s session through the public API, waits for pod Ready, execs, checks the python-client stream + PVC (the deprecated k8s path) |
| Audit store / vault / chain init | `clickhouse-init.sql`, `vault-init.sh`, `fisco-init.sh`, `init-db.sql` | `clickhouse-init.sql` is the **canonical** `cds_audit.audit_events` schema; `fisco-init.sh` deploys `contracts/AuditRegistry.sol` |

## CONVENTIONS
- Every script signals with its exit code, never with a log line — and a caller must capture it (`cmd; rc=$?`), because `cmd | tail; echo OK` reports tail's status.
- Pins live in exactly one place per artifact (`_VERSION.json`, `cube-bundle-manifest.json`, the sha constants in `prepare_tongsuo.sh`); scripts read them, they never hard-code a second copy.
- Anything touching upstream or the build host is read-only first and refuses rather than repairs.
- `install-k3s.sh` / `e2e-k8s.sh` assume China-network mirrors; do not swap in an upstream URL for a "simpler" one.

## ANTI-PATTERNS (THIS PROJECT)
- Never hand-edit generated output here (`app/_stubs/**`) instead of re-running the generator, and never relax a refusal guard to make a run succeed.
- `init-db.sql` and `k8s/configmaps.yaml` are **drifted copies** of the ClickHouse/PG schemas — `scripts/clickhouse-init.sql` (30+ columns) is the authority, the k8s copy is 8 columns. Fix the copy only together with the authority, or say so.
- The one file with a non-`bash` shebang is `vault-init.sh` (`/bin/sh`, must stay POSIX — it runs inside the vault container).
