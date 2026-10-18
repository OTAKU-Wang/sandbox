# Verdicts — FE medium batch 1 (w4)

Reviewer scope: `utils/cn.ts`, `utils/roles.ts`, `types/enums.ts`, `services/dataResourceApi.ts`, `services/dataProductApi.ts`, `services/dataPipelineApi.ts` (29 claims: M-134…M-142, M-172…M-174, M-192…M-193, M-279…M-287, M-306, M-310…M-311, M-314…M-316).

Baseline confirmed before judging any "wrong response shape" claim: `cds-frontend/src/services/api.ts:37` → `api.interceptors.response.use((response) => response.data, …)` and `api.ts:100` → `Promise.reject(error.response?.data || error)`. So the awaited value inside a service module **is** the parsed body (no double unwrap anywhere in these three files), and 4xx already surfaces as the W2 envelope. `api.ts:6` pins `timeout: 30000` for every request.

---

### cds-frontend/src/services/dataPipelineApi.ts:29 — 潜在 (—)
Mechanism (M-134): `skip/limit` are passed through unclamped, so an out-of-range `page`/`page_size` would hit FastAPI's `skip: int = Query(0, ge=0)` / `limit: int = Query(20, ge=1, le=100)` (verified `app/api/data_pipeline.py:157-158`) and return 422 with a Pydantic-array `detail`.
Evidence: the only caller in the whole tree is `cds-frontend/src/pages/DataPipeline.tsx:53` → `listTasks({ page: 1, page_size: 10 })` (grep `dataPipelineApi` → 3 matches, 2 files); `read` of DataPipeline.tsx:51-54 confirms the literals. Nothing can produce `page=0`/`limit>100` today, so the 422 is reachable only after someone wires a real pager or a status filter.
Convention note: the repo does align `page_size` to the backend bound by hand elsewhere (`services/flow.ts:598` "与后端 page_size 上界（le=100）对齐"), so a clamp is in-convention — but it is hardening, not a live defect.

### cds-frontend/src/services/dataPipelineApi.ts:36 — 潜在 (—)
Mechanism (M-135): `` `/data-pipeline/tasks/${taskId}` `` is un-encoded, so a value containing `/`, `?` or `..` would change the request path (backend takes `task_id: str`, `app/api/data_pipeline.py:191` — not UUID-validated, so traversal would actually resolve).
Evidence: `getTask` is never called — grep for `dataPipelineApi.getTask|getTask\(` over `cds-frontend/src` → `matches=0`; the only pipeline route is `App.tsx:133` `<Route path="data-pipeline" …>` with no `:id` child, so no detail screen exists to feed it.
Becomes real the moment a task-detail page passes a URL-derived id; `encodeURIComponent` at the boundary is the right pre-emptive fix.

### cds-frontend/src/services/dataPipelineApi.ts:6 — 潜在 (—)
Mechanism (M-280): `status`/`task_type` typed as bare `string` (dataPipelineApi.ts:8-9) loses the closed enum, and the consumer map drifts from it.
Evidence: the drift is real — `pages/DataPipeline.tsx:13-18` colours only `pending/running/completed/failed`; backend declares `queued`/`cancelled` (`app/models/pipeline_task.py:15,19`). But grep `PipelineTaskStatus` over `app/` shows the only writes are `PENDING` (`app/api/data_pipeline.py:69,126`) and `COMPLETED`/`FAILED` (`:82,:137`) — `queued`/`cancelled` are never assigned to a PipelineTask, and the render falls back to `<Tag color={… ?? 'default'}>{v}</Tag>` (DataPipeline.tsx:106), i.e. honest grey tag with the raw code, no wrong information. The `status` filter argument is likewise never passed by any caller.

### cds-frontend/src/services/dataPipelineApi.ts:29 — 高估 (—)
Mechanism (M-279): `/data-pipeline/tasks` is written twice (lines 29 and 37) instead of a module constant.
Verified the two occurrences, but inline endpoint strings inside the single `services/` boundary module are the declared convention (`services/AGENTS.md`: "page→offset conversion is inline per call"; same shape in `auditApi.ts:177`, `contractApi.ts:39`, `catalogApi.ts:32`). A rename is a two-line grep-and-replace inside one file that no other module imports paths from → no user-observable consequence. Maintainability taste, not a defect.

