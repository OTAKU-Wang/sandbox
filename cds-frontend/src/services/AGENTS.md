# src/services/ — the single API boundary (scored 9: 18 modules, every page data path funnels here)

## OVERVIEW
One file per backend domain; `services/api.ts` (default export) is the only axios instance in the app (18 importers). Every page's react-query call lands in exactly one module here.

Request flow: page (`useQuery`/`useMutation`) → domain module → shared `api` instance → FastAPI `/api/v1`; on 401 the interceptor queues the request, refreshes single-flight, retries once.

## WHERE TO LOOK
| Module | What it owns |
|--------|--------------|
| `api.ts` | shared axios: baseURL `VITE_API_BASE_URL \|\| '/api/v1'`, 30s timeout, Bearer from authStore, single-flight 401→`/auth/refresh` with failed-request queue, `response.data` unwrap |
| `sandboxApi.ts` (379 LOC, biggest) | session lifecycle: execute/exec/logs/usage/files/snapshots/proof-bundle/network-policy/pause/resume/terminate; ~22 exported interfaces incl. SessionProofBundle, ExecResult, FileDownloadBlockedError (raised on blocked file downloads) |
| `identityApi.ts` | two objects: `kmsApi` + `certificateApi`; `normalizeKmsKey`/`normalizeCertificate` absorb backend renames |
| `k8sOpsApi.ts` | DEVIATION: standalone functions (listDeployments, listOpsNetworkPolicies, listOpsPvcs, getOpsLogs) + `cluster_available` flag on every response — backend degrades honestly (empty items, never fabricated live state) |
| rest | audit, auth, catalog, connector, contract (DPBudgetStatus), dataProduct, dataResource, fieldExposure, federation (TrustRelationship/TrustScore), monitoring (Alert), outputControl (InspectionResult/DPNoiseResult/OutputGatewayResult), rag, training (TrainingJob/DPConfig), users |
| `__tests__/` | `api.test.ts` (interceptor/refresh behavior), `k8sOpsApi.test.ts`; shared test setup in `src/test/` (test-utils.tsx, setup.ts) |

## CONVENTIONS
- Import the shared `api` from `./api` — every call rides the auth/refresh interceptors.
- Typed endpoint-function object + exported interfaces per domain file.
- Boundary cast: `as unknown as X` at the fetch site; `normalize*` helpers map backend field renames before values leave the service.
- Paginated endpoints return `PaginatedResponse` (types/api.ts); page→offset conversion is inline per call (e.g. `monitoringApi.getAlerts` computes `(page-1)*page_size`).
- Errors surface as the raw backend payload: the response interceptor rejects with `error.response?.data || error`, so handlers receive the unified `{code,message,detail,request_id}` shape (contract: `docs/error-codes.md`) — display via `getErrorMessage` from QueryFeedback, never re-stringify.
- Token state lives in `stores/authStore` (zustand persist, key `auth-storage`): user/token/refreshToken/isAuthenticated + `setTokens`/`logout`; services read it via `useAuthStore.getState()` (no React subscription needed outside components).
- Refresh mechanics: the refresh call itself uses raw `axios.post` to `/auth/refresh` (NOT the shared instance, so interceptors can't loop); each 401 request retries exactly once, flagged by `originalRequest._retry`; concurrent 401s queue on `failedQueue` until the single-flight refresh settles.

## ANTI-PATTERNS
- No new axios instances, no raw fetch — all calls through the shared instance.
- The 401 refresh-failure hard redirect (`window.location.href`) in api.ts is intentional last-resort; do not "fix" it into router navigation.
- High-risk deletes carry `{reason, ticket_id}` in the DELETE body (paired with `utils/highRiskOperation.tsx`) — keep the body on `api.delete(url, { data })`.
