import api from './api';
import { unwrapList } from '../utils/unwrapList';

/** 后端 GET /federation/catalog/entries 的硬上限：`limit: int = Query(100, ge=1, le=500)`（app/api/federation.py:261）。 */
export const CATALOG_ENTRY_LIMIT = 500;

export interface TrustRelationship {
  trust_id: string;
  /** 后端未给 remote_space 时保持 undefined：空字符串是一个“看起来很正常”的虚假联合键。 */
  remote_space_id?: string;
  remote_space_name: string;
  endpoint?: string;
  trust_level: string;
  status: string;
  allowed_operations: string[];
  /**
   * A262：created_at 是跨空间互信记录的证据的一部分，后端没给就是 null（界面显示 UNKNOWN_TEXT），
   * 绝不用 `new Date()` 兜一个「看起来完全正常」的当前时间——那是伪造审计时间。
   */
  created_at: string | null;
  updated_at?: string | null;
}

export interface CatalogSyncEntry {
  id: string;
  remote_space_id: string;
  product_name: string;
  product_id: string;
  synced_at: string;
}

export interface CatalogSyncEntryPage {
  items: CatalogSyncEntry[];
  /** 本次请求实际带上的 limit。 */
  limit: number;
  /**
   * 后端只返回裸列表，没 total/has_more——条目数追平 limit 时就是「可能被截断」，
   * 界面必须区分「共这些」和「至少这些」。
   */
  possiblyTruncated: boolean;
}

export interface SyncStatus {
  space_id: string;
  space_name: string;
  last_sync_at: string | null;
  entry_count: number;
  status: string;
}

export interface TrustScore {
  level: string;
  score: number;
  data_quality: number;
  compliance: number;
  reputation: number;
  security: number;
}

interface TrustResponse {
  trust_id: string;
  remote_space?: string;
  remote_space_name?: string;
  endpoint?: string;
  trust_level: string;
  status: string;
  allowed_operations?: string[];
  created_at?: string;
  updated_at?: string;
}

/** 缺字段/空串/非字符串一律报 null：中性兜底，不编时间（A262）。 */
const toAuditTime = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() !== '' ? value : null;

/** 时间缺失时的展示口径，与 services/flow.ts 的 `UNKNOWN_TEXT` 同词。 */
export const TRUST_TIME_UNKNOWN = '未记录';

/** 只在能解析时格式化；缺了给「未记录」，解不了原样透出——不出 1970、也不出「现在」（A262）。 */
export function formatTrustTimestamp(value: string | null): string {
  if (!value) return TRUST_TIME_UNKNOWN;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? value : new Date(ms).toLocaleString();
}

const normalizeTrust = (item: TrustResponse): TrustRelationship => ({
  trust_id: item.trust_id,
  remote_space_id: item.remote_space,
  remote_space_name: item.remote_space_name || item.remote_space || '',
  endpoint: item.endpoint,
  trust_level: item.trust_level,
  status: item.status,
  allowed_operations: item.allowed_operations || [],
  created_at: toAuditTime(item.created_at),
});

export const federationApi = {
  // Trust relationships —— 规范形态裸数组（app/api/federation.py:94 list_trusts）
  listTrusts: (): Promise<TrustRelationship[]> =>
    api.get('/federation/trusts').then((res) =>
      unwrapList<TrustResponse>(res, '/federation/trusts', 'array').map(normalizeTrust),
    ),

  getTrust: (id: string): Promise<TrustRelationship> =>
    api.get(`/federation/trusts/${id}`).then((res) => normalizeTrust(res as unknown as TrustResponse)),

  establishTrust: (data: {
    space_id: string;
    space_name: string;
    endpoint: string;
    public_key?: string;
    certificate?: string;
    trust_level: string;
    allowed_operations: string[];
    policy_sync?: boolean;
  }): Promise<TrustRelationship> =>
    api.post('/federation/trust', data).then((res) => normalizeTrust(res as unknown as TrustResponse)),

  suspendTrust: (id: string): Promise<TrustRelationship> =>
    api.post(`/federation/trusts/${id}/suspend`).then((res) => normalizeTrust(res as unknown as TrustResponse)),

  revokeTrust: (id: string): Promise<TrustRelationship> =>
    api.post(`/federation/trusts/${id}/revoke`).then((res) => normalizeTrust(res as unknown as TrustResponse)),

  // Catalog sync —— 规范形态裸数组（app/api/federation.py:211 get_catalog_sync_status）
  getSyncStatus: (): Promise<SyncStatus[]> =>
    api.get('/federation/catalog/sync-status').then((res) =>
      unwrapList<SyncStatus>(res, '/federation/catalog/sync-status', 'array'),
    ),

  triggerSync: (space_id: string, mode: 'full' | 'incremental' = 'incremental'): Promise<{ synced: number; status: string; errors?: string[] }> =>
    api.post(`/federation/catalog/sync/${space_id}`, null, { params: { mode } }),

  // 后端默认 limit=100 且不回报 total，不显式传 limit 就会默不作声地只拿前 100 条。
  listSyncedEntries: (space_id?: string, limit: number = CATALOG_ENTRY_LIMIT): Promise<CatalogSyncEntryPage> =>
    api.get('/federation/catalog/entries', { params: { ...(space_id ? { space_id } : {}), limit } })
      .then((res) => {
        const items = unwrapList<CatalogSyncEntry>(res, '/federation/catalog/entries', 'array');
        return { items, limit, possiblyTruncated: items.length >= limit };
      }),

  // Trust score
  getTrustScore: (space_id: string): Promise<TrustScore> =>
    api.get(`/federation/trusts/${space_id}/score`),
};
