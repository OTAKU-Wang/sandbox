import api from './api';

// MPC 密钥共享（Shamir 秘密托管）— 后端 app/api/mpc.py（/api/v1/mpc）。
// 诚实边界（N11）：当前运行时是密钥托管（custody），HE/MPC 计算未部署（评估中）。

export interface MpcCapabilities {
  mode: string;
  label: string;
  compute: string;
  backend: string | null;
  backend_label: string;
  note: string;
}

export interface MpcKey {
  key_id: string;
  algorithm: string;
  threshold: number;
  total_shares: number;
  share_count: number;
  status: string;
  persisted?: boolean;
}

export interface MpcShare {
  share_id: string;
  key_id: string;
  index: number;
  share_value: string;
  threshold: number;
  total_shares: number;
  holder: string | null;
}

export interface SplitKeyRequest {
  secret_hex: string;
  threshold: number;
  total_shares: number;
  algorithm?: string;
  holders?: string[];
}

export const mpcApi = {
  getCapabilities: (): Promise<MpcCapabilities> => api.get('/mpc/capabilities'),
  listKeys: (): Promise<MpcKey[]> => api.get('/mpc/keys'),
  getShares: (keyId: string): Promise<MpcShare[]> =>
    api.get(`/mpc/keys/${keyId}/shares`),
  splitKey: (payload: SplitKeyRequest): Promise<MpcShare[]> =>
    api.post('/mpc/split', payload),
  rotateKey: (
    keyId: string,
    payload: { reason: string; ticket_id?: string | null },
  ): Promise<MpcKey> => api.post(`/mpc/keys/${keyId}/rotate`, payload),
  destroyKey: (
    keyId: string,
    payload: { reason: string; ticket_id?: string | null },
  ): Promise<{ key_id: string; destroyed: boolean; persisted: boolean }> =>
    api.post(`/mpc/keys/${keyId}/destroy`, payload),
};
