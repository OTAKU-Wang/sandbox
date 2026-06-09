import api from './api';

export interface MonitoringStats {
  active_sessions: number;
  today_contracts: number;
  total_products: number;
  total_audit_records: number;
  total_anchored: number;
  inspection_pass_rate: number;
}

export interface Alert {
  id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
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

export const monitoringApi = {
  getStats: (): Promise<MonitoringStats> => api.get('/monitoring/stats'),
  getAlerts: (params?: { page?: number; page_size?: number }): Promise<{ items: Alert[]; total: number }> =>
    api.get('/monitoring/alerts', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
      },
    }),
  getTaskTrend: (days?: number): Promise<TaskTrendPoint[]> =>
    api.get('/monitoring/task-trend', { params: { days } }),
  getPolicyRejection: (): Promise<PolicyRejection> => api.get('/monitoring/policy-rejection'),
  getSecurityDistribution: (): Promise<SecurityDistribution[]> => api.get('/monitoring/security-distribution'),
  getRecentEvents: (limit?: number): Promise<RecentEvent[]> =>
    api.get('/monitoring/recent-events', { params: { limit } }),
};
