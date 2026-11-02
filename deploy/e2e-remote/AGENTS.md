# deploy/e2e-remote/ — remote deploy script + four e2e harnesses (scored 8: 9 files / 1.5k LOC, distinct domain: the only code that drives an already-deployed stack end to end)

Parent `deploy/AGENTS.md` owns the shared doctrine (sentinel completion, `run()` `--` split, env-file secrets, never pipe a checked command). Everything here is remote-lane specific.

## WHERE TO LOOK
| Task | File |
|------|------|
| Bring the whole stack up with plain `docker run` | `remote_deploy.sh` (286) — 7 infra legs (postgres, redis, clickhouse, minio, opa, vault, cds-api) + frontend, `flock -n 9` single-run |
| Produce the env file that script consumes | `make_env.sh` (62) — `make_env.sh <OUT> [CUBE_DP] [CUBE_TLS]`, hex via `openssl rand`, atomic 0600, verify-before-publish |
| Smoke the running stack | `s1_check.sh` (51) — `ERR` trap prints `S1_CHECK_FAILED (line N, rc=)`; `S1_CHECK_DONE` only after every probe passes |
| Prove the schema on real PG16 | `pg_validate.sh` (69) — alembic head + `create_all` → `PG_VALIDATION_ALL_GREEN` |
| Business closed loop / fail-closed sandbox posture / TDS T1-T6 / live node session N1-N10 | `e2e_business_loop.py` (323) / `e2e_sandbox_security.py` (174) / `e2e_tds_scenarios.py` (202) / `e2e_node_live_session.py` (314) |
| Key list a host must fill | `cds.env.template` (41) |
| Evidence ledger of a real run of this lane | `docs/e2e-2026-10-13-remote/` (01-deployment … 05-tds-scenarios) |

## CONVENTIONS
- Run order is fixed: `make_env.sh` → `remote_deploy.sh` → `s1_check.sh` → `pg_validate.sh` → harness(es).
- Each guard in `remote_deploy.sh` carries the defect ID it exists for: A286 redis `--maxmemory-policy noeviction` (allkeys-lru evicted sessions), A284 OPA published on `127.0.0.1:8181` only (its data API is unauthenticated and its answer is trusted as ALLOW), D-e2e-11 Vault `transit` enabled **and** re-verified via `secrets list` on every deploy, A89 `CDS_JWT_SECRET_KEY` ≥32 bytes checked pre-deploy (`-e VAR=""` is not "unset" for pydantic), A301 fail-fast whitelist covering every `-e VAR="${VAR:-}"`.
- `wait_healthy` / `wait_healthy_port` take an argv **array** — the earlier string form glob-expanded a `*` password against the host CWD.
- Harnesses are scripts, not a library: `step()`/`finish()` + a module-global `EVIDENCE` → one JSON evidence file, non-zero exit on any FAIL, `httpx.Client(timeout=120)` as the only client. `--admin-user` defaults to `cdsadmin`.
- Two CLI shapes coexist: positional `BASE_URL` + `CDS_E2E_ADMIN_PASS` (business_loop, sandbox_security, tds_scenarios — fail fast, no default) versus `--base`/`--node-endpoint` + `E2E_ADMIN_PASS` (node_live_session).
- The only automated gate over this directory is `tests/test_deploy_manifest_gates.py` (34 tests): it path-loads `e2e_sandbox_security._as_items` and runs `s1_check.sh` / `make_env.sh` in subprocesses.

## ANTI-PATTERNS
- Adding a default credential is a red test, not a convenience: `tests/test_deploy_manifest_gates.py:257` asserts both `"CdsE2e!Admin123"` and the `os.environ.get("CDS_E2E_ADMIN_PASS", ` default pattern are absent.
- Two concurrent `remote_deploy.sh` runs `rm -f` each other's containers over fixed ports — the `flock` is load-bearing, don't remove or bypass it.
- `pg_validate.sh` is `set -u` **without** `-e` and hardcodes one host (`/root/cds.env`, a `cds-api*.tar.gz` it `rm -f` after `docker load`) — a one-shot, never a reusable validator.
- `SKIP` is non-failing only in `e2e_tds_scenarios.py`; elsewhere SKIP ≠ PASS, and a `WARN` line never substitutes for the sentinel. The final `/health` flake deliberately only WARNs so it cannot swallow `DEPLOY_DONE` — don't tighten it into a hard fail.
- No `#` comment inside a `\` line-continuation: bash hands the comment to the command (2026-11-02 remote 实锤).
- Don't import these from `app/` or add `__init__.py`/packaging — they stay out-of-process by design; tests reach them with `importlib`/`subprocess`. Untracked `__pycache__/` here is that residue.

## COMMANDS
```bash
bash -n deploy/e2e-remote/*.sh && python3 -m py_compile deploy/e2e-remote/*.py
bash deploy/e2e-remote/make_env.sh /root/cds.env [CUBE_DP] [CUBE_TLS]
bash deploy/e2e-remote/remote_deploy.sh /root/cds.env                       # ends DEPLOY_DONE
bash deploy/e2e-remote/s1_check.sh ; bash deploy/e2e-remote/pg_validate.sh
python3 deploy/e2e-remote/e2e_business_loop.py BASE_URL [--out E.json]      # CDS_E2E_ADMIN_PASS
python3 deploy/e2e-remote/e2e_sandbox_security.py BASE_URL [--admin-user U]
python3 deploy/e2e-remote/e2e_tds_scenarios.py BASE_URL                      # SKIP ok here only
python3 deploy/e2e-remote/e2e_node_live_session.py --base URL --node-endpoint H:P   # E2E_ADMIN_PASS
python -m pytest -q tests/test_deploy_manifest_gates.py                      # the only auto gate
```
