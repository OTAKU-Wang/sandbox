import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import ProtectedRoute from './components/Auth/ProtectedRoute';
import RoleRoute from './components/Auth/RoleRoute';
import MainLayout from './components/Layout/MainLayout';
import { ROLE_GROUPS } from './utils/roles';
import { ADMIN_WORKSPACE_ROLES } from './utils/workspace';
import { UserRole } from './types/enums';

// Route-level lazy loading — each page becomes a separate chunk
const Login = lazy(() => import('./pages/Auth/Login'));
const Register = lazy(() => import('./pages/Auth/Register'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
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
      <ConfigProvider locale={zhCN}>
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
                <Route index element={<Dashboard />} />
                <Route path="data-products" element={<RoleRoute allowedRoles={ROLE_GROUPS.productReaders}><ProductList /></RoleRoute>} />
                <Route path="data-resources" element={<RoleRoute allowedRoles={ROLE_GROUPS.dataResourceManagers}><ResourceList /></RoleRoute>} />
                <Route path="data-products/create" element={<RoleRoute allowedRoles={ROLE_GROUPS.productWriters}><ProductCreate /></RoleRoute>} />
                <Route path="data-products/:id" element={<RoleRoute allowedRoles={ROLE_GROUPS.productReaders}><ProductDetail /></RoleRoute>} />
                <Route path="contracts" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><ContractList /></RoleRoute>} />
                <Route path="contracts/create" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractWriters}><ContractCreate /></RoleRoute>} />
                <Route path="contracts/:id" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><ContractDetail /></RoleRoute>} />
                <Route path="sandbox-sessions" element={<RoleRoute allowedRoles={ROLE_GROUPS.sandboxReaders}><SessionList /></RoleRoute>} />
                <Route path="sandbox-sessions/:id" element={<RoleRoute allowedRoles={ROLE_GROUPS.sandboxReaders}><SessionDetail /></RoleRoute>} />
                <Route path="dev-sandbox" element={<RoleRoute allowedRoles={ROLE_GROUPS.devSandboxUsers}><DataProductDev /></RoleRoute>} />
                <Route path="notebooks" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><NotebookWorkbench /></RoleRoute>} />
                <Route path="workloads" element={<RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}><WorkloadCenter /></RoleRoute>} />
                <Route path="compute" element={<RoleRoute allowedRoles={ROLE_GROUPS.sandboxReaders}><ComputeSchedule /></RoleRoute>} />
                <Route path="deliveries" element={<RoleRoute allowedRoles={ROLE_GROUPS.contractReaders}><DeliveryCenter /></RoleRoute>} />
                <Route path="audit" element={<RoleRoute allowedRoles={ROLE_GROUPS.auditReaders}><AuditLog /></RoleRoute>} />
                <Route path="output-control" element={<RoleRoute allowedRoles={ROLE_GROUPS.outputControlReaders}><InspectionPipeline /></RoleRoute>} />
                <Route path="catalog" element={<RoleRoute allowedRoles={ROLE_GROUPS.catalogReaders}><CatalogPage /></RoleRoute>} />
                <Route path="monitoring" element={<RoleRoute allowedRoles={ROLE_GROUPS.monitoringReaders}><MonitoringDashboard /></RoleRoute>} />
                <Route path="federation" element={<RoleRoute allowedRoles={ROLE_GROUPS.federationUsers}><CrossSpaceDashboard /></RoleRoute>} />
                <Route path="training" element={<RoleRoute allowedRoles={ROLE_GROUPS.trainingUsers}><TrainingDashboard /></RoleRoute>} />
                <Route path="connectors" element={<RoleRoute allowedRoles={ROLE_GROUPS.connectorManagers}><Connectors /></RoleRoute>} />
                <Route path="identity/keys" element={<RoleRoute allowedRoles={ROLE_GROUPS.identityManagers}><KeyManagement /></RoleRoute>} />
                <Route path="certificates" element={<RoleRoute allowedRoles={ROLE_GROUPS.identityManagers}><Certificates /></RoleRoute>} />
                <Route path="ops/deployments" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><K8sDeployments /></RoleRoute>} />
                <Route path="ops/network-policies" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><K8sNetworkPolicies /></RoleRoute>} />
                <Route path="ops/pvcs" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><K8sPvcs /></RoleRoute>} />
                <Route path="ops/logs" element={<RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}><K8sLogs /></RoleRoute>} />
                <Route path="capability-matrix" element={<RoleRoute allowedRoles={ADMIN_WORKSPACE_ROLES}><CapabilityMatrix /></RoleRoute>} />
                <Route path="data-pipeline" element={<DataPipeline />} />
                <Route path="compliance-disclosure" element={<RoleRoute allowedRoles={ROLE_GROUPS.monitoringReaders}><ComplianceDisclosure /></RoleRoute>} />
                <Route path="mpc" element={<RoleRoute allowedRoles={ROLE_GROUPS.identityManagers}><MpcPage /></RoleRoute>} />
                <Route path="system-status" element={<RoleRoute allowedRoles={ROLE_GROUPS.monitoringReaders}><SystemStatus /></RoleRoute>} />
                <Route path="users" element={<RoleRoute allowedRoles={[UserRole.ADMIN]}><UserManagement /></RoleRoute>} />
                <Route path="api-keys" element={<RoleRoute allowedRoles={[UserRole.ADMIN]}><ApiKeys /></RoleRoute>} />
                <Route path="*" element={<NotFound />} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
