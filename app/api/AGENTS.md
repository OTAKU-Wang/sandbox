# app/api/

**Score 15 — distinct domain: HTTP route layer.** 34 route modules (~12.5k LOC) + empty `__init__.py`. Each module's only export is `router = APIRouter()`, consumed by `app/main.py`.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Session lifecycle + KMS key distribution + DLP-gated exec | `sandbox_sessions.py` (2158 L) | god module: contract validation, attestation rollback, proof bundles, snapshots; `create_sandbox_session` ~300 lines |
| Encryption config for sessions | `sandbox_db.py::get_encryption_config_for_session` | the only in-scope cross-route import |
| WS exec stream | `session_stream.py` | absolute path, no prefix; `issue_ws_ticket` mints tickets |
| Training (SFT + watermark + secure checkpoint) | `training.py` (864 L) | |
| K8s status | `k8s_ops.py` | read-only, honest: no cluster → last-known view, never a fabricated list |
| Synthetic data | `data_products.py`, `data_resources.py` | old `test-data/mock` / `generate-mock` endpoints are `deprecated=True` aliases of the new generator |
| Audit trail + Merkle anchoring | `audit.py` | `/anchor` SM3-hashes records (`id|action|resource_type|resource_id|created_at`) → Merkle root via `blockchain_adapter` (PG append-only / FISCO BCOS / AntChain), stamps `blockchain_tx_hash`; every anchor/verify response carries `_backend_disclosure` (T8: `is_consortium_chain`) so the PG log is never mistaken for on-chain |
| Unstructured pipeline (OCR/ASR/video/dicom) | `data_pipeline.py` | `pipe-` tasks are written to the `pipeline_task` table AND executed inline in the request via in-memory `pipeline` — not the durable `task_queue`; uploads land in `/tmp/cds-uploads/<user_id>/` (created at import); `/process-path` accepts arbitrary local filesystem paths, ADMIN/OPERATOR only |

## CONVENTIONS
- No module-level prefix: the prefix is assigned ONCE in `main.py` `include_router(...)`. Deviations are deliberate: `health`/`metrics` at root, `session_stream` absolute WS path, `admin` on a separate app.
- AuthN via `app.core.deps.get_current_user` / `require_roles`. Authorization is NOT shared — each module defines local `_can_view_all_*` / `_can_operate_*` / `_can_manage_*` helpers.
- List envelope `{items, total, page, page_size}`; opt-in keyset cursor (W8) via `X-Next-Cursor` header (no `total` in cursor mode).
- Every mutating endpoint calls `audit_service.log(db, action=...)`. `audit.py` adds Merkle anchoring; the PG log is append-only tamper-evident, NOT the chain.
- Fail-closed intent is written into comments ("never released", "never widen", "never written to disk") — new endpoints must keep that posture.

## ANTI-PATTERNS (THIS PROJECT)
- Role helpers are copy-pasted per module (`_can_view_all_products` identical in `data_products.py` AND `field_exposure.py`; near-identical variants in `sandbox_sessions`, `contracts`, `network_policy`, `output_control`, `sandbox_db`). Consolidating into `core/deps` is the known direction — don't add more copies.
- `sandbox_sessions.py` is the complexity sink; split before adding features there.
- Some HTTPException details are Chinese (429 quota messages) — match local language when editing that file.
- `admin.py` uses raw Jinja2 + `HTMLResponse` on purpose (Starlette TemplateResponse breaks on Python 3.14+); the pages have no auth — leave `ADMIN_PAGES_ENABLED` off.

## HOTSPOTS
>500 L: `sandbox_sessions` 2158, `training` 864, `connectors` 718, `data_products` 708, `sandbox_db` 635, `monitoring` 580. `monitoring` security-posture payloads are advisory prose, not enforcement.
