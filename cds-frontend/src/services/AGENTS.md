# src/services/ — the single API boundary (scored 9: 22 modules, every page data path funnels here)

## OVERVIEW
One file per backend domain; `services/api.ts` (default export) is the only axios instance in the app (every other service module imports it). Every page's react-query call lands in exactly one module here.

Request flow: page (`useQuery`/`useMutation`) → domain module → shared `api` instance → FastAPI `/api/v1`; on 401 the interceptor queues the request, refreshes single-flight, retries once.

## WHERE TO LOOK
| Module | What it owns |
|--------|--------------|
| `api.ts` | shared axios: baseURL `VITE_API_BASE_URL \|\| '/api/v1'`, 30s timeout, Bearer from authStore, single-flight 401→`/auth/refresh` with failed-request queue, `response.data` unwrap; PZ-15: 408 gets ONE auto-retry honoring `Retry-After`, GET/HEAD/OPTIONS only (non-idempotent POST never re-sent) |
| `flow.ts` (1466 LOC, biggest; the most-imported service, 21 page importers) | SS-15 flow surface in one module: `flowApi` + `flowKeys` (react-query key factory) + ~35 hooks via private `createFlowMutationHook` factory + ~40 types/enums (`DeliveryMethod`, `PUBLISH_GATE_CODES`, `FLOW_RESOURCE_LIMITS`, `DEFAULT_COMPUTE_TZ='Asia/Shanghai'`) + pure helpers (`parseFlowError`/`describeFlowError` for the W2 envelope, notebook parse/serialize/canonicalize, `validateFlowConfig`, `buildCurlExample`) |
| `sandboxApi.ts` (434 LOC) | session lifecycle: execute/exec/logs/usage/files/snapshots/proof-bundle/network-policy/pause/resume/terminate; 202-vs-sync discriminator (`isTerminalOperation`/`isSessionOperation`); blob download with header-sniffing `transformResponse` workaround; `FileDownloadBlockedError` (409 DLP) |
| `identityApi.ts` | two objects: `kmsApi` + `certificateApi`; `normalizeKmsKey`/`normalizeCertificate` absorb backend renames |
| `k8sOpsApi.ts` | DEVIATION: standalone functions (listDeployments, listOpsNetworkPolicies, listOpsPvcs, getOpsLogs) + `cluster_available` flag on every response — backend degrades honestly (empty items, never fabricated live state) |
| `metaApi.ts` | `CAPABILITY_LABELS`/`capabilityLabel`/`levelSelectOptions` — the single source for sandbox-level labels (PZ-13 capability matrix) |
| rest | apiKeys (SS-12), audit, auth, catalog, compliance, connector, contract (DPBudgetStatus), dataPipeline, dataProduct, dataResource, federation (TrustRelationship/TrustScore), fieldExposure, monitoring (Alert), mpc, outputControl (InspectionResult/DPNoiseResult/OutputGatewayResult), training (TrainingJob/DPConfig), users |
| `__tests__/` | `api.test.ts` (interceptor/refresh), `flow.test.ts` (521 L, 7 describes: request contract, form validation, payload↔form round-trip, notebook parse, W2 envelope, list normalization, OpenAPI display), `k8sOpsApi.test.ts`; shared setup in `src/test/` |

## CONVENTIONS
- Import the shared `api` from `./api` — every call rides the auth/refresh interceptors.
- Typed endpoint-function object + exported interfaces per domain file.
- Boundary cast: `as unknown as X` at the fetch site; `normalize*` helpers map backend field renames before values leave the service.
- Paginated endpoints return `PaginatedResponse` (types/api.ts); page→offset conversion is inline per call (e.g. `monitoringApi.getAlerts` computes `(page-1)*page_size`).
- Errors surface as the raw backend payload: the response interceptor rejects with `error.response?.data || error`, so handlers receive the unified `{code,message,detail,request_id}` shape (contract: `docs/error-codes.md`) — display via `getErrorMessage` from QueryFeedback, never re-stringify.
- flow.ts centralizes react-query invalidation through `flowKeys` + `createFlowMutationHook` so all ~15 SS-15 actions share one invalidation strategy — new flow mutations go through the factory, not ad-hoc `useMutation`.
- Token state lives in `stores/authStore` (zustand persist, key `auth-storage`): user/token/refreshToken/isAuthenticated + `setTokens`/`logout`; services read it via `useAuthStore.getState()` (no React subscription needed outside components).
- Refresh mechanics: the refresh call itself uses raw `axios.post` to `/auth/refresh` (NOT the shared instance, so interceptors can't loop); each 401 request retries exactly once, flagged by `originalRequest._retry`; concurrent 401s queue on `failedQueue` until the single-flight refresh settles.

## ANTI-PATTERNS
- No new axios instances, no raw fetch — all calls through the shared instance.
- The 401 refresh-failure hard redirect (`window.location.href`) in api.ts is intentional last-resort; do not "fix" it into router navigation.
- High-risk deletes carry `{reason, ticket_id}` in the DELETE body (paired with `utils/highRiskOperation.tsx`) — keep the body on `api.delete(url, { data })`.
- Blocked file downloads read `findings_count` off `detail`, NOT off the W2 envelope top level (reading the envelope made every block report "0 findings").
- Sandbox level labels come only from `metaApi`'s capability matrix — 禁止在页面中第二处硬编码 level labels (PZ-13); the contract side excludes L0, all-planned levels auto-hide.
- `usersApi` role change calls `/auth/users/{id}/role`, not `/users/...` — the route lives in the auth router (users router only has GET /options).
