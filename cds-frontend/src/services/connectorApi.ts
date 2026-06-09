import api from './api';

export interface Connector {
  id: string;
  name: string;
  endpoint_url: string;
  status: 'active' | 'suspended' | 'revoked';
  trust_level: string;
  api_key?: string;
  created_at: string;
  last_heartbeat: string | null;
}

export interface HeartbeatResponse {
  connector_id: string;
  status: 'healthy' | 'unhealthy' | 'unknown';
  last_seen: string;
  latency_ms: number;
}

export interface RegisterConnectorPayload {
  name: string;
  endpoint_url: string;
  api_key: string;
}

export const connectorApi = {
  // List all connectors
  listConnectors: (): Promise<Connector[]> =>
    api.get('/connectors'),

  // Get single connector
  getConnector: (id: string): Promise<Connector> =>
    api.get(`/connectors/${id}`),

  // Register new connector
  registerConnector: (data: RegisterConnectorPayload): Promise<Connector> =>
    api.post('/connectors/register', data),

  // Suspend connector
  suspendConnector: (id: string): Promise<Connector> =>
    api.post(`/connectors/${id}/suspend`),

  // Reactivate connector
  reactivateConnector: (id: string): Promise<Connector> =>
    api.post(`/connectors/${id}/reactivate`),

  // Rotate API key
  rotateKey: (id: string): Promise<{ api_key: string }> =>
    api.post(`/connectors/${id}/rotate-key`),

  // Get heartbeat status
  getHeartbeat: (id: string): Promise<HeartbeatResponse> =>
    api.get(`/connectors/${id}/heartbeat`),
};
