# app/services/

**Score 15 — distinct domain: the security/sandbox core.** 107 flat modules (~39.5k LOC), no subpackages. The most-imported layer in the repo (`audit_service` 42 importers, `crypto_service` 26, `kms_service` 19).

## WHERE TO LOOK
| Domain | Modules |
|--------|---------|
| Sandbox runtimes | `sandbox_runtime` (5 adapters L0/L1/L2/L3/K8s + 7 `SceneRuntime` subclasses, 2561 L), `firecracker_runtime`, `tee_simulator`, `gpu_tee_simulator`, `sandbox_security`, `k8s_sandbox`/`k8s_client` |
| Session lifecycle | `session_lifecycle` (cleanup loop), `session_state_machine`, `session_operations`, `session_files`, `session_snapshots`, `session_templates`, `shared_volumes`, `quota_manager`, `sandbox_manager` |
| Task & ML pipelines | `task_pipeline` (orchestrator, 872 L), `task_queue`, `task_state_machine`, `rag_service`, `inference_service`, `agent_service`, `cpu_trainer`, `llm_sft_runtime`, `vision_pipeline`, `unstructured_pipeline`, `cdc_agent` |
| Crypto / KMS / identity | `crypto_service`, `kms_service` (+ `kms_recovery`), `certificate_service` (hand-rolled X.509 DER, 922 L), `remote_attestation`, `hsm_adapter`, `tlcp_service`, `mpc_service`, `federation_connector`, `blockchain_adapter` |
| Output / data protection | `output_gateway`, `output_security`, `output_inspection`, `output_policy`, `pii_ner`, `dp_budget`, `network_policy`, `egress_audit`, `rls_engine`, `column_encryption`, `secure_duckdb` |
| Audit / compliance | `sandbox_audit`, `audit_service`, `audit_partition`, `merkle_service`, `retention_janitor`, `code_scanner` |

## CONVENTIONS
- Import the module-level singleton at file tail (`xxx = XxxService()`) or the factory (`k8s_sandbox.get_k8s_adapter()`) — no DI container, no FastAPI `Depends` for services.
- Lazy in-function imports are the norm (break cycles with torch, redis, kubernetes, Cryptodome). Keep new heavy imports lazy.
- Fail-closed + honest degradation: unsupported input returns `SECURITY ERROR: ...`; simulations expose `is_simulation`/`backend` flags and `main.py` surfaces every enabled fallback at boot (T12) — no silent degradation.
- Envelope layout `[nonce(12)][tag(16)][ciphertext]` (SM4-GCM via `app/utils/crypto.SM4Cipher`) is canonical for workspace/session-file/DB-file encryption. `storage_service` uses a DIFFERENT envelope with a `key_id` header — don't cross-mix.
- Session/DEK keys travel only via env vars (`CDS_SESSION_KEY`, `CDS_DEK_HEX`); never written to disk (KMS enforces).
- Container-ID prefix selects the adapter: `l0-`, `tee-`, `fc-`, `bwrap-`, `k8s-`. Workspaces: `/tmp/cds-sandbox*/{user_id}/{session_id}/{input,output,tmp}`.
- Runner-template services (`rag_service`, `inference_service`, `agent_service`) embed self-contained Python as string literals executed inside the sandbox and validated byte-exact against the trusted template — change template and `validate_*_runner` together.
- Quotas: Redis Lua atomic scripts (`quota_manager`), in-memory fallback when Redis absent; tests use `fakeredis[lua]`.
- Validation errors raise `CDSError(code=...)` from `app.core.errors`, not bare exceptions.
- K8s dual path: typed `kubernetes` client when `K8S_USE_PYTHON_CLIENT`, else kubectl subprocess with a `__CDS_EXIT__:` stdout sentinel protocol.

## ANTI-PATTERNS (THIS PROJECT)
- Simulation ladders are design, not bugs to delete: firecracker `firecracker > qemu-tcg > simulation` (simulation = plain subprocess, NO isolation), `tee_simulator` software attestation, `cpu_trainer._simulate_fallback` (fail-closed under `TRAINING_REQUIRE_TORCH`), `gpu_tee_runtime` stubs.
- Known defect (S4): firecracker seccomp retry (`cmd_no_seccomp`) is NOT gated by `SECCOMP_FALLBACK_ALLOWED`, unlike `sandbox_runtime` — inconsistent isolation posture.
- Duplicated logic to consolidate, not copy: bwrap arg construction (4 copies), SM3 (3 implementations: `utils/crypto`, `crypto_service`, `gateway_service`), X.509/PEM building (3), gmssl ImportError guards (3), ClickHouse URL parsing (`audit_service` + `gateway_service`).
- `vision_pipeline`/`vision_multimodal_runtime` training is a deterministic hash-seeded loop, NOT torch; `model_watermark` is a seeded-PRNG scheme. Tests pin these behaviors.
- `retention_janitor`: audit logs & Merkle leaves are NEVER purged (retention=0 = keep forever); dry-run is ON by default.

## HOTSPOTS
`sandbox_runtime` 2561, `secure_duckdb` 1006, `firecracker_runtime` 1004, `certificate_service` 922, `sandbox_audit` 880, `task_pipeline` 872 (its `_default_running_handler` is a ~200-line monolith). `remote_attestation`, `federation_connector`, `gateway_service` (593, AuthN→AuthZ→Route→Execute→ContentSecurity→Meter→Audit pipeline).

## COMMANDS
```bash
python -m pytest -q tests/test_sandbox_runtime.py tests/test_rag_service.py tests/test_kms_attestation_required.py
```
Scoped failures must not grow the `ci/known-failures.md` baseline.
