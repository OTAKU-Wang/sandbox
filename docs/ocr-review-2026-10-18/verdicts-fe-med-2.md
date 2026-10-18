# ocr 前端中低危复核 verdicts — fe-med batch 2

Scope: contractApi.ts (8) / connectorApi.ts (7) / catalogApi.ts (3) / apiKeysApi.ts (6) / pages/Flow/flowResource.ts (4) / pages/Flow/directoryContract.ts (3) = 31 claims.
Note: the two files the brief listed as `src/services/flowResource.ts` and `src/services/directoryContract.ts` actually live in `src/pages/Flow/` (verified by read; `tool.glob **/flowResource.ts` matches only `cds-frontend/src/pages/Flow/flowResource.ts`).

Convention confirmed before judging: `services/api.ts:36` `api.interceptors.response.use((response) => response.data, ...)` and `:100` `return Promise.reject(error.response?.data || error)` — inside a service module `res`/the rejection payload are already the parsed body. Every unwrap claim below is judged against that, and against the actual backend response shape.

---

## cds-frontend/src/services/contractApi.ts

### cds-frontend/src/services/contractApi.ts:39 — 潜在 (n/a)
Mechanism: skip/limit are computed without clamping, so an out-of-range page would 422 (backend `app/api/contracts.py:105-106` `skip: ge=0`, `limit: ge=1, le=500`). Not reachable: the only `contractApi.list` caller is `pages/Contracts/ContractList.tsx:34`, which passes `page` straight from antd `Pagination`/`Table` `current` (always ≥1, ContractList.tsx:65) plus the literal `page_size: 20`; the claim itself concedes callers are currently legal. vitest: yes — a new `services/__tests__/contractApi.test.ts` spying on `api.get` could pin a clamp (nothing to pin today).

### cds-frontend/src/services/contractApi.ts:37 — 高估 (duplication is real, severity inflated)
`'/contracts'` really appears 7× (contractApi.ts:37,47,50,53,56,59,62) and the `20` default twice (:39-40), but this repo documents the pattern as intentional: `cds-frontend/AGENTS.md` ANTI-PATTERNS "page→offset pagination is computed inline in each service (no shared helper) — keep inline until a real refactor" (same wording in `src/services/AGENTS.md` CONVENTIONS). No runtime or user-visible effect; extracting constants is a style choice that the project has already declined. vitest: no (non-behavioral).

### cds-frontend/src/services/contractApi.ts:47 — 伪
Mechanism claimed: `contractApi.get(id!)` on a missing route param yields `/contracts/undefined`, and an unencoded id is a path-injection surface. Refuted at the call site: `pages/Contracts/ContractDetail.tsx:42` and `:48` both set `enabled: !!id` / `enabled: !!id && ...`, so no request is issued without an id; the mutations at :76/:90/:96 only run from the rendered detail page. Backend `contract_id: uuid.UUID` (contracts.py:151) turns any odd id into a 422 for the caller's own session only — no privilege or endpoint change an authenticated caller could not do directly. vitest: no (no defect; would need a router harness to assert the guard).

### cds-frontend/src/services/contractApi.ts:53 — 高估 (premise accurate, medium inflated)
The sign inputs are machine-generated, not user-typed: `signature` comes from `sm2Sign` and `timestamp` from `new Date().toISOString()` (ContractDetail.tsx:66-76), so no "unvalidated input" defect exists in this layer and a service-layer guard would be dead code. What is real is diagnosability: the ±300 s freshness rule rejects with English text (`app/services/contract_service.py:120-121`), the envelope copies `detail` into `message` (`app/core/error_handlers.py:89`), and the page prints `签署失败: ${err.message}` (ContractDetail.tsx:84). vitest: only after a helper is introduced (`services/__tests__/contractApi.test.ts`); nothing to pin now.

