# cds-frontend/ — React admin console for CDS (scored 13: 255 ts/tsx / 48.1k LOC under src/, 281 tracked files, top reference centrality)

Admin UI for the Confidential Data Sandbox (密态沙箱): FastAPI backend at `/api/v1`, all UI copy Chinese (zhCN antd locale, zh-CN html lang).

## STRUCTURE
```
src/
├── App.tsx        # 42 React.lazy imports / 48 <Route>; the only QueryClient + antd theme token (#1b6ef3)
├── main.tsx       # entry: imports ./antdReact19Patch FIRST (must precede React), then App, then ./index.css
├── index.css      # 1039 L global CSS-var surface (--cds-primary/--cds-danger/…), 4 breakpoints; edits policed by disk-reading gates
├── antdLocale.ts / antdReact19Patch.ts  # bundler/React-19 shims, see CONVENTIONS
├── pages/         # 42 lazy routes: 16 domain folders (incl. Flow/) + 8 top-level .tsx → pages/AGENTS.md
├── services/      # 28 API modules (5.6k L), one per backend domain → services/AGENTS.md
├── components/    # Auth guards, Feedback, Layout, Audit, Sandbox → components/AGENTS.md (Common/ is an empty dir)
├── stores/        # 4 zustand: authStore (persist `auth-storage`), workspaceStore (persist `cds.workspace`), uiStore + crumbStackStore (deliberately unpersisted)
├── utils/         # roles, workspace, crypto (SM2/SM3), unwrapList, highRiskOperation → utils/AGENTS.md (cn.ts = dead, 0 importers)
├── types/         # enums.ts/models.ts/api.ts/security.ts — `as const` + mirrored type, 34 exports
├── test/          # setup.ts + test-utils.tsx (renderWithProviders, createTestQueryClient)
└── __tests__/     # 5 static gates locking antdLocale/antdReact19Patch/index.css + page CSS
```
`src/hooks/` exists but is empty (0 files, untracked) — add a real hook or drop the directory.
`public/` is copied verbatim to `dist/`: favicon.svg + logo.svg are CDS marks; `icons.svg` is a leftover scaffold sprite (bluesky/discord/x glyphs, mtime 2026-06-04) — not product icons.

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Add a page | `pages/<Domain>/X.tsx` + lazy import + RoleRoute line in `App.tsx` |
| Add an API call | `services/<domain>Api.ts` via the shared axios instance in `services/api.ts` |
| Role → route access | `utils/roles.ts` ROLE_GROUPS (18 groups) consumed by RoleRoute in App.tsx; `LEGACY_ROLE_ALIASES` normalizes old role names once, here |
| Workspace (admin vs developer) | `utils/workspace.ts` `resolveWorkspace`/`filterMenuByWorkspace`; persisted choice in `useWorkspaceStore`; `useAuthStore.logout()` resets it — the single workspace-leak guard |
| In-page permissions | per-page `can*` booleans (e.g. SessionDetail) |
| In-browser SM2/SM3 | `utils/crypto.ts` (sm-crypto; sign payload `CDS-SIGN|<id>|<no>|<role>|<ts>[|purpose:<p>][|purpose_scope:<a,b>]`, must stay byte-identical to backend `crypto_service.contract_sign_data`) |
| High-risk confirm | `utils/highRiskOperation.tsx` — reason ≥8 chars, optional ticket_id |
| Error/loading/empty UI | `components/Feedback/QueryFeedback.tsx` (getErrorMessage, QueryErrorAlert, CenteredLoading, EmptyState, tableEmpty — 45 non-test importers) |
| Sandbox exec terminal | `components/Sandbox/ExecStreamTerminal.tsx` (@xterm + fit addon) |
| Charts / editor | recharts (Monitoring, Dashboard); @monaco-editor/react |

