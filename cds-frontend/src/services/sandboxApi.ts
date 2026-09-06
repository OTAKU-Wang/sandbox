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
  template?: string | null;
}

// ---------- Exec ----------

export interface ExecRequest {
  command: string;
  timeout_seconds?: number;
}

export interface ExecResult {
  exit_code: number;
  output: string;
  output_blocked: boolean;
  blocked_reason?: string;
  duration_ms?: number;
  security_report?: SecurityReport;
}

// ---------- Logs ----------

export interface SessionLogEntry {
  id: string;
  action: string;
  detail?: unknown;
  created_at: string;
  user_id: string;
}

export interface SessionLogsResponse {
  logs: SessionLogEntry[];
  count: number;
  latest_created_at?: string;
}

export interface SessionLogsParams {
  limit?: number;
  since?: string;
  action?: string;
}

// ---------- Usage ----------

export interface SessionUsageResponse {
  workspace: {
    files: number;
    bytes: number;
  };
  uploaded_files: {
    count: number;
    bytes: number;
  };
  snapshots: {
    count: number;
    bytes: number;
  };
  timeout: {
    timeout_seconds: number;
    extended_seconds: number;
  };
}

// ---------- Templates ----------

export interface SessionTemplate {
  name: string;
  description: string;
  files: string[];
  env: Record<string, string>;
}

export interface SessionTemplatesResponse {
  templates: SessionTemplate[];
}

// ---------- Files ----------

export interface SessionFile {
  name: string;
  size: number;
  encrypted: boolean;
}

export interface SessionFilesResponse {
  files: SessionFile[];
}

export interface FileDownloadBlockedError {
  status: 409;
  detail: {
    findings_count: number;
    error?: string;
  };
}

// ---------- Snapshots ----------

export interface SessionSnapshot {
  id: string;
  created_at: string;
  size_bytes: number;
  file_count: number;
  description?: string;
}

export interface SessionSnapshotsResponse {
  snapshots: SessionSnapshot[];
}

export interface CreateSnapshotRequest {
  description?: string;
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

export interface SessionProofBundle {
  schema_version: string;
  generated_at: string;
  session: Record<string, unknown> & { id: string; status: string; sandbox_level: string; sandbox_mode: string };
  runtime: {
    proof_level: 'hardware_tee' | 'software_confidential' | 'runtime_isolation' | string;
    attestation: Record<string, unknown> & {
      present?: boolean;
      required?: boolean;
      type?: string | null;
      measurement?: string | null;
      quote_hash?: string | null;
      is_simulation?: boolean | null;
    };
  };
  policy: Record<string, unknown> & {
    resource_limits_hash?: string;
    network_policy_hash?: string;
    contract_policy_hash?: string;
    combined_policy_hash?: string;
  };
  keys: Record<string, unknown> & {
    session_key_id?: string | null;
  };
  output_security: Record<string, unknown> & {
    available?: boolean;
    blocked?: boolean | null;
    findings_count?: number | null;
    watermark?: string | null;
    signature?: string | null;
    report_hash?: string | null;
  };
  audit: Record<string, unknown> & {
    event_count?: number;
    latest_events_hash?: string;
  };
  integrity: {
    hash_alg: string;
    evidence_hash?: string;
    bundle_hash: string;
    excluded_fields: string[];
  };
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

  getProofBundle: (id: string): Promise<SessionProofBundle> =>
    api.get(`/sandbox-sessions/${id}/proof-bundle`),

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

  // ---------- Session usability (Rounds 39-40) ----------

  // Exec console
  exec: (id: string, data: ExecRequest): Promise<ExecResult> =>
    api.post(`/sandbox-sessions/${id}/exec`, data),

  // Audit logs
  getLogs: (id: string, params?: SessionLogsParams): Promise<SessionLogsResponse> =>
    api.get(`/sandbox-sessions/${id}/logs`, { params }),

  // Usage
  getUsage: (id: string): Promise<SessionUsageResponse> =>
    api.get(`/sandbox-sessions/${id}/usage`),

  // Session templates
  getSessionTemplates: (): Promise<SessionTemplatesResponse> =>
    api.get('/sandbox-sessions/session-templates'),

  // Files
  listFiles: (id: string): Promise<SessionFilesResponse> =>
    api.get(`/sandbox-sessions/${id}/files`),

  uploadFile: (id: string, file: File): Promise<SessionFile> => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(`/sandbox-sessions/${id}/files`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  // Blob download — returns blob plus redacted flag from response header
  downloadFile: async (id: string, filename: string): Promise<{ blob: Blob; redacted: boolean }> => {
    const axios = (await import('axios')).default;
    const { useAuthStore } = await import('../stores/authStore');
    const token = useAuthStore.getState().token;
    const baseURL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

    try {
      const response = await axios.get(
        `${baseURL}/sandbox-sessions/${id}/files/${encodeURIComponent(filename)}`,
        {
          responseType: 'blob',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      const reviewHeader = response.headers['x-cds-output-review'] || response.headers['X-CDS-Output-Review'];
      const redacted = typeof reviewHeader === 'string' && reviewHeader.startsWith('redacted');
      return { blob: response.data, redacted };
    } catch (error: unknown) {
      // 409 DLP block: the response data is a JSON blob with detail.findings_count
      const axiosErr = error as { response?: { status: number; data: Blob; headers: Record<string, string> } };
      if (axiosErr.response?.status === 409 && axiosErr.response.data instanceof Blob) {
        // Try to parse JSON from blob for findings_count; re-throw with structured info
        const text = await axiosErr.response.data.text();
        let detail: { findings_count?: number; error?: string } = {};
        try {
          detail = JSON.parse(text);
        } catch {
          // ignore parse errors
        }
        const blockedErr = new Error('输出审查阻断') as Error & { status: number; detail: typeof detail };
        blockedErr.status = 409;
        blockedErr.detail = detail;
        throw blockedErr;
      }
      throw error;
    }
  },

  deleteFile: (id: string, filename: string): Promise<void> =>
    api.delete(`/sandbox-sessions/${id}/files/${encodeURIComponent(filename)}`),

  // Snapshots
  listSnapshots: (id: string): Promise<SessionSnapshotsResponse> =>
    api.get(`/sandbox-sessions/${id}/snapshots`),

  createSnapshot: (id: string, data?: CreateSnapshotRequest): Promise<SessionSnapshot> =>
    api.post(`/sandbox-sessions/${id}/snapshots`, data || {}),

  rollbackSnapshot: (id: string, snapshotId: string): Promise<{ status: string }> =>
    api.post(`/sandbox-sessions/${id}/snapshots/${snapshotId}/rollback`),

  deleteSnapshot: (id: string, snapshotId: string): Promise<void> =>
    api.delete(`/sandbox-sessions/${id}/snapshots/${snapshotId}`),

  // Lifecycle
  pauseSession: (id: string): Promise<SandboxSession> =>
    api.post(`/sandbox-sessions/${id}/pause`),

  resumeSession: (id: string): Promise<SandboxSession> =>
    api.post(`/sandbox-sessions/${id}/resume`),

  refreshSession: (id: string): Promise<SandboxSession> =>
    api.post(`/sandbox-sessions/${id}/refreshes`),
};
