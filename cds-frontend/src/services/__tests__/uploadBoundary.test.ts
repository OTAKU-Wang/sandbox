import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from '../api';
import { dataResourceApi } from '../dataResourceApi';
import { sandboxApi } from '../sandboxApi';

const post = vi.mocked(api.post);

const csv = () => new File(['a,b\n1,2\n'], 'orders.csv', { type: 'text/csv' });

beforeEach(() => {
  post.mockReset();
  post.mockResolvedValue({ id: 'x' } as never);
});

const headersOf = (call: unknown[]) => (call[2] as { headers?: Record<string, string> } | undefined)?.headers;

describe('multipart 上传不得手写 Content-Type', () => {
  it('数据资源上传只交 FormData，boundary 留给浏览器', async () => {
    await dataResourceApi.upload({ file: csv(), name: '订单' });

    const call = post.mock.calls[0];
    expect(call[0]).toBe('/data-resources/upload');
    expect(call[1]).toBeInstanceOf(FormData);
    expect((call[1] as FormData).get('name')).toBe('订单');
    expect((call[1] as FormData).get('file')).toBeInstanceOf(File);
    expect(headersOf(call)?.['Content-Type']).toBeUndefined();
  });

  it('数据资源上传逐调用放宽超时（A311：后端上限 500MB，全局 30s 必先把请求掐了）', async () => {
    await dataResourceApi.upload({ file: csv(), name: '订单' });

    const config = post.mock.calls[0][2] as { timeout?: number } | undefined;
    expect(config?.timeout).toBeGreaterThanOrEqual(300_000);
  });

  it('会话文件上传同样不带手写 Content-Type', async () => {
    await sandboxApi.uploadFile('sess-1', csv());

    const call = post.mock.calls[0];
    expect(call[0]).toBe('/sandbox-sessions/sess-1/files');
    expect(call[1]).toBeInstanceOf(FormData);
    expect(headersOf(call)?.['Content-Type']).toBeUndefined();
  });
});
