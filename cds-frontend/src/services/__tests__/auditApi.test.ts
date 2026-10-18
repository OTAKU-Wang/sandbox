// GET /audit/export 的 2xx 响应体在浏览器侧只拿到 Blob（共享拦截器把 response.data
// 原样返回，Content-Type / Content-Disposition 头在这层已经不存在），所以「这坨字节
// 到底是不是 CSV」只能靠 Blob 自己的 type + 首行内容判定。
// 本文件锁的就是这条判定：nginx/网关的 HTML 错误页、W2 JSON 错误信封、空响应体
// 一律不得被当成 CSV 落盘（用户会拿到一个「导出的 audit_export_30d.csv」，打开是 502 页面）。
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import api from '../api';
import { auditApi } from '../auditApi';

const get = vi.mocked(api.get);

beforeEach(() => {
  get.mockReset();
});

describe('auditApi.export CSV 嗅探', () => {
  it('真 CSV 附件流按后端同名文件落盘，参数只有 days + format', async () => {
    const payload = new Blob(['id,user_id,action\n1,u-1,user.login\n'], { type: 'text/csv' });
    get.mockResolvedValue(payload as never);

    const res = await auditApi.export({ days: 90 });

    expect(get).toHaveBeenCalledWith('/audit/export', {
      params: { days: 90, format: 'csv' },
      responseType: 'blob',
    });
    expect(res.filename).toBe('audit_export_90d.csv');
    expect(res.blob).toBe(payload);
    expect(await res.blob.text()).toContain('user.login');
  });

  it('text/html 的 2xx 响应体不是 CSV：抛出说明原因的错，不返回 Blob', async () => {
    get.mockResolvedValue(
      new Blob(['<html><body><h1>502 Bad Gateway</h1></body></html>'], { type: 'text/html' }) as never,
    );

    await expect(auditApi.export({ days: 30 })).rejects.toThrow(/CSV/);
  });

  it('content-type 缺失但首行是 HTML 标签时同样拒收（网关会改写响应头）', async () => {
    get.mockResolvedValue(new Blob(['<!DOCTYPE html>\n<html>'] as BlobPart[], { type: '' }) as never);

    await expect(auditApi.export({ days: 30 })).rejects.toThrow(/HTML/);
  });

  it('W2 JSON 错误信封被当成 2xx 下发时，把后端 message 带进错误里', async () => {
    get.mockResolvedValue(
      new Blob([JSON.stringify({ code: 'INTERNAL_ERROR', message: '导出服务暂不可用', detail: null, request_id: 'r-1' })], {
        type: 'application/json',
      }) as never,
    );

    await expect(auditApi.export({ days: 30 })).rejects.toThrow('导出服务暂不可用');
  });

  it('空响应体不是合法导出（后端 0 条也会写表头行）', async () => {
    get.mockResolvedValue(new Blob([], { type: 'text/csv' }) as never);

    await expect(auditApi.export({ days: 30 })).rejects.toThrow(/空/);
  });

  it('json 分支仍按 JSON 落盘（不受 CSV 嗅探影响）', async () => {
    get.mockResolvedValue({ records: [], total: 0, period_days: 7 } as never);

    const res = await auditApi.export({ days: 7, format: 'json' });
    expect(res.filename).toBe('audit_export_7d.json');
    expect(res.contentType).toBe('application/json');
    expect(JSON.parse(await res.blob.text())).toEqual({ records: [], total: 0, period_days: 7 });
  });

  it('错误体是 Blob 时还原 W2 信封再抛，页面才能显示后端真实原因', async () => {
    get.mockRejectedValue(new Blob([JSON.stringify({ code: 'FORBIDDEN', message: '无权导出' })], { type: 'application/json' }) as never);

    await expect(auditApi.export({ days: 30 })).rejects.toMatchObject({ code: 'FORBIDDEN', message: '无权导出' });
  });
});
