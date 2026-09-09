import api from './api';

// N8: K8s runtime operations (read-only, ADMIN/OPERATOR).
// The backend degrades honestly: without a reachable cluster, cluster_available
// is false and items is empty — never fabricated live state.

export interface OpsDeployment {
  pod_name: string;
  phase: string;
  cds_status: string;
  ready: boolean;
  pod_ip: string;
  user_id: string;
  session_id: string;
  reason: string;
}

export interface DeploymentsResponse {
  cluster_available: boolean;
  namespace: string;
  items: OpsDeployment[];
  count: number;
  error?: string;
}

export interface OpsNetworkPolicy {
  session_id: string;
  mode: string;
  allowed_ips: string[];
  allowed_domains: string[];
  allowed_ports: number[];
  dns_proxy_enabled: boolean;
  live_pod: boolean;
}

export interface NetworkPoliciesResponse {
  cluster_available: boolean;
  items: OpsNetworkPolicy[];
  total: number;
  page: number;
  page_size: number;
}

export interface OpsPvcAttachment {
  session_id: string;
  read_only: boolean;
}

export interface OpsPvc {
  volume_id: string;
  name: string;
  owner_id: string;
  size_limit_mb: number;
  read_only: boolean;
  created_at: string | null;
  claim_name: string;
  attachments: OpsPvcAttachment[];
  pvc_status: string;
  pvc_capacity?: string | null;
  pvc_access_modes: string[];
  pvc_storage_class?: string | null;
}

export interface PvcsResponse {
  cluster_available: boolean;
  items: OpsPvc[];
  count: number;
}

export interface OpsLogLine {
  line: string;
  stream: string;
  created_at?: string | null;
}

export interface LogsResponse {
  session_id: string;
  source: 'k8s' | 'audit';
  pod_name: string | null;
  cluster_available: boolean;
  logs: OpsLogLine[];
  count: number;
  error?: string;
}

export async function listDeployments(): Promise<DeploymentsResponse> {
  return api.get('/ops/deployments');
}

export async function listOpsNetworkPolicies(
  skip = 0,
  limit = 20,
): Promise<NetworkPoliciesResponse> {
  return api.get('/ops/network-policies', { params: { skip, limit } });
}

export async function listOpsPvcs(): Promise<PvcsResponse> {
  return api.get('/ops/pvcs');
}

export async function getOpsLogs(sessionId: string, tail = 200): Promise<LogsResponse> {
  return api.get(`/ops/logs/${sessionId}`, { params: { tail } });
}
