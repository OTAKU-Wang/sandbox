# src/pages/ — 42 lazy route pages (scored 11: 138 files / 33.4k LOC (22.1k non-test) in 16 domain folders + 8 top-level .tsx; distinct routing domain)

Every page is a default export wired through `React.lazy` in `App.tsx` (42 lazy imports, 48 `<Route>`). Route gates live in App.tsx, not in pages: `RoleRoute allowedRoles={ROLE_GROUPS.*}` (role) wraps `WorkspaceRoute workspace="developer"` (工作台口径 D-16) inside it. `/federation` `/training` `/data-pipeline` deliberately carry NO WorkspaceRoute — their role gate admits business roles, and marking them admin would lock those users out of their own page.

## WHERE TO LOOK (route → file)
| Route | File (LOC) | Gate (ROLE_GROUPS group) |
|-------|------|------|
| `/login`, `/register` | `Auth/{Login,Register}.tsx` (98/94) | public — the only pages outside ProtectedRoute; Login reads **public** `/health` capability surface pre-JWT (SS-11 PZ-13) and maps `l2c` → "L2'" |
| `/` (index) | `Dashboard/index.tsx` (606) | monitoringReaders · admin; `canApprove` = operator |
| `/data-products[/{id}\|/create]` | `DataProducts/{ProductList,ProductCreate,ProductDetail}.tsx` (194/230/584) | productReaders / productWriters · developer |
| `/data-resources` | `DataResources/ResourceList.tsx` (192) + DesensitizedPreviewModal (101) | dataResourceManagers · developer |
| `/contracts[/{id}\|/create]` | `Contracts/{ContractList,ContractCreate,ContractDetail}.tsx` (71/367/260) | contractReaders / contractWriters · developer |
| `/sandbox-sessions`, `/:id` | `Sandbox/{SessionList,SessionDetail}.tsx` (275/1645) | sandboxReaders · developer — **SessionDetail 1645 LOC is the complexity concentration** |
| `/dev-sandbox` | `Sandbox/DataProductDev.tsx` (359) | devSandboxUsers · developer |
| `/notebooks` | `Flow/NotebookWorkbench.tsx` (1361) | flowDevUsers · developer — JupyterLab iframe + cell run; panels split out to `notebookWorkbenchPanels.tsx` (348) + `notebookWorkbenchModel.ts` |
| `/workloads` | `Flow/WorkloadCenter.tsx` (567) | flowDevUsers · developer — publish-gate UI |
| `/compute` | `Flow/ComputeSchedule.tsx` (584) | sandboxReaders · developer |
| `/deliveries[/dataset\|/push]` | `Flow/DeliveryCenter.tsx` (2267) | contractReaders · developer — biggest page in the app: delivery methods/OpenAPI/webhook/curl + `deliveryOpenapi.ts` (308) + own CSS |
| `/home` | `Flow/DevHome.tsx` (662) | flowDevUsers · developer |
| `/flow/tasks`, `/new`, `/:taskId` | `Flow/{DevTasks,DevTaskCreatePage,TaskDetail}.tsx` (280/338/600) | flowDevUsers · developer |
| `/flow/guide`, `/flow/quota` | `Flow/{Guide,Quota}.tsx` (357/245) | flowDevUsers · developer — Quota is platform-level (D-19), not per-task |
| `/audit` | `Audit/AuditLog.tsx` (569) + `auditDisplay.ts` (133) + `auditLog.css` | auditReaders · **developer** workspace |
| `/output-control` | `OutputControl/InspectionPipeline.tsx` (413) + `dpBudgetView.ts` (34) | outputControlReaders · admin |
| `/catalog` | `Catalog/index.tsx` (265) | catalogReaders · admin |
| `/monitoring` | `Monitoring/index.tsx` (550) | monitoringReaders · admin |
| `/federation` | `Federation/CrossSpaceDashboard.tsx` (187) | federationUsers (no workspace gate) |
| `/training` | `Training/TrainingDashboard.tsx` (209) | trainingUsers (no workspace gate) |
| `/connectors` | `Connectors/index.tsx` (441) | connectorManagers · admin |
| `/identity/keys`, `/certificates` | `Identity/{KeyManagement,Certificates}.tsx` (251/316) | identityManagers · admin |
| `/ops/{deployments,network-policies,pvcs,logs}` | `K8sOps/{Deployments,NetworkPolicies,Pvcs,Logs}.tsx` (113/122/102/91) | k8sOpsReaders · admin |
| `/mpc` | `MPC.tsx` (312) | monitoringReaders · admin — Shamir custodianship UI, NOT MPC compute |
| `/capability-matrix` `/compliance-disclosure` `/system-status` | `CapabilityMatrix/ComplianceDisclosure/SystemStatus.tsx` (122/188/193) | monitoringReaders · admin |
| `/data-pipeline` | `DataPipeline.tsx` (175) | dataResourceManagers (no workspace gate) |
| `/users` `/api-keys` | `UserManagement.tsx` (152), `ApiKeys.tsx` (687, SS-12 key lifecycle) | identityManagers / k8sOpsReaders · admin |