### cds-frontend/src/services/dataProductApi.ts:39 — 潜在 (—)
Mechanism (M-136): unclamped `skip/limit` against `skip: Query(0, ge=0)` / `limit: Query(20, ge=1, le=500)` (verified `app/api/data_products.py:142-143`) → `page=0` would send `skip=-20` and get a 422 whose `detail` is an array, which `getErrorMessage` cannot render.
Evidence: every caller passes valid values — `ProductList.tsx:59,185-186` (`useState(1)`, fixed `pageSize: 20`, filter changes reset to page 1), `ContractCreate.tsx:47` (`page:1,page_size:100`), `DataPipeline.tsx:45` (`page:1,page_size:1`), `DevTaskDataPicker.tsx:27,86,104` (`PICKER_PAGE_SIZE = 10`). No caller can exceed `le=500` today.
The sub-claim about `q` is wrong — see the M-281 verdict below.

### cds-frontend/src/services/dataProductApi.ts:43 — 伪 (—)
Mechanism claimed (M-281): an empty `q` is forwarded as `?q=` and makes the backend run a useless `ILIKE '%%'` filter.
Both halves fail. Callers already coalesce: `ProductList.tsx:66` `q: search || undefined`, `DevTaskDataPicker.tsx:104` `q: applied || undefined` (axios drops `undefined` params). And even a literal `?q=` is inert: `app/api/data_products.py:159` is `if q:` — an empty string is falsy, so no `search_filter` is appended and the result/pagination semantics are identical to "no search". No behavior to fix.

### cds-frontend/src/services/dataProductApi.ts:49 — 高估 (—)
Mechanism (M-282): `id` interpolated into the path without `encodeURIComponent`; a crafted `1/../../users` would retarget the request.
Real code shape (`dataProductApi.ts:49,56,59,…`), but no untrusted producer exists: ids come from `useParams()` on `data-products/:id` (`App.tsx:98`, `ProductDetail.tsx:142`) or from `DataProduct.id` in list payloads, every target route is typed `product_id: uuid.UUID` (`app/api/data_products.py:197` etc.) and auth-gated, so the worst case is the logged-in user re-pointing their own request at an endpoint their token already governs. Defense-in-depth nit; the filed "越权路径命中" severity is inflated.

### cds-frontend/src/services/dataProductApi.ts:52 — 高估 (—)
Mechanism (M-137): the resource path `/data-products` is hard-coded 12 times.
Count verified exactly 12 (`(t.match(/\/data-products/g)||[]).length === 12`), i.e. one per method. This is the repo's normal single-boundary style (see the pipeline verdict above); the suggested `productPath()` helper is a fine refactor but its absence causes no defect, and the file is the only place that path appears, so drift risk is a grep away.

### cds-frontend/src/services/dataProductApi.ts:55 — 潜在 (—)
Mechanism (M-283): `PUT` carries partial-update semantics, so if the backend ever tightens PUT to full replacement, omitted optional fields would be nulled.
Verified the premise is currently harmless: `app/api/data_products.py:233-252` (PUT) and `:211-230` (PATCH) are the same body, both running `_update_payload_without_lifecycle_fields` = `model_dump(exclude_unset=True)` (`:42-49`). Stronger than the claim: `dataProductApi.update` has zero call sites (grep `dataProductApi` → 9 files, no `.update(` hit), so even the hypothetical is unreachable until someone builds an edit form. Switching to `api.patch` is correct-by-semantics and free.

### cds-frontend/src/services/dataProductApi.ts:76 — 伪 (—)
Mechanism claimed (M-284): `change_summary = ''` always sent pollutes version history (empty string instead of NULL), and the inline return type duplicates `ProductVersionSummary`.
The DB gets `""` either way — the backend signature is `change_summary: str = ""` (`app/api/data_products.py:607`), so omitting the param stores the identical value; and the only consumer renders `render: (value) => value || '-'` (`pages/DataProducts/ProductDetail.tsx:447`), so "user left it blank" vs "not filled" is indistinguishable by design, not by this bug. The inline return type is exactly what the endpoint returns (`data_products.py:654-660`); `ProductVersionSummary` adds `is_latest`/`created_at` which this response does not carry, so the proposed `Pick<>` reuse would type-lie.

