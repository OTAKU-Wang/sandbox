# deploy/ — host bring-up + e2e harness scripts (scored 12: 22 files / 4.3k LOC, 100% bash+python+templates, 41 files elsewhere reference these paths; distinct domain: nothing else in the repo touches a live host)

Three script sets, no package and no service config: `cube-node/` (Cube 计算节点 install/accept — own AGENTS.md), `e2e-remote/` (remote deploy + four e2e harnesses — own AGENTS.md), `local/` (single-host node-track stack, covered here: one 295-L script).

## WHERE TO LOOK
| Task | File |
|------|------|
| Deploy the whole stack on a host without the compose v2 plugin | `e2e-remote/remote_deploy.sh` (286) — scripted `docker run` (7 infra legs + frontend), mirrors root `docker-compose.yml` 1:1, idempotent, `flock -n` single-run |
| All-in-one local stack | `local/local_deploy.sh` (295) — same `run()` shape, ends `LOCAL_DEPLOY_DONE` |
| Generate a host's secret file | `e2e-remote/make_env.sh` — `make_env.sh <OUT> [CUBE_DP] [CUBE_TLS]`, `openssl rand`, `umask 077`, atomic 0600 write with verify-before-publish |
| Post-deploy acceptance (health, metrics, :3000, `docker ps`) | `e2e-remote/s1_check.sh` (51) — `S1_CHECK_DONE` prints only when every probe passed, else `S1_CHECK_FAILED` |
| Alembic head + `create_all` against real PostgreSQL | `e2e-remote/pg_validate.sh` (69) — one-shot |
| Business closed loop / sandbox posture / TDS scenarios / live node session | `e2e-remote/e2e_{business_loop,sandbox_security,tds_scenarios,node_live_session}.py` (323/174/202/314) — see `e2e-remote/AGENTS.md` |
| Which automated gate covers this directory | `tests/test_deploy_manifest_gates.py` (34 tests, the only gate on `e2e-remote/`) + `tests/test_deploy_manifests.py` (14) + `tests/test_cube_node_{scripts,tooling}.py` (51 tests on `cube-node/`) |

## CONVENTIONS
- `remote_deploy.sh` and `local_deploy.sh` are two of the **seven deploy config copies** (root compose ×4, `k8s/`, `helm/`, these two scripts): a service or env change belongs in all of them.
- Their `run()` wrapper splits arguments at a literal `--` — every docker flag BEFORE the image name, the container command AFTER.
- Completion is a trailing sentinel token, not an exit line: `DEPLOY_DONE`, `LOCAL_DEPLOY_DONE`, `S1_CHECK_DONE`, `BRINGUP_DONE`, `PG_VALIDATION_ALL_GREEN`, plus per-step `*_RC=` lines. Skip/warn lines never stand in for a sentinel.
- Secrets never enter the repo: `set -a; source /root/cds.env; set +a` behind a required-var guard loop (`/tmp/cds-local.env` for the local stack). Every script and harness **fails fast** when its secret env var is absent — there is no default credential anywhere to copy.
- Image tags are pinned by discipline (钉版本纪律, `:latest` forbidden for upstream images; the locally built `cds-frontend:latest` is the one exception). The MinIO `RELEASE.2025-01-20T14-49-07Z` tag exists in 7 places and must move in lockstep — a human release review, not a drive-by bump.
- Untracked `__pycache__/` under `cube-node/` and `e2e-remote/` is residue from import-by-path test runs, not source.

## ANTI-PATTERNS
- Never pipe a checked command through `tail`: a pipeline's exit code is `tail`'s, and an `echo OK` sentinel after it lies. `pg_validate.sh` checks `rc=$?` after every step for exactly this reason (D-e2e-08).
- `pg_validate.sh` runs `set -u` WITHOUT `-e` and hardcodes one host's paths (`/root/cds.env`, a `cds-api3.tar.gz` image tarball) — a one-shot script, not a reusable validator.
- The harness CLIs disagree on arguments: three take a positional `BASE_URL` + `CDS_E2E_ADMIN_PASS`; `e2e_node_live_session.py` takes `--base`/`--node-endpoint` + `E2E_ADMIN_PASS`. Copy the sibling's usage header instead of inventing flags.
- Reintroducing a default admin-password literal is a red test, not a convenience: `tests/test_deploy_manifest_gates.py:257` asserts `"CdsE2e!Admin123"` and `os.environ.get("CDS_E2E_ADMIN_PASS", ` are **absent** from the harnesses.
- `SKIP` counts as non-failing only in `e2e_tds_scenarios.py`; elsewhere SKIP ≠ PASS.
- `local_deploy.sh` runs everything on `--network host` with shifted ports because 3306/6379/9000/9001/9443/9999 are taken by the CubeSandbox one-click stack — don't "restore" the compose defaults.
- `.github/workflows/cube-node-smoke.yml` is `workflow_dispatch`-only on self-hosted `kvm` runners: a normal push/PR never executes these scripts, so the pytest gates above are their only automatic check.

## COMMANDS
```bash
# syntax/compile gates (safe anywhere)
bash -n deploy/cube-node/*.sh deploy/e2e-remote/*.sh deploy/local/*.sh
python3 -m py_compile deploy/cube-node/*.py deploy/e2e-remote/*.py
python -m pytest -q tests/test_deploy_manifest_gates.py tests/test_deploy_manifests.py

# never run install/bringup/remote_deploy from a test loop; they mutate a live host
bash deploy/e2e-remote/make_env.sh /root/cds.env [CUBE_DP] [CUBE_TLS]
bash deploy/e2e-remote/remote_deploy.sh /root/cds.env        # ends with DEPLOY_DONE
bash deploy/local/local_deploy.sh                            # ends with LOCAL_DEPLOY_DONE
# per-file e2e harness invocations: see e2e-remote/AGENTS.md
```
