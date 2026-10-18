import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from '../../stores/authStore';
import api from '../api';

describe('API client', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, refreshToken: null });
    localStorage.clear();
  });

  it('authStore provides token for requests', () => {
    useAuthStore.getState().login(
      { id: 'u1', username: 'test', email: 't@t.com', role: 'buyer', organization: null },
      'my-token',
      'my-refresh',
    );
    expect(useAuthStore.getState().token).toBe('my-token');
  });

  it('authStore logout clears token', () => {
    useAuthStore.getState().login(
      { id: 'u1', username: 'test', email: 't@t.com', role: 'buyer', organization: null },
      'tok',
      'rtok',
    );
    useAuthStore.getState().logout();
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

describe('multipart 上传的请求头', () => {
  const sent: Array<Record<string, unknown>> = [];
  const capture = async (config: { headers?: unknown }) => {
    sent.push({ ...(config.headers as Record<string, unknown>) });
    return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config };
  };

  beforeEach(() => {
    sent.length = 0;
  });

  it('FormData 请求不得带 application/json（带了 axios 会把 FormData 呌成 JSON，file 部件丢失）', async () => {
    const fd = new FormData();
    fd.append('file', new Blob(['a,b\n1,2\n'], { type: 'text/csv' }), 'orders.csv');
    fd.append('name', '零售订单');
    await api.post('/data-resources/upload', fd, { adapter: capture as never });

    expect(sent).toHaveLength(1);
    const ct = Object.entries(sent[0]).find(([k]) => k.toLowerCase() === 'content-type');
    expect(String(ct?.[1] ?? '')).not.toContain('application/json');
  });

  it('JSON 请求照旧带 application/json', async () => {
    await api.post('/contracts', { contract_name: 'x' }, { adapter: capture as never });

    expect(sent).toHaveLength(1);
    const ct = Object.entries(sent[0]).find(([k]) => k.toLowerCase() === 'content-type');
    expect(String(ct?.[1])).toContain('application/json');
  });
});
