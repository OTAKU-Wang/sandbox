# helm/ — the cds chart (scored 8: single chart at `helm/cds/` — NOT `helm/cds/cds/` as some task lists claim)

Only deploy path with HPA/PDB/ingress. `values.yaml` is one of two copies of the canonical CDS_* env contract (~40 vars; the other is root `docker-compose.prod.yml`).

## STRUCTURE
```
helm/cds/
├── Chart.yaml       # bitnami postgresql + redis subcharts — declared, NOT vendored
├── values.yaml      # full CDS_* env contract + externalComponents whitelist
└── templates/       # deployment-api, deployment-frontend, service, configmap, hpa, pdb, ingress, pvc
                      # _helpers.tpl: cds.name / cds.fullname / cds.labels / cds.api.selectorLabels / cds.frontend.selectorLabels
```

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Change a CDS_* knob | `values.yaml` AND root `docker-compose.prod.yml` — dual copy, update both |
| Secrets | `values.yaml` `secrets.existingSecret` (default `cds-secrets`) — must carry the same 14 keys as `k8s/secrets.yaml` |
| In-chart vs external infra | `values.yaml` `externalComponents` list |

## CONVENTIONS
- `helm dependency build ./helm/cds` is REQUIRED before `helm template`/`install` (bitnami charts fetched from charts.bitnami.com, not vendored — see Chart.yaml comment).
- Namespace `cds-system`; secret `cds-secrets` is created outside the chart.
- `tests/test_helm_chart.py` (in the backend test suite) asserts the externalComponents invariant: every component with `enabled: true` but no in-chart template MUST be listed in `externalComponents`.

## ANTI-PATTERNS
- ClickHouse and OPA are EXTERNALLY MANAGED in this chart (no templates). Do not add their templates without removing them from `externalComponents` and updating the test expectation.
- The historical dangling `-opa` Service template was removed; don't reintroduce an OPA Service with no matching selector.
- Known drift vs compose paths: `CDS_LOG_JSON` "false" here vs true in prod compose; postgres user `cds_admin` here vs `cds` in compose.
- `minio/minio:latest` is unpinned in all five deploy paths.
