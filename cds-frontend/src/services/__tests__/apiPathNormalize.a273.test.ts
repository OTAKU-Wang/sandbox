/**
 * A273 收口的第二半：拦截器真的挂在共享 axios 实例上，且**只改路径、不动别的**。
 *
 * 这里不打桩 `services/api`（打桩了就测不到拦截器），用的是仓内既有套路：
 * 给实例换一个 capture adapter（见 api.test.ts 的 multipart 那组），adapter 收到的
 * config 已经跑完全部请求拦截器 ⇒ 断言的是真出站形状。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from '../../stores/authStore';
import api from '../api';

type Captured = { url?: string; params?: unknown; data?: unknown; method?: string };

const sent: Captured[] = [];
const echo = async (config: Captured & { headers?: unknown }) => {
  sent.push({ url: config.url, params: config.params, data: config.data, method: config.method });
  return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config };
};

beforeEach(() => {
  sent.length = 0;
  useAuthStore.setState({ token: null, refreshToken: null });
  api.defaults.adapter = echo as never;
});

describe('A273：请求拦截器把补编码统一收在一处', () => {
  it('模板串里没编码的段（空格）在出站前被补编码', async () => {
    await api.get(`/sandbox/sessions/中 西/files`);
    expect(sent[0].url).toBe('/sandbox/sessions/%E4%B8%AD%20%E8%A5%BF/files');
  });

  it('已经编码好的段不被二次编码（幂等）', async () => {
    await api.get('/data-resources/a%20b%2Fc');
    expect(sent[0].url).toBe('/data-resources/a%20b%2Fc');
  });

  it('查询参数与请求体都不受这次收口影响（编码口径仍属于 axios params）', async () => {
    await api.get('/flow/openapi-calls/abc/stats', { params: { window: '7d', q: 'a b&c' } });
    expect(sent[0].url).toBe('/flow/openapi-calls/abc/stats');
    expect(sent[0].params).toEqual({ window: '7d', q: 'a b&c' });
  });

  it('空 id 拼出的空段直接抛错，不发这条注定打偏的请求', async () => {
    const emptyId = '';
    await expect(api.get(`/sandbox/sessions/${emptyId}/logs`)).rejects.toThrow();
    expect(sent).toHaveLength(0);
  });

  it('带尾斜杠与查询串的既有形状原样保留（只补编码，不裁不动）', async () => {
    await api.get('/meta/sandbox-levels/?pretty=1');
    expect(sent[0].url).toBe('/meta/sandbox-levels/?pretty=1');
  });
});
