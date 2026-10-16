# deploy/ — host bring-up + e2e harness scripts (scored 8: 20 files / ~3.1k LOC, all bash+python, distinct domain: nothing else in the repo touches a live host)

Three script sets, no package and no service config: `cube-node/` (Cube 计算节点 install/accept — has its own AGENTS.md), `e2e-remote/` (remote deploy + four e2e harnesses), `local/` (single-host node-track stack).

## WHERE TO LOOK
| Task | File |
|------|------|
| Deploy the whole stack on a host without the compose v2 plugin | `e2e-remote/remote_deploy.sh` (192) — scripted `docker run`, mirrors root `docker-compose.yml` 1:1, idempotent |
| Generate that host's secret file | `e2e-remote/make_env.sh` — `make_env.sh <OUT> [CUBE_DP] [CUBE_TLS]`, `openssl rand`, `umask 077` |
| Post-deploy acceptance (health, metrics, :3000, `docker ps`) | `e2e-remote/s1_check.sh` (13) |
| Alembic head + `create_all` against real PostgreSQL | `e2e-remote/pg_validate.sh` (59) — one-shot |
| Business closed loop through the platform API key | `e2e-remote/e2e_business_loop.py` (257) |
| Fail-closed sandbox posture with no node (→ 400 `RUNTIME_UNAVAILABLE`, retired levels → 400) | `e2e-remote/e2e_sandbox_security.py` (141) |
| Trusted-data-space scenarios T1-T6 | `e2e-remote/e2e_tds_scenarios.py` (191) |
| Live session on a schedulable Cube node (steps N1-N10) | `e2e-remote/e2e_node_live_session.py` (304) |
| All-in-one local stack | `local/local_deploy.sh` (200) |

## CONVENTIONS
- `remote_deploy.sh` is effectively the **sixth deploy-path copy** (root compose ×4, `k8s/`, `helm/` + this script): a service or env change belongs here too.
- Its `run()` wrapper splits arguments at a literal `--` — every docker flag BEFORE the image name, the container command AFTER.
- Completion is a trailing sentinel token, not an exit line: `DEPLOY_DONE`, `BRINGUP_DONE`, `S1_CHECK_DONE`, `PG_VALIDATION_ALL_GREEN`, `LOCAL_DEPLOY_DONE`, plus per-step `*_RC=` lines.
- Secrets never enter the repo: `set -a; source /root/cds.env; set +a` behind a required-var guard loop (`/tmp/cds-local.env` for the local stack).
- The four e2e harnesses are scripts, not a library: `step()` / `finish()` + a module-global `EVIDENCE` → one JSON evidence file per run, non-zero exit on any FAIL.

## ANTI-PATTERNS
- Never pipe a checked command through `tail`: a pipeline's exit code is `tail`'s, and an `echo OK` sentinel after it lies. `pg_validate.sh` checks `rc=$?` after every step for exactly this reason (D-e2e-08).
- `pg_validate.sh` runs `set -u` WITHOUT `-e` and hardcodes one host's paths (`/root/cds.env`, a `cds-api3.tar.gz` image tarball) — a one-shot script, not a reusable validator.
- The e2e CLIs disagree on arguments: three take a positional `BASE_URL` and read `CDS_E2E_ADMIN_PASS`; `e2e_node_live_session.py` takes `--base` and reads `E2E_ADMIN_PASS`. Copy the sibling's usage header instead of inventing flags.
- Three harnesses carry a default admin password literal (`CdsE2e!Admin123`) — e2e posture only; never reuse it anywhere real or quote it into docs.
- `SKIP` counts as non-failing only in `e2e_tds_scenarios.py`; elsewhere SKIP ≠ PASS.
- `local_deploy.sh` runs everything on `--network host` with shifted ports because 3306/6379/9000/9001/9443/9999 are taken by the CubeSandbox one-click stack — don't "restore" the compose defaults.
- Untracked `__pycache__/` under `cube-node/` and `e2e-remote/` is residue from import-by-path runs, not source.

## COMMANDS
```bash
bash deploy/e2e-remote/make_env.sh /root/cds.env [CUBE_DP] [CUBE_TLS]
bash deploy/e2e-remote/remote_deploy.sh /root/cds.env        # ends with DEPLOY_DONE
bash deploy/e2e-remote/s1_check.sh ; bash deploy/e2e-remote/pg_validate.sh
python3 deploy/e2e-remote/e2e_business_loop.py BASE_URL [--out E.json]
python3 deploy/e2e-remote/e2e_sandbox_security.py BASE_URL [--admin-user U]
python3 deploy/e2e-remote/e2e_tds_scenarios.py BASE_URL
python3 deploy/e2e-remote/e2e_node_live_session.py --base URL --node-endpoint H:P
bash deploy/local/local_deploy.sh                            # ends with LOCAL_DEPLOY_DONE
```