### cds-frontend/src/services/contractApi.ts:12-14 — 真 (low)
The "type inconsistency" half is wrong — the FE mirrors the backend exactly (`str` / `list[str]` / `str` at `app/schemas/contract.py:21-23`); the observable half is right: clearing 允许操作 (`pages/Contracts/ContractCreate.tsx:228-235`, `allowClear`) makes `joinOrUndefined` return `undefined` (ContractCreate.tsx:105,138), the field is omitted, and the backend default `"read,analyze"` (schemas/contract.py:23) is applied — the user who cleared permissions ends up granting read+analyze, shown as such on ContractDetail.tsx:184, with no "留空=默认" hint. vitest: yes — `pages/__tests__/ContractCreate.test.tsx` asserting the submitted payload for a cleared selection (or the added hint).

### cds-frontend/src/services/contractApi.ts:6-19 — 真 (medium)
`CreateContractRequest` (contractApi.ts:6-19) omits `purpose` / `purpose_scope`, which the backend accepts and persists (`app/schemas/contract.py:29-31`; passed through at `app/api/contracts.py:85-86`) — so every UI-created contract has `purpose=NULL`, the Gap A4 purpose gate stays vacuous (`app/api/sandbox_tasks.py:26,48-53`, `app/services/inference_service.py:146-149` fire only when `purpose_scope` is set), and the SM2 payload signs an empty purpose (`src/utils/crypto.ts:78-80` ↔ `app/services/contract_service.py:127-128`). No create-form field exists: `tool.grep "purpose" src/pages/Contracts` → 3 hits, all non-fields (ContractCreate.tsx:255 placeholder, ContractDetail.tsx:72-73 read). vitest: yes — a ContractCreate payload test asserting `purpose`/`purpose_scope` are sent.

### cds-frontend/src/services/contractApi.ts:58-59 — 真 (low, located in the page)
The service forwards the interceptor's rejection (W2 body, api.ts:100) and a normalizer already exists (`getErrorMessage`, `src/components/Feedback/QueryFeedback.tsx:5-18`), but the consumer swallows it: `onError: () => message.error('激活失败')` / `('终止失败')` (ContractDetail.tsx:92,98) discard the backend reason such as "Cannot activate contract in draft status" (contracts.py:197) — user-visible, and it is ContractDetail that deviates from the documented convention, not contractApi. vitest: yes — a ContractDetail test with a mocked 400 asserting the toast contains the detail.

### cds-frontend/src/services/contractApi.ts:27-32 — 真 (low)
`entries` is the FE name for `ledger[:20]` (`app/api/contracts.py:295-303`) and the page renders it with `pagination={false}` and no "最近 20 条" caption (ContractDetail.tsx:220-232), so a contract with a longer ledger silently shows 20 rows — an honest-disclosure gap; the missing `last_consumed_at` (returned at contracts.py:294) is inert: `tool.grep last_consumed_at cds-frontend/src` → 0 hits. vitest: yes — a ContractDetail assertion on the caption, or a `DPBudgetEntry` type-lock test.

---

## cds-frontend/src/services/connectorApi.ts

### cds-frontend/src/services/connectorApi.ts:71 — 真 (medium)
`GET /connectors/` is now keyset-paginated with `limit: ge=1, le=500` default **100** and the continuation handed out only through the `X-Next-Cursor` header (`app/api/connectors.py:191,203-205`; `app/core/pagination.py:56-62`), which the shared interceptor throws away (api.ts:36) — and `pages/Connectors/index.tsx:276-282` renders the table with `pagination={false}`, so connectors 101+ silently disappear from the admin list with `total: null` (connectors.py:207) and no disclosure. vitest: yes — `services/__tests__/connectorApi.test.ts` asserting the request carries a limit/cursor (or that a truncated page is surfaced instead of swallowed).

