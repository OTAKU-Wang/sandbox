import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, Spin } from 'antd';
import { zhCN } from './antdLocale';
import ProtectedRoute from './components/Auth/ProtectedRoute';
import RoleRoute from './components/Auth/RoleRoute';
import MainLayout from './components/Layout/MainLayout';
import WorkspaceHome from './components/Layout/WorkspaceHome';
import WorkspaceRoute from './components/Layout/WorkspaceRoute';
import { ROLE_GROUPS } from './utils/roles';
import { ADMIN_WORKSPACE_ROLES } from './utils/workspace';
import { UserRole } from './types/enums';

// Route-level lazy loading — each page becomes a separate chunk
const Login = lazy(() => import('./pages/Auth/Login'));
const Register = lazy(() => import('./pages/Auth/Register'));
// 首页按工台分派（原型 A-1）：两张首页都改由 WorkspaceHome 懒加载，App.tsx 不再直接挂。
const ProductList = lazy(() => import('./pages/DataProducts/ProductList'));
const ProductCreate = lazy(() => import('./pages/DataProducts/ProductCreate'));
const ProductDetail = lazy(() => import('./pages/DataProducts/ProductDetail'));
const ContractList = lazy(() => import('./pages/Contracts/ContractList'));
const ContractCreate = lazy(() => import('./pages/Contracts/ContractCreate'));
const ContractDetail = lazy(() => import('./pages/Contracts/ContractDetail'));
const SessionList = lazy(() => import('./pages/Sandbox/SessionList'));
const SessionDetail = lazy(() => import('./pages/Sandbox/SessionDetail'));
const DataProductDev = lazy(() => import('./pages/Sandbox/DataProductDev'));
const NotebookWorkbench = lazy(() => import('./pages/Flow/NotebookWorkbench'));
const NotFound = lazy(() => import('./pages/NotFound'));
const WorkloadCenter = lazy(() => import('./pages/Flow/WorkloadCenter'));
const ComputeSchedule = lazy(() => import('./pages/Flow/ComputeSchedule'));
const DeliveryCenter = lazy(() => import('./pages/Flow/DeliveryCenter'));
const FlowGuide = lazy(() => import('./pages/Flow/Guide'));
const FlowDevTasks = lazy(() => import('./pages/Flow/DevTasks'));
const FlowDevTaskCreate = lazy(() => import('./pages/Flow/DevTaskCreatePage'));
const FlowTaskDetail = lazy(() => import('./pages/Flow/TaskDetail'));
const FlowQuota = lazy(() => import('./pages/Flow/Quota'));
const AuditLog = lazy(() => import('./pages/Audit/AuditLog'));
const InspectionPipeline = lazy(() => import('./pages/OutputControl/InspectionPipeline'));
const MonitoringDashboard = lazy(() => import('./pages/Monitoring'));
const CatalogPage = lazy(() => import('./pages/Catalog'));
const ResourceList = lazy(() => import('./pages/DataResources/ResourceList'));
const CrossSpaceDashboard = lazy(() => import('./pages/Federation/CrossSpaceDashboard'));
const TrainingDashboard = lazy(() => import('./pages/Training/TrainingDashboard'));
const Connectors = lazy(() => import('./pages/Connectors'));
const KeyManagement = lazy(() => import('./pages/Identity/KeyManagement'));
const Certificates = lazy(() => import('./pages/Identity/Certificates'));
const K8sDeployments = lazy(() => import('./pages/K8sOps/Deployments'));
const K8sNetworkPolicies = lazy(() => import('./pages/K8sOps/NetworkPolicies'));
const K8sPvcs = lazy(() => import('./pages/K8sOps/Pvcs'));
const K8sLogs = lazy(() => import('./pages/K8sOps/Logs'));
const CapabilityMatrix = lazy(() => import('./pages/CapabilityMatrix'));
const ComplianceDisclosure = lazy(() => import('./pages/ComplianceDisclosure'));
const MpcPage = lazy(() => import('./pages/MPC'));
const DataPipeline = lazy(() => import('./pages/DataPipeline'));
const SystemStatus = lazy(() => import('./pages/SystemStatus'));
const UserManagement = lazy(() => import('./pages/UserManagement'));
const ApiKeys = lazy(() => import('./pages/ApiKeys'));

