# k8s/ — raw manifests, the k3s deploy path (scored 8: 14 flat files, ns `cds-system`; secrets referenced by ~9 manifests + the helm chart)

One of FIVE parallel deploy paths (root docker-compose*.yml, k8s/, helm/ each carry their own copies of service config and CDS_* env).

## WHERE TO LOOK
| File | What it is |
|------|------------|
| `deploy.sh` | 9-step k3s deploy (prefers `k3s kubectl`); ends by exec'ing `scripts/vault-init.sh` into the vault pod |
| `secrets.yaml` | Secret `cds-secrets`: 14 keys (POSTGRES/REDIS/MINIO/VAULT/CLICKHOUSE creds, `CDS_DATABASE_URL`, `CDS_REDIS_URL`, `CDS_JWT_SECRET_KEY`, `CDS_CDC_KAFKA_BROKERS`) |
| `configmaps.yaml` | 2 ConfigMaps with init SQL embedded (postgres GRANT, clickhouse DDL) |
| `cds-app.yaml` | `cds-api` + `cds-frontend` Deployments, NodePort 30080/30081, `imagePullPolicy: Never` |
| `postgres/clickhouse/minio/redis/opa/vault.yaml` | one file per infra service (Deployment+Service+PVC) |
| `kafka.yaml`, `debezium.yaml`, `redis.yaml` | CDC path: zookeeper+kafka+PVCs, debezium, redis |
| `registries.yaml` | CN mirrors (docker.1ms.run / daocloud) → `/etc/rancher/k3s/registries.yaml` |
| `namespace.yaml` | `cds-system` |

## CONVENTIONS
- Labels here: `app=<svc>`. The helm path uses `app.kubernetes.io/name`+`instance` — known drift, don't "unify" one side without touching the other.
- Sandbox pods run in ns `cds-sandbox` (see `scripts/e2e-k8s.sh`); infra in `cds-system`.
- `imagePullPolicy: Never` on cds-api/cds-frontend: the node MUST have local images — `scripts/install-k3s.sh` imports them (`CDS_IMPORT_LOCAL_IMAGES`).
- China-network defaults: `INSTALL_K3S_MIRROR=cn`; k3d mode via `CDS_K3S_MODE=k3d` + `scripts/k3d-kubectl.sh`.
- Work-item comments (W3/W12/N7/FG-001…) cross-reference `docs/retention.md` and `specs/productization-deployment-runbook.md` — keep IDs when editing.

## ANTI-PATTERNS
- `secrets.yaml` is COMMITTED DEV-ONLY credentials (header says so publicly-readable, rotate in any real env). Never copy values from it; prod injects via existingSecret/Vault.
- `deploy.sh` ends every `kubectl wait` with `|| true` — "=== CDS Infrastructure Deployed ===" does NOT mean success; check each resource after deploy.
- Init SQL is duplicated AND drifted: `configmaps.yaml` (GRANT TO `cds_admin`, 8-column `audit_events`) vs `scripts/init-db.sql` (GRANT TO `cds`) and `scripts/clickhouse-init.sql` (40-column `audit_events`, the canonical schema). Change both copies.
- Vault init is weak: `scripts/vault-init.sh` uses `-key-shares=1 -key-threshold=1` (no Shamir split).
- Postgres user is `cds_admin` here vs `cds` in compose paths — drift, not a bug to fix in one place.
- OPA healthcheck is `/opa version` — passes even when the policy store is down.
