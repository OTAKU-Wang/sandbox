/**
 * A294 前端半边（调试环境批量取回）：X-CDS-Output-Review 缺失必须是显式第三态。
 *
 * 台账 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A294 的第二处消费点：
 * `downloadSelected` 旧代码只有 `if (review?.status === 'redacted') messageApi.warning(...)`，
 * `review === null`（跨域未 expose_headers ⇒ 浏览器不把这个头交给 JS）时一声不响，
 * 而左栏页脚写着「逐个过 T5 输出审查后取回」⇒ 用户读到的是「已通过审查」。
 *
 * 三条用例：① 头缺失 → 必须出现「未读取到输出审查披露」的显式告知（输出卡一条 warn 日志行 +
 * 一条 toast），且不得出现任何「审查通过」口径；② `passed; findings=0` → 正常放行、不得误报缺失
 * （挡一刀切式修法）；③ `redacted; findings=3` → 既有脱敏警告保持，同样不得误报缺失。
 *
 * toast 只能接管 `message.useMessage` 的参数断言：contextHolder 渲染的 toast 依赖 CSS motion，
 * jsdom 下不作为唯一证据；真证据是输出卡里那条持久日志行（真 DOM）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within, configure } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import NotebookWorkbench from '../NotebookWorkbench';
import api from '../../../services/api';

const { messageApiSpy } = vi.hoisted(() => ({
  messageApiSpy: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
    open: vi.fn(),
    destroy: vi.fn(),
  },
}));

// 页重、依赖两轮 GET，放宽等待以免把「慢」读成「缺第三态」。
configure({ asyncUtilTimeout: 10_000 });

vi.mock('@monaco-editor/react', () => ({ default: () => null }));
vi.mock('../monacoSetup', () => ({}));

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
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(), request: vi.fn() },
}));

// 静态 message 在 React 19 下不渲染 DOM；hook 版在 jsdom 里靠 CSS motion，不作断言依据。
// 这里接管 useMessage 的参数，页面里的下载/审查判定链路（downloadSelected + 输出卡日志）全是真代码。
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return {
    ...actual,
    message: {
      ...actual.message,
      useMessage: () => [messageApiSpy, null] as unknown as ReturnType<typeof actual.message.useMessage>,
    } as typeof actual.message,
  };
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

const notebook = (id: string) => ({
  id,
  contract_id: 'c-1',
  title: '流失评分',
  path: `/nb/${id}.ipynb`,
  kernel: 'python3',
  language: 'python',
  content: { cells: [{ cell_type: 'code', source: ['print(1)'], metadata: {}, outputs: [] }] },
  version: 1,
  status: 'active',
  session_id: 's-1',
  execution_backend: 'cds_kernel',
  last_executed_at: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z',
});

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 50 });

/** 下载通道的一次性设定：header=null 模拟跨域未 expose_headers（浏览器不给 JS 这个头）。 */
let downloadHeader: string | null = null;

async function serve(url: string, config?: GetConfig): Promise<unknown> {
  if (url.startsWith('/sandbox-sessions/s-1/files/')) {
    // 与 axios 一致：先跑 transformResponse(data, headers)，再按 validateStatus 决定 resolve。
    const headers: Record<string, unknown> = downloadHeader ? { 'x-cds-output-review': downloadHeader } : {};
    let data: unknown = new Blob(['a,b\n1,2\n']);
    const configured = config?.transformResponse;
    const fns = Array.isArray(configured) ? configured : configured ? [configured] : [];
    for (const fn of fns) data = fn(data, headers);
    config?.validateStatus?.(200);
    return data;
  }
  if (url === '/notebooks') return page([notebook('n-1')]);
  if (url.startsWith('/notebooks/')) return notebook(url.split('/')[2]);
  if (url === '/sandbox-sessions/s-1') {
    return { id: 's-1', status: 'running', sandbox_level: 'l2c', trust_root: 'none', user_id: 'u-1', created_at: '2026-10-20T00:00:00Z' };
  }
  if (url.endsWith('/network-policy')) return { mode: 'deny_all' };
  if (url.endsWith('/files')) return { files: [{ name: 'out.csv', size: 2048, encrypted: false }] };
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
  if (url.startsWith('/deliveries/quota-usage')) return {};
  return {};
}

beforeEach(() => {
  vi.clearAllMocks();
  downloadHeader = null;
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: vi.fn(() => 'blob:a294') });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
  get.mockImplementation(serve);
});

async function downloadTheFile() {
  const checkbox = await screen.findByRole('checkbox', { name: '选择 out.csv' });
  fireEvent.click(checkbox);
  const trigger = await screen.findByText(/下载选中/);
  fireEvent.click(trigger);
}

describe('NotebookWorkbench 输出审查披露的第三态（A294）', () => {
  it('头缺失（跨域未 expose_headers）：必须显式说「未读取到输出审查披露」，不得静默按通过放行', async () => {
    downloadHeader = null;
    renderWithProviders(<NotebookWorkbench />, { route: '/notebooks?task=n-1' });
    await downloadTheFile();

    const outputCard = await screen.findByRole('region', { name: '输出' });
    expect(within(outputCard).getByText(/未读取到输出审查披露/)).toBeInTheDocument();
    await waitFor(() => expect(messageApiSpy.warning).toHaveBeenCalled());
    expect(String(messageApiSpy.warning.mock.calls[0][0])).toContain('未读取到输出审查披露');
    expect(messageApiSpy.success).not.toHaveBeenCalled();
  }, 30_000);

  it('头为 passed; findings=0：正常取回，不得把通过误报成缺失', async () => {
    downloadHeader = 'passed; findings=0';
    renderWithProviders(<NotebookWorkbench />, { route: '/notebooks?task=n-1' });
    await downloadTheFile();

    await waitFor(() => expect(get.mock.calls.map((call) => String(call[0]))).toContain('/sandbox-sessions/s-1/files/out.csv'));
    expect(screen.queryByText(/未读取到输出审查披露/)).not.toBeInTheDocument();
    expect(messageApiSpy.warning).not.toHaveBeenCalled();
  }, 30_000);

  it('头为 redacted; findings=3：既有脱敏警告保持，同样不得误报缺失', async () => {
    downloadHeader = 'redacted; findings=3';
    renderWithProviders(<NotebookWorkbench />, { route: '/notebooks?task=n-1' });
    await downloadTheFile();

    await waitFor(() => expect(messageApiSpy.warning).toHaveBeenCalled());
    expect(String(messageApiSpy.warning.mock.calls[0][0])).toContain('输出审查已脱敏后才放行');
    expect(screen.queryByText(/未读取到输出审查披露/)).not.toBeInTheDocument();
  }, 30_000);
});
