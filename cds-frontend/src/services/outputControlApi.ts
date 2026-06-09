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
    api.post(`/output-control/dp/budget/init?session_id=${sessionId}&epsilon=${epsilon}`),

  getDPBudget: (sessionId: string): Promise<{ session_id: string; epsilon_remaining: number }> =>
    api.get(`/output-control/dp/budget/${sessionId}`),

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
