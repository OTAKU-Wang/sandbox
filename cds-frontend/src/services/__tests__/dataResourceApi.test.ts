// A310/A312：数据资源服务边界的分页与删除结果契约。
// 只钉行为：请求参数形状、"服务端给几行就是几行"（证明这里不再 slice）、
// 以及删除响应体要原样透传给页面（页面才有据可依，不一律报成功）。
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import api from '../api';
import { dataResourceApi, type DataResource } from '../dataResourceApi';

const get = vi.mocked(api.get);
const del = vi.mocked(api.delete);

function resource(id: string): DataResource {
  return {
    id,
    provider_id: 'p-1',
    name: `资源 ${id}`,
    description: null,
    resource_type: 'file',
    format: 'csv',
    status: 'ready',
    schema_fields: null,
    row_count: 2,
    file_size_bytes: 1024,
    error_message: null,
    created_at: '2026-10-18T00:00:00Z',
    updated_at: '2026-10-18T00:00:00Z',
  };
}

beforeEach(() => {
  get.mockReset();
  del.mockReset();
});

describe('数据资源列表分页（A310）', () => {
  it('把分页交给后端：paginated=true 与 page/page_size 一起发出去', async () => {
    get.mockResolvedValue({ items: [resource('r-1')], total: 7, page: 3, page_size: 5 });

    const res = await dataResourceApi.list({ page: 3, page_size: 5 });

    expect(get).toHaveBeenCalledWith('/data-resources/', {
      params: { paginated: true, page: 3, page_size: 5 },
    });
    expect(res.total).toBe(7);
    expect(res.items).toHaveLength(1);
  });

  it('不传参数时按第 1 页 20 条请求', async () => {
    get.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });

    await dataResourceApi.list();

    expect(get).toHaveBeenCalledWith('/data-resources/', {
      params: { paginated: true, page: 1, page_size: 20 },
    });
  });

  it('不再在前端 slice：后端回几行就是几行，total 用后端的而不是数组长度', async () => {
    const items = Array.from({ length: 25 }, (_, i) => resource(`r-${i}`));
    get.mockResolvedValue({ items, total: 999, page: 1, page_size: 20 });

    const res = await dataResourceApi.list({ page: 1, page_size: 20 });

    expect(res.items).toHaveLength(25);
    expect(res.total).toBe(999);
  });
});

describe('删除结果如实透传（A312）', () => {
  it('响应体的 storage_deleted/key_destroyed 原样交给调用方', async () => {
    del.mockResolvedValue({
      deleted: true,
      resource_id: 'r-1',
      storage_deleted: false,
      key_destroyed: false,
    });

    const result = await dataResourceApi.delete('r-1');

    expect(del).toHaveBeenCalledWith('/data-resources/r-1');
    expect(result.deleted).toBe(true);
    expect(result.storage_deleted).toBe(false);
    expect(result.key_destroyed).toBe(false);
  });
});
