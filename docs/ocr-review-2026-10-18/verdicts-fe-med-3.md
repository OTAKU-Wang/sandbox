# Verdicts — FE batch med 3 (READ-ONLY review)

Scope: cds-frontend/src/utils/copyText.ts (does not exist — see note), src/pages/Flow/copyText.ts,
src/main.tsx, src/components/Layout/WorkspaceHome.tsx, src/components/Auth/RoleRoute.tsx,
src/components/Auth/ProtectedRoute.tsx, index.html. 21 claims: M-1..M-4, M-198/M-199, M-11..M-15,
M-206/M-207, M-38/M-224, M-43/M-44/M-228, M-67/M-68/M-243.

> File-path note: the batch header says `cds-frontend/src/utils/copyText.ts`; that path does not exist
> (`read` → ENOENT). `tool.grep copyText cds-frontend/src` → 3 files only: `src/pages/Flow/copyText.ts`,
> `src/pages/ApiKeys.tsx:110` (a verbatim private re-implementation), `src/pages/Flow/DeliveryCenter.tsx:118`
> (the only importer). All three copyText claims are judged against `src/pages/Flow/copyText.ts`.

Deploy baseline used for the index.html / main.tsx judgements: `cds-frontend/Dockerfile:39`
(`COPY nginx.conf /etc/nginx/conf.d/default.conf`) — the nginx.conf read here IS the serving config for
compose / k3s / helm; `tool.grep "try_files"` across the repo hits only that file, so there is no second SPA config.

---

## cds-frontend/index.html

### cds-frontend/index.html:10 — 高估 (n/a; residue 低)  [M-1]
The code fact is right — `<div id="root"></div>` (index.html:10), no `<noscript>`, no placeholder — but the
claim's *amplifying* mechanism is refuted by the shipped config: nginx.conf:9-16 has a dedicated
`location /assets/ { try_files $uri =404; }` whose comment reads 「缺文件要 404，不能回 HTML」, so a missing
hashed chunk 404s instead of returning 200 + text/html. The 2026-10-17 white-screen incident it cites
(nginx.conf:18-20) is precisely what that `=404` plus `Cache-Control "no-cache"` (nginx.conf:27) fixed.
What is left is the (valid) no-fallback-content point, which M-4 states cleanly — this claim is the
duplicate-with-a-wrong-mechanism version.

### cds-frontend/index.html:6 — 真 (低)  [M-2]
Verified: index.html:3-8 carries only charset / icon / viewport / title — no CSP, no referrer meta. In the
deployed app the main document is served by nginx.conf `location /` (22-29) and `location = /index.html`
(31-37), which add only nosniff / X-Frame-Options / X-XSS-Protection / Referrer-Policy / Cache; the server
block (100-103) adds nothing more. CSP exists solely for `/api/jupyter/` (nginx.conf:75) and the matching
backend prefix (app/core/security.py:154-172, pinned by tests/test_security.py:22). So "no script-source
constraint on the SPA shell" is observable in the deployed app — but this claim and M-3 are one finding, and
its extra 「vite dev :3000 缺安全头」 half is a dev-only surface (vite.config.ts:10-27).

### cds-frontend/index.html:7 — 真 (低)  [M-3]
Same root as M-2, stated more precisely (it correctly notes the nginx fix is the real one, since a `<meta>`
cannot express `frame-ancestors`). Hardening gap, not an exploitable defect: no XSS sink was proven here, so
severity is 低/中 rather than plainly medium. Two implementation caveats the claim misses: a main-document CSP
needs `style-src 'self' 'unsafe-inline'` for antd's runtime CSS-in-JS (the jupyter CSP at nginx.conf:75 already
ships exactly that), and `script-src 'self'` alone can break the bundled Monaco worker (`?worker` import at
src/pages/Flow/monacoSetup.ts:6-17) unless `worker-src 'self' blob:` is added. Not pinnable by vitest (nginx
config + static HTML; only a repo-level config-text lock test could pin it).

### cds-frontend/index.html:10 — 真 (低)  [M-4]
Root node empty (index.html:10) and `createRoot(document.getElementById('root')!)` (main.tsx:7) mean any
module-load failure — hashed chunk 404 after a rebuild, a browser without `type="module"`, a CSP that blocks
the script — leaves zero DOM signal, only a console line. That IS observable in the deployed app (this is the
failure the nginx comment at 18-20 recorded). Fix is static and cheap (placeholder inside #root, which
createRoot clears, plus `<noscript>`). Pin-able only as a repo-style static assertion (read index.html in a
vitest and require a `<noscript>`), the same category as the doc↔code locks the repo already runs.