### cds-frontend/src/services/connectorApi.ts:73 — 伪
The claimed crash needs a `null` body; after the interceptor unwrap an empty/204 body is the empty string, and `''.items` is `undefined` → `undefined || []` → `[]`, so `.map` is safe (also true for `{items: null}`). The proposed `?.` + silent `|| []` also contradicts this repo's own rule: `src/utils/unwrapList.ts:1-14` exists precisely because silently coercing an unrecognized shape to `[]` is the forbidden fake-empty, and it **throws** `ListShapeError` (unwrapList.ts:65); identityApi.ts:158 and federationApi.ts:82 already use it. vitest: yes, but to pin `unwrapList(res, '/connectors/', 'items')` — reuse `src/utils/__tests__/unwrapList.test.ts`'s contract, not a `?.` band-aid.

### cds-frontend/src/services/connectorApi.ts:57 — 高估 (security medium inflated)
`api_key` really is folded into `Connector` (connectorApi.ts:10,57), but the "随 useQuery(['connectors']) 进缓存" premise is false: the backend attaches `api_key` only to register/rotate bodies (`app/api/connectors.py:122-128`), so list/detail payloads never carry it; the one-time key has to reach the UI once by design (Connectors/index.tsx:82-85, 383-401) and nothing persists it — `App.tsx:63-64` registers no react-query persister, and `tool.grep "localStorage|console\.(log|info)"` over `pages/ApiKeys.tsx` and `pages/Flow/DeliveryCenter.tsx` → 0 hits. Residual ask is a JSDoc. vitest: no.

### cds-frontend/src/services/connectorApi.ts:78 — 潜在
Path params are un-encoded at :78,86,90,94,98, but every call site passes `record.id` taken from the backend list (`Connectors/index.tsx:219,232,243,253`) or the register response; no connector id ever comes from a route/query param, and the backend parses `connector_id: uuid.UUID` (connectors.py:213,227,247,269,293) so a malformed value only 422s the caller. Real hygiene, reachable only if a future caller forwards external input. vitest: yes — `services/__tests__/connectorApi.test.ts` asserting the encoded URL.

### cds-frontend/src/services/connectorApi.ts:8 — 潜在
The union really does drop `pending` (connectorApi.ts:8,41) while `ConnectorStatus.PENDING` exists and is the model column default (`app/models/connector.py:21,50`), and the page has no label/color/action branch for it (`Connectors/index.tsx:34-44,213-237`) — but the only writer forces ACTIVE (`app/api/connectors.py:172`), so no pending row can exist until an approval flow is added; then the Tag falls through to raw English and the row offers only 轮换 Key/心跳. vitest: yes — a Connectors render test with a `status:'pending'` fixture.

### cds-frontend/src/services/connectorApi.ts:46-47 — 高估 (real dead declaration, no user impact)
Confirmed dead: `tool.grep supported_protocols cds-frontend/src` and `max_concurrent_sessions` each return exactly one hit — the declarations at connectorApi.ts:46/47 — while the backend does send both (`app/api/connectors.py:123-124`). Dropping them costs no behavior today; "无法展示限流阈值" is a feature request, not a defect. vitest: no (nothing observable to pin).

### cds-frontend/src/services/connectorApi.ts:56 — 真 (low)
`trust_level` is health renamed as trust, and no renderer reads its value: the 信任等级 column is keyed `trust_level` but renders `record.is_healthy` (Connectors/index.tsx:189-194, same in the drawer :338-340). The claim's addendum is also correct and user-observable: the backend hardcodes `latency_ms: 0` and emits only `healthy|unknown` (`app/api/connectors.py:236-242`), yet the modal prints "延迟: 0 ms" as a measurement (`Connectors/index.tsx:376-378`) — an honest-disclosure violation; the `unhealthy` label map (:52-56) is unreachable dead copy. vitest: yes — a Connectors modal test asserting the latency row is absent or marked unavailable.

---

## cds-frontend/src/services/catalogApi.ts

