# k8s/ — raw manifests, the k3s deploy path (scored 10: 16 files / 1.7k LOC — 14 manifests ≈ 42 objects + `deploy.sh` + `README.md`; flat, ns `cds-system`; `cds-secrets` 16 keys shared with the helm chart; 30 files elsewhere reference these paths)

One of the SEVEN deploy config copies (root compose ×4, `helm/`, `deploy/e2e-remote/remote_deploy.sh`, `deploy/local/local_deploy.sh`). `k8s/README.md` is the freshest prose for this path (P6 收敛 2026-10-30) — read it alongside this file.

## WHERE TO LOOK
| File | What it is |
|------|------------|
| `deploy.sh` | 9-step k3s deploy (prefers `k3s kubectl`); applies exactly 10 of the 14 manifests (namespace, secrets, configmaps, postgres, redis, minio, vault, clickhouse, opa, cds-app), then exec's `scripts/vault-init.sh` into the vault pod |
| `secrets.yaml` | Secret `cds-secrets`: 16 keys, **all `<REPLACE_ME_*>` placeholders** since P6 — replacing them (or switching to external injection) is a precondition of apply, not a follow-up |
| `configmaps.yaml` | 2 ConfigMaps with init SQL embedded: `GRANT … ON DATABASE cds TO cds` + DDL for the 7 `cds_audit` tables (33-column `audit_events`) |
| `cds-app.yaml` | `cds-api` + `cds-frontend` Deployments/Services, the api env ConfigMap, the `cds-migrate` Job, 7 PVCs, NodePort 30080/30081, `imagePullPolicy: Never` |
| `postgres/clickhouse/minio/redis/opa/vault.yaml` | one file per infra service (Deployment+Service+PVC). `opa.yaml` creates Service `opa:8181` — the exact target the helm chart's `opa.url` assumes |
| `kafka.yaml`, `debezium.yaml` | CDC path (zookeeper + kafka + debezium connect) — deliberately NOT applied by `deploy.sh`; README §CDC is the procedure (apply kafka → debezium, then `CDS_CDC_KAFKA_BROKERS=kafka:9092` + rollout restart) |
| `registries.yaml` | CN mirrors (docker.1ms.run / daocloud) → `/etc/rancher/k3s/registries.yaml`; infra images (`IfNotPresent`) come through these, only cds-api/cds-frontend are `Never` |
| `README.md` / `namespace.yaml` | 配置单一真值源 table + CDC rationale / the `cds-system` namespace |

## CONVENTIONS
- Single-source-of-truth values, each machine-pinned by `tests/test_deploy_manifests.py` / `tests/test_deploy_manifest_gates.py`: PG app user `cds` (README: a pre-P6 dev volume needs `ALTER USER cds_admin RENAME TO cds;` before apply, or the `pg_isready` probe stays red), `CDS_LOG_JSON: "true"` across the 4 prod-posture copies, MinIO `RELEASE.2025-01-20T14-49-07Z` across 7 copies.
- CubeSandbox compute nodes are NOT part of this path and never live in `cds-system` (runbook §5.1 红线 2): they are bare-metal/VM installs driven by `deploy/cube-node/`. Nothing here should grow a Cube workload.
- Labels here: `app=<svc>`. The helm path uses `app.kubernetes.io/name`+`instance` via `_helpers.tpl` — known drift, don't "unify" one side without touching the other.
- Sandbox pods run in ns `cds-sandbox` (see `scripts/e2e-k8s.sh`); infra in `cds-system`.
- `imagePullPolicy: Never` on cds-api/cds-frontend: the node MUST have local `cds-api:latest`/`cds-frontend:latest` — `scripts/install-k3s.sh` imports them (`CDS_IMPORT_LOCAL_IMAGES`).
- `cds-migrate` is a Job: re-running the migration means `kubectl delete job cds-migrate` first, not re-applying.
- China-network defaults: `INSTALL_K3S_MIRROR=cn`; k3d mode via `CDS_K3S_MODE=k3d` + `scripts/k3d-kubectl.sh`.
- Work-item comments (W3/W12/N7/P6/FG-001…) cross-reference `docs/retention.md` and `specs/productization-deployment-runbook.md` — keep IDs when editing.
- `CDS_KMS_MASTER_KEY_FILE` belongs to the env ConfigMap (`cds-app.yaml`, `""` by default): it is a **path**, never a Secret value.

## ANTI-PATTERNS
- Never put a real credential into `secrets.yaml` — it is a placeholder template as of P6; prod injects through `secrets.existingSecret`/Vault. Never copy a `<REPLACE_ME_*>` value into another deploy copy either.
- `deploy.sh` ends every `kubectl wait` with `|| true` (9 of them) — "=== CDS Infrastructure Deployed ===" does NOT mean success; check each resource after deploy (`deploy/e2e-remote/s1_check.sh` or `kubectl get pods -n cds-system`).
- Do not re-introduce the P6 drift this directory used to carry: no `cds_admin` GRANT, no 8-column `audit_events`. `scripts/init-db.sql` and `scripts/clickhouse-init.sql` (7 tables, canonical) are the sources; `configmaps.yaml` must match them.
- Vault init is weak: `scripts/vault-init.sh` uses `-key-shares=1 -key-threshold=1` (no Shamir split) — dev-only.
- OPA healthcheck is `/opa version` in `docker-compose.prod.yml` (passes even when the policy store is down); the k8s probe is `httpGet /health`, which is up-or-down only and equally policy-store blind.

## COMMANDS
```bash
bash k8s/deploy.sh                                   # prefers k3s kubectl; CDC manifests stay manual
bash -n scripts/install-k3s.sh k8s/deploy.sh         # release-gate §4 syntax check
CDS_K3S_MODE=k3d bash scripts/install-k3s.sh         # local cluster mode
python -m pytest -q tests/test_deploy_manifests.py tests/test_deploy_manifest_gates.py tests/test_ops_scripts_gates.py
```