## CONVENTIONS (deviations from stock Vite/React)
- Stack: Vite 8 · React 19 · TS ~6.0 · antd 5 · Tailwind v4 via `@tailwindcss/vite` (NO tailwind.config.js) · react-query 5 (global `retry: 1`, `refetchOnWindowFocus: false`) · react-router 7 · zustand 5 · ESLint 10 flat config.
- `tsconfig.app.json`: `noUnusedLocals`/`noUnusedParameters` deliberately false; `verbatimModuleSyntax` + `erasableSyntaxOnly` on.
- Three TS projects are built by `tsc -b`: `app` (product code, `types` MUST stay `["vite/client"]` so a stray `process`/`Buffer` in shipped code still errors), `test` (`tsconfig.test.json` — same options, only widened by `"node"` in `types`, because static gates under `src/**/__tests__/` read source files off disk via `node:fs`; must include `src/test/**/*` or every `toBeInTheDocument`-style matcher loses its types), `node` (vite.config). Test files are `exclude`d from `app` — never move them back in.
- Backend field renames absorbed at the service boundary: `as unknown as X` + `normalize*` helpers.
- High-risk mutations (revoke key/cert, terminate) send `{reason, ticket_id}` in the DELETE body: `api.delete(url, { data })`.
- `nginx.conf`: SPA fallback + `/api/` → k8s service `cds-api:8000`, SSE on (`proxy_buffering off`, HTTP/1.1, 300s read timeout) + security headers. `add_header` does NOT inherit into a location that declares its own — `/api/jupyter/` re-lists all 6 headers.
- Dev proxy (`vite.config.ts`): `/api` **and** `/open` → `CDS_API_TARGET ?? http://localhost:8000`, with `ws: true` (without it the JupyterLab kernel channel connects to the shell but never gets a kernel).
- Vitest has no config file: `vite.config.ts` `test` block — jsdom, `globals`, `setupFiles: ./src/test/setup.ts`, and `maxWorkers: 4` pinned with measured evidence (16 workers push the heaviest page suites past the hardcoded 5 s timeout: red 2/4/1 on the same tree, 0 assertion errors).
- CI frontend job (`.github/workflows/ci.yml`, node 20, cache on `cds-frontend/package-lock.json`) runs all four: `npm ci` → `npm test` → `npm run build` → `npm run lint` (lint = hard gate, 0-error baseline). Authoritative run is `scripts/remote-harness/fe_lane.sh` in `node:20-bookworm-slim`.
- Boot-path fail-loud rules: `main.tsx` writes a plain-DOM red box when `#root` is missing (A305) instead of a white screen; `assertUsableLocale` throws `TypeError` at startup when the antd locale object has no `locale` string, because the alternative is a silent fall-back to English builtins ("No data").
- Interop hardening lives in-repo instead of as deps: `unwrapLocale` peels up to 4 CJS `default` shells ("whoever has the `locale` field is the real locale"); `antdReact19Patch` re-implements `@ant-design/v5-patch-for-react-19` via antd's `unstableSetRender`. The patch is load-bearing — without it all ~217 `message.*()` call sites render nothing, silently.
- Design tokens come from PNG pixel probes of the prototype; components reference `var(--cds-*)`, not new inline hex. Flow pages keep two blue tokens: `--cds-dtc-ink-blue #1b6ff2` for measured ink, `var(--cds-primary) #1b6ef3` for interactive.

## ANTI-PATTERNS (THIS PROJECT)
- SM2 private key in localStorage (`cds_sm2_privkey_<userId>`, utils/crypto.ts) — tolerated only because the backend re-verifies; do not extend this pattern to other secrets. The posture is disclosed, not hidden: `SM2_KEY_STORAGE_DISCLOSURE` (utils/crypto.ts) is rendered on the contract-signing page and pinned by `src/utils/__tests__/crypto.test.ts`, which forbids copy like 「私钥安全存储在本地」/「不落盘」.
- Refresh-failure path hard-redirects `window.location.href = '/login'` (services/api.ts), bypassing React Router — do not add more hard redirects.
- page→offset pagination is computed inline in each service (no shared helper) — keep inline until a real refactor.
- `utils/cn.ts` (clsx+tailwind-merge) has **0 importers** — dead; do not cite it as the styling helper. No new generic util goes there without a caller.
- `antdReact19Patch.ts` comments pin "全站 212 处 message.* 调用点" — actual count is 217 and nothing machine-checks it; treat such counts as prose, not contract.
- `README.md` is the untouched stock Vite template — not project documentation.
- `package-lock.json` (7k lines) is the declared lockfile: npm scripts, npm as package manager; no bun.lock committed. Do not run `bun install` here.
- Do not raise `test.maxWorkers`, and do not raise the 5 s vitest `testTimeout` to silence a timeout — both hide the load-induced flake the pin exists for.
- `tests/test_frontend_api_contract.py` regex-scans `src/services/*.ts` literal paths against the live `app.main:app` route table, and `tests/test_ocr_a255_xlang_parity.py` shares its SM2 vector table with `utils/crypto.ts` — a backend route or signing-format change reddens this repo.

## COMMANDS (run in cds-frontend/)
```bash
npm run dev          # vite :3000, /api → http://localhost:8000 (FastAPI)
npm run build        # tsc -b && vite build → dist/
npm run lint         # eslint . (flat config, ignores dist)
npm test             # vitest run (maxWorkers 4); also test:watch, test:coverage (v8 provider)
npx vitest run src/pages/Flow   # scoped run — always prefer this over the full suite while iterating
npm run preview      # vite preview
# Dockerfile: node:20-bookworm-slim build (glibc — alpine/musl cannot build: rolldown
# native binding is lockfile-pinned to glibc) → nginx:alpine serving dist/ with nginx.conf
# Base images are build-arg overridable (NODE_IMAGE / NGINX_IMAGE, same pattern as
# NPM_REGISTRY) for CN-restricted networks; on the remote test host both must use the
# ddn mirror prefix swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/<image>
```