### cds-frontend/src/services/catalogApi.ts:32 — 潜在
No clamp against `skip: ge=0`, `limit: ge=1, le=100` (`app/api/catalog.py:22-23`), yet all three callers pass legal values: `pages/Catalog/index.tsx:43` (page from `Pagination current`, size 12 — :188-192), `pages/Sandbox/SessionList.tsx:70` (1 / 100 — exactly the `le=100` ceiling, so a bump to 200 is the latent 422), `pages/DataPipeline.tsx:49` (1 / 1). The `...rest` empty-string half is self-refuted: the backend filters with `if q:` (catalog.py:31,39-49). vitest: yes — `services/__tests__/catalogApi.test.ts` pinning clamped skip/limit and dropped empty params.

### cds-frontend/src/services/catalogApi.ts:36 — 潜在 (the empty-id half is 伪)
The "`/catalog/` on empty id" scenario is blocked by `enabled: !!detail?.id` (`pages/Catalog/index.tsx:55`) and the id comes from server list data (:54). Encoding is genuinely absent, but `product_id: uuid.UUID` (`app/api/catalog.py:77`) means an injected `/`,`?`,`#` only produces a 422/404 for the caller's own request. vitest: yes — `services/__tests__/catalogApi.test.ts` asserting `encodeURIComponent`.

### cds-frontend/src/services/catalogApi.ts:12-16 — 高估 (real type lie, unreachable harm)
The shape divergence is real: list items omit `output_constraints` / `data_schema` / `updated_at` (`app/api/catalog.py:59-70`) while catalogApi.ts:12,14 declares them non-optional, and `created_at` can be `null` in both responses (catalog.py:69,104) vs `string` at :16. But no consumer can crash: `selectedDetail?.data_schema?.fields` is optional-chained behind `Array.isArray` (`pages/Catalog/index.tsx:63-65`), the detail rows are truthiness-guarded (:236-241), the loading branch shows a `Spin` before the list-item fallback can render (:218-219), and no catalog caller renders `created_at`. The "no generic → unchecked any" note describes the documented boundary convention for the whole services layer (`src/services/AGENTS.md`). vitest: no (type-level; a compile-time assertion, not a runtime test).

---

## cds-frontend/src/services/apiKeysApi.ts

### cds-frontend/src/services/apiKeysApi.ts:70-74 — 潜在
Four path params are interpolated raw (:70,71,73,74) — the claim's own text concedes today's ids are backend UUIDs, and they are: `pages/ApiKeys.tsx:389,447,456,466` pass `record.id` from the list, `pages/Flow/DeliveryCenter.tsx:1703` passes `keyCreated.id` from the create response; every backend handler declares `key_id: uuid.UUID` (`app/api/api_keys.py:173,205,248,277`), and rotate/revoke are admin-only endpoints the caller could call directly anyway, so the "打到非预期端点" framing buys an attacker nothing. vitest: yes — `services/__tests__/apiKeysApi.test.ts` asserting the encoded paths.

### cds-frontend/src/services/apiKeysApi.ts:45-46 — 高估 (security medium inflated to a JSDoc ask)
The leak vectors named (react-query persist store, console log, error reporting) do not exist here: `App.tsx:63-64` configures only `retry`/`refetchOnWindowFocus` with no persister plugin, and grepping `localStorage|console\.(log|info)` in `pages/ApiKeys.tsx` and `pages/Flow/DeliveryCenter.tsx` returns nothing. The bare key must be shown once by contract (`app/api/api_keys.py:4-5`) and both consumers keep it only in component state (ApiKeys.tsx:219,286-287; DeliveryCenter.tsx:1659,1668) to render that one-time disclosure. vitest: no.

### cds-frontend/src/services/apiKeysApi.ts:41 — 高估 (both posited failures refuted)
`expires_in_days: 0` is explicitly whitelisted by the backend validator (`app/api/api_keys.py:43-44` "must be one of 0, 30, 90, 180, 365") and 0 means no expiry, not immediate expiry (`api_keys.py:133` `... if payload.expires_in_days else None`), while the UI only offers that exact set (`pages/ApiKeys.tsx:66-72` 永不过期=0, :243 defaults to 90). What remains is a unit/range comment. vitest: no — pinning option prose is forbidden by the test discipline; a unit test could only pin the shipped value.

