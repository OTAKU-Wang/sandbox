# app/services/

**Score 15 — distinct domain: the security/sandbox core.** 117 flat modules + `__init__.py` (~45.7k LOC), no subpackages. The most-imported layer in the repo (`audit_service` 52 importers, `sandbox_runtime` 27, `crypto_service` 35, `storage_service` 21, `kms_service` 26 — counts are files, not references).

## WHERE TO LOOK
| Domain | Modules |
|--------|---------|
| Sandbox runtimes | `sandbox_runtime` (adapter registry + facade + `SceneRuntime` factory, 2621 L), `cube_runtime_adapter` (L2' Cube microVM data plane — the SS-13 production path, 1059 L), `firecracker_runtime` / `k8s_sandbox` + `k8s_client` (LEGACY, registered only behind `SANDBOX_LEGACY_ENABLED`/`LEGACY_L2_ENABLED`), `tee_simulator`, `gpu_tee_simulator`, `sandbox_security` |
| Cube dataplane (SS-10/SS-13) | `cube_node_client` (gRPC pool + `map_ret_code` → `CDSError`), `cube_template_service` (build/sign/push/mirror/breaker, 896 L), `cube_egress` + `cube_egress_audit`, `policy_compiler` (→ Cube annotation strings), `capability_matrix` (single authority for real/simulated/unsupported/planned) |
| Session lifecycle | `session_lifecycle` (cleanup loop), `session_state_machine`, `session_operations`, `session_files`, `session_snapshots`, `session_templates`, `shared_volumes`, `quota_manager`, `sandbox_manager` |
| Task & ML pipelines | `task_pipeline` (orchestrator, 872 L), `task_queue`, `task_state_machine`, `rag_service`, `inference_service`, `agent_service`, `cpu_trainer`, `llm_sft_runtime`, `vision_pipeline`, `unstructured_pipeline`, `cdc_agent` |
| Crypto / KMS / identity | `crypto_service`, `kms_service` (+ `kms_recovery`), `certificate_service` (hand-rolled X.509 DER, 922 L), `remote_attestation`, `hsm_adapter`, `tlcp_service`, `mpc_service`, `federation_connector`, `blockchain_adapter` |
| Output / data protection | `output_gateway`, `output_security`, `output_inspection`, `output_policy`, `pii_ner`, `dp_budget`, `network_policy`, `egress_audit`, `rls_engine`, `column_encryption`, `secure_duckdb` |
| Audit / compliance | `sandbox_audit`, `audit_service`, `audit_partition`, `merkle_service`, `retention_janitor`, `code_scanner` |
| SS-15 flow (dev→test→prod→deliver) | `workflow_service` (the admission gate — production may only run a workload that passed the test sandbox on the SAME entrypoint hash; `GATE_*` codes), `compute_scheduler`, `delivery_service`, `notebook_service`, `data_product_sandbox`, `contract_service` |

## CONVENTIONS
- Import the module-level singleton at file tail (`xxx = XxxService()`) or the factory (`k8s_sandbox.get_k8s_adapter()`) — no DI container, no FastAPI `Depends` for services.
- Lazy in-function imports are the norm (break cycles with torch, redis, kubernetes, Cryptodome). Keep new heavy imports lazy.
- Fail-closed + honest degradation: unsupported input returns `SECURITY ERROR: ...`; simulations expose `is_simulation`/`backend` flags and `main.py` surfaces every enabled fallback at boot (T12) — no silent degradation.
- Envelope layout `[nonce(12)][tag(16)][ciphertext]` (SM4-GCM via `app/utils/crypto.SM4Cipher`) is canonical for workspace/session-file/DB-file encryption. `storage_service` uses a DIFFERENT envelope with a `key_id` header — don't cross-mix.
- Session/DEK keys travel only via env vars (`CDS_SESSION_KEY`, `CDS_DEK_HEX`); never written to disk (KMS enforces).
- Container-ID prefix selects the adapter: `l0-`, `tee-`, `fc-`, `bwrap-`, `k8s-`, `l2c-`. Workspaces: `/tmp/cds-sandbox*/{user_id}/{session_id}/{input,output,tmp}`. Per SS-13 an L2'/`l2c` request never degrades to process isolation — closed nodes or dataplane disabled returns `RUNTIME_UNAVAILABLE`.
- Sandbox-level capability claims come from `capability_matrix.get_capability_matrix()` only — never re-derive `real|simulated|unsupported|planned` at a call site.
- Runner-template services (`rag_service`, `inference_service`, `agent_service`) embed self-contained Python as string literals executed inside the sandbox and validated byte-exact against the trusted template — change template and `validate_*_runner` together.
- Quotas: Redis Lua atomic scripts (`quota_manager`), in-memory fallback when Redis absent; tests use `fakeredis[lua]`.
- Validation errors raise `CDSError(code=...)` from `app.core.errors`, not bare exceptions.
- K8s dual path: typed `kubernetes` client when `K8S_USE_PYTHON_CLIENT`, else kubectl subprocess with a `__CDS_EXIT__:` stdout sentinel protocol. The whole k8s level is deprecated in favour of the Cube dataplane (SS-10 CB-01) — do not extend it.
- Not everything is GM crypto: `delivery_service.sign_payload` is HMAC-SHA256 (`X-Signature: t=…,v=…`, replay window ±300 s) while audit anchoring is SM3 — check the module before assuming 国密.

## ANTI-PATTERNS (THIS PROJECT)
- Simulation ladders are design, not bugs to delete: firecracker `firecracker > qemu-tcg > simulation` (simulation = plain subprocess, NO isolation), `tee_simulator` software attestation, `cpu_trainer._simulate_fallback` (fail-closed under `TRAINING_REQUIRE_TORCH`), `gpu_tee_runtime` stubs.
- ~~Known defect (S4)~~ CLOSED (PZ-04/MR-A13): `firecracker_runtime` now mirrors `sandbox_runtime._seccomp_fallback_allowed`, so the seccomp-less retry is gated by `SECCOMP_FALLBACK_ALLOWED` everywhere and fails honestly when denied.
- Two SM3 paths in `crypto_service`: `sm3_hash` honours `SM3_FALLBACK_ALLOWED`, but the internal `_sm3_hash_with_z`/`sm2_sign` use gmssl directly and only raise `RuntimeError` when gmssl is absent — that path has no GM/T-claim gate.
- Duplicated logic to consolidate, not copy: bwrap arg construction (4 copies), SM3 (3 implementations: `utils/crypto`, `crypto_service`, `gateway_service`), X.509/PEM building (3), gmssl ImportError guards (3), ClickHouse URL parsing (`audit_service` + `gateway_service`).
- `vision_pipeline`/`vision_multimodal_runtime` training is a deterministic hash-seeded loop, NOT torch; `model_watermark` is a seeded-PRNG scheme. Tests pin these behaviors.
- `retention_janitor`: audit logs & Merkle leaves are NEVER purged (retention=0 = keep forever); dry-run is ON by default.

## HOTSPOTS
`sandbox_runtime` 2621, `cube_runtime_adapter` 1059, `firecracker_runtime` 1056, `secure_duckdb` 1005, `llm_sft_runtime` 958, `certificate_service` 921, `cube_template_service` 896, `sandbox_audit` 879, `task_pipeline` 871 (its `_default_running_handler` is a ~200-line monolith). Also `rag_service` 817, `k8s_sandbox` 806, `remote_attestation`, `federation_connector`, `gateway_service` (592, AuthN→AuthZ→Route→Execute→ContentSecurity→Meter→Audit pipeline).

## COMMANDS
```bash
python -m pytest -q tests/test_sandbox_runtime.py tests/test_rag_service.py tests/test_kms_attestation_required.py
```
Scoped failures must not grow the `ci/known-failures.md` baseline.
