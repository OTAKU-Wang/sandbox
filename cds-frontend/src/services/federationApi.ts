import api from './api';

export interface TrustRelationship {
  trust_id: string;
  remote_space_id: string;
  remote_space_name: string;
  endpoint?: string;
  trust_level: string;
  status: string;
  allowed_operations: string[];
  created_at: string;
  updated_at?: string;
}

export interface CatalogSyncEntry {
  id: string;
  remote_space_id: string;
  product_name: string;
  product_id: string;
  synced_at: string;
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
}

const normalizeTrust = (item: TrustResponse): TrustRelationship => ({
  trust_id: item.trust_id,
  remote_space_id: item.remote_space || '',
  remote_space_name: item.remote_space_name || item.remote_space || '',
  endpoint: item.endpoint,
  trust_level: item.trust_level,
  status: item.status,
  allowed_operations: item.allowed_operations || [],
  created_at: item.created_at || new Date().toISOString(),
});

export const federationApi = {
  // Trust relationships
  listTrusts: (): Promise<TrustRelationship[]> =>
    api.get('/federation/trusts').then((res) => (res as unknown as TrustResponse[]).map(normalizeTrust)),

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

  // Catalog sync
  getSyncStatus: (): Promise<SyncStatus[]> =>
    api.get('/federation/catalog/sync-status'),

  triggerSync: (space_id: string, mode: 'full' | 'incremental' = 'incremental'): Promise<{ synced: number; status: string; errors?: string[] }> =>
    api.post(`/federation/catalog/sync/${space_id}`, null, { params: { mode } }),

  listSyncedEntries: (space_id?: string): Promise<CatalogSyncEntry[]> =>
    api.get('/federation/catalog/entries', { params: space_id ? { space_id } : {} }),

  // Trust score
  getTrustScore: (space_id: string): Promise<TrustScore> =>
    api.get(`/federation/trusts/${space_id}/score`),
};
