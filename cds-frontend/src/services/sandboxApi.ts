import api from './api';
import type { SandboxSession } from '../types/models';
import type { PaginatedResponse } from '../types/api';
import type { SecurityReport } from '../types/security';

export interface CreateSessionRequest {
  data_product_id: string;
  contract_id?: string | null;
  sandbox_level: string;
  timeout_seconds?: number;
  resource_limits?: Record<string, unknown> | null;
}

export interface DevSession {
  session_id: string;
  mode: string;
  status: string;
  container_id: string | null;
  input_files: string[];
  output_files: string[];
}

export interface CreateDevSessionRequest {
  mode: 'structured' | 'unstructured' | 'semi_structured';
  sandbox_level?: string;
  max_duration_seconds?: number;
  dp_epsilon_budget?: number | null;
}

export interface DevExecuteResult {
  output?: string;
  stdout?: string;
  stderr?: string;
  error?: string;
  exit_code: number;
  output_blocked?: boolean;
  security_report?: SecurityReport;
}

export interface SessionExecuteResult {
  output?: string;
  error?: string;
  exit_code: number;
  duration_ms?: number;
  output_truncated?: boolean;
  output_blocked?: boolean;
  blocked_reason?: string;
  audit_events_collected?: number;
  security_report?: SecurityReport;
}

export interface NetworkPolicy {
  id: string;
  session_id: string;
  user_id: string;
  mode: 'deny_all' | 'allowlist';
  allowed_ips: string[] | null;
  allowed_domains: string[] | null;
  allowed_ports: number[] | null;
  dns_proxy_enabled: boolean;
  max_connections_per_second: number;
  max_bandwidth_bytes_per_second: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface UpdateNetworkPolicyRequest {
  mode?: 'deny_all' | 'allowlist';
  allowed_ips?: string[];
  allowed_domains?: string[];
  allowed_ports?: number[];
  dns_proxy_enabled?: boolean;
  max_connections_per_second?: number;
  max_bandwidth_bytes_per_second?: number;
  active?: boolean;
}

export const sandboxApi = {
  list: (params?: { page?: number; page_size?: number; status?: string }): Promise<PaginatedResponse<SandboxSession>> =>
    api.get('/sandbox-sessions', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
        status: params?.status,
      },
    }),

  get: (id: string): Promise<SandboxSession> =>
    api.get(`/sandbox-sessions/${id}`),

  create: (data: CreateSessionRequest): Promise<SandboxSession> =>
    api.post('/sandbox-sessions', data),

  terminate: (id: string): Promise<SandboxSession> =>
    api.post(`/sandbox-sessions/${id}/terminate`),

  execute: (id: string, code: string, language: string = 'python'): Promise<SessionExecuteResult> =>
    api.post(`/sandbox-sessions/${id}/execute`, { code, language }),

  getNetworkPolicy: (id: string): Promise<NetworkPolicy> =>
    api.get(`/sandbox-sessions/${id}/network-policy`),

  updateNetworkPolicy: (id: string, data: UpdateNetworkPolicyRequest): Promise<{ network_policy: NetworkPolicy; enforcement: Record<string, unknown> | null }> =>
    api.put(`/sandbox-sessions/${id}/network-policy`, data),

  // Data Product Development Sandbox
  devList: (): Promise<DevSession[]> =>
    api.get('/dev-sandbox/sessions'),

  devGet: (id: string): Promise<DevSession> =>
    api.get(`/dev-sandbox/sessions/${id}`),

  devCreate: (data: CreateDevSessionRequest): Promise<DevSession> =>
    api.post('/dev-sandbox/sessions', data),

  devExecute: (id: string, code: string, language: string = 'python'): Promise<DevExecuteResult> =>
    api.post(`/dev-sandbox/sessions/${id}/execute`, { code, language }),

  devTerminate: (id: string): Promise<{ status: string }> =>
    api.post(`/dev-sandbox/sessions/${id}/terminate`),

  devGetTemplate: (name: string): Promise<{ template: string }> =>
    api.get(`/dev-sandbox/templates/${name}`),
};
