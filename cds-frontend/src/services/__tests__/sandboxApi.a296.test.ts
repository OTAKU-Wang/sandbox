/**
 * A296（sandbox 服务层三处）+ A311 的另一半（会话文件上传也吃全局 30s）。
 *
 * - M-300：`list()` 的 page/page_size 不做钳制 ⇒ `?page=0` 变成 `skip=-20`，后端 422，
 *   列表页只剩错误态；负数/小数/超大同理。
 * - M-302：`FileDownloadBlockedError` 定义后从未被引用（downloadFile 抛的是当场手搓的交叉类型），
 *   `downloadFile` 抛的是当场手搓的交叉类型 ⇒ 页面想区分「被输出审查阻断」只能猜 `.status`。
 * - M-301：`status: '…' | string` 让联合退化成 `string`（编译期不再拦拼错的 `'succeded'`）。
 * - A311：`uploadFile` 逐调用放宽超时，值与数据资源上传同一个常量（两处各自写死就会漂）。
 */
import type { AxiosRequestConfig } from 'axios';
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
import { FileDownloadBlockedError, sandboxApi, type SessionOperationStatus } from '../sandboxApi';
import { dataResourceApi } from '../dataResourceApi';
import { UPLOAD_TIMEOUT_MS } from '../timeouts';

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  get.mockResolvedValue({ items: [], total: 0 } as never);
  post.mockResolvedValue({} as never);
});

const paramsOf = (call: unknown[]) => (call[1] as { params?: Record<string, unknown> } | undefined)?.params;

describe('A296 M-300：分页参数钳制', () => {
  it('page=0 / 负数不再算出负 skip', async () => {
    await sandboxApi.list({ page: 0, page_size: 20 });
    expect(paramsOf(get.mock.calls[0])).toMatchObject({ skip: 0, limit: 20 });

    await sandboxApi.list({ page: -7, page_size: 20 });
    expect(paramsOf(get.mock.calls[1])).toMatchObject({ skip: 0, limit: 20 });
  });

  it('page_size 下限 1、上限 200（0/负数不再打后端 422，超大不再一次拉穿）', async () => {
    await sandboxApi.list({ page_size: 0 });
    expect(paramsOf(get.mock.calls[0])).toMatchObject({ limit: 1 });

    await sandboxApi.list({ page_size: -5 });
    expect(paramsOf(get.mock.calls[1])).toMatchObject({ limit: 1 });

    await sandboxApi.list({ page_size: 100_000 });
    expect(paramsOf(get.mock.calls[2])).toMatchObject({ limit: 200 });
  });

  it('小数按 trunc 取整，正常分页算术不变', async () => {
    await sandboxApi.list({ page: 3.9, page_size: 20 });
    expect(paramsOf(get.mock.calls[0])).toMatchObject({ skip: 40, limit: 20 });
  });
});

describe('A296 M-302：409 阻断错误是真正的 Error 子类', () => {
  it('downloadFile 在 409 上抛 FileDownloadBlockedError，instanceof 可用且 detail 原样带出', async () => {
    get.mockImplementation(async (_url: string, config?: AxiosRequestConfig) => {
      (config?.validateStatus as ((status: number) => boolean) | undefined)?.(409);
      // 共享拦截器把 response.data 交出来；responseType:'blob' 时它就是一个 Blob。
      throw new Blob([JSON.stringify({ code: 'OUTPUT_BLOCKED', detail: { findings_count: 3 } })]);
    });

    await expect(sandboxApi.downloadFile('s1', 'out.csv')).rejects.toBeInstanceOf(FileDownloadBlockedError);
    const err = await sandboxApi.downloadFile('s1', 'out.csv').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect((err as FileDownloadBlockedError).status).toBe(409);
    // findings_count 从 detail 里读（读信封顶层会让每条阻断都报 0 findings）。
    expect((err as FileDownloadBlockedError).detail.findings_count).toBe(3);
  });

  it('非 409 的失败照原样抛，不被包成阻断', async () => {
    const boom = new Blob([JSON.stringify({ code: 'NOT_FOUND' })]);
    get.mockImplementation(async (_url: string, config?: AxiosRequestConfig) => {
      (config?.validateStatus as ((status: number) => boolean) | undefined)?.(404);
      throw boom;
    });
    await expect(sandboxApi.downloadFile('s1', 'x')).rejects.toBe(boom);
  });
});

describe('A296 M-301：SessionOperationStatus 不再退化成 string', () => {
  it('四个已知态是字面量（可补全、可 switch 收窄），未知字符串仍可赋值（后端加状态不炸编译）', () => {
    const known: SessionOperationStatus[] = ['pending', 'running', 'succeeded', 'failed'];
    const future: SessionOperationStatus = 'queued-by-new-backend';
    expect(known).toHaveLength(4);
    expect(future).toBe('queued-by-new-backend');
    // 说清楚这条修的是什么、不是什么：`(string & {})` 保住兼容性的同时让四个字面量重新
    // 出现在类型里（`| string` 时整个联合就是 string，IDE 什么都不补全）；它**不**拒绝
    // 拼错的字符串——真要编译期拦拼写，得把兜底摘掉，那会让后端新增状态变成前端编译失败。
    const typo: SessionOperationStatus = 'succeded';
    expect(typo).toBe('succeded');
  });

  it('isTerminalOperation 只认 succeeded/failed', async () => {
    const { isTerminalOperation } = await import('../sandboxApi');
    const shape = (status: SessionOperationStatus) => ({
      operation_id: 'op', session_id: 's', op_type: 'exec', status, progress: 0,
      error: null, result_ref: null, created_at: null, updated_at: null,
    });
    expect(isTerminalOperation(shape('succeeded'))).toBe(true);
    expect(isTerminalOperation(shape('failed'))).toBe(true);
    expect(isTerminalOperation(shape('running'))).toBe(false);
    expect(isTerminalOperation(shape('pending'))).toBe(false);
    expect(isTerminalOperation(null)).toBe(false);
  });
});

describe('A311：上传超时两处共用一个常量', () => {
  it('常量本身≥后端 500MB 的现实需要（30s 全局值只是普通请求的口径）', () => {
    expect(UPLOAD_TIMEOUT_MS).toBeGreaterThanOrEqual(300_000);
  });

  it('会话文件上传逐调用带上放宽后的超时', async () => {
    await sandboxApi.uploadFile('s1', new File(['a'], 'x.csv', { type: 'text/csv' }));
    const config = post.mock.calls[0][2] as { timeout?: number } | undefined;
    expect(config?.timeout).toBe(UPLOAD_TIMEOUT_MS);
  });

  it('数据资源上传走的是同一个常量（两处各自写死迟早漂开）', async () => {
    await dataResourceApi.upload({ file: new File(['a'], 'x.csv'), name: 'n' });
    const config = post.mock.calls[0][2] as { timeout?: number } | undefined;
    expect(config?.timeout).toBe(UPLOAD_TIMEOUT_MS);
  });
});

describe('A273 在 sandboxApi 的点名处', () => {
  it('devGetTemplate 的模板名进段前编码（含 / 的名字不能改写命中路由）', async () => {
    await sandboxApi.devGetTemplate('a b/c');
    expect(get.mock.calls[0][0]).toBe('/dev-sandbox/templates/a%20b%2Fc');
  });
});
