# cds-frontend/ — React admin console for CDS (scored 17: 104 TS files, ~20k LOC, top reference centrality)

Admin UI for the Confidential Data Sandbox (密态沙箱): FastAPI backend at `/api/v1`, all UI copy Chinese (zhCN antd locale, zh-CN html lang).

## STRUCTURE
```
src/
├── App.tsx        # all routes; every page React.lazy (route-level code splitting)
├── main.tsx       # entry
├── pages/         # 38 lazy pages: 16 domain folders (incl. Flow/) + 8 top-level .tsx → pages/AGENTS.md
├── services/      # 22 API modules, one per backend domain → services/AGENTS.md
├── components/    # Auth (ProtectedRoute/RoleRoute), Feedback (QueryFeedback, ErrorCard, SimulationBadge honest-degradation disclosure), Layout, Sandbox (ExecStreamTerminal)
├── stores/        # zustand: useAuthStore (persist, key auth-storage), useUIStore (sidebar/theme)
├── utils/         # cn, crypto.ts (SM2/SM3), highRiskOperation.tsx, roles.ts (ROLE_GROUPS)
├── types/         # enums.ts (domain enums, as const), models.ts, api.ts, security.ts
└── test/          # test-utils.tsx (renderWithProviders), setup.ts
```

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Add a page | `pages/<Domain>/X.tsx` + lazy import + RoleRoute line in `App.tsx` |
| Add an API call | `services/<domain>Api.ts` via the shared axios instance in `services/api.ts` |
| Role → route access | `utils/roles.ts` ROLE_GROUPS (17 groups) consumed by RoleRoute in App.tsx; `LEGACY_ROLE_ALIASES` normalizes old role names once, here |
| In-page permissions | per-page `can*` booleans (e.g. SessionDetail) |
| In-browser SM2/SM3 | `utils/crypto.ts` (sm-crypto; sign payload `CDS-SIGN|<id>|<no>|<role>|<ts>`) |
| High-risk confirm | `utils/highRiskOperation.tsx` — reason ≥8 chars, optional ticket_id |
| Error/loading/empty UI | `components/Feedback/QueryFeedback.tsx` (getErrorMessage, QueryErrorAlert, CenteredLoading, EmptyState, tableEmpty — used by 13 pages) |
| Sandbox exec terminal | `components/Sandbox/ExecStreamTerminal.tsx` (@xterm + fit addon) |
| Charts / editor | recharts (Monitoring, Dashboard); @monaco-editor/react |

## CONVENTIONS (deviations from stock Vite/React)
- Stack: Vite 8 · React 19 · TS ~6.0 · antd 5 · Tailwind v4 via `@tailwindcss/vite` (NO tailwind.config.js) · react-query 5 (global `retry: 1`, `refetchOnWindowFocus: false`) · react-router 7 · zustand 5 · ESLint 10 flat config.
- `tsconfig.app.json`: `noUnusedLocals`/`noUnusedParameters` deliberately false; `verbatimModuleSyntax` + `erasableSyntaxOnly` on.
- Backend field renames absorbed at the service boundary: `as unknown as X` + `normalize*` helpers.
- High-risk mutations (revoke key/cert, terminate) send `{reason, ticket_id}` in the DELETE body: `api.delete(url, { data })`.
- `nginx.conf`: SPA fallback + `/api/` → k8s service `cds-api:8000`, SSE on (`proxy_buffering off`, HTTP/1.1, 300s read timeout) + security headers.

## ANTI-PATTERNS (THIS PROJECT)
- SM2 private key in localStorage (`cds_sm2_privkey_<userId>`, utils/crypto.ts) — tolerated only because the backend re-verifies; do not extend this pattern to other secrets.
- Refresh-failure path hard-redirects `window.location.href = '/login'` (services/api.ts), bypassing React Router — do not add more hard redirects.
- page→offset pagination is computed inline in each service (no shared helper) — keep inline until a real refactor.
- `README.md` is the untouched stock Vite template — not project documentation.
- `package-lock.json` (7k lines) is the declared lockfile: npm scripts, npm as package manager; no bun.lock committed. Do not run `bun install` here.

## COMMANDS (run in cds-frontend/)
```bash
npm run dev          # vite :3000, /api → http://localhost:8000 (FastAPI)
npm run build        # tsc -b && vite build → dist/
npm run lint         # eslint . (flat config, ignores dist)
npm test             # vitest run; also test:watch, test:coverage (v8 provider)
npm run preview      # vite preview
# Dockerfile: node:20-alpine build → nginx:alpine serving dist/ with nginx.conf
```
