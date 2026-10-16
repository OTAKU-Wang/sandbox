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
  /** PZ-03 honest chain state: pg-log | fisco-sim | ant-sim | not_configured. */
  chain_anchoring?: string;
}

export interface AnchorResult {
  anchored: number;
  merkle_root: string;
  tx_hash: string | null;
  success: boolean;
  error: string | null;
  anchor_id?: string | null;
  confirmed?: boolean;
  /** 区块高度：仅当锚定后端真实提供时有值（PG 追加日志为 null，绝不伪造）。 */
  block_number?: number | null;
  backend?: AnchorBackendDisclosure | null;
}

export interface AuditVerifyResult {
  verified: boolean;
  tx_hash: string | null;
  record_id: string;
  /** 仅在未锚定的早退响应里出现。 */
  reason?: string | null;
  anchor_id?: string | null;
  /** null = 后端没有区块高度概念（本地追加日志），不是 0。 */
  block_number?: number | null;
  confirmed?: boolean;
  /** 本次重新计算出的 Merkle root（与锚定值比对的结果体现在 verified 里）。 */
  merkle_root?: string | null;
  backend?: AnchorBackendDisclosure | null;
  verification_note?: string | null;
}

export interface MerkleProofStep {
  hash: string;
  position: 'left' | 'right';
}

export interface MerkleProofResult {
  record_id: string;
  record_hash: string;
  tx_hash: string;
  anchor_id?: string | null;
  block_number?: number | null;
  confirmed?: boolean;
  merkle_root: string | null;
  proof_path: MerkleProofStep[];
  leaf_index: number;
  total_leaves: number;
  backend?: AnchorBackendDisclosure | null;
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

  getMerkleProof: (id: string): Promise<MerkleProofResult> =>
    api.get(`/audit/merkle-proof/${id}`),
};
