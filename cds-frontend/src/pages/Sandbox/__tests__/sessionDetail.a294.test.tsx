/**
 * A294 前端半边（工作区文件下载）：X-CDS-Output-Review 缺失必须是显式第三态。
 *
 * 台账 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A294：后端只会发
 * `passed; findings=N` / `redacted; findings=N` 两种值，而 `parseReviewHeader` 在头缺失时返回
 * `null`——跨域部署下 `expose_headers` 没配时浏览器根本不会把这个头交给 JS。旧消费端只写
 * `if (review?.status === 'redacted')`，于是 `null` 一路静默走到 `message.success('下载成功')`，
 * 把「没拿到审查披露」渲染成「审查通过」，正踩 honest degradation 铁律。
 *
 * 三条用例（缺一条都不算把第三态与两态分开）：
 * ① 头缺失 → 必须出现「未读取到输出审查披露」的显式告知（内联 Alert + 行徽标），
 *    且不得出现「审查通过」徽标、不得报「下载成功」；
 * ② `passed; findings=0` → 仍按两态里的「通过」渲染，不得误报缺失（挡一刀切式修法）；
 * ③ `redacted; findings=2` → 既有脱敏说明不变，同样不得误报缺失。
 *
 * 断言只锁文案里连续可子串匹配的哨兵（「未读取到输出审查披露」/「审查披露缺失」/「审查通过」），
 * 不锁整句措辞。toast 侧走 antd 局部 mock：静态 message 在 React 19 下不往 body 渲染（见
 * src/pages/Auth/__tests__/Login.test.tsx 的同款处理），只能接管调用参数断言。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, configure } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import SessionDetail from '../SessionDetail';
import api from '../../../services/api';
import { useAuthStore } from '../../../stores/authStore';
import { UserRole } from '../../../types/enums';
import type { User } from '../../../types/models';

const { messageSpy } = vi.hoisted(() => ({
  messageSpy: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
    open: vi.fn(),
    destroy: vi.fn(),
  },
}));

// 整页渲染（1613 行的会话详情）在 jsdom 里偏慢，放宽 asyncUtil 等待以免把「慢」读成「缺第三态」。
configure({ asyncUtilTimeout: 10_000 });

// antd 响应式栅格依赖 window.matchMedia（jsdom 未实现）
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), patch: vi.fn(), request: vi.fn() },
}));

// 静态 message 在 React 19 下不渲染 DOM（项目已知事实，见 src/antdReact19Patch.ts 的由来），
// 所以 toast 只能接管参数断言；页面里的下载/审查判定链路全部是真代码。
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return { ...actual, message: messageSpy as unknown as typeof actual.message };
});

const get = api.get as unknown as ReturnType<typeof vi.fn>;

interface GetConfig {
  params?: Record<string, unknown>;
  responseType?: string;
  transformResponse?:
    | ((data: unknown, headers: Record<string, unknown>) => unknown)
    | Array<(data: unknown, headers: Record<string, unknown>) => unknown>;
  validateStatus?: (status: number) => boolean;
}

const OWNER: User = {
  id: 'u-owner',
  username: 'owner',
  email: 'owner@example.invalid',
  role: UserRole.BUYER,
  organization: null,
};

const SESSION = {
  id: 's-1',
  user_id: 'u-owner',
  status: 'running',
  sandbox_level: 'l2c',
  trust_root: 'none',
  runtime_kind: 'l2c',
  sandbox_type: 'l2c',
  purpose: 'a294',
  created_at: '2026-10-20T00:00:00Z',
  timeout_seconds: 900,
  resource_limits: { cpu: 2, memory_mb: 2048 },
  output_review_enabled: true,
};

const FILES = [{ name: 'out.csv', size: 2048, encrypted: false }];

/** 下载通道的一次性设定：header=null 模拟跨域未 expose_headers（浏览器不给 JS 这个头）。 */
let downloadHeader: string | null = null;

