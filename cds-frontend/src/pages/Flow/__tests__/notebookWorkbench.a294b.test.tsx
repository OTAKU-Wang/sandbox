/**
 * A294b：A294 的**接线腿**——把「未读取到 X-CDS-Output-Review 披露」的文件数从
 * `NotebookWorkbench.downloadSelected` 传给 `FileDirectoryPanel`（prop
 * `disclosureMissingCount`），让左栏页脚在审查结果未知时不再断言
 * 「（逐个过 T5 输出审查后取回）」。
 *
 * 台账 docs/ocr-review-2026-10-18/ocr-verified-findings.md §A294：跨域部署下后端没配
 * `expose_headers` ⇒ 浏览器不会把该头交给 JS ⇒ `review === null`。panel 半边
 * （`notebookWorkbenchPanels.tsx:204-213`）已落好第三态，本 spec 钉的是**父组件真的把计数传下去**，
 * 且计数形态正确（按文件计数，不是布尔）：
 *   ① 两个文件都没披露 → 页脚出现「本轮 2 个文件未读取到输出审查披露」，断言句消失；
 *   ② 披露齐全 → 页脚保持「（逐个过 T5 输出审查后取回）」，不得伪造第三态（挡一刀切式修法）；
 *   ③ 一个 passed 一个缺失 → 「本轮 1 个文件…」（证明是计数而非「有/无」开关）。
 *
 * 断言只用页脚独有口径（「本轮 N 个文件」只在页脚；「逐个过 T5」同理），
 * 不与输出卡日志行（`notebookWorkbench.a294.test.tsx` 的取证面）混用。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, configure } from '@testing-library/react';
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

// 页重、依赖两轮 GET + 多次下载，放宽等待以免把「慢」读成「没接线」。
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

const FILE_NAMES = ['a.csv', 'b.csv'];

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

/** 文件名 → X-CDS-Output-Review 取值；null = 头没到页面（跨域未 expose_headers）。 */
let downloadHeaders: Record<string, string | null> = {};

async function serve(url: string, config?: GetConfig): Promise<unknown> {
  if (url.startsWith('/sandbox-sessions/s-1/files/')) {
    const name = decodeURIComponent(url.split('/').pop() ?? '');
    const header = downloadHeaders[name] ?? null;
    const headers: Record<string, unknown> = header ? { 'x-cds-output-review': header } : {};
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
    return {
      id: 's-1',
      status: 'running',
      sandbox_level: 'l2c',
      trust_root: 'none',
      user_id: 'u-1',
      created_at: '2026-10-20T00:00:00Z',
    };
  }
  if (url.endsWith('/network-policy')) return { mode: 'deny_all' };
  if (url.endsWith('/files')) {
    return { files: FILE_NAMES.map((name) => ({ name, size: 2048, encrypted: false })) };
  }
  if (url.endsWith('/usage')) {
    return {
      workspace: { files: FILE_NAMES.length, bytes: 2048 * FILE_NAMES.length },
      uploaded_files: { count: FILE_NAMES.length, bytes: 2048 * FILE_NAMES.length },
      snapshots: { count: 0, bytes: 0 },
      timeout: { timeout_seconds: 900, extended_seconds: 0 },
    };
  }
  if (url.endsWith('/metering')) return { available: false, reason: 'a294b: 无 cgroup 读数' };
  if (url.endsWith('/logs')) return { logs: [], count: 0, latest_created_at: null };
  if (url.endsWith('/snapshots')) return { snapshots: [], total: 0 };
  if (url.startsWith('/deliveries/quota-usage')) return {};
  return {};
}

beforeEach(() => {
  vi.clearAllMocks();
  downloadHeaders = {};
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: vi.fn(() => 'blob:a294b'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
  get.mockImplementation(serve);
});

async function selectBothFiles() {
  for (const name of FILE_NAMES) {
    fireEvent.click(await screen.findByRole('checkbox', { name: `选择 ${name}` }));
  }
  await screen.findByText(/下载选中/);
}

async function clickDownloadAndWait() {
  const trigger = await screen.findByText(/下载选中/);
  fireEvent.click(trigger);
  await waitFor(() =>
    expect(
      get.mock.calls
        .map((call) => String(call[0]))
        .filter((u) => u.startsWith('/sandbox-sessions/s-1/files/')).length,
    ).toBe(FILE_NAMES.length),
  );
}

async function downloadBothFiles() {
  await selectBothFiles();
  await clickDownloadAndWait();
}

const FOOTER_ASSERTION = /逐个过 T5 输出审查后取回/;
const FOOTER_THIRD_STATE = /本轮 \d+ 个文件未读取到输出审查披露/;

describe('NotebookWorkbench → FileDirectoryPanel 页脚第三态接线（A294b）', () => {
  it('两个文件都没披露：页脚必须换成「本轮 2 个文件未读取到输出审查披露」，断言句消失', async () => {
    downloadHeaders = { 'a.csv': null, 'b.csv': null };
    renderWithProviders(<NotebookWorkbench />, { route: '/notebooks?task=n-1' });

    await selectBothFiles();
    expect(screen.getByText(FOOTER_ASSERTION)).toBeInTheDocument();
    await clickDownloadAndWait();

    expect(await screen.findByText(/本轮 2 个文件未读取到输出审查披露/)).toBeInTheDocument();
    expect(screen.queryByText(FOOTER_ASSERTION)).not.toBeInTheDocument();
  }, 30_000);

  it('披露齐全（passed）：页脚保持「逐个过 T5 输出审查后取回」，不得伪造第三态', async () => {
    downloadHeaders = { 'a.csv': 'passed; findings=0', 'b.csv': 'passed; findings=0' };
    renderWithProviders(<NotebookWorkbench />, { route: '/notebooks?task=n-1' });

    await downloadBothFiles();

    expect(await screen.findByText(FOOTER_ASSERTION)).toBeInTheDocument();
    expect(screen.queryByText(FOOTER_THIRD_STATE)).not.toBeInTheDocument();
  }, 30_000);

  it('一个 passed 一个缺失：页脚按文件计数（本轮 1 个），不是有/无开关', async () => {
    downloadHeaders = { 'a.csv': 'passed; findings=0', 'b.csv': null };
    renderWithProviders(<NotebookWorkbench />, { route: '/notebooks?task=n-1' });

    await downloadBothFiles();

    expect(await screen.findByText(/本轮 1 个文件未读取到输出审查披露/)).toBeInTheDocument();
    expect(screen.queryByText(/本轮 2 个文件/)).not.toBeInTheDocument();
    expect(screen.queryByText(FOOTER_ASSERTION)).not.toBeInTheDocument();
  }, 30_000);
});
