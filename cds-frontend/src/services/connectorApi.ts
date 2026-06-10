import api from './api';
import type { HighRiskOperationPayload } from './identityApi';

export interface Connector {
  id: string;
  name: string;
  endpoint_url: string;
  status: 'active' | 'suspended' | 'revoked';
  trust_level: string;
  api_key?: string;
  api_key_prefix?: string;
  space_id?: string;
  space_name?: string;
  space_url?: string;
  description?: string | null;
  is_healthy?: boolean;
  created_at: string;
  last_heartbeat: string | null;
}

export interface HeartbeatResponse {
  connector_id: string;
  status: 'healthy' | 'unhealthy' | 'unknown';
  last_seen: string | null;
  latency_ms: number;
}

export interface RegisterConnectorPayload {
  space_id: string;
  space_name: string;
  space_url: string;
  description?: string;
}

interface ConnectorResponse {
  id: string;
  space_id: string;
  space_name: string;
  space_url: string;
  description?: string | null;
  status: 'active' | 'suspended' | 'revoked';
  is_healthy?: boolean;
  last_heartbeat: string | null;
  api_key?: string;
  api_key_prefix?: string;
  supported_protocols?: string[];
  max_concurrent_sessions?: number;
  created_at: string;
}

const normalizeConnector = (item: ConnectorResponse): Connector => ({
  id: item.id,
  name: item.space_name,
  endpoint_url: item.space_url,
  status: item.status,
  trust_level: item.is_healthy ? 'healthy' : 'unknown',
  api_key: item.api_key,
  api_key_prefix: item.api_key_prefix,
  space_id: item.space_id,
  space_name: item.space_name,
  space_url: item.space_url,
  description: item.description,
  is_healthy: item.is_healthy,
  created_at: item.created_at,
  last_heartbeat: item.last_heartbeat,
});

export const connectorApi = {
  // List all connectors
  listConnectors: (): Promise<Connector[]> =>
    api.get('/connectors').then((res) => {
      const data = res as unknown as { items?: ConnectorResponse[] } | ConnectorResponse[];
      return (Array.isArray(data) ? data : data.items || []).map(normalizeConnector);
    }),

  // Get single connector
  getConnector: (id: string): Promise<Connector> =>
    api.get(`/connectors/${id}`).then((res) => normalizeConnector(res as unknown as ConnectorResponse)),

  // Register new connector
  registerConnector: (data: RegisterConnectorPayload): Promise<Connector> =>
    api.post('/connectors/register', null, { params: data }).then((res) => normalizeConnector(res as unknown as ConnectorResponse)),

  // Suspend connector
  suspendConnector: (id: string, data: HighRiskOperationPayload): Promise<Connector> =>
    api.post(`/connectors/${id}/suspend`, data).then((res) => normalizeConnector(res as unknown as ConnectorResponse)),

  // Reactivate connector
  reactivateConnector: (id: string, data: HighRiskOperationPayload): Promise<Connector> =>
    api.post(`/connectors/${id}/reactivate`, data).then((res) => normalizeConnector(res as unknown as ConnectorResponse)),

  // Rotate API key
  rotateKey: (id: string, data: HighRiskOperationPayload): Promise<{ api_key: string; api_key_prefix?: string }> =>
    api.post(`/connectors/${id}/rotate-key`, data),

  // Get heartbeat status
  getHeartbeat: (id: string): Promise<HeartbeatResponse> =>
    api.get(`/connectors/${id}/heartbeat`),
};