### cds-frontend/src/services/apiKeysApi.ts:51 — 高估 (type nit, zero consumers)
`string` here mirrors the backend rather than lying: `ApiKeyCreatedResponse.status: str` (`app/api/api_keys.py:60`) and `ApiKeyListItem.status: str` (:71) are untyped strings server-side, so `ApiKeyStatus` would be a frontend-only assumption. No consumer reads `ApiKeyCreated.status` (`pages/ApiKeys.tsx` uses `created.key` / `key_prefix` only, :286-287). vitest: no.

### cds-frontend/src/services/apiKeysApi.ts:63 — 高估 (type nit, zero consumers)
Revoke returns the literal `"revoked"` (`app/api/api_keys.py:291`), and no consumer inspects the response at all — `pages/ApiKeys.tsx:466` revoke mutation only invalidates the list query. Tightening to `ApiKeyStatus` changes no behavior. vitest: no.

### cds-frontend/src/services/apiKeysApi.ts:33 — 伪
The claim asserts FastAPI will serialize `"denied_reason": null`, but the handler hand-builds each row and adds the key only when truthy: `item = {time, path, method, status_code, ip}` then `if detail.get("denied_reason"): item["denied_reason"] = ...` (`app/api/api_keys.py:188-199`) — so `denied_reason?: string` is the accurate contract, and the column already renders `v ?? '—'` (`pages/ApiKeys.tsx:403`). Changing it to `| null` would misdescribe the wire. vitest: yes if you want a lock (`services/__tests__/apiKeysApi.test.ts` with a fixture lacking the key), but there is nothing to fix.

---

## cds-frontend/src/pages/Flow/flowResource.ts

### cds-frontend/src/pages/Flow/flowResource.ts:4 — 潜在
`RESOURCE_FIELDS` hand-enumerates the same 5 keys as `FLOW_RESOURCE_LIMITS` (`src/services/flow.ts:151-157`) and `flow.ts` itself derives from the object (`Object.keys(FLOW_RESOURCE_LIMITS)` at flow.ts:1229,1241), so a 6th limit key would silently lose a form input (`FlowConfigFields.tsx:136`) and a `formatResourceSpec` segment with no compile error — but the key sets match exactly today and the array is never mutated (`tool.grep RESOURCE_FIELDS cds-frontend/src` → FlowConfigFields.tsx:23,136 + this file). vitest: yes — `pages/Flow/__tests__/flowResource.test.ts` asserting `Object.keys(FLOW_RESOURCE_LIMITS)` equals `RESOURCE_FIELDS` (that single assertion removes the drift risk).

### cds-frontend/src/pages/Flow/flowResource.ts:16 — 高估 (defensive nit)
Both dirty-input routes are unreachable: JSON cannot carry `NaN`, and the spec's numeric fields are pydantic-typed (`app/schemas/flow.py:16-22` `cpu_cores: float|None`, `memory_mb/disk_gb/gpu_count/timeout_seconds: int|None`), so a `"1024"` string is coerced to a number server-side before it becomes `sandbox_resource_spec`; the FE writer also rounds before submit (`flow.ts:1240-1244`). A `Number.isFinite` guard is cheap hardening, not a bug fix. vitest: yes — `pages/Flow/__tests__/flowResource.test.ts` with `NaN` / `"1024"` inputs.

