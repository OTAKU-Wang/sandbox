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

/** D-e2e-48：状态条 CPU/内存——后端只报 cgroup v2 真读数，读不到就 available:false。 */
export interface SessionMeteringResponse {
  available: boolean;
  reason?: string | null;
  source?: string | null;
  cpu_percent: number | null;
  memory_used_mb: number | null;
  memory_limit_mb: number | null;
  pids: number | null;
  sampled_ms?: number | null;
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
  // PZ-16：list/preview 若有审查预告字段则消费（后端当前不下发，防御式可选）
  output_review?: { status?: string; findings_count?: number } | null;
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

// ---------- Operations (W11 202 异步模型) ----------

export interface SessionOperation {
  operation_id: string;
  session_id: string;
  op_type: string;
  status: 'pending' | 'running' | 'succeeded' | 'failed' | string;
  progress: number;
  error: string | null;
  result_ref: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

export const isTerminalOperation = (op: SessionOperation | null | undefined): boolean =>
  !!op && (op.status === 'succeeded' || op.status === 'failed');

/** 202 响应与同步响应的判别（同步路径也带 operation_id，但没有 progress 字段）。 */
export const isSessionOperation = (payload: unknown): payload is SessionOperation =>
  typeof payload === 'object' && payload !== null && 'progress' in payload && 'op_type' in payload;

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

// X-CDS-Output-Review 头解析："passed; findings=N" / "redacted; findings=N"（PZ-16 ③）
export interface FileReviewStatus {
  status: 'passed' | 'redacted';
  findings: number;
}

export const parseReviewHeader = (header?: string | null): FileReviewStatus | null => {
  if (typeof header !== 'string' || !header) return null;
  const status: 'passed' | 'redacted' = header.trim().startsWith('redacted') ? 'redacted' : 'passed';
  const m = /findings=(\d+)/.exec(header);
  return { status, findings: m ? Number(m[1]) : 0 };
};

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

  // D-e2e-48：cgroup v2 真计量（读不到时 available:false，前端继续显示未计量）
  getMetering: (id: string): Promise<SessionMeteringResponse> =>
    api.get(`/sandbox-sessions/${id}/metering`),

  // Session templates
  getSessionTemplates: (): Promise<SessionTemplatesResponse> =>
    api.get('/sandbox-sessions/session-templates'),

  // Files
  listFiles: (id: string): Promise<SessionFilesResponse> =>
    api.get(`/sandbox-sessions/${id}/files`),

  uploadFile: (id: string, file: File): Promise<SessionFile> => {
    const formData = new FormData();
    formData.append('file', file);
    return api.post(`/sandbox-sessions/${id}/files`, formData);
  },

  // Blob download — returns blob plus review status parsed from the
  // X-CDS-Output-Review header (passed/redacted + findings count, PZ-16 ③).
  // Rides the shared instance (single-flight 401 refresh); since that
  // interceptor unwraps `response.data`, the review header and status are
  // captured here via the transformResponse/validateStatus hooks.
  downloadFile: async (id: string, filename: string): Promise<{ blob: Blob; review: FileReviewStatus | null }> => {
    const meta: { review: FileReviewStatus | null; status?: number } = { review: null };
    try {
      const blob: Blob = await api.get(`/sandbox-sessions/${id}/files/${encodeURIComponent(filename)}`, {
        responseType: 'blob',
        transformResponse: [(data: unknown, headers: Record<string, unknown>) => {
          const reviewHeader = headers?.['x-cds-output-review'] ?? headers?.['X-CDS-Output-Review'];
          meta.review = parseReviewHeader(typeof reviewHeader === 'string' ? reviewHeader : null);
          return data;
        }],
        validateStatus: (status: number) => {
          meta.status = status;
          return status >= 200 && status < 300;
        },
      });
      return { blob, review: meta.review };
    } catch (error: unknown) {
      // 409 DLP block: the shared interceptor rejects with the raw response body,
      // which is a JSON Blob here (responseType 'blob'); parse findings_count
      // and re-throw with structured info.
      if (error instanceof Blob && meta.status === 409) {
        const text = await error.text();
        let envelope: Record<string, unknown> = {};
        try {
          envelope = JSON.parse(text);
        } catch {
          // ignore parse errors
        }
        // The W2 body wraps the payload as {code, message, detail, request_id};
        // reading findings off the envelope itself made every blocked download
        // report "0 findings" to the operator.
        const detail = ((envelope as { detail?: unknown }).detail ?? envelope) as
          { findings_count?: number; error?: string; findings?: unknown[] };
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

  // Operations (W11)：202 异步操作的轮询端点
  getOperation: (opId: string): Promise<SessionOperation> =>
    api.get(`/sandbox-sessions/operations/${opId}`),

  // Snapshots — asyncMode=true 时 202 {operation_id}（PZ-16 ④），否则同步返回
  listSnapshots: (id: string): Promise<SessionSnapshotsResponse> =>
    api.get(`/sandbox-sessions/${id}/snapshots`),

  createSnapshot: (
    id: string,
    data?: CreateSnapshotRequest,
    asyncMode = false,
  ): Promise<SessionSnapshot | SessionOperation> =>
    api.post(`/sandbox-sessions/${id}/snapshots`, data || {}, { params: asyncMode ? { async: true } : undefined }),

  rollbackSnapshot: (
    id: string,
    snapshotId: string,
    asyncMode = false,
  ): Promise<{ status: string } | SessionOperation> =>
    api.post(`/sandbox-sessions/${id}/snapshots/${snapshotId}/rollback`, undefined, { params: asyncMode ? { async: true } : undefined }),

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
