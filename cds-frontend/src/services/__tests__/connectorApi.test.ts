// 连接器列表本地契约（A313/A318，全部离线跑）：
// ① 后端 GET /connectors/ 是 keyset 分页（app/api/connectors.py:191-208）：limit 默认 100（le=500），
//    续拉游标只放在 X-Next-Cursor 响应头里；body.total 只在「确定没有下一页」时才是数字，其余为 null。
//    ⇒ 服务层必须把游标与截断显式暴露出去（transformResponse 触达响应头，同 sandboxApi blob 的既有手法），
//      而不是让第 101 条连接器在 UI 上凭空消失。
// ② 解包走 utils/unwrapList（铁律：禁止 `|| []` 静默兜底，形状不对要抛 ListShapeError）。
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

import api from '../api';
import { connectorApi, CONNECTOR_PAGE_LIMIT, captureNextCursor } from '../connectorApi';
import { ListShapeError } from '../../utils/unwrapList';

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const row = (id: string) => ({
  id: `c-${id}`,
  space_id: `sp-${id}`,
  space_name: `CONN-${id}`,
  space_url: `https://sp-${id}.example/api`,
  status: 'active',
  is_healthy: true,
  last_heartbeat: null,
  created_at: '2026-10-01T00:00:00',
});

beforeEach(() => {
  get.mockReset();
});

describe('connectorApi.listConnectors — 游标与截断显式化（A313）', () => {
  it('请求显式带上 limit（= 后端默认 100），transformResponse 挂着游标捕获器', async () => {
    get.mockResolvedValue({ items: [], total: 0, __next_cursor: null });
    await connectorApi.listConnectors();
    expect(CONNECTOR_PAGE_LIMIT).toBe(100);
    expect(get).toHaveBeenCalledWith(
      '/connectors/',
      expect.objectContaining({
        params: { limit: CONNECTOR_PAGE_LIMIT, cursor: undefined },
        transformResponse: [captureNextCursor],
      }),
    );
  });

  it('续拉：游标进 params.cursor', async () => {
    get.mockResolvedValue({ items: [], total: null, __next_cursor: null });
    await connectorApi.listConnectors('cursor-2');
    const config = get.mock.calls[0][1] as { params: Record<string, unknown> };
    expect(config.params.cursor).toBe('cursor-2');
  });

  it('还有下一页：暴露 nextCursor，possiblyTruncated=true', async () => {
    get.mockResolvedValue({ items: [row('1')], total: null, __next_cursor: 'cursor-2' });
    const res = await connectorApi.listConnectors();
    expect(res.items).toHaveLength(1);
    expect(res.nextCursor).toBe('cursor-2');
    expect(res.possiblyTruncated).toBe(true);
  });

  it('后端给了 total 且无游标 → 不谎称截断', async () => {
    get.mockResolvedValue({ items: [row('1'), row('2')], total: 2, __next_cursor: null });
    const res = await connectorApi.listConnectors();
    expect(res.items).toHaveLength(2);
    expect(res.nextCursor).toBeNull();
    expect(res.possiblyTruncated).toBe(false);
  });

  it('首页 total=null 而游标头丢失（代理剥头）→ 仍如实报截断', async () => {
    get.mockResolvedValue({ items: [row('1')], total: null, __next_cursor: null });
    const res = await connectorApi.listConnectors();
    expect(res.nextCursor).toBeNull();
    expect(res.possiblyTruncated).toBe(true);
  });

  it('续拉页（cursor 已给）total 恒 null：游标为空即到底，不报截断', async () => {
    get.mockResolvedValue({ items: [row('2')], total: null, __next_cursor: null });
    const res = await connectorApi.listConnectors('cursor-2');
    expect(res.nextCursor).toBeNull();
    expect(res.possiblyTruncated).toBe(false);
  });

  it('items 形状不对 → 抛 ListShapeError，绝不 || [] 静默空表', async () => {
    get.mockResolvedValue({ items: 'oops', total: 1 });
    await expect(connectorApi.listConnectors()).rejects.toThrow(ListShapeError);
  });
});

describe('captureNextCursor — X-Next-Cursor 响应头并进 body（A313）', () => {
  it('原始小写键对象：并入 __next_cursor', () => {
    const out = captureNextCursor(JSON.stringify({ items: [], total: null }), { 'x-next-cursor': 'abc' }) as Record<string, unknown>;
    expect(out).toMatchObject({ items: [], __next_cursor: 'abc' });
  });

  it('AxiosHeaders 形态（带 get 方法）也能取到', () => {
    const headers = { get: (k: string) => (k.toLowerCase() === 'x-next-cursor' ? 'zz' : null) };
    const out = captureNextCursor({ items: [] }, headers) as Record<string, unknown>;
    expect(out.__next_cursor).toBe('zz');
  });

  it('没有游标头 → __next_cursor=null（上层据此判断不可续拉）', () => {
    const out = captureNextCursor({ items: [], total: 2 }, {}) as Record<string, unknown>;
    expect(out.__next_cursor).toBeNull();
  });

  it('非 JSON 体原样返回（错误响应不许在 transformResponse 里炸）', () => {
    expect(captureNextCursor('<html>oops</html>', {})).toBe('<html>oops</html>');
  });
});
