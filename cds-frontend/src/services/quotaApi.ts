import { useQuery } from '@tanstack/react-query';
import api from './api';

// SS-17 配额用量（开发门户「配额用量」页唯一数据源）。
// 契约 = 后端 app/api/quota.py 的真实返回：未采集的数是 null（渲染「未采集」），
// 计数器后端由 sources.* 如实披露（Redis / 进程内兜底的信任边界不同）。

export interface QuotaSource {
  backend: 'redis' | 'in_memory' | 'in_memory+redis_write_through' | 'database' | string;
  label: string;
  cross_replica: boolean;
  survives_restart: boolean;
  note: string;
}

export interface SessionQuotaItem {
  type: string;
  label: string;
  /** 计数单位（dp_epsilon 是 ε×1e-4，直接按 ε 展示会夸大 1e4 倍） */
  unit: string;
  usage: number | null;
  /** null = 未设限（后端 limit 0 语义为不限），不是「额度为 0」 */
  limit: number | null;
}

export interface QuotaSessionItem {
  session_id: string;
  status: string;
  /** 历史值（SS-13 前的 L0/L1/L2/L3/k8s）仍会出现在这里，必须可渲染 */
  sandbox_level: string;
  contract_id: string | null;
  created_at: string | null;
  quotas: SessionQuotaItem[];
}

export interface QuotaEndpointItem {
  id: string;
  name: string;
  slug: string;
  status: string;
  contract_id: string;
  contract_no: string;
  role: 'provider' | 'consumer' | string;
  calls_used: number;
  quota_total: number | null;
  calls_remaining: number | null;
  rate_limit_per_minute: number;
  last_called_at: string | null;
  created_at: string | null;
}

export interface QuotaUsage {
  generated_at: string;
  scope: { user_id: string; username: string; role: string | null; note: string };
  sources: {
    sandbox_counters: QuotaSource;
    tenant_resources: QuotaSource;
    openapi_calls: QuotaSource;
  };
  sandbox_sessions: {
    active: number;
    active_limit: number;
    active_remaining: number;
    total: number;
    by_status: Record<string, number>;
    per_session: QuotaSessionItem[];
    note: string;
  };
  tenant_resources: {
    limits: { cpu_cores: number; memory_mb: number; disk_mb: number; max_sessions: number };
    used: { used_cpu_cores?: number; used_memory_mb?: number; used_disk_mb?: number } | null;
  };
  openapi_endpoints: {
    count: number;
    status_enabled: number;
    items: QuotaEndpointItem[];
    totals: { calls_used: number; quota_total: number | null; calls_remaining: number | null };
  };
  not_collected: { key: string; label: string; reason: string }[];
}

export const quotaApi = {
  getUsage: (): Promise<QuotaUsage> =>
    api.get('/deliveries/quota-usage') as unknown as Promise<QuotaUsage>,
};

export function useQuotaUsage() {
  return useQuery({
    queryKey: ['quota-usage'],
    queryFn: quotaApi.getUsage,
    refetchInterval: 30_000,
  });
}

/** null / undefined ⇒ 「未采集」。后端从不为缺失的数编一个 0，前端也不许反推。 */
export function quotaNumber(value: number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined) return '未采集';
  return `${value.toLocaleString('zh-CN')}${suffix}`;
}