### cds-frontend/src/services/dataResourceApi.ts:35 — 真 (low)
Mechanism (M-138): `list()` downloads the entire resource list on every page switch and slices in the browser, because the backend really has no pagination — `app/api/data_resources.py:222-231` is `@router.get("/", response_model=list[DataResourceResponse])` with only `db`/`current_user` params, and each row carries `schema_fields` (inferred array, wide tables → hundreds of entries).
Evidence: `dataResourceApi.ts:34-40`; callers include count-only uses that pay the full download: `pages/DataPipeline.tsx:41` `list({page:1,page_size:1})` and `pages/DataProducts/ProductCreate.tsx:37` `list({page:1,page_size:100})`, plus `ResourceList.tsx:29` re-fetching everything per page change.
Vitest in one file: yes — `services/__tests__/dataResourceApi.test.ts` with the existing `vi.mock('../api')` harness (see `uploadBoundary.test.ts`) can pin "请求必须带分页参数/不得全量拉取"; today it would fail, which is exactly why the fix belongs in the backend first — flagging that the FE workaround is forced, so severity stays low.

### cds-frontend/src/services/dataResourceApi.ts:37 — 潜在 (—)
Mechanism (M-139): `page`/`page_size` are unvalidated, so `page=0` → negative `start` → `slice(-20,…)` returns the *last* page, and `NaN` → `slice(NaN,NaN)` silently yields `[]` (rendered as "暂无数据").
Evidence: the only live caller is `ResourceList.tsx:21,29` (`useState(1)` + antd `onChange: setPage`, fixed `page_size: 20`), and no query-string/URL feeds it — so neither branch is reachable now. Latent until a caller derives `page` from a URL query or `Number(...)`.
The typing sub-point (`const allItems: DataResource[] = await api.get(...)`, line 35, is an unchecked annotation over axios's `any`) is accurate but has no runtime effect given the interceptor already unwraps `api.ts:37`.

### cds-frontend/src/services/dataResourceApi.ts:42 — 高估 (—)
Mechanism (M-140): `get/delete/sample/desensitize` (lines 42,55,58,61) interpolate `id` without `encodeURIComponent`, so `../` could escape the prefix.
Real pattern, inflated framing: `data-resources` has no `:id` route (App.tsx has no such path), so ids only ever come from backend rows — `ResourceList.tsx:75` `setSelectedResourceId(record.id)`, `:78` preview target, `:79` delete — i.e. `DataResource.id` UUID strings. The backend path params are typed `resource_id: uuid.UUID` (`app/api/data_resources.py:236,251,299,342`) and every route enforces ownership (`:245,261,316,355`), so there is no cross-tenant read and no unauthenticated probe — worst case is self-inflicted path surgery with one's own bearer token. Worth the `resourcePath()` helper as hygiene, not a security finding.

### cds-frontend/src/services/dataResourceApi.ts:45 — 真 (low)
Mechanism (M-141): the upload rides the global `timeout: 30000` (`services/api.ts:6`) while the backend cap is 500 MB (`app/api/data_resources.py:20`, rejected at `:151`), and there is no client-side size/type pre-check — a big file on a slow link dies with `ECONNABORTED` after the browser already streamed most of the body.
Evidence: `dataResourceApi.ts:45-52` passes no config; `ResourceList.tsx:47-57` maps every failure to `message.error('上传失败')`, so the user cannot tell a timeout from a 413. The repo already does per-call overrides for long operations (`flow.ts:1477,1480,1504,1530,1553` with `{ timeout: TIMEOUT_* }`, pinned by `services/__tests__/flow.test.ts:114-115`), so the omission here is a real gap against an established pattern.
Vitest in one file: yes — `services/__tests__/uploadBoundary.test.ts` already asserts on the third `api.post` argument (`headersOf` reads `call[2]`); one added expectation that the upload call carries a raised `timeout` fails today and passes after the fix.

### cds-frontend/src/services/dataResourceApi.ts:58 — 潜在 (—)
Mechanism (M-142): `sample` returns `Promise<unknown>` and forwards `sample_size`/`method` unchecked against `ge=1, le=10000` / `pattern="^(random|systematic|first_n)$"` (`app/api/data_resources.py:300-301`).
Evidence: the method has zero call sites — grep `\.sample\(` over `cds-frontend/src` → `matches=0` (only `desensitize` is used, `DesensitizedPreviewModal.tsx:18`). So the untyped/`as any` pain and the 422 path both require a future caller. The typed `SampleResult` is a cheap improvement to make when the sampling UI lands; the "returns unmasked rows" caution is valid and matches `data_resources.py:330-337`.

### cds-frontend/src/services/dataResourceApi.ts:55 — 真 (low)
Mechanism (M-285): the delete endpoint answers `{deleted, resource_id, storage_deleted}` (`app/api/data_resources.py:294`) but the service declares `Promise<void>`, and the caller ignores the result anyway — so "record deleted, encrypted object left behind" is reported to the user as a clean success.
Evidence: `dataResourceApi.ts:55-56`; `ResourceList.tsx:38-45` → `onSuccess: () => message.success('删除成功')`. `storage_deleted` really can be false: `app/services/storage_service.py:247-258` returns `False` when the local object path is missing (and the KMS destroy is swallowed by `except Exception: pass`, `data_resources.py:281-286`). Against the project's honest-degradation rule this is a real, if narrow, disclosure gap.
Vitest in one file: no — the runtime already returns the body, so no vitest assertion fails today; this is a type + page-message fix (`DeleteResourceResult` + conditional copy), verifiable only by a page-level test after the type change.

### cds-frontend/src/services/dataResourceApi.ts:61 — 真 (medium)
Mechanism (M-286): the UI treats `masking_applied` as proof of masking, but the backend hard-codes it — `app/api/data_resources.py:373` returns `"masking_applied": True` unconditionally, while `_desensitize_csv` masks only fields whose schema sensitivity is `pii|sensitive|high` (`app/services/test_data_generator.py:199-215`) and the upload-time inferrer stamps every column as `"sensitivity": "unknown"` (`app/api/data_resources.py:55`). So for an ordinary uploaded CSV **nothing is masked**, yet `pages/DataResources/DesensitizedPreviewModal.tsx:56-58` shows "以下为脱敏后的样例数据：PII 字段已按策略掩码，不是原始明文".
Evidence read: `dataResourceApi.ts:23-30,61-62` (typed pass-through), `DesensitizedPreviewModal.tsx:49-65` (the green-adjacent assurance Alert), plus the two backend lines above.
Vitest in one file: no — the frontend faithfully renders a field that lies; a passing/failing local vitest test cannot express it. The failing test belongs server-side (`tests/test_data_resources_desensitize*.py`: assert `masking_applied is False` when the schema has no pii/sensitive field); the FE follow-up is to derive the badge from `schema_fields` sensitivity instead of the boolean.

### cds-frontend/src/services/dataResourceApi.ts:37 — 高估 (—)
Mechanism (M-287): `/data-resources/` repeated across 6 methods, `page_size` default 20 inline, and no shared constants for the backend limits (sample 1..10000, upload 500 MB).
All counts verified, all paths live in one boundary module; the "shared constant across FE/BE" proposal has zero precedent in the 22 service modules (`MAX_UPLOAD_BYTES` appears nowhere in `cds-frontend/src`), and the numbers are only observable through a 422/413 that today's callers cannot trigger. Style preference, no user impact.

### cds-frontend/src/types/enums.ts:44 — 高估 (—)
Mechanism (M-172): `| (string & {})` in `SandboxLevel` (line 44) makes the union absorb any string, so literal-typo checks and switch-exhaustiveness on it never fire.
Verified the type and that the trade-off is written down on purpose at `enums.ts:41-43` ("保留 … 字面量补全，同时容忍后端新回传的未知级别"), which matches the real data: `sandbox_level` is a `String(16)` column (`app/models/sandbox_session.py:56`) whose enum can grow without a frontend redeploy. The claimed damage does not exist: nothing branches exhaustively on it — grep `switch (` in `pages/Sandbox/SessionList.tsx` → `matches=0`, and the only consumers are `sandboxLevelLabel`/`isLegacySandboxLevel` (4 call sites, all listed by grep). Real observation, deliberate design, zero impact → inflated.

### cds-frontend/src/types/enums.ts:49 — 高估 (—)
Mechanism (M-173): `SANDBOX_LEVEL_LABELS: Record<string, string>` (line 49) gives no compile-time completeness pressure, and indexing it with `'constructor'`/`'toString'` would return a function through the prototype chain, bypassing `??`.
The prototype path is unreachable: keys can only be the six values of `app/models/sandbox_session.py:13-19` (`L0/L1/L2/l2c/L3/k8s`) and all six exist in the table, so no such value can reach `sandboxLevelLabel(level)` from the API. The completeness half is also inert because `enums.ts:60` has a text fallback. `Object.create(null)` / `hasOwnProperty` is cheap hygiene, same as the roles.ts case below.

### cds-frontend/src/types/enums.ts:60 — 潜在 (—)
Mechanism (M-174): the fallback labels every unmapped value `${level}（历史）` (line 60) while `isLegacySandboxLevel` (line 63) checks `LEGACY_SANDBOX_LEVELS` — so a future new level would print "l2d（历史）" inside the *non*-legacy tag colour at `pages/Sandbox/SessionList.tsx:145` and `pages/Flow/Quota.tsx:217-218`.
Evidence: today the two sets agree exactly (label table lines 49-56 vs backend enum `app/models/sandbox_session.py:13-19`), so nothing mismatched is displayable; SS-13 also froze `l2c` as the only creatable level. Real inconsistency, only reachable when the backend adds a level — then it is a misleading audit label, so worth fixing with `isLegacySandboxLevel(level) ? '（历史）' : ''`.

### cds-frontend/src/types/enums.ts:94 — 伪 (—)
Mechanism claimed (M-306): `SEMI_STRUCTURED: 'semi-structured'` is a snake_case violation that may break the FE↔BE contract.
The backend contract really is hyphenated: `app/models/data_product.py:23` `SEMI_STRUCTURED = "semi-structured"  # JSON, XML, JSONL` and `app/schemas/data_product.py:7` `VALID_PRODUCT_TYPES = ("structured", "unstructured", "semi-structured", "api")`. The FE hard-codes the same string in every consumer (`ProductList.tsx:18,42,152`, `ProductCreate.tsx:89`, `Catalog/index.tsx:24,114`, `Flow/devTasksModel.ts:503`). The `semi_structured` occurrences are a *different* field — the dev-sandbox mode (`app/api/dev_sandbox.py:88`, `pages/Sandbox/DataProductDev.tsx:16,54`) — so there is no inconsistency to fix. Only the "add a comment" suggestion survives as a nicety.

### cds-frontend/src/utils/roles.ts:50 — 高估 (—)
Mechanism (M-192): `LEGACY_ROLE_ALIASES[role]` (line 50) is an object-literal lookup, so `role = 'toString'` reads an inherited member instead of `undefined`.
The very next line neutralises it: `return ROLE_VALUES.includes(normalized) ? normalized as UserRole : undefined` (roles.ts:51) — an inherited function is not in `ROLE_VALUES`, so the result is `undefined` and every gate fails closed. The claim itself concedes 没有造成越权; the `Record<string, UserRole>` type-lie is real but has no observable effect. `Object.create(null)` or a `Map` is a fair cleanup, not a security defect.

### cds-frontend/src/utils/roles.ts:10 — 高估 (—)
Mechanism (M-193): `developer → DATA_PROVIDER` and `security_admin → REGULATOR` would silently upgrade legacy identities.
Unreachable in this system: the backend role domain is exactly the five values at `app/models/user.py:11-16` (`data_provider/buyer/operator/regulator/admin`) with `default=UserRole.BUYER` (`:26`); grep `security_admin` over `app/` → `matches=0`, grep `developer` over `app/` → only doc/spec references. And FE gates grant no authority — `create_data_product` only needs `get_business_user` (`app/api/data_products.py:92`), so nothing the alias unlocks is a real privilege; at worst a legacy value would show entries the backend then rejects. The "与后端授权契约逐条核对" ask is reasonable review hygiene; the "自动获得发布产品/创建合约入口" severity is inflated.

### cds-frontend/src/utils/roles.ts:25 — 潜在 (—)
Mechanism (M-314): `ROLE_GROUPS` carries only `as const`, so nothing ties its members to `UserRole`; a new or renamed role compiles and silently lands in zero groups (invisible pages), whereas `ROLE_LABELS: Record<UserRole, string>` (line 14) would at least fail the build.
Evidence: verified the shape at roles.ts:25-45, and no guard test exists — `src/utils/__tests__/` contains crypto/csvPreview/unwrapList/workspace only. No orphan exists today: all five `UserRole` values appear in at least one group (data_provider in productReaders/productWriters/…, buyer in catalogReaders/sandboxUsers/trainingUsers/…, regulator in productReaders/auditReaders/…, admin nearly everywhere).
Vitest in one file: yes for the guard (a `utils/__tests__/roles.test.ts` iterating `ROLE_VALUES` and asserting ≥1 group), though note it passes today — it is regression protection, which matches the 潜在 classification.

### cds-frontend/src/utils/roles.ts:39 — 高估 (—)
Mechanism (M-315): `[OPERATOR, REGULATOR, ADMIN]` repeats across `outputControlReaders`/`auditReaders`/`monitoringReaders` (lines 38-40) and other pairs repeat, with containment maintained by eye.
Repetition verified, but the cited "口径不一致" is domain semantics rather than drift: `productReaders` opens with `DATA_PROVIDER` because 数商 owns their products, while `catalogReaders` opens with `BUYER` because the catalog is the buyer-facing view (and `contractReaders` correctly excludes OPERATOR as a non-party). A named `COMPLIANCE_READERS` constant is a fine 3-line cleanup; a missed role here would show/hide a menu entry, and no such mismatch exists today.

### cds-frontend/src/utils/roles.ts:14 — 伪 (—)
Mechanism claimed (M-316): `ROLE_LABELS[role]` returns `undefined` for legacy/unrecognised roles so the UI renders blank; wants a `roleLabel()` helper.
Every consumer already coalesces or pre-normalises: `components/Layout/Header.tsx:48-49` renders only inside `if (role)` where `role = normalizeUserRole(user?.role)` (Header.tsx:22) — a normalised value is by construction a key of `ROLE_LABELS`; `pages/Audit/AuditLog.tsx:113` `ROLE_LABELS[record.role as …] ?? record.role`; `pages/UserManagement.tsx:51,52,77,89` all `?? record.role` / `?? v`. The blank-label failure mode the claim needs does not occur anywhere (grep `ROLE_LABELS` → 12 matches, 5 files).

### cds-frontend/src/utils/cn.ts:4 — 伪 (—)
Mechanism claimed (M-310): the "全局共享的类名合并入口" lacks JSDoc/return type, so callers misread twMerge's conflict resolution.
There are no callers: `cn.ts` is dead code. `tool.grep pattern:"utils/cn" path:cds-frontend/src` → `matches=0`; `tool.grep pattern:"\bcn\s*\(" path:cds-frontend/src` → 1 match, its own definition at `utils/cn.ts:4`; `import { cn }` across `cds-frontend` → `matches=0`. The premise ("调用方容易误解") is false, so the documentation request has no beneficiary. The genuinely useful finding nearby is that `cn.ts` plus its two dependencies (`clsx`, `tailwind-merge` in package.json) are unused weight — worth deleting, not documenting.

### cds-frontend/src/utils/cn.ts:4 — 伪 (—)
Mechanism claimed (M-311): as a "全站使用的基础工具" it needs `__tests__/cn.test.ts` to survive clsx/tailwind-merge upgrades.
Same zero-consumer evidence as above (grep commands quoted in the previous verdict), so an upgrade could not break any screen; the factual basis of the claim ("全站使用") is wrong. The observation that sibling utils are tested is accurate (`utils/__tests__/crypto|csvPreview|unwrapList|workspace.test.ts`) — which only sharpens that an unused helper is the outlier, and the right action is deletion or adoption, not a test.

---

## 汇总 / Summary
- 真 (real): 4 — M-138 (`dataResourceApi.ts:35`, low), M-141 (`:45`, low), M-285 (`:55`, low), M-286 (`:61`, medium)
- 伪 (false positive): 6 — M-281, M-284, M-306, M-316, M-310, M-311
- 高估 (overstated): 10 — M-279, M-137, M-282, M-140, M-287, M-172, M-173, M-192, M-193, M-315
- 潜在 (latent): 9 — M-134, M-135, M-280, M-136, M-283, M-139, M-142, M-174, M-314
- Total judged: 29 (14 medium + 15 low claims under the six assigned headings).