const PageSpinner = () => <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1b6ef3', colorLink: '#1b6ef3' } }}>
        <BrowserRouter>
          <Suspense fallback={<PageSpinner />}>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
              <Route
                path="/*"
                element={
                  <ProtectedRoute>
                    <MainLayout />
                  </ProtectedRoute>
                }
              >
                {/* 工作台口径门（D-16）：RoleRoute 管角色，内层 WorkspaceRoute 管工作台 ——
                    developer = 两个工作台都在（含两端共用的详情子页与沙箱会话），admin = 管理态专属页。
                    /federation /training /data-pipeline 不标 admin：它们的角色门放行了业务角色，
                    而业务角色切不到管理员工台，标了等于把这些页面从数商/买方手里拿走（超出 D-16 范围）。 */}
                {/* 原型 A-1：index = 当前工台的首页（开发者工台落开发者首页，管理员工台落运营概览）。 */}
                <Route index element={<WorkspaceHome />} />
                <Route path="data-products" element={<RoleRoute allowedRoles={ROLE_GROUPS.productReaders}><WorkspaceRoute workspace="developer"><ProductList /></WorkspaceRoute></RoleRoute>} />
                <Route path="data-resources" element={<RoleRoute allowedRoles={ROLE_GROUPS.dataResourceManagers}><WorkspaceRoute workspace="developer"><ResourceList /></WorkspaceRoute></RoleRoute>} />
                <Route path="data-products/create" element={<RoleRoute allowedRoles={ROLE_GROUPS.productWriters}><WorkspaceRoute workspace="developer"><ProductCreate /></WorkspaceRoute></RoleRoute>} />
                <Route path="data-products/:id" element={<RoleRoute allowedRoles={ROLE_GROUPS.productReaders}><WorkspaceRoute workspace="developer"><ProductDetail /></WorkspaceRoute></RoleRoute>} />
                <Route path="contracts" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><WorkspaceRoute workspace="developer"><ContractList /></WorkspaceRoute></RoleRoute>} />
                <Route path="contracts/create" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractWriters}><WorkspaceRoute workspace="developer"><ContractCreate /></WorkspaceRoute></RoleRoute>} />
                <Route path="contracts/:id" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><WorkspaceRoute workspace="developer"><ContractDetail /></WorkspaceRoute></RoleRoute>} />
                <Route path="sandbox-sessions" element={<RoleRoute allowedRoles={ROLE_GROUPS.sandboxReaders}><WorkspaceRoute workspace="developer"><SessionList /></WorkspaceRoute></RoleRoute>} />
                <Route path="sandbox-sessions/:id" element={<RoleRoute allowedRoles={ROLE_GROUPS.sandboxReaders}><WorkspaceRoute workspace="developer"><SessionDetail /></WorkspaceRoute></RoleRoute>} />
                <Route path="dev-sandbox" element={<RoleRoute allowedRoles={ROLE_GROUPS.devSandboxUsers}><WorkspaceRoute workspace="developer"><DataProductDev /></WorkspaceRoute></RoleRoute>} />
                <Route path="notebooks" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><WorkspaceRoute workspace="developer"><NotebookWorkbench /></WorkspaceRoute></RoleRoute>} />
                <Route path="workloads" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><WorkspaceRoute workspace="developer"><WorkloadCenter /></WorkspaceRoute></RoleRoute>} />
                <Route path="compute" element={<RoleRoute allowedRoles={ROLE_GROUPS.sandboxReaders}><WorkspaceRoute workspace="developer"><ComputeSchedule /></WorkspaceRoute></RoleRoute>} />
                <Route path="deliveries" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><WorkspaceRoute workspace="developer"><DeliveryCenter /></WorkspaceRoute></RoleRoute>} />
                <Route path="deliveries/dataset" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><WorkspaceRoute workspace="developer"><DeliveryCenter /></WorkspaceRoute></RoleRoute>} />
                <Route path="deliveries/push" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><WorkspaceRoute workspace="developer"><DeliveryCenter /></WorkspaceRoute></RoleRoute>} />
                {/* 旧开发者首页 /home：SS-18 把开发者首页提到 index 路由（原型 A-1 的「首页」），
                    旧链接与旧收藏一律 replace 到 '/'，由 WorkspaceHome 按工台分派。 */}
                <Route path="home" element={<Navigate to="/" replace />} />
                <Route path="flow/tasks" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><WorkspaceRoute workspace="developer"><FlowDevTasks /></WorkspaceRoute></RoleRoute>} />
        <Route path="flow/tasks/new" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><WorkspaceRoute workspace="developer"><FlowDevTaskCreate /></WorkspaceRoute></RoleRoute>} />
        <Route path="flow/tasks/:taskId" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><WorkspaceRoute workspace="developer"><FlowTaskDetail /></WorkspaceRoute></RoleRoute>} />
                <Route path="flow/guide" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><WorkspaceRoute workspace="developer"><FlowGuide /></WorkspaceRoute></RoleRoute>} />
                <Route path="flow/quota" element={<RoleRoute allowedRoles={ROLE_GROUPS.sandboxReaders}><WorkspaceRoute workspace="developer"><FlowQuota /></WorkspaceRoute></RoleRoute>} />
                <Route path="audit" element={<RoleRoute allowedRoles={ROLE_GROUPS.auditReaders}><WorkspaceRoute workspace="developer"><AuditLog /></WorkspaceRoute></RoleRoute>} />
                <Route path="output-control" element={<RoleRoute allowedRoles={ROLE_GROUPS.outputControlReaders}><WorkspaceRoute workspace="admin"><InspectionPipeline /></WorkspaceRoute></RoleRoute>} />
                <Route path="catalog" element={<RoleRoute allowedRoles={ROLE_GROUPS.catalogReaders}><CatalogPage /></RoleRoute>} />
                <Route path="monitoring" element={<RoleRoute allowedRoles={ROLE_GROUPS.monitoringReaders}><WorkspaceRoute workspace="admin"><MonitoringDashboard /></WorkspaceRoute></RoleRoute>} />
                <Route path="federation" element={<RoleRoute allowedRoles={ROLE_GROUPS.federationUsers}><CrossSpaceDashboard /></RoleRoute>} />
                <Route path="training" element={<RoleRoute allowedRoles={ROLE_GROUPS.trainingUsers}><TrainingDashboard /></RoleRoute>} />
                <Route path="connectors" element={<RoleRoute allowedRoles={ROLE_GROUPS.connectorManagers}><WorkspaceRoute workspace="admin"><Connectors /></WorkspaceRoute></RoleRoute>} />
                <Route path="identity/keys" element={<RoleRoute allowedRoles={ROLE_GROUPS.identityManagers}><WorkspaceRoute workspace="admin"><KeyManagement /></WorkspaceRoute></RoleRoute>} />
                <Route path="certificates" element={<RoleRoute allowedRoles={ROLE_GROUPS.identityManagers}><WorkspaceRoute workspace="admin"><Certificates /></WorkspaceRoute></RoleRoute>} />
                <Route path="ops/deployments" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><WorkspaceRoute workspace="admin"><K8sDeployments /></WorkspaceRoute></RoleRoute>} />
                <Route path="ops/network-policies" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><WorkspaceRoute workspace="admin"><K8sNetworkPolicies /></WorkspaceRoute></RoleRoute>} />
                <Route path="ops/pvcs" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><WorkspaceRoute workspace="admin"><K8sPvcs /></WorkspaceRoute></RoleRoute>} />
                <Route path="ops/logs" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><WorkspaceRoute workspace="admin"><K8sLogs /></WorkspaceRoute></RoleRoute>} />
                <Route path="capability-matrix" element={<RoleRoute allowedRoles={ADMIN_WORKSPACE_ROLES}><WorkspaceRoute workspace="admin"><CapabilityMatrix /></WorkspaceRoute></RoleRoute>} />
                <Route path="data-pipeline" element={<DataPipeline />} />
                <Route path="compliance-disclosure" element={<RoleRoute allowedRoles={ROLE_GROUPS.monitoringReaders}><WorkspaceRoute workspace="developer"><ComplianceDisclosure /></WorkspaceRoute></RoleRoute>} />
                <Route path="mpc" element={<RoleRoute allowedRoles={ROLE_GROUPS.identityManagers}><WorkspaceRoute workspace="admin"><MpcPage /></WorkspaceRoute></RoleRoute>} />
                <Route path="system-status" element={<RoleRoute allowedRoles={ROLE_GROUPS.monitoringReaders}><WorkspaceRoute workspace="admin"><SystemStatus /></WorkspaceRoute></RoleRoute>} />
                <Route path="users" element={<RoleRoute allowedRoles={[UserRole.ADMIN]}><WorkspaceRoute workspace="admin"><UserManagement /></WorkspaceRoute></RoleRoute>} />
                <Route path="api-keys" element={<RoleRoute allowedRoles={[UserRole.ADMIN]}><WorkspaceRoute workspace="admin"><ApiKeys /></WorkspaceRoute></RoleRoute>} />
                <Route path="*" element={<NotFound />} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
