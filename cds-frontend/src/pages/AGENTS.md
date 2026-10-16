# src/pages/ — 38 lazy route pages (scored 9: default-export components in 16 domain folders + 8 top-level .tsx)

Every page is a default export wired through `React.lazy` in `App.tsx`. Role gates live in App.tsx (`RoleRoute allowedRoles={ROLE_GROUPS.*}`), not in pages.

## WHERE TO LOOK (route → file)
| Route | File |
|-------|------|
| `/` (index) | `Dashboard/index.tsx` (518) |
| `/data-products`, `/create`, `/:id` | `DataProducts/{ProductList,ProductCreate,ProductDetail}.tsx` (578) |
| `/data-resources` | `DataResources/ResourceList.tsx` |
| `/contracts`, `/create`, `/:id` | `Contracts/{ContractList,ContractCreate,ContractDetail}.tsx` |
| `/sandbox-sessions`, `/:id` | `Sandbox/{SessionList,SessionDetail}.tsx` — **SessionDetail 1613 LOC, the complexity concentration** |
| `/dev-sandbox` | `Sandbox/DataProductDev.tsx` (359) |
| `/notebooks` | `Flow/NotebookWorkbench.tsx` (807, Monaco + cell run + publish) |
| `/workloads` | `Flow/WorkloadCenter.tsx` (603, publish-gate UI) |
| `/compute` | `Flow/ComputeSchedule.tsx` (317) |
| `/deliveries` | `Flow/DeliveryCenter.tsx` (1048, widest surface: delivery methods/OpenAPI/webhook/curl) |
| `/audit` | `Audit/AuditLog.tsx` |
| `/output-control` | `OutputControl/InspectionPipeline.tsx` (403) |
| `/catalog` | `Catalog/index.tsx` |
| `/monitoring` | `Monitoring/index.tsx` (578) |
| `/federation` | `Federation/CrossSpaceDashboard.tsx` |
| `/training` | `Training/TrainingDashboard.tsx` |
| `/connectors` | `Connectors/index.tsx` (404) |
| `/identity/keys`, `/certificates` | `Identity/{KeyManagement,Certificates}.tsx` |
| `/mpc` | `MPC.tsx` (311) |
| `/ops/{deployments,network-policies,pvcs,logs}` | `K8sOps/{Deployments,NetworkPolicies,Pvcs,Logs}.tsx` |
| `/capability-matrix` `/compliance-disclosure` `/system-status` `/data-pipeline` | top-level `CapabilityMatrix/ComplianceDisclosure/SystemStatus/DataPipeline.tsx` |
| `/users` `/api-keys` | `UserManagement.tsx`, `ApiKeys.tsx` (709, SS-12 key lifecycle) — admin-only routes |
| `/login`, `/register` | `Auth/{Login,Register}.tsx` — the only pages outside ProtectedRoute; Login reads **public** `/health` capability surface pre-JWT (SS-11 PZ-13) and maps `l2c` → "L2'" |

## CONVENTIONS
- Data via react-query `useQuery`/`useMutation` on `services/*`; feedback UI via `components/Feedback/QueryFeedback` (getErrorMessage, QueryErrorAlert, CenteredLoading, EmptyState, tableEmpty).
- In-page `can*` permission booleans govern intra-page actions; route-level roles stay in App.tsx — do not duplicate role checks inside pages.
- UI copy is Chinese, matching backend messages.
- `Flow/` (SS-15 流通编排): shared pure logic lives in `Flow/display.ts` (status-meta maps, publish gate, backend-view resolution — the only unit-tested surface, 34 cases); all server types/enums/query hooks come from `services/flow`. Even inside Flow/, pages never import each other — sibling helpers (`display.ts`, `flowResource.ts`, `useContractOptions.ts`, `RuntimeStatusBanner`) are the sharing mechanism.

## ANTI-PATTERNS
- `SessionDetail.tsx`: ~18 useState + ~6 useQuery + many useMutation in one 1613-line file — extract before adding more; new sandbox features go to a sibling file in `Sandbox/` (that is how sessionWorkbench/SessionStatusStrip/ProofBundleDrawer came to be).
- No page imports another page; shared UI belongs in `components/`, shared pure logic in the domain folder's own module.
- K8sOps pages must render `cluster_available`: an empty list means no reachable cluster, not "nothing deployed".
- Flow status copy maps backend values only and passes unknown values through unmodified; the publish gate authority is the backend (412 + error code) — frontend `evaluatePublishGate` copy only pre-disables the button, never invents an explanation (禁伪造铁律, `display.ts`).