### cds-frontend/index.html:5 — 潜在 (低)  [M-198]
`/favicon.svg` (index.html:5) and `/src/main.tsx` (index.html:11) are absolute, and vite.config.ts sets no
`base` (default '/'), which the claim itself concedes is currently self-consistent. Vite rewrites both at build
time once `base` is set, so the breakage requires a future subpath deploy *without* setting `base` — a
misconfiguration, not a present defect. Documentation ask.

### cds-frontend/index.html:11 — 潜在 (低)  [M-199]
Duplicate of M-198 with the same admission (「当前是自洽的」) and the same ask (a comment / explicit `base`).
Judged latent for the same reason; also the claim's premise that the entry-script path must stay in sync with
`getElementById('root')` conflates two things — the container id is a single-source fact today (index.html:10 is
the only `#root` in the repo) and has nothing to do with `base`.

---

## cds-frontend/src/components/Auth/ProtectedRoute.tsx

### cds-frontend/src/components/Auth/ProtectedRoute.tsx:10 — 高估 (低 residue)  [M-11]
`"/login"` is hardcoded here and declared again at App.tsx:79; other literals: Header.tsx:31, Register.tsx:80,
routeCrumb.ts:105. Real duplication, but medium is unsupportable: the repo has no route-constant module at all and
App.tsx:94-139 inlines every one of its ~43 paths — singling out one guard is inconsistent. Honest residue: a
rename would silently produce a dead redirect that tsc cannot catch. Low.

### cds-frontend/src/components/Auth/ProtectedRoute.tsx:9 — 潜在 (低)  [M-12]
`isAuthenticated` starts false (authStore.ts:23) and the store uses persist — but the persist config is only
`{ name: 'auth-storage' }` (authStore.ts:35), i.e. zustand ^5.0.14 (package.json) with the default localStorage
storage, which rehydrates **synchronously during store creation**, before React's first render. There is no async
storage and no SSR/prerender in this repo (plain SPA, index.html:11 + BrowserRouter at App.tsx:71). The claim is
itself written as a conditional ("只要 hydrate 不是同步完成") — not observable today.

### cds-frontend/src/components/Auth/ProtectedRoute.tsx:10 — 真 (低)  [M-206]
`<Navigate to="/login" replace />` carries no `state={{ from: location }}`, and Login consumes nothing:
`navigate('/')` at pages/Auth/Login.tsx:41 fires unconditionally after a successful login. Reachable path: a
logged-out user opens or pastes `/contracts/5` → bounced to /login → after login lands on 首页, deep link gone.
`homeLandingPath` (utils/workspace.ts:64) is workspace-correct but never from-aware. One vitest can pin it:
App.routes.test.tsx:35-40 already renders the real App against `window.history`, so asserting
`window.history.state?.usr?.from.pathname` (or that Login returns to the origin path) is a few lines.

### cds-frontend/src/components/Auth/ProtectedRoute.tsx:5 — 伪  [M-207]
`React.ReactNode` without an import (ProtectedRoute.tsx:5, same at RoleRoute.tsx:6) compiles because @types/react 19
still declares the UMD namespace — `node_modules/@types/react/index.d.ts:68` `export as namespace React;`. And the
claimed trigger is **already enabled**: tsconfig.app.json:13 sets `"verbatimModuleSyntax": true` and
`npm run build` = `tsc -b && vite build` is the CI frontend job. TS2686 covers value-position UMD use, not type
positions, so neither stated failure mode fires. At most a style preference (ErrorBoundary.tsx:2 shows the
explicit-import alternative), not a defect.

---

## cds-frontend/src/components/Auth/RoleRoute.tsx

### cds-frontend/src/components/Auth/RoleRoute.tsx:11 — 潜在 (低)  [M-13]
The inconsistency is real in code: ProtectedRoute gates on `isAuthenticated` (ProtectedRoute.tsx:9), RoleRoute on
`user` (RoleRoute.tsx:11). But `isAuthenticated` is written in exactly two places — authStore.ts:24 `login` (sets
user+token+refreshToken+flag in one `set`) and authStore.ts:26 `logout` (clears all four); `setTokens` (:29) never
touches the flag, and `tool.grep isAuthenticated cds-frontend/src` shows no other writer (only tests). So
`isAuthenticated && !user` needs hand-edited localStorage or a future store-shape change → unobservable. The
"subscribes the whole user object" half is negligible: the guarded page subtree re-renders on user change anyway.

