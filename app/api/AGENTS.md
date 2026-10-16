# app/api/

**Score 15 — distinct domain: HTTP route layer.** 44 route modules (~15.9k LOC) + empty `__init__.py`. Each module's only export is `router = APIRouter()`, consumed by `app/main.py`.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Session lifecycle + KMS key distribution + DLP-gated exec | `sandbox_sessions.py` (2609 L, 26 routes) | god module: contract validation, attestation rollback, proof bundles, snapshots; `create_sandbox_session` ~300 lines |
| SS-15 flow surface | `flow_runtime.py`, `workloads.py`, `notebooks.py`, `compute.py`, `deliveries.py`, `meta.py` | the 11 business routers that take `get_business_user` (human OR `cds_ak_` machine key) instead of `get_current_user`; per-customer prefix `/open/v1` lives in `open_api.py` |
| In-sandbox SQL / DuckDB | `sandbox_db.py::get_encryption_config_for_session` | cross-route import hub — `deliveries.py`/`notebooks.py` also lazily import `sandbox_sessions` privates (`_l2c_file_op`, `_session_dek`, `_session_workspace_or_400`) |
| WS exec stream | `session_stream.py` | absolute path, no prefix; `issue_ws_ticket` mints tickets |
| Training (SFT + watermark + secure checkpoint) | `training.py` (922 L) | |
| K8s status | `k8s_ops.py` | read-only, honest: no cluster → last-known view, never a fabricated list |
| Synthetic data | `data_products.py`, `data_resources.py` | old `test-data/mock` / `generate-mock` endpoints are `deprecated=True` aliases of the new generator |
| Audit trail + Merkle anchoring | `audit.py` | `/anchor` SM3-hashes records (`id|action|resource_type|resource_id|created_at`) → Merkle root via `blockchain_adapter` (PG append-only / FISCO BCOS / AntChain), stamps `blockchain_tx_hash`; every anchor/verify response carries `_backend_disclosure` (T8: `is_consortium_chain`) so the PG log is never mistaken for on-chain |
| Unstructured pipeline (OCR/ASR/video/dicom) | `data_pipeline.py` | `pipe-` tasks are written to the `pipeline_task` table AND executed inline in the request via in-memory `pipeline` — not the durable `task_queue`; uploads land in `/tmp/cds-uploads/<user_id>/` (created at import); `/process-path` accepts arbitrary local filesystem paths, ADMIN/OPERATOR only |

## CONVENTIONS
- No module-level prefix: the prefix is assigned ONCE in `main.py` `include_router(...)`. Deviations are deliberate: `health`/`metrics` at root, `open_api` under `/open/v1`, `cube_egress` under `/internal/audit` (mTLS + `require_human`), `session_stream` absolute WS path, `admin` on a separate app.
- AuthN via `app.core.deps.get_current_user` / `require_roles` (30 modules) or `get_business_user` (11 SS-15/business modules); 5 modules use neither (`health`, `metrics`, `admin`, `session_stream` WS-ticket, `cube_egress` mTLS + `require_human`). Function-permission checks (`require_group`) are mounted in `main.py`, never repeated inside a handler. Authorization is NOT shared — each module defines local `_can_view_all_*` / `_can_operate_*` / `_can_manage_*` helpers.
- List envelope `{items, total, page, page_size}`; opt-in keyset cursor (W8) via `X-Next-Cursor` header (no `total` in cursor mode).
- Every mutating endpoint calls `audit_service.log(db, action=...)` (140 call sites in this dir). `audit.py` adds Merkle anchoring; the PG log is append-only tamper-evident, NOT the chain.
- Fail-closed intent is written into comments ("never released", "never widen", "never written to disk") — new endpoints must keep that posture.

## ANTI-PATTERNS (THIS PROJECT)
- Role helpers are copy-pasted per module (`_can_view_all_products` identical in `data_products.py` AND `field_exposure.py`; near-identical variants in `sandbox_sessions`, `contracts`, `network_policy`, `output_control`, `sandbox_db`). Consolidating into `core/deps` is the known direction — don't add more copies.
- `sandbox_sessions.py` is the complexity sink; split before adding features there.
- Some HTTPException details are Chinese (429 quota messages) — match local language when editing that file.
- `admin.py` uses raw Jinja2 + `HTMLResponse` on purpose (Starlette TemplateResponse breaks on Python 3.14+); the pages have no auth — leave `ADMIN_PAGES_ENABLED` off.

## HOTSPOTS
>500 L: `sandbox_sessions` 2609, `training` 922, `connectors` 717, `data_products` 714, `sandbox_db` 634, `deliveries` 588, `monitoring` 582, `auth` 551. `monitoring` security-posture payloads are advisory prose, not enforcement.
