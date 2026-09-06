import api from './api';
import type { PaginatedResponse } from '../types/api';

export interface AuditRecord {
  id: string;
  user_id: string | null;
  session_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  detail: Record<string, unknown> | null;
  ip_address: string | null;
  blockchain_tx_hash: string | null;
  created_at: string;
}

export interface AnchorBackendDisclosure {
  backend: string;
  backend_label: string;
  is_consortium_chain: boolean;
}

export interface AnchorResult {
  anchored: number;
  merkle_root: string;
  tx_hash: string | null;
  success: boolean;
  error: string | null;
  anchor_id?: string | null;
  confirmed?: boolean;
  backend?: AnchorBackendDisclosure | null;
}

export interface AuditVerifyResult {
  verified: boolean;
  tx_hash: string | null;
  record_id: string;
  backend?: AnchorBackendDisclosure | null;
  verification_note?: string | null;
}

export const auditApi = {
  list: (params?: { page?: number; page_size?: number; action?: string; resource_type?: string }): Promise<PaginatedResponse<AuditRecord>> =>
    api.get('/audit/records', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
        action: params?.action,
        resource_type: params?.resource_type,
      },
    }),

  get: (id: string): Promise<AuditRecord> =>
    api.get(`/audit/records/${id}`),

  anchor: (ids: string[]): Promise<AnchorResult> =>
    api.post('/audit/anchor', { record_ids: ids }),

  verify: (id: string): Promise<AuditVerifyResult> =>
    api.get(`/audit/verify/${id}`),

  getMerkleProof: (id: string): Promise<{ record_id: string; record_hash: string; tx_hash: string }> =>
    api.get(`/audit/merkle-proof/${id}`),
};