## CONVENTIONS
- Data via react-query `useQuery`/`useMutation` on `services/*`; feedback UI via `components/Feedback/QueryFeedback` (getErrorMessage, QueryErrorAlert, CenteredLoading, EmptyState, tableEmpty — 45 non-test importers).
- In-page `can*` permission booleans govern intra-page actions; route-level roles stay in App.tsx — do not duplicate role checks inside pages.
- UI copy is Chinese, matching backend messages.
- Tests sit in the folder's own `__tests__/` (pages hold 60 of the app's 112 test files; Flow alone 40) and inherit the repo naming `<subject>.<ledger-id>.test.tsx` (`taskDetail.a233.test.tsx`), so a `a###` suffix maps back to the defect ledger in `docs/`.
- `Flow/` (SS-15 流通编排, 35 src files / 10.9k non-test LOC = 49% of this directory's product code, 18.9k with its 40 test files): shared pure logic lives in sibling model modules — `display.ts` (305: status-meta maps, `UNKNOWN_TEXT` sentinel, `evaluatePublishGate`, backend-view resolution; 45 cases across `display.test.ts` + `display.a246.test.ts`), `notebookWorkbenchModel.ts`, `devTasksModel.ts`, `homeModel.ts`, `contractDataModel.ts`, `flowResource.ts`, `useContractOptions.ts`, `directoryContract.ts`, `monacoSetup.ts`. All server types/enums/query hooks come from `services/flow`. In-Flow page→page imports are ordinary (DeliveryCenter→BundlePreviewModal, TaskDetail→TaskRunRecords, FlowConfigCard→FlowConfigFields, 3 pages→RuntimeStatusBanner): inside a domain folder a sibling page doubles as a component.
- Page-owned CSS exists in exactly 5 files / 1908 LOC — `Flow/{taskDetail,notebookWorkbench,devTaskCreate,deliveryOpenapi}.css` + `Audit/auditLog.css`. Everywhere else use `var(--cds-*)` or inline style; `cssHygiene.wave3.test.tsx` scans page source off disk for banned tokens.

## ANTI-PATTERNS
- `SessionDetail.tsx` is 1645 lines with 6 `useState` + 11 `useQuery` + 13 `useMutation` — it is already the mutation hub; extract to a `Sandbox/` sibling before adding more (that is how `sessionWorkbench.ts` / `SessionStatusStrip` / `ProofBundleDrawer` came to be). The old "~18 useState" warning is stale: state was extracted, mutations were not.
- Cross-**folder** page imports exist at exactly 2 sites, both Contracts→Flow (`ContractDetail.tsx:17` → FlowConfigCard, `ContractCreate.tsx:12` → FlowConfigFields). They are tolerated reuse of a Flow-owned card, not a pattern: new shared UI belongs in `components/`, new shared pure logic in the domain folder's own module.
- K8sOps pages must render `cluster_available`: an empty list means no reachable cluster, not "nothing deployed".
- Flow status copy maps backend values only and passes unknown values through unmodified; the publish gate authority is the backend (412 + error code) — frontend `evaluatePublishGate` copy only pre-disables the button, never invents an explanation (禁伪造铁律, `display.ts`; unknown values render the `UNKNOWN_TEXT` '未记录' sentinel, never a placeholder that looks like data).
- Never draw a sandbox-level selector or label from `SandboxLevel` alone: L2' is the only offered level (`LEGACY_SANDBOX_LEVELS` exists solely to render historical rows), and a request without a MicroVM must surface 400 `RUNTIME_UNAVAILABLE`, never a downgrade.
- `directoryContract.ts` hard-codes the guest paths `/data/input` and `/data/output` mirroring `app/services/cube_runtime_adapter.py` `GUEST_DATA_DIR`/`GUEST_OUTPUT_DIR`; Guide and ContractDataBoard render from this one constant, so a backend path change is a two-file change with no test catching it.
