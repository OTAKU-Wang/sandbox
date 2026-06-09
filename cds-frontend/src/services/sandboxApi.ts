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