### cds-frontend/src/pages/Flow/flowResource.ts:18 — 潜在
Whitespace-only `image_template` cannot enter through this UI: the payload builder trims and drops empty (`src/services/flow.ts:1247-1248`, pinned by `services/__tests__/flow.test.ts:255-256`), but `ResourceSpec.image_template: str | None` (`app/schemas/flow.py:21`) does not trim, so a non-UI writer could store `"  "` and `pages/Flow/FlowConfigCard.tsx:179` would print "镜像  " as if set. vitest: yes — `pages/Flow/__tests__/flowResource.test.ts` with `{ image_template: ' ' }`.

### cds-frontend/src/pages/Flow/flowResource.ts:6 — 潜在
All three call sites pass sane bounds — literals `0,59` / `0,23` and `limit.min/limit.max` (`FlowConfigFields.tsx:94,111,140`) — so an inverted or NaN range requires a future caller; the second half of the claim is factually right that integer enforcement is duplicated at FlowConfigFields.tsx:98 and :115 (only two users today, and the resource fields get integers from `precision` instead). vitest: no for a nonexistent caller; yes for an `integer` option once added (`pages/Flow/__tests__/flowResource.test.ts`).

---

## cds-frontend/src/pages/Flow/directoryContract.ts

### cds-frontend/src/pages/Flow/directoryContract.ts:11-18 — 潜在
Type-level the claim is correct — `readonly SandboxDirectoryEntry[]` (directoryContract.ts:18) still leaves `path`/`purpose` writable (:13,15) and nothing is `Object.freeze`d — but no consumer mutates the shared array: both render from a copy, `dataSource={[...SANDBOX_DIRECTORY_ENTRIES]}` (`Guide.tsx:236`, `ContractDataBoard.tsx:112`), so the drift scenario requires a future in-place `sort()`/assignment. vitest: yes — one `expect(Object.isFrozen(...))` / mutation-attempt case in `pages/Flow/__tests__/directoryContract.test.tsx`.

### cds-frontend/src/pages/Flow/directoryContract.ts:19 — 潜在
The guard really is tautological: `pages/Flow/__tests__/directoryContract.test.tsx:14` compares the FE entries against another hardcoded FE literal `BACKEND_GUEST_PATHS`, and no test reads the authority `app/services/cube_runtime_adapter.py:52-55` (`GUEST_DATA_DIR/OUTPUT/WORK/EXEC`); `tool.grep "\.cds-exec" tests/` → 0 hits — so a backend rename would keep the suite green even though `specs/ss-18-prototype-alignment.md:177` records this test as the 逐字同序 sentinel. Nothing user-visible until a rename happens (Guide/NotebookWorkbench would then display stale paths). vitest: yes — the FE test can `readFile` `app/services/cube_runtime_adapter.py` and regex the four constants (or add a backend pytest cross-check).

### cds-frontend/src/pages/Flow/directoryContract.ts:9 — 真 (low / info)
Typo confirmed in both places cited: "由 T5 出口网关把行" at directoryContract.ts:9 and the same "由 T5 出口网关把行）" at `app/services/cube_runtime_adapter.py:47` — should be 把关. Comment-only: it is never rendered, and the existing prose assertions in directoryContract.test.tsx:27-40 cover `SANDBOX_HARVEST_RULE`, not this comment. vitest: no — pinning Chinese prose in a comment would be a pure-prose test, which the project's test discipline excludes.

---

## Counts

- 真: **7** — M-125 (connectorApi.ts:71 truncation), M-132 (contractApi.ts:12-14), M-133 (contractApi.ts:6-19), M-276 (connectorApi.ts:56), M-277 (contractApi.ts:58-59), M-278 (contractApi.ts:27-32), M-248 (directoryContract.ts:9)
- 伪: **3** — M-126 (connectorApi.ts:73), M-130 (contractApi.ts:47), M-268 (apiKeysApi.ts:33)
- 高估: **10** — M-129, M-131, M-127, M-275, M-118, M-104, M-105, M-266, M-267, M-83
- 潜在: **11** — M-128, M-103, M-273, M-274, M-116, M-117, M-82, M-253, M-254, M-74, M-75

Total 31 / 31 claims judged.
