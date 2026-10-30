import api from './api';
import type { SecurityFinding, SecurityReport } from '../types/security';

export interface InspectionResult {
  passed: boolean;
  stage_results: Record<string, boolean>;
  findings: SecurityFinding[];
  redacted_output: string | null;
  watermark: string | null;
  signature?: string | null;
  dp_applied: boolean;
  security_report?: SecurityReport;
}

export interface DPNoiseResult {
  original: number;
  noisy_value: number;
  mechanism: string;
  epsilon: number;
  sensitivity: number;
}

/** A192e 三态口径（与后端 InspectionResult.dp_budget_state / GET /dp/budget 同一词汇表）。 */
export type DPBudgetState = 'sufficient' | 'exhausted' | 'unverified' | 'unallocated';

export interface DPBudgetResult {
  session_id: string;
  /**
   * 后端读不到预算时是 null：剩余量未知。渲染成 0 就是把「无法核实」谎报成
   * 「没有预算」（与后端 A192e 修的同一个病），所以这里不允许用 number。
   */
  epsilon_remaining: number | null;
  budget_state: DPBudgetState;
  budget_source: string;
  budget_authoritative: boolean;
}

export interface OutputGatewayResult {
  success: boolean;
  output_format: string | null;
  row_count: number;
  truncated: boolean;
  output_data: string | null;
  findings: SecurityFinding[];
  security_report: SecurityReport;
  watermark: string | null;
  signature: string | null;
  error: string | null;
}

export const outputControlApi = {
  inspect: (data: { output: string; session_id: string; dp_epsilon?: number }): Promise<InspectionResult> =>
    api.post('/output-control/inspect', data),

  applyDPNoise: (data: {
    value: number;
    sensitivity: number;
    epsilon: number;
    mechanism?: 'laplace' | 'gaussian';
    delta?: number;
  }): Promise<DPNoiseResult> =>
    api.post('/output-control/dp/noise', data),

  initDPBudget: (sessionId: string, epsilon: number): Promise<{ session_id: string; epsilon_allocated: number }> =>
    api.post('/output-control/dp/budget/init', undefined, {
      params: { session_id: sessionId, epsilon },
    }),

  getDPBudget: (sessionId: string): Promise<DPBudgetResult> =>
    api.get(`/output-control/dp/budget/${encodeURIComponent(sessionId)}`),

  processGateway: (data: {
    data: Record<string, unknown>[];
    output_format?: string;
    max_output_rows?: number;
    allowed_output_formats?: string[];
    dp_epsilon_budget?: number | null;
    session_id?: string | null;
  }): Promise<OutputGatewayResult> =>
    api.post('/output-control/gateway', data),
};
