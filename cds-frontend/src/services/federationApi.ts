import api from './api';

export interface TrustRelationship {
  trust_id: string;
  remote_space_id: string;
  remote_space_name: string;
  trust_level: string;
  status: string;
  allowed_operations: string[];
  created_at: string;
  updated_at: string;
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

export const federationApi = {
  // Trust relationships
  listTrusts: (): Promise<TrustRelationship[]> =>
    api.get('/federation/trusts'),

  getTrust: (id: string): Promise<TrustRelationship> =>
    api.get(`/federation/trusts/${id}`),

  establishTrust: (data: { remote_space_id: string; trust_level: string; allowed_operations: string[] }): Promise<TrustRelationship> =>
    api.post('/federation/trusts', data),

  suspendTrust: (id: string): Promise<TrustRelationship> =>
    api.post(`/federation/trusts/${id}/suspend`),

  revokeTrust: (id: string): Promise<TrustRelationship> =>
    api.post(`/federation/trusts/${id}/revoke`),

  // Catalog sync
  getSyncStatus: (): Promise<SyncStatus[]> =>
    api.get('/federation/catalog/sync-status'),

  triggerSync: (space_id: string, mode: 'full' | 'incremental' = 'incremental'): Promise<{ synced: number }> =>
    api.post(`/federation/catalog/sync/${space_id}`, null, { params: { mode } }),

  listSyncedEntries: (space_id?: string): Promise<CatalogSyncEntry[]> =>
    api.get('/federation/catalog/entries', { params: space_id ? { space_id } : {} }),

  // Trust score
  getTrustScore: (space_id: string): Promise<TrustScore> =>
    api.get(`/federation/trusts/${space_id}/score`),
};
