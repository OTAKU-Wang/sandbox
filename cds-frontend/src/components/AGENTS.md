# src/components/ — cross-page UI + route guards (scored 14: 45 files = 22 product / 2.1k LOC + 23 test files, 6 subdirs, 48 importer files outside)

Earned its file on size + centrality + a rule set no other directory shares: this is where the honest-degradation disclosure widgets and the static source gates live.

## STRUCTURE
6 subdirs, 22 product files (2149 LOC): `Layout/` 9 / 855, `Feedback/` 9 / 581, `Audit/` 1 / 378, `Sandbox/` 1 / 307, `Auth/` 2 / 28, `Common/` **empty — delete it, don't fill it**. Tests sit in each subdir's `__tests__/` (23 files ≈ 2.3k LOC), not in a central test dir.

## WHERE TO LOOK
| Need | File | Contract it holds |
|---|---|---|
| Token / role gate | `Auth/ProtectedRoute.tsx` (14) · `Auth/RoleRoute.tsx` (14) | Navigate, never render: unauthenticated → `/login` **with `state.from`** (A306, so login can bounce back); wrong role → `/`. Both are 14-liners by design — the real role truth is `utils/roles.ts`. |
| Workspace gate | `Layout/WorkspaceRoute.tsx` | Route-level half of the 双工作台 split (D-16). `WORKSPACE_RANK` makes admin ⊇ developer, so only `workspace="admin"` routes can actually deny — `workspace="developer"` pins *ownership*, it blocks nobody. |
| Menu ↔ route parity | `Layout/Sidebar.tsx` (61 keys) + `Layout/routeCrumb.ts` (145, 43 prefix entries) | `ROUTE_CRUMBS` drives both the menu label and the breadcrumb; a key missing there silently dead-ends the selection. Empty prefix / bare `/` is forbidden — `startsWith` would match the whole site. |
| Breadcrumb dynamic half | `Layout/useCrumbSegments.ts` + `stores/crumbStackStore` | Page static half = routeCrumb; drill-in views push segments here and clear on unmount. `MainLayout` renders ONLY PageCrumb (A253 gate reads its source). |
| Query error / loading / empty / table-empty | `Feedback/QueryFeedback.tsx` · `Feedback/errorMessage.ts` · `Feedback/tableEmpty.tsx` | 45 non-test importer files. `getErrorMessage` precedence: `message` > `detail.error` > `detail.field_errors[0]` > `detail` > HTTP status label — never re-stringify an error in a page. |
| W2 code → user copy | `Feedback/errorHints.ts` (`ERROR_HINTS`, 20 codes) | Mirrors the 用户提示/下一步 columns of `docs/error-codes.md`: the doc is authoritative, adding a code means editing both. |
| Uncaught-render fallback | `Feedback/ErrorBoundary.tsx` | Mounted exactly once in `App.tsx`, inside `BrowserRouter`, outside `Suspense`+`Routes`. |
| Honest simulation disclosure | `Feedback/SimulationBadge.tsx` | `SimulationBadgeState = 'real' \| 'simulated' \| 'unsupported'`; `DISCLOSURE_VALUE_STATES` + `simulationStateForValue` are the one classifier — unknown value → no badge, never a guess. |
| Preview watermark | `Feedback/PreviewWatermark.tsx` + `watermarkText.ts` | Pure text builder split from the overlay so the 5-field copy (username/org/userId/bundleId/capturedAt) is assertable without a DOM. |
| Chain-verification verdict | `Audit/AuditVerifyModal.tsx` | `VerdictLevel = ok \| degraded \| failed \| unanchored` + `pending`. Green (`ok`) requires a real on-chain confirm hash — locally appended PG logs, simulated chains and any degraded path render warning/failed, never green (A292/A293). |
| Exec stream WebSocket | `Sandbox/ExecStreamTerminal.tsx` | Terminal over `/api/v1/sandboxes/{id}/exec/stream` with a one-shot ticket minted via `POST /auth/ws-ticket`; base URL from `VITE_API_BASE_URL`; `connectingRef` is the mutex closing the double-click window before `socketRef` is assigned (A248: two tickets = one uncloseable stream). |

## CONVENTIONS (differ from parent)
- `react-refresh/only-export-components` forbids a component + an exported constant in one file. The split is load-bearing (`routeCrumb.ts`, `errorMessage.ts`, `watermarkText.ts`) — do not silence it. The sanctioned exception is a co-located map that *dies* if separated from its component (`DISCLOSURE_VALUE_STATES`), marked with an inline `eslint-disable-next-line`.
- 19 test files repo-wide are **static source gates** that `readFileSync` production source and assert on its text (Sidebar keys ↔ `ROUTE_CRUMBS`, MainLayout crumb-only render, forbidden CSS tokens, disclosure strings). Renaming an identifier or a Chinese literal can redden a green suite — that is intended.
- Disclosure vocabulary (`is_simulation` / `simulated` / `degraded` / `unsupported`) is displayed verbatim here, classified in one place; pages must not re-decide what a degraded value means.

## ANTI-PATTERNS
- Do not render audit/proof success from an HTTP 200 — only `VerificationResult.verified === true` with a real chain id.
- Do not move `ErrorBoundary` outside `BrowserRouter` or wrap it around `ConfigProvider`.
- Do not hard-code the WS/HTTP base in `ExecStreamTerminal`; do not drop the ticket for a query-string token.
- `Common/` staying empty is the current state: new shared UI goes into the subdir that matches its concern, not into a generic bucket.
