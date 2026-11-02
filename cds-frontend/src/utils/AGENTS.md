# src/utils/ — the app's permission + wire-format primitives (scored 9, distinct domain: 7 product files / 712 LOC but 32 importer files and two contracts that cross the Python/TypeScript boundary)

Earned its file on reference centrality and contract risk, not size: 68 assertions in 7 test files test more utility code than any other directory of its size.

## WHERE TO LOOK
| Concern | File (LOC) | Anchor |
|---|---|---|
| Role vocabulary + route groups | `roles.ts` (62) | `ROLE_LABELS` (5 UserRole entries, zh) `:14`, `ROLE_GROUPS` (18 groups) `:25`, `normalizeUserRole` `:52`, `hasAnyRole` `:58`, `LEGACY_ROLE_ALIASES` (provider/consumer/developer/security_admin → current) `:7` |
| Workspace split (管理员工台 vs 开发者工作台) | `workspace.ts` (174) | `Workspace = 'developer' \| 'admin'`, `ADMIN_WORKSPACE_ROLES` = operator/regulator/admin `:8`, `DEVELOPER_MENU_KEYS` (20 route keys) `:10`, `resolveWorkspace` `:79`, `filterMenuByWorkspace` `:89`, `matchMenuSelection` `:155`, `homeLandingPath()` → `/` `:72` |
| SM2/SM3 + contract payload | `crypto.ts` (200) | `generateSM2KeyPair` `:20`, `sm2Sign` `:34`, `sm3Hash` `:59`, `contractSignPayload` `:127`, payload string `CDS-SIGN\|<contractId>\|<contractNo>\|<partyRole>\|<timestamp>` `:156`, limits `:70-72`, `SM2_KEY_STORAGE_DISCLOSURE` `:177` |
| List-payload unwrap | `unwrapList.ts` (69) | `ListShapeKey = 'array' \| 'items' \| 'data' \| 'results'` `:16`, `ListShapeError` `:34`, `unwrapList(payload, endpoint, canonical)` `:54` |
| CSV preview | `csvPreview.ts` (149) | `splitCsvRecords` (CRLF/quoted/multi-line) `:44`, `splitCsvLine` `:90`, `parseCsvPreview(text, maxRows=50)` `:116` |
| High-risk confirm modal | `highRiskOperation.tsx` (52) | `confirmHighRiskOperation` `:13` — collects `{reason, ticket_id}` (reason maxLength 500) and always passes a payload, blank fields included |
| cn.ts (6) | — | dead: 0 importers. Not the styling helper; delete rather than adopt |

Tests live in `utils/__tests__/` (7 files, 811 LOC, 68 cases) under the repo's `<subject>.<ledger-id>.test.ts` naming — `workspace.a296.test.ts` maps back to defect M-195/M-196 in the portal ledger. `highRiskOperation.tsx` is the only `.tsx` here: everything else is React-free, so all of it unit-tests without a DOM.

## CONVENTIONS (differ from parent)
- Every exported symbol here has a non-test consumer; a new helper lands here only with its caller in the same change.
- `csvPreview` exposes both `extra` (object keyed by header) and `extraCells` (index-aligned array); new callers use `extraCells` — the object form is retained only for existing consumers and mislabels when the header itself has duplicates.
- `roles.ts` is the **only** place role names are normalized — components and pages call `hasAnyRole`, they never string-compare `user.role`.
- `resolveWorkspace` whitelists the two literals read back from localStorage (A296/M-195) and `filterMenuByWorkspace` treats a non-array `children` as a leaf (A296/M-196): persisted JSON is untrusted input, so unknown values fall back to the role default instead of propagating.
- `unwrapList` accepts the four shapes the backend actually returns (bare array vs `{items|data|results}`) and **throws** on anything else — an unwrap failure and an empty list must never look the same in the UI.
- `csvPreview` de-duplicates colliding headers with `#2/#3` suffixes and parks over-width rows under `_extra` (index-aligned with `headers`), so a malformed row degrades visibly instead of dropping cells.

## ANTI-PATTERNS
- `contractSignPayload` output must stay **byte-identical** to `app/services/crypto_service.py::contract_sign_data` and `sdk/cds_sdk/client.py::sign_data`. The three-way vector table is shared with `tests/test_ocr_a255_xlang_parity.py`; change it from the pytest side (it refuses vectors that disagree with the backend), never by editing the frontend copy alone. Same for the length limits, which mirror the DB column widths (`crypto_service.py:217-219`).
- Over-limit payload fields are **rejected, not escaped** (`ContractPayloadError`): escaping would change the signed bytes and split signature verification across languages (`crypto_service.py:208-216`).
- Do not "fix" `SM2_KEY_STORAGE_DISCLOSURE` by dropping the disclosure: SM2 private keys come from the server and sit in localStorage in plaintext under `cds_sm2_privkey_<userId>`. That is a known accepted posture the UI must state, not a secret to hide.
- Never re-implement a role check or a workspace check inline in a page — an inline comparison silently skips the legacy-alias and persisted-value normalization, which is exactly the class of defect these tests pin (`crypto.test.ts`, `workspace.{test,a259,a296}.test.ts`).
- Do not restore `SELF_REGISTER_ROLE_OPTIONS`: it offered 数商/买方 at self-registration while the backend's `ALLOWED_SELF_REGISTER_ROLES` is `{admin}` (omitted ⇒ buyer). Deleted in PZ-10 with a 勿恢复 marker in `roles.ts:22`.
- Route strings that `DEVELOPER_MENU_KEYS` / `matchMenuSelection` compare against must stay equal to the `App.tsx` path constants (including the bare `'/'` entry, which is a real key, not a placeholder) — the workspace menu filter and the breadcrumb both key off them.
