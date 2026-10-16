# app/schemas/

**Score 13 — distinct domain: pydantic wire contract.** 11 modules + empty `__init__.py` / 1,148 LOC / 55 models (22 importing files; no barrel, no `__all__` — import by full path).

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Session create/exec/file requests | `sandbox_session.py` | the most-imported module here (6 importers) |
| High-risk operation confirmation | `high_risk_operation.py` | `HighRiskOperationRequest{reason: 8–500 chars, ticket_id}` reused by 4 routers (contracts, certificates, connectors, kms) — the shared base for any endpoint that can leak or destroy data |
| Flow / delivery config (SS-15) | `flow.py` | `DeliveryMethod = Literal["dataset","open_api","result_push"]`, `ComputeFrequency = Literal["manual","hourly","daily"]`, `WorkloadEnv = Literal["test","production"]` — Literal aliases, not enums; plus the `Notebook*/Workload*/ComputeRun*` Create/Out shapes |
| Contract terms validation | `contract.py` (189 L) | 12 `@field_validator`s enforce the contract policy ceiling (sandbox modes/levels, duration, DP epsilon, output rows/formats, signature, timestamp) at the edge |
| Volume-DEK wire shapes | `kms.py` | machine-consumed JSON exchanged with `cds-volume-plugin/kms.go` — see ANTI-PATTERNS |
| Catalog / product / resource shapes | `data_product.py`, `data_resource.py`, `compliance.py`, `field_exposure.py`, `network_policy.py`, `contract.py`, `user.py` | `data_product.py` keeps its own `VALID_*` tuples instead of importing model enums |

## CONVENTIONS
- Naming is `XxxCreate` / `XxxUpdate` / `XxxResponse` (older modules use `XxxOut`); 13 response models set `model_config = {"from_attributes": True}` for ORM reads.
- Two enum styles coexist deliberately: ORM layers use `str, Enum`, request schemas use `Literal[...]`. Match the module you are extending; never invent a third style.
- Validation belongs here (pydantic validators), not in the router — `contract.py` is the model to copy for policy ceilings.
- Responses never expose key material: `kms.py` returns the wrapped-key ref only ("never raw DEK material"), and no schema here carries an app secret or its hash — `app_secret_hash` exists only on `models/app_credential.py` (`SM3(app_secret)`, compared with `hmac.compare_digest` in `services/gateway_service.py`).
- Field validation errors surface as FastAPI 422; domain errors use `CDSError(code=...)` from `app.core.errors` — a schema must not raise `CDSError` itself.

## ANTI-PATTERNS (THIS PROJECT)
- `schemas/kms.py` field names are a **wire contract with the Go plugin** (`cds-volume-plugin/kms.go`, contract in `cds-volume-plugin/README.md` “KMS 契约”): `Register {volume_id, name, dek_hex}` (+ the later optional `cipher`) → `{key_id}`, `Distribute {session_id}` → `{key_hex}`, `Revoke` → 204. Never rename or reshape these without changing the Go client in lockstep — no Python-side test catches a rename.
- The optional `cipher` field must stay optional and must NOT default to a guessed SM4-GCM: the plugin declares what it actually sealed with, and an omitted value records `unknown` so compliance never claims GM crypto for an AES-sealed volume (honest-degradation rule).
- `schemas/__init__.py` is empty on purpose — do not add re-exports to "clean up" imports.

## COMMANDS
```bash
python -m pytest -q tests/test_kms_volume_keys.py tests/test_ss15_flow_unit.py   # wire-shape regressions (see tests/AGENTS.md)
```
