# src/services/ — the single API boundary (scored 14: 28 modules / 5.6k LOC + 16 test files, 119 importer files — top reference centrality in the subtree)

## OVERVIEW
One file per backend domain; `services/api.ts` (default export, 111 L) is the only axios instance in the app (every other service module imports it). Every page's react-query call lands in exactly one module here.

Request flow: page (`useQuery`/`useMutation`) → domain module → shared `api` instance → FastAPI `/api/v1`; on 401 the interceptor queues the request, refreshes single-flight, retries once.

## WHERE TO LOOK
| Module | What it owns |
|--------|--------------|
| `api.ts` | shared axios: baseURL `VITE_API_BASE_URL \|\| '/api/v1'`, 30s timeout, Bearer from authStore, single-flight 401→`/auth/refresh` with failed-request queue, `response.data` unwrap; PZ-15: 408 gets ONE auto-retry honoring `Retry-After`, GET/HEAD/OPTIONS only (non-idempotent POST never re-sent); request interceptor runs `normalizeRequestPath()` (A273) |
| `flow.ts` (2422 L, biggest; most-imported service — 23 non-test importers, 40 files incl. tests) | SS-15 flow surface in one module: `flowApi` (line 1534, ~86 ops) + `flowKeys` (line 1880, react-query key factory) + 42 exported `use*` query/mutation hooks via private `createFlowMutationHook`/`createQueryHook` factories + ~40 types/enums (`DeliveryMethod`, `PUBLISH_GATE_CODES:391`, `FLOW_RESOURCE_LIMITS:151`, `DEFAULT_COMPUTE_TZ:139`, `UNKNOWN_TEXT:44`) + pure helpers (`parseFlowError`/`describeFlowError` for the W2 envelope, notebook parse/serialize/canonicalize, `validateFlowConfig`, `buildCurlExample`) |
| `sandboxApi.ts` (478 L) | session lifecycle: execute/exec/logs/usage/files/snapshots/proof-bundle/network-policy/pause/resume/terminate; 202-vs-sync discriminator (`isTerminalOperation`/`isSessionOperation`); blob download with header-sniffing `transformResponse` workaround; `FileDownloadBlockedError` (409 DLP) |
| `pathSegment.ts` (88 L) | A273 single entry/exit for path segments: `p()` encodes user-controllable segments and throws on empty; `normalizeRequestPath()` is the idempotent whole-URL normalizer mounted on the request interceptor (already-encoded `%XX` is never double-encoded) |
| `timeouts.ts` (19 L) | A311: `UPLOAD_TIMEOUT_MS = 300_000` — deliberately NOT in `api.ts` because every service test mocks that module whole, which would make the pinned value `undefined` |
| `jupyterContents.ts` (166 L) | Jupyter Contents API (PUT + GET one-then-404-POST); `JUPYTER_FILE_POLICY` holds the 3 backend validation literals (dir `notebooks`, ext `.ipynb`, ≤5 MiB, `app/api/notebook_proxy.py:27-29`) |
| `identityApi.ts` | two objects: `kmsApi` + `certificateApi`; `normalizeKmsKey`/`normalizeCertificate` absorb backend renames |
| `metaApi.ts` | `CAPABILITY_LABELS`/`capabilityLabel`/`levelSelectOptions` — the single source for sandbox-level labels (PZ-13 capability matrix) |
| rest | apiKeys (SS-12), audit, auth, catalog, compliance, compute, connector, contract (DPBudgetStatus), dataPipeline, dataProduct, dataResource, federation (TrustRelationship/TrustScore), fieldExposure, monitoring (Alert), mpc, outputControl (InspectionResult/DPNoiseResult/OutputGatewayResult), quota, training (TrainingJob/DPConfig), users |
| `__tests__/` | 15 files: `api.test.ts` (interceptor/refresh), `flow.test.ts` (521 L: request contract, form validation, payload↔form round-trip, notebook parse, W2 envelope, list normalization, OpenAPI display), `pathSegment.a273.test.ts` + `pathSegments.a246.test.ts`, `apiPathNormalize.a273.test.ts`, `uploadBoundary.test.ts`; shared setup in `src/test/` |

## CONVENTIONS
- Import the shared `api` from `./api` — every call rides the auth/refresh interceptors.
- Typed endpoint-function object + exported interfaces per domain file.
- Boundary cast: `as unknown as X` at the fetch site; `normalize*` helpers map backend field renames before values leave the service.
- Paginated endpoints return `PaginatedResponse` (types/api.ts); page→offset conversion is inline per call (e.g. `monitoringApi.getAlerts` computes `(page-1)*page_size`).
- Errors surface as the raw backend payload: the response interceptor rejects with `error.response?.data || error`, so handlers receive the unified `{code,message,detail,request_id}` shape (contract: `docs/error-codes.md`) — display via `getErrorMessage` from QueryFeedback, never re-stringify.
- flow.ts centralizes react-query invalidation through `flowKeys` + the hook factories so all SS-15 actions share one invalidation strategy — new flow mutations go through the factory, not ad-hoc `useMutation`.
- New endpoint paths go through `pathSegment.ts` `p()` when any segment is user-controllable; backend-issued ids interpolated raw are a repo convention, not 112 defects (A273 ledger is explicit about this).
- Token state lives in `stores/authStore` (zustand persist, key `auth-storage`): user/token/refreshToken/isAuthenticated + `setTokens`/`logout`; services read it via `useAuthStore.getState()` (no React subscription needed outside components).
- Refresh mechanics: the refresh call itself uses raw `axios.post` to `/auth/refresh` (NOT the shared instance, so interceptors can't loop); each 401 request retries exactly once, flagged by `originalRequest._retry`; concurrent 401s queue on `failedQueue` until the single-flight refresh settles.

## ANTI-PATTERNS
- No new axios instances — all calls through the shared instance. The ONE sanctioned exception: `flow.ts:2373` OpenAPI try-out uses raw `fetch` so a caller-supplied `Authorization` header survives (the interceptor would overwrite it with this browser's Bearer).
- The 401 refresh-failure hard redirect (`window.location.href`) in api.ts is intentional last-resort; do not "fix" it into router navigation.
- High-risk deletes carry `{reason, ticket_id}` in the DELETE body (paired with `utils/highRiskOperation.tsx`) — keep the body on `api.delete(url, { data })`.
- Blocked file downloads read `findings_count` off `detail`, NOT off the W2 envelope top level (reading the envelope made every block report "0 findings").
- Sandbox level labels come only from `metaApi`'s capability matrix — 禁止在页面中第二处硬编码 level labels (PZ-13); the contract side excludes L0, all-planned levels auto-hide.
- `usersApi` role change calls `/auth/users/{id}/role`, not `/users/...` — the route lives in the auth router (users router only has GET /options).
- Literal paths in this directory are machine-checked: `tests/test_frontend_api_contract.py` (repo root) regex-scans `src/services/*.ts` against the live `app.main:app` route table, so a path renamed backend-side reddens CI here — never "fix" the frontend by guessing a new path.
