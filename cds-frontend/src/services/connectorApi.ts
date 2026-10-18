import api from './api';
import type { HighRiskOperationPayload } from './identityApi';
import { unwrapList } from '../utils/unwrapList';

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
  /**
   * 后端当前恒返回 0（app/api/connectors.py get_connector_heartbeat_status 写死
   * `"latency_ms": 0`），**不是实测值** ⇒ UI 不得把它当测量值展示（禁伪造铁律，A318）。
   * 将来后端真量了延迟，再连同字段一起恢复展示。
   */
  latency_ms?: number;
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

/**
 * 后端 GET /connectors/ 是 keyset 分页（app/api/connectors.py:191-208）：limit 默认 100
 *（上限 500）；续拉游标只放在 `X-Next-Cursor` 响应头；body.total 只在「无游标入参且
 * 无 next_cursor」（首页且确定没有更多）时才是数字，其余为 null。
 */
export const CONNECTOR_PAGE_LIMIT = 100;

export interface ConnectorListResult {
  items: Connector[];
  /** 后端 X-Next-Cursor；null = 没有（或没取到）续拉游标。 */
  nextCursor: string | null;
  /**
   * true = 本页之外还有数据被截断：游标在 ⇒ 还有下一页；首页 total=null 而游标又不在
   * ⇒ 后端声明了「还有更多」但游标头没透传过来（代理剥头），同样如实报截断。
   */
  possiblyTruncated: boolean;
}

function readCursorHeader(headers: unknown): string | null {
  if (!headers || typeof headers !== 'object') return null;
  const bag = headers as Record<string, unknown> & { get?: (name: string) => unknown };
  if (typeof bag.get === 'function') {
    const viaGet = bag.get('X-Next-Cursor');
    if (typeof viaGet === 'string' && viaGet) return viaGet;
  }
  for (const [name, value] of Object.entries(bag)) {
    if (name.toLowerCase() === 'x-next-cursor' && typeof value === 'string' && value) return value;
  }
  return null;
}

/**
 * transformResponse 钩子：api.ts 拦截器只透传 response.data，触达响应头只能在这里做
 *（同仓 sandboxApi blob 下载的既有手法）。把只存在于 X-Next-Cursor 里的续拉游标并进 body；
 * 取不到头就在 body 上显式写 null，不悄悄丢。非 JSON / 非对象体（错误响应）原样返回，
 * 不在 transformResponse 里炸。
 */
export function captureNextCursor(data: unknown, headers?: unknown): unknown {
  let body: unknown = data;
  if (typeof data === 'string') {
    if (!data.trim()) body = {};
    else {
      try {
        body = JSON.parse(data);
      } catch {
        return data;
      }
    }
  }
  const cursor = readCursorHeader(headers);
  if (body && typeof body === 'object' && !Array.isArray(body)) {
    return { ...(body as Record<string, unknown>), __next_cursor: cursor };
  }
  return body;
}

export const connectorApi = {
  // List all connectors
  /**
   * 连接器列表：归一化 + 游标/截断显式化（A313）。
   * 解包走 unwrapList（铁律：形状不对可炸，不可 `|| []` 静默空表）；
   * cursor 传入即续拉（游标只存在于响应头，用 transformResponse 取回）。
   */
  listConnectors: (cursor?: string | null): Promise<ConnectorListResult> =>
    api.get('/connectors/', {
      params: { limit: CONNECTOR_PAGE_LIMIT, cursor: cursor ?? undefined },
      transformResponse: [captureNextCursor],
    }).then((res: unknown) => {
      const body = (res ?? {}) as { items?: unknown; total?: number | null; __next_cursor?: unknown };
      const items = unwrapList<ConnectorResponse>(body, '/connectors/', 'items').map(normalizeConnector);
      const nextCursor = typeof body.__next_cursor === 'string' && body.__next_cursor ? body.__next_cursor : null;
      // 续拉页的 total 恒为 null（后端只在首页无游标时才给数），那里只信游标；
      // 首页额外用 total=null 兑住「游标头被代理剥掉」的情形。
      const possiblyTruncated = nextCursor !== null || (cursor == null && body.total === null);
      return { items, nextCursor, possiblyTruncated };
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
