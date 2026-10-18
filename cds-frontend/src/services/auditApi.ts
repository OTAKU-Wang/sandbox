import api from './api';
import type { PaginatedResponse } from '../types/api';

export interface AuditRecord {
  id: string;
  user_id: string | null;
  /** 4-1「操作人」：后端 join users 给出的展示名；系统行（无归属）为 null。 */
  username: string | null;
  /** 操作人所属单位（users.organization，注册时可填，null 就是真的没有）。 */
  organization?: string | null;
  /** 操作人角色码（users.role），展示名用 utils/roles.ts::ROLE_LABELS。 */
  role?: string | null;
  /**
   * 4-2「日志编号」：后端用 created_at + 记录 ID 前 4 位确定性拼的展示编码
   * （audit.py::audit_display_no），库里没有业务编号列，页面上要写清推导规则。
   */
  display_no?: string | null;
  session_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  detail: Record<string, unknown> | null;
  ip_address: string | null;
  blockchain_tx_hash: string | null;
  created_at: string;
  /**
   * 4-1「操作结果」：后端由动作名派生（audit.py::audit_outcome，失败/拒绝/阻断类
   * 动作名为 failure）。库里没有独立结果列，页面不得自行推算。
   */
  outcome?: 'success' | 'failure';
  /** 4-1 类型页签归属；不属于任何页签时为 null。 */
  event_type?: string | null;
}

export interface AuditRelatedContract {
  contract_no: string;
  title: string;
}

export interface AuditRecordDetail extends AuditRecord {
  related_contract: AuditRelatedContract | null;
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

export interface AuditExportParams {
  /** 导出时间窗口（天）：后端 GET /audit/export 只接受 days(ge=1, le=365) 与 format 两个查询参数。 */
  days: number;
  /** csv → text/csv 附件流；json → 后端直接返回 {records,total,period_days}（audit.py::export_audit_records）。 */
  format?: 'csv' | 'json';
}

export interface AuditExportResult {
  blob: Blob;
  /**
   * 与后端 Content-Disposition 同名的落盘文件名（audit.py 里是
   * `audit_export_{days}d.csv`）：共享 axios 实例的拦截器只回传 response.data，
   * 响应头拿不到，所以在此按后端同一命名规则给出，不自造前缀。
   */
  filename: string;
  contentType: string;
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
  list: (params?: {
    page?: number;
    page_size?: number;
    action?: string;
    resource_type?: string;
    /** 4-1 筛选四件套：操作时间区间 / 关键字 / 操作结果 / 类型页签。 */
    date_from?: string;
    date_to?: string;
    keyword?: string;
    outcome?: 'success' | 'failure';
    event_type?: string;
  }): Promise<PaginatedResponse<AuditRecord>> =>
    api.get('/audit/records', {
      params: {
        skip: ((params?.page ?? 1) - 1) * (params?.page_size ?? 20),
        limit: params?.page_size ?? 20,
        action: params?.action,
        resource_type: params?.resource_type,
        date_from: params?.date_from,
        date_to: params?.date_to,
        keyword: params?.keyword,
        outcome: params?.outcome,
        event_type: params?.event_type,
      },
    }),

  get: (id: string): Promise<AuditRecordDetail> =>
    api.get(`/audit/records/${id}`),

  anchor: (ids: string[]): Promise<AnchorResult> =>
    api.post('/audit/anchor', { record_ids: ids }),

  verify: (id: string): Promise<AuditVerifyResult> =>
    api.get(`/audit/verify/${id}`),

  getMerkleProof: (id: string): Promise<MerkleProofResult> =>
    api.get(`/audit/merkle-proof/${id}`),

  /**
   * 导出审计日志（PRD 4-1 顶栏「导出审计日志」）。镜像 audit.py::export_audit_records：
   * 只有 days + format 两个参数 —— 列表页的 action 等筛选后端在此端点上不存在，
   * 传了也不会生效，所以这里不传，页面必须自己说明导出范围。
   */
  export: async ({ days, format = 'csv' }: AuditExportParams): Promise<AuditExportResult> => {
    if (format === 'json') {
      // 非 csv 分支返回的是 JSON 响应体（不是附件流），在前端包成 Blob 后统一落盘。
      const payload = await api.get('/audit/export', { params: { days, format: 'json' } });
      return {
        blob: new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }),
        filename: `audit_export_${days}d.json`,
        contentType: 'application/json',
      };
    }
    try {
      const blob: Blob = await api.get('/audit/export', {
        params: { days, format: 'csv' },
        responseType: 'blob',
      });
      return { blob, filename: `audit_export_${days}d.csv`, contentType: 'text/csv' };
    } catch (error: unknown) {
      // responseType='blob' 时连错误体都是 Blob：还原成 W2 envelope 再抛，
      // 页面才能继续用 getErrorMessage 显示后端真实原因（而非「导出失败」兜底）。
      if (error instanceof Blob) {
        let envelope: unknown;
        try {
          envelope = JSON.parse(await error.text());
        } catch {
          // 非 JSON 错误体：继续抛原始 Blob，由页面给兜底文案
        }
        if (envelope !== undefined) throw envelope;
      }
      throw error;
    }
  },
};
