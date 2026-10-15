# src/pages/ — 26 lazy route pages (scored 9: 26 default-export components in 15 domain folders)

Every page is a default export wired through `React.lazy` in `App.tsx`. Role gates live in App.tsx (`RoleRoute allowedRoles={ROLE_GROUPS.*}`), not in pages.

## WHERE TO LOOK (route → file)
| Route | File |
|-------|------|
| `/` (index) | `Dashboard/index.tsx` (464) |
| `/data-products`, `/create`, `/:id` | `DataProducts/{ProductList,ProductCreate,ProductDetail}.tsx` (578) |
| `/data-resources` | `DataResources/ResourceList.tsx` |
| `/contracts`, `/create`, `/:id` | `Contracts/{ContractList,ContractCreate,ContractDetail}.tsx` |
| `/sandbox-sessions`, `/:id` | `Sandbox/{SessionList,SessionDetail}.tsx` — **SessionDetail 1304 LOC, the complexity concentration** |
| `/dev-sandbox` | `Sandbox/DataProductDev.tsx` (358) |
| `/audit` | `Audit/AuditLog.tsx` |
| `/output-control` | `OutputControl/InspectionPipeline.tsx` (404) |
| `/catalog` | `Catalog/index.tsx` |
| `/monitoring` | `Monitoring/index.tsx` (579) |
| `/federation` | `Federation/CrossSpaceDashboard.tsx` |
| `/training` | `Training/TrainingDashboard.tsx` |
| `/connectors` | `Connectors/index.tsx` (404) |
| `/identity/keys`, `/certificates` | `Identity/{KeyManagement,Certificates}.tsx` |
| `/ops/{deployments,network-policies,pvcs,logs}` | `K8sOps/{Deployments,NetworkPolicies,Pvcs,Logs}.tsx` |
| `/login`, `/register` | `Auth/{Login,Register}.tsx` — the only pages outside ProtectedRoute |

## CONVENTIONS
- Data via react-query `useQuery`/`useMutation` on `services/*`; feedback UI via `components/Feedback/QueryFeedback` (getErrorMessage, QueryErrorAlert, CenteredLoading, EmptyState, tableEmpty).
- In-page `can*` permission booleans govern intra-page actions; route-level roles stay in App.tsx — do not duplicate role checks inside pages.
- UI copy is Chinese, matching backend messages.

## ANTI-PATTERNS
- `SessionDetail.tsx`: ~6 useQuery + ~13 useMutation + 9 `can*` flags in one 1304-line file — extract before adding more; new sandbox features go to a sibling file in `Sandbox/`.
- No page imports another page; shared UI belongs in `components/`.
- K8sOps pages must render `cluster_available`: an empty list means no reachable cluster, not "nothing deployed".