### cds-frontend/src/components/Auth/RoleRoute.tsx:12 — 高估 (低 residue)  [M-14]
Item 2's headline is unreachable: RoleRoute renders only inside ProtectedRoute (App.tsx:81-88 wraps the whole `/*`
subtree), so the "!user while logged out" branch can never be observed — the outer guard already redirects to
/login. The reachable residue is the role-insufficient bounce, e.g. BUYER opens `/data-resources` (gate =
`dataResourceManagers` = [DATA_PROVIDER, ADMIN], roles.ts:28) → silent bounce to `/` → Dashboard, with no 403
explanation and no `state.from`. But that bounce is the documented, intended 拒止语义 (WorkspaceHome.tsx:18-21
explains why RoleRoute cannot even be mounted on the index route), and the "outer layer is more opaque than the
inner WorkspaceRoute EmptyState" comparison is inverted by App.tsx:95-138: the inner WorkspaceRoute is only
reached *after* the role gate passes. Hardcoded `"/"` is the same repo-wide constant-free convention as M-11. Low.

### cds-frontend/src/components/Auth/RoleRoute.tsx:7 — 潜在 (低)  [M-15]
`RoleLike = UserRole | string | null | undefined` (roles.ts:3) does widen `allowedRoles` to anything, and `hasAnyRole`
(roles.ts:54-58) matches only after `normalizeUserRole`, so a typo'd or empty list makes `some()` false forever — all
true as type-reading. Two corrections: the failure direction is **fail-closed** (a bad entry denies, never grants),
so labelling this "security" inverts it; and no call site is exposed — all 43 RoleRoute sites pass `ROLE_GROUPS.*`,
`ADMIN_WORKSPACE_ROLES`, or `[UserRole.ADMIN]` (App.tsx:95-138), and no ROLE_GROUPS key is empty (roles.ts:26-46).
Latent type-hardening only.

---

## cds-frontend/src/components/Layout/WorkspaceHome.tsx

### cds-frontend/src/components/Layout/WorkspaceHome.tsx:22 — 潜在 (低)  [M-38]
Code-reading confirmed: with an unresolvable role, `resolveWorkspace` → `defaultWorkspace` → 'developer'
(workspace.ts:35-42, 71-74, since `canAccessAdminWorkspace(undefined)` is false), while
`hasAnyRole(undefined, flowDevUsers)` is false, so line 25 renders `<Dashboard />` — the two 口径 do diverge. But no
value the system can produce reaches it: the backend role set is exactly the five the frontend knows
(app/models/user.py:11-16) plus four aliases already mapped (roles.ts:7-12), and `user === null` with
`isAuthenticated === true` is unreachable (see M-13). The "fail-open 暴露到管理端首页" framing is also overstated:
Dashboard gates every card and section on `hasAnyRole` (pages/Dashboard/index.tsx:191, 308-323, 462) and prints
「无权限」/「未记录」 (index.tsx:36-40), so an unknown role sees the empty-card screen the file header already describes
(WorkspaceHome.tsx:8-9), not admin data. Pin-able: yes — WorkspaceHome.test.tsx:27-59 is exactly this harness; one
case with `role: 'mystery'` locks whichever 口径 is chosen.

### cds-frontend/src/components/Layout/WorkspaceHome.tsx:10 — 真 (低)  [M-224]
Both home pages are lazy (WorkspaceHome.tsx:10-11) and `tool.grep "<Suspense" src` returns only App.tsx:77 and a
test file — MainLayout renders `<Outlet />` with no boundary (MainLayout.tsx:61). So the nearest boundary sits
outside the shell: a cold load of `/` (or any lazy route) replaces header+sider+content with `PageSpinner`, and
client-side navigation keeps the previous page mounted because react-router 7 applies router updates in a
transition. Two refinements: this is not index-specific — fixing only WorkspaceHome leaves every other lazy route
with the same shell flash, the boundary belongs around MainLayout's `<Outlet />`; and it is weakly pin-able, since
rendering `<WorkspaceHome />` without an ancestor Suspense currently suspends past it, which is exactly why the
existing test must supply one (WorkspaceHome.test.tsx:20-24).

---

## cds-frontend/src/main.tsx

### cds-frontend/src/main.tsx:7 — 高估 (低 residue)  [M-43]
`document.getElementById('root')!` is real, but every trigger the claim lists is absent from this repo: index.html:10
and main.tsx are built and shipped as one artifact by cds-frontend/Dockerfile, nothing renames `#root` (it is the
only occurrence in the repo), and there is no microfrontend host, iframe skeleton, or SSR entry. A stale-HTML/new-JS
skew does not remove the node either. What remains is a diagnostic nicety — a guard that prints 「缺少 #root 挂载节点」
instead of React's `Target container is not a DOM element`. Zero-risk one-liner, not a medium bug.