async function serve(url: string, config?: GetConfig): Promise<unknown> {
  if (url.startsWith('/sandbox-sessions/s-1/files/')) {
    // 与 axios 一致：transformResponse(data, headers) 先跑，再按 validateStatus 决定 resolve。
    const headers: Record<string, unknown> = downloadHeader
      ? { 'x-cds-output-review': downloadHeader }
      : {};
    let data: unknown = new Blob(['a,b\n1,2\n']);
    const configured = config?.transformResponse;
    const fns = Array.isArray(configured) ? configured : configured ? [configured] : [];
    for (const fn of fns) data = fn(data, headers);
    config?.validateStatus?.(200);
    return data;
  }
  if (url === '/sandbox-sessions/s-1') return SESSION;
  if (url.endsWith('/network-policy')) return { mode: 'deny_all' };
  if (url.endsWith('/files')) return { files: FILES };
  if (url.endsWith('/usage')) {
    return {
      workspace: { files: 1, bytes: 2048 },
      uploaded_files: { count: 1, bytes: 2048 },
      snapshots: { count: 0, bytes: 0 },
      timeout: { timeout_seconds: 900, extended_seconds: 0 },
    };
  }
  if (url.endsWith('/metering')) return { available: false, reason: 'a294: 无 cgroup 读数' };
  if (url.endsWith('/logs')) return { logs: [], count: 0, latest_created_at: null };
  if (url.endsWith('/snapshots')) return { snapshots: [], total: 0 };
  if (url === '/meta/capability-matrix') return { matrix: {} };
  return {};
}

function renderDetail() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/sandbox-sessions/s-1']}>
        <Routes>
          <Route path="/sandbox-sessions/:id" element={<SessionDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  downloadHeader = null;
  useAuthStore.setState({ user: OWNER });
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: vi.fn(() => 'blob:a294') });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
  get.mockImplementation(serve);
});

async function clickDownload() {
  const button = await screen.findByRole('button', { name: /下\s*载/ });
  fireEvent.click(button);
}

describe('SessionDetail 输出审查披露的第三态（A294）', () => {
  it('头缺失（跨域未 expose_headers）：必须显式说「未读取到输出审查披露」，且不得渲染成审查通过', async () => {
    downloadHeader = null;
    renderDetail();
    await screen.findByText('out.csv');
    await clickDownload();

    expect(await screen.findByText(/未读取到输出审查披露/)).toBeInTheDocument();
    expect(screen.getByText('审查披露缺失')).toBeInTheDocument();
    expect(screen.queryByText('审查通过')).not.toBeInTheDocument();
    expect(screen.queryByText('下载成功')).not.toBeInTheDocument();
    await waitFor(() => expect(messageSpy.warning).toHaveBeenCalled());
    expect(String(messageSpy.warning.mock.calls[0][0])).toContain('未读取到输出审查披露');
    expect(messageSpy.success).not.toHaveBeenCalled();
  }, 30_000);

  it('头为 passed; findings=0：仍渲染「审查通过」，不得把通过误报成缺失', async () => {
    downloadHeader = 'passed; findings=0';
    renderDetail();
    await screen.findByText('out.csv');
    await clickDownload();

    expect(await screen.findByText('审查通过')).toBeInTheDocument();
    expect(screen.queryByText(/未读取到输出审查披露/)).not.toBeInTheDocument();
    expect(screen.queryByText('审查披露缺失')).not.toBeInTheDocument();
    await waitFor(() => expect(messageSpy.success).toHaveBeenCalledWith('下载成功'));
  }, 30_000);

  it('头为 redacted; findings=2：既有脱敏说明保持，同样不得误报缺失', async () => {
    downloadHeader = 'redacted; findings=2';
    renderDetail();
    await screen.findByText('out.csv');
    await clickDownload();

    expect(await screen.findByText(/已通过输出审查，部分敏感内容已被脱敏/)).toBeInTheDocument();
    expect(screen.getByText(/已脱敏 · 发现 2 项/)).toBeInTheDocument();
    expect(screen.queryByText(/未读取到输出审查披露/)).not.toBeInTheDocument();
    expect(screen.queryByText('审查披露缺失')).not.toBeInTheDocument();
  }, 30_000);
});
