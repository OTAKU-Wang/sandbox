import api from './api';

// SS-12: 平台 API 密钥（机器凭据）管理面。
// 后端路由 /api/v1/api-keys（admin + human-only 门，见 app/api/api_keys.py）。
// 裸密钥仅在 create/rotate 响应中出现一次；列表/详情只回 key_prefix。

export type ApiKeyStatus = 'active' | 'disabled' | 'revoked';

export interface ApiKey {
  id: string;
  name: string;
  description: string | null;
  key_prefix: string;
  scopes: string[];
  status: ApiKeyStatus;
  rate_limit: number;
  issued_by_username: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  // SS-12 §4.7.7 冷却披露：进程内计数器，多实例部署为近似值；
  // 后端列表当前未下发该字段，仅在存在时渲染（如实披露，不模拟）。
  cooldown_remaining_minutes?: number | null;
}

export interface ApiKeyActivity {
  time: string;
  path: string | null;
  method: string | null;
  status_code: number | null;
  ip: string | null;
  denied_reason?: string;
}

export interface ApiKeyCreatePayload {
  name: string;
  description?: string | null;
  preset?: 'readonly' | 'operator' | 'full' | 'custom';
  scopes?: string[];
  expires_in_days?: number;
  rate_limit?: number;
}

export interface ApiKeyCreated {
  key: string;
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  status: string;
  expires_at: string | null;
  created_at: string;
}

export interface ApiKeyRotated {
  key: string;
  id: string;
}

export interface ApiKeyRevoked {
  id: string;
  status: string;
  revoked_at: string | null;
}

export const apiKeysApi = {
  list: (): Promise<ApiKey[]> => api.get('/api-keys'),
  create: (payload: ApiKeyCreatePayload): Promise<ApiKeyCreated> => api.post('/api-keys', payload),
  activity: (id: string): Promise<ApiKeyActivity[]> => api.get(`/api-keys/${id}/activity`),
  rotate: (id: string): Promise<ApiKeyRotated> => api.post(`/api-keys/${id}/rotate`),
  patchStatus: (id: string, status: 'active' | 'disabled'): Promise<ApiKey> =>
    api.patch(`/api-keys/${id}`, { status }),
  revoke: (id: string): Promise<ApiKeyRevoked> => api.delete(`/api-keys/${id}`),
};
