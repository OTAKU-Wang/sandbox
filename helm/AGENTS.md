# helm/ — the cds chart (scored 10: 13 files / 901 LOC — single chart at `helm/cds/`, NOT `helm/cds/cds/` as some task lists claim; 27 files elsewhere reference `helm/cds`)

Only deploy path with HPA/PDB/Ingress. `values.yaml` (309 L) is one canonical copy of the CDS_* env contract — 61 `api.env` keys; its prod-posture twin is root `docker-compose.prod.yml`.

## STRUCTURE
```
helm/cds/
├── Chart.yaml       # bitnami postgresql 15.x.x + redis 19.x.x — declared, NOT vendored
├── values.yaml      # 61 CDS_* api.env keys, secrets.existingSecret, externalComponents whitelist
└── templates/       # 10 files: deployment-api (183), minio (124), deployment-frontend, ingress, service,
                     # pvc, hpa, pdb, configmap, _helpers.tpl
                     # _helpers.tpl: cds.name / cds.fullname / cds.labels / cds.api.selectorLabels / cds.frontend.selectorLabels
```

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Change a CDS_* knob | `values.yaml` AND root `docker-compose.prod.yml` — plus `.env.example`, `docker-compose.yml`, `app/core/config.py`, `tests/test_config.py` (配置四同步) |
| Secrets | `values.yaml` `secrets.existingSecret` (default `cds-secrets`) — same 16-key set as `k8s/secrets.yaml`; all 61 `api.env` keys ride the ConfigMap via `envFrom`, 12 explicit `secretKeyRef` entries override their twins, 3 of them `optional: true` so a missing key lands on the boot refusal instead of a stuck pod |
| Subchart creds | `postgresql.auth.existingSecret: cds-db-credentials`, `redis.auth.existingSecret: cds-redis-credentials` (both `condition:`-gated) |
| In-chart vs external infra | `values.yaml` `externalComponents` list (currently `clickhouse`, `opa`) |
| MinIO | `templates/minio.yaml` (P6, newest template): default `minio.enabled: false`, `strategy: Recreate` (RWO singleton — a RollingUpdate surge pod hangs in ContainerCreating), image pinned, root creds deliberately NOT `optional` |
| Ship gate for this chart | `tests/test_helm_chart.py` (externalComponents invariant + hardening) + `tests/test_deploy_manifests.py` + `tests/test_integrity_patrol.py` + release-gate `helm template cds helm/cds` |
| Helper labels/selectors | `templates/_helpers.tpl`: `cds.fullname` (18 uses), `cds.labels` (12), `cds.api.selectorLabels` (5), `cds.name` (3), `cds.frontend.selectorLabels` (3) |

## CONVENTIONS
- `helm dependency build ./helm/cds` is REQUIRED before `helm template`/`install` (bitnami charts fetched from charts.bitnami.com, not vendored — see Chart.yaml comment).
- Posture in `values.yaml` is fail-closed on purpose: empty `CDS_MFA_SECRET_ENCRYPTION_KEY` / `CDS_COLUMN_ENCRYPTION_KEY` / attestation measurements RAISE at boot via `validate_security_config`; empty service tokens (`CDS_KMS_SERVICE_TOKEN`, `CDS_CUBE_AUDIT_SERVICE_TOKEN`) mean 401; retention `0` = keep forever with `CDS_RETENTION_JANITOR_ENABLED=true` + `_DRY_RUN=true` and `CDS_JANITOR_ENABLED=false`; `CDS_SM3_/SECCOMP_/HSM_SOFTWARE_FALLBACK_ALLOWED` are `"false"` — flipping one to `true` while `CDS_DEBUG="false"` CrashLoops every API pod.
- HPA owns the replica count: `replicas:` renders **only** when `autoscaling.enabled=false`. The frontend deployment always pins `replicaCount`.
- Config drift guard: the pod template carries `checksum/config` (sha256 of the rendered ConfigMap) and `checksum/secret` (`secrets.checksum | default existingSecret`). After rotating the out-of-chart Secret, bump `secrets.checksum` manually or nothing rolls.
- Env-precedence idiom: an explicit `env:` entry shadows the same-named empty in `api.env` — that is how the 12 secret-backed keys land while `api.env` stays a plain `range` over `CDS_*`.
- One RWX PVC (`<fullname>-sandbox`, `accessModes: [ReadWriteMany]` — RWO strands replica 2 in Multi-Attach under HPA + zone spread) is carved into 7 subPaths: `sandbox`, `sandbox-l0`, `storage`, `egress-audit`, `shared-volumes`, `cube-results`, `kms-master` — because the container write paths are hardcoded (`/tmp/cds-sandbox`, `/tmp/cds-storage`, `/data/egress-audit`, `/data/shared-volumes`, `/var/lib/cds/sandbox-results`).
- Empty-string suppression idiom (`pvc.yaml`, `minio.yaml`, `kms.masterKeyFile`): a blank value must render nothing — `""` in `storageClassName` means "no StorageClass" and kills dynamic provisioning.
- Every non-obvious value carries its decision in a Chinese comment with a task ID (P2-3/P2-5/P6/A302/CB-01..06/W3/W12/N10/SS-19/FG-008) — the chart's comments are its ledger; mirror them when editing.
- Namespace `cds-system`; the Secret is created outside the chart; `tests/test_helm_chart.py` asserts every `enabled: true` component is templated, a declared dep, or whitelisted.

## ANTI-PATTERNS
- ClickHouse and OPA are EXTERNALLY MANAGED in this chart (no templates). Do not add their templates without removing them from `externalComponents` and updating `test_enabled_components_have_templates_or_whitelist`.
- Never reintroduce a dangling `-opa` Service with no matching selector (A302: NXDOMAIN on every policy call). `CDS_OPA_URL` stays a values key, default `http://opa:8181` — the Service `k8s/opa.yaml` creates in the same namespace.
- Never render `replicas` while the HPA owns the count; never render an empty `storageClassName`; keep the PDB template guarded on `{{- if .Values.podDisruptionBudget }}` so an overlay that deletes the block skips the object instead of nil-pointering the render.
- Don't flip `minio.enabled` to `true` in shipped values — it would rewrite existing installs. Keep the MinIO creds non-optional: `CreateContainerConfigError` is a better failure than a credential-less pod.
- `helm/cds/charts/` is `helm dependency build` output — generated, never hand-edited; the dependency declaration lives in `Chart.yaml`.
- The drift older copies of this file recorded is CLOSED and test-pinned, so don't restate or re-create it: `CDS_LOG_JSON: "true"` (P6, 2026-10-30, single source = prod posture, `test_deploy_manifest_gates` PROD_POSTURE) and PG user `cds` (`postgresql.auth.username` + `cds-db-credentials`). The MinIO tag `RELEASE.2025-01-20T14-49-07Z` here is one of the 7 lockstep copies — `:latest` is banned, an upgrade is a human release review.

## COMMANDS
```bash
helm dependency build ./helm/cds          # REQUIRED first
helm lint ./helm/cds
helm template cds helm/cds                # release-gate §4
helm upgrade --install cds helm/cds -n cds-system --create-namespace -f helm/cds/values.yaml
python -m pytest -q tests/test_helm_chart.py tests/test_deploy_manifests.py tests/test_integrity_patrol.py
```