### cds-frontend/src/main.tsx:8 — 伪  [M-44]
"没有任何错误边界（Error Boundary）或全局异常兜底" is factually wrong. App.tsx:76 mounts `<ErrorBoundary>` around
`<Suspense><Routes>`, and components/Feedback/ErrorBoundary.tsx:29-75 implements `getDerivedStateFromError` +
`componentDidCatch` with `console.error` of the raw error and componentStack, plus a 重试本页 / 回首页 panel; it has
its own test (components/Feedback/__tests__/ErrorBoundary.test.tsx). The exact residual the claim invents (provider
boot failure, event-handler and Promise errors) is declared out-of-scope by the code itself at App.tsx:72-75 and
ErrorBoundary.tsx:12-17, per React semantics.

### cds-frontend/src/main.tsx:1 — 高估 (低)  [M-228]
The side-effect import ordering is currently correct and load-bearing only hypothetically: ESM evaluates imports in
source order, so `unstableSetRender` (antdReact19Patch.ts:8-19) registers before App's module graph runs, no module
in src calls `message.*` at module scope, and antd is a single `^5.29.3` dependency — there is no second copy to
fork the patch off. The ask is "add a comment / call `setupAntdReact19Render()` explicitly", i.e. a doc nit raised
to medium. (If the target is real fragility in that file, ocr picked the wrong line here — M-6, unhandled
`root.render`/`unmount`, belongs to another reviewer's file.)

---

## cds-frontend/src/pages/Flow/copyText.ts

### cds-frontend/src/pages/Flow/copyText.ts:3 — 高估 (低)  [M-67]
Half 1 is not a defect: `navigator.clipboard` being undefined on an http origin throws inside `try`, and `catch` at
line 5 drops into the `execCommand` path (6-15) — that IS the availability fallback, and it produces the correct
outcome, so conflating "unavailable" with "denied" costs nothing user-visible. Half 2 (WebIDL coercing
`undefined`/`null` to the literal strings and returning `true`) is mechanically right but has no reachable caller:
all six CopyButton sites pass non-nullable strings — `bareKey` and `snippet` (ApiKeys.tsx:174, 197), `secret`,
`curl`, `attempt?.bodyText` behind a ternary guard, and `keyCreated.key` (DeliveryCenter.tsx:157, 540, 1750, 1814).
The guard/explicit-check suggestion stands as a nit; the severity does not.

### cds-frontend/src/pages/Flow/copyText.ts:11 — 潜在 (低)  [M-68]
The missing `try/finally` is a genuine code-shape gap: `removeChild` sits after `select()` and
`document.execCommand('copy')` (lines 12-14), so any throw there strands a hidden textarea in `document.body`, and
it accumulates per call. But no current engine takes that path — `execCommand('copy')` returns false rather than
throwing in Chrome/Firefox/Edge — and `document.body` is guaranteed (the script runs at the end of body,
index.html:11), so the leak is latent, not observable. The selection-steal point is real but confined to the
fallback path, which only runs after the async API already failed. Pin-able with one vitest: stub
`document.execCommand` to throw and assert `document.body.querySelector('textarea')` is null once `copyText`
resolves false.

### cds-frontend/src/pages/Flow/copyText.ts:16 — 高估 (低)  [M-243]
The claimed hazard — "调用方误以为是 void 方法而不处理 false 分支" — is contradicted by both consumers, which already do
exactly what the claim asks: ApiKeys.tsx:134-140 and DeliveryCenter.tsx:126-132 each
`message.error('复制失败，请手动选择文本复制')` on `false`. A silent catch with no log is a legitimate maintainability
nit, nothing more. Real adjacent finding ocr missed while picking this line: ApiKeys.tsx:110-129 re-implements this
helper **verbatim** instead of importing `./Flow/copyText`, so any M-67/M-68 hardening must be applied twice today.

---

## Counts

- 真: 5 — M-2, M-3 (same root as M-2; should merge), M-4, M-206, M-224
- 伪: 2 — M-44, M-207
- 高估: 7 — M-1, M-11, M-14, M-43, M-228, M-67, M-243
- 潜在: 7 — M-12, M-13, M-15, M-38, M-68, M-198, M-199
- Total: 21

Duplicates worth collapsing: M-2 ≡ M-3 (no CSP on the SPA document), M-1 ≡ M-4 (no blank-page fallback),
M-198 ≡ M-199 (absolute paths vs vite `base`), M-11 ≡ M-14.1 (constant-free route paths, repo-wide).
Highest-value real items: M-3 (nginx CSP, with the two feasibility caveats), M-206 (deep-link loss),
M-224 (the Suspense boundary belongs at MainLayout's Outlet, not inside WorkspaceHome).
