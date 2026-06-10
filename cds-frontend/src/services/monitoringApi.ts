import api from './api';

export interface MonitoringStats {
  active_sessions: number;
  today_contracts: number;
  total_products: number;
  total_audit_records: number;
  total_anchored: number;
  inspection_pass_rate: number;
}

export type AlertStatus = 'open' | 'acknowledged' | 'resolved';
export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low';
export type AlertNotificationStatus = 'not_configured' | 'pending' | 'delivered' | 'failed';

export interface Alert {
  id: string;
  alert_type?: string | null;
  severity?: AlertSeverity | string | null;
  status?: AlertStatus | string | null;
  message?: string | null;
  user_id?: string | null;
  session_id?: string | null;
  resource_type?: string | null;
  resource_id: string | null;
  metadata?: Record<string, unknown> | null;
  occurrence_count?: number | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
  resolved_by?: string | null;
  resolved_at?: string | null;
  resolution_note?: string | null;
  notification_status?: AlertNotificationStatus | string | null;
  notification_results?: unknown[] | null;
  created_at?: string | null;
  updated_at?: string | null;
  action?: string;
  detail?: Record<string, unknown> | null;
}

export interface TaskTrendPoint {
  date: string;
  count: number;
}

export interface PolicyRejection {
  total: number;
  rejected: number;
  pass_rate: number;
  categories: { category: string; count: number }[];
}

export interface SecurityDistribution {
  level: string;
  count: number;
}

export interface RecentEvent {
  id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  user_id: string | null;
  created_at: string;
}

export type SecurityCapabilityStatus = 'verified' | 'configured' | 'software' | 'not_configured' | 'risk';
export type SecurityRiskLevel = 'ok' | 'info' | 'warn' | 'critical';
export type ReleaseGate = 'pass' | 'conditional' | 'block';
export type ReleaseRecommendation = 'go' | 'conditional_go' | 'no_go';

export interface SecurityCapability {
  id: string;
  name: string;
  status: SecurityCapabilityStatus | string;
  risk_level: SecurityRiskLevel | string;
  summary: string;
  release_gate: ReleaseGate | string;
  action: string;
  evidence: string[];
}

export interface SecurityPosture {
  generated_at: string;
  release_recommendation: ReleaseRecommendation | string;
  capabilities: SecurityCapability[];
}

export interface AlertListParams {
  page?: number;
  page_size?: number;
  status?: AlertStatus | string;
  severity?: AlertSeverity | string;
  alert_type?: string;
  include_legacy?: boolean;
}

export const monitoringApi = {
  getStats: (): Promise<MonitoringStats> => api.get('/monitoring/stats'),
  getSecurityPosture: (): Promise<SecurityPosture> => api.get('/monitoring/security-posture'),
  getAlerts: (params?: AlertListParams): Promise<{ items: Alert[]; total: number }> =>
    api.get('/monitoring/alerts', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
        status: params?.status,
        severity: params?.severity,
        alert_type: params?.alert_type,
        include_legacy: params?.include_legacy ?? true,
      },
    }),
  acknowledgeAlert: (alertId: string, note?: string): Promise<Alert> =>
    api.post(`/monitoring/alerts/${alertId}/acknowledge`, { note: note || undefined }),
  resolveAlert: (alertId: string, note?: string): Promise<Alert> =>
    api.post(`/monitoring/alerts/${alertId}/resolve`, { note: note || undefined }),
  getTaskTrend: (days?: number): Promise<TaskTrendPoint[]> =>
    api.get('/monitoring/task-trend', { params: { days } }),
  getPolicyRejection: (): Promise<PolicyRejection> => api.get('/monitoring/policy-rejection'),
  getSecurityDistribution: (): Promise<SecurityDistribution[]> => api.get('/monitoring/security-distribution'),
  getRecentEvents: (limit?: number): Promise<RecentEvent[]> =>
    api.get('/monitoring/recent-events', { params: { limit } }),
};
