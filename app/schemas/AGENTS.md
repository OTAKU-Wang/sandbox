# app/schemas/

**Score 25 — distinct domain: pydantic wire contract.** 11 modules + empty `__init__.py` = 1,213 LOC / 55 models / 48 `@field_validator`s / 13 `from_attributes` responses (each = `grep -c` over `app/schemas/*.py`). 38 `from app.schemas…` import statements in tracked .py (`git grep -c "from app\.schemas" -- "*.py"`; 30 of them in `app/`, 29 importer files); no barrel, no `__all__` — import by full path.

## WHERE TO LOOK
| Task | Module | Notes |
|------|--------|-------|
| Session create/exec/file requests | `sandbox_session.py` (174 L, 4 models) | the most-imported module here (6 importer files outside `app/schemas/`, then flow / contract at 5, high_risk_operation / user at 4 — `git grep -l "from app\.schemas.<mod>" -- "*.py"`). `sandbox_session.py:129-130` sets `sandbox_engine = "cube_microvm"` only for `l2c` — a non-cube level must never claim the cube engine |
| High-risk operation confirmation | `high_risk_operation.py` | `HighRiskOperationRequest{reason: 8–500 chars, ticket_id}` reused by 4 routers (contracts, certificates, connectors, kms) — the shared base for any endpoint that can leak or destroy data |
| Flow / delivery config (SS-15) | `flow.py` (236 L, 17 models — the largest module here) | `DeliveryMethod = Literal["dataset","open_api","result_push"]`, `ComputeFrequency = Literal["manual","hourly","daily"]`, `WorkloadEnv = Literal["test","production"]` — Literal aliases, not enums; plus the `Notebook*/Workload*/ComputeRun*` Create/Out shapes |
| Zero-trust refusals at the edge | `network_policy.py:23-25`, `field_exposure.py:56-58`, `compliance.py:36-40` | an empty `allowed_ports` is refused ("use `mode=deny_all` to block all egress") rather than meaning "no port filter"; `requested_fields` cannot be empty; the anchoring report carries a backend-disclosure model so it "must never be read as 已上链". These refusals belong in the schema, not the router |
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
- `schemas/kms.py` field names are a **wire contract with the Go plugin** (`cds-volume-plugin/kms.go`, contract in `cds-volume-plugin/README.md` “KMS 契约”): `Register {volume_id, name, dek_hex, cipher?}` → `{key_id}`, `Distribute {session_id}` → `{key_hex}`, `Revoke` → 204, served at `app/api/kms.py:438/497/533`. Never rename or reshape these without changing the Go client in lockstep — the Go side builds the body as `map[string]string` and decodes `key_id`/`key_hex` by literal. Partial coverage exists: `tests/test_kms_volume_keys.py` pins `GO_REGISTER_RESP_KEY="key_id"`, `GO_DISTRIBUTE_REQ_KEY="session_id"`, `GO_DISTRIBUTE_RESP_KEY="key_hex"` and the exact `Cipher.Name()` strings, but the REQUEST-side names (`volume_id`, `name`, `dek_hex`) are only caught by the integration path, not by a schema test.
- The optional `cipher` field must stay optional and must NOT default to a guessed SM4-GCM: the plugin declares what it actually sealed with, and an omitted value records `unknown` so compliance never claims GM crypto for an AES-sealed volume (honest-degradation rule).
- `schemas/__init__.py` is empty on purpose — do not add re-exports to "clean up" imports.
- Never let an empty collection mean "everything": that class of bug is refused here (`network_policy.py:25`, `field_exposure.py:56-58`), and the same reasoning applies to any new list-typed request field.
- Never leak storage internals: `data_product.py:122` — "`encrypted_storage_path` and `encryption_key_id` are NEVER exposed to clients".

## COMMANDS
```bash
python -m pytest -q tests/test_kms_volume_keys.py tests/test_ss15_flow_unit.py   # wire-shape regressions (see tests/AGENTS.md)
```
