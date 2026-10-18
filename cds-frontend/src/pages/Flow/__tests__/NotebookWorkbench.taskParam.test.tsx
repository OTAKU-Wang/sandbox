// SS-18 任务详情入口：卡片跳来的 /notebooks?task=<id>&focus=runs 必须真的选中那个本子。
// 参数是刻意留在地址栏的（刷新/收藏都能回到同一个本子），只在用户手动换本子时清掉。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { useSearchParams } from 'react-router-dom';
import { renderWithProviders } from '../../../test/test-utils';
import NotebookWorkbench from '../NotebookWorkbench';
import api from '../../../services/api';

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
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const notebook = (id: string, title: string) => ({
  id,
  contract_id: 'c-1',
  title,
  path: `/nb/${id}.ipynb`,
  kernel: 'python3',
  language: 'python',
  content: { cells: [] },
  version: 1,
  status: 'draft',
  session_id: null,
  execution_backend: 'cds_kernel',
  last_executed_at: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z',
});

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 50 });

function UrlProbe() {
  const [search] = useSearchParams();
  return <span data-testid="url">{search.toString()}</span>;
}

function renderWorkbench(route: string) {
  renderWithProviders(
    <>
      <NotebookWorkbench />
      <UrlProbe />
    </>,
    { route },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  get.mockImplementation((url: string) => {
    if (url === '/notebooks') return Promise.resolve(page([notebook('n-1', '旧本子'), notebook('n-2', '流失评分')]));
    if (url === '/contracts') return Promise.resolve(page([]));
    if (url === '/workloads' || url === '/compute/runs') return Promise.resolve(page([]));
    if (url.startsWith('/notebooks/')) return Promise.resolve(notebook(url.split('/')[2], url.split('/')[2]));
    return Promise.resolve({});
  });
});

describe('NotebookWorkbench 的任务上下文参数', () => {
  it('带 ?task=n-2 进来：拉的是 n-2 的详情，参数留在地址栏（卡头显示它的文件名）', async () => {
    renderWorkbench('/notebooks?task=n-2&focus=runs');

    // 外框重排后卡头只显真文件名（图 1 口径），标题在文件名下拉里，所以这里锁 n-2.ipynb。
    expect(await screen.findByText('n-2.ipynb')).toBeInTheDocument();
    await waitFor(() => expect(get.mock.calls.map((call) => String(call[0]))).toContain('/notebooks/n-2'));
    expect(get.mock.calls.map((call) => String(call[0]))).not.toContain('/notebooks/n-1');
    expect(screen.getByTestId('url')).toHaveTextContent('task=n-2&focus=runs');
  });

  it('不带参数进来：不猜一个本子选中，也不发详情请求', async () => {
    renderWorkbench('/notebooks');

    expect(await screen.findByText(/还没有选中的调试对象/)).toBeInTheDocument();
    expect(screen.getByTestId('url').textContent).toBe('');
    expect(get.mock.calls.map((call) => String(call[0])).filter((url) => /^\/notebooks\/[^/]+$/.test(url))).toEqual([]);
  });

  it('卡头文件名下拉是开发任务切换器：选另一项就换详情并清掉 ?task=', async () => {
    renderWorkbench('/notebooks?task=n-1');

    expect(await screen.findByText('n-1.ipynb')).toBeInTheDocument();
    fireEvent.click(screen.getByText('n-1.ipynb'));
    fireEvent.click(await screen.findByText('流失评分'));

    await waitFor(() => expect(get.mock.calls.map((call) => String(call[0]))).toContain('/notebooks/n-2'));
    await waitFor(() => expect(screen.getByTestId('url').textContent).toBe(''));
  });
});
