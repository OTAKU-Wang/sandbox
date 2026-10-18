// 首页（原型 1、首页-平台总览）KPI 带用例。
// 口径：卡名逐字照图；「今日操作日志」与 4-1 列表「共 N 条记录」同源同数（同一天区间 + 同一个 total）；
// 取数失败一律「未记录」，不得渲染 0；趋势卡没有分项数据源就不画序列。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import dayjs from 'dayjs';
import { renderWithProviders } from '../../../test/test-utils';
import Dashboard from '../index';
import api from '../../../services/api';

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

const STATS = {
  active_sessions: 4,
  today_contracts: 2,
  total_products: 30,
  total_audit_records: 9999,
  total_anchored: 7,
  inspection_pass_rate: 91.4,
};

/** 首页只读这些端点；audit 的当天总数与 notebooks 的任务总数都是「后端算好的 total」。 */
function okRoutes(url: string) {
  const path = String(url);
  if (path === '/audit/records') return Promise.resolve({ items: [], total: 186, page: 1, page_size: 1 });
  if (path === '/notebooks') return Promise.resolve({ items: [], total: 12, page: 1, page_size: 1 });
  if (path === '/deliveries/openapi/endpoints') {
    return Promise.resolve({
      items: [{ id: 'e-1', status: 'enabled' }, { id: 'e-2', status: 'disabled' }, { id: 'e-3', status: 'enabled' }],
      total: 3,
      page: 1,
      page_size: 100,
    });
  }
  if (path === '/monitoring/stats') return Promise.resolve(STATS);
  if (path === '/monitoring/policy-rejection') return Promise.resolve({ categories: [], total: 0, pass_rate: 100 });
  if (path === '/monitoring/security-distribution') return Promise.resolve([]);
  if (path === '/monitoring/recent-events') return Promise.resolve([]);
  return Promise.resolve({ items: [], total: 0, page: 1, page_size: 20 });
}

function cardOf(title: string): HTMLElement {
  const label = screen.getByText(title);
  const card = label.closest('.ant-card');
  if (!card) throw new Error(`没有找到「${title}」所在的卡片`);
  return card as HTMLElement;
}

beforeEach(() => {
  get.mockReset();
  get.mockImplementation(okRoutes);
});

describe('首页 KPI 带（原型 A-1）', () => {
  it('四张 KPI 卡名逐字照图，数字取后端 total', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('开发任务总数')).toBeInTheDocument());

    expect(screen.getByText('结果集导出接口')).toBeInTheDocument();
    expect(screen.getByText('今日数据调用次数')).toBeInTheDocument();
    expect(screen.getByText('今日操作日志')).toBeInTheDocument();

    // 开发任务总数 = /notebooks 的 total；导出接口 = 端点列表长度（在线 2 · 离线 1）
    await waitFor(() => expect(within(cardOf('开发任务总数')).getByText('12')).toBeInTheDocument());
    await waitFor(() => expect(within(cardOf('结果集导出接口')).getByText('3')).toBeInTheDocument());
    expect(within(cardOf('结果集导出接口')).getByText('在线 2 · 离线 1')).toBeInTheDocument();
  });

  it('今日操作日志与 4-1 同源同数：当天零点 date_from + page_size 1，值就是响应 total', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(within(cardOf('今日操作日志')).getByText('186')).toBeInTheDocument());

    const from = dayjs().startOf('day').toISOString();
    const calls = get.mock.calls
      .filter(([url]) => String(url) === '/audit/records')
      .map(([, config]) => config as { params: Record<string, unknown> });
    expect(calls.length).toBeGreaterThanOrEqual(1);
    for (const call of calls) {
      expect(call.params.date_from).toBe(from);
      // 后端 limit 上界 100，这里只要 1 行读 total，不能拿大 page_size 去整页拉
      expect(call.params.limit).toBe(1);
    }
    // 失败分项同区间
    expect(calls.some((call) => call.params.outcome === 'failure')).toBe(true);
  });

  it('审计取数失败时今日操作日志写「未记录」，不把失败伪装成 0', async () => {
    get.mockImplementation((url: string) => {
      const path = String(url);
      if (path === '/audit/records') {
        return Promise.reject({ code: 'INTERNAL_ERROR', message: 'boom', detail: null, request_id: 'r-1' });
      }
      return okRoutes(path);
    });

    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(within(cardOf('今日操作日志')).getByText('未记录')).toBeInTheDocument());

    const card = cardOf('今日操作日志');
    expect(within(card).queryByText('0')).toBeNull();
    expect(within(card).queryByText('186')).toBeNull();
  });

  // 不变量（与具体画法无关）：卡题/副标题逐字照图，但不得臆造图里那两个后端没有的系列，
  // 也不得补图上没有的 Y 轴刻度（改回原来的「任务趋势 (7天) + YAxis + 任务数」就变红）。
  it('趋势卡逐字照图给标题，但不臆造「调试次数 / 执行次数」两条序列，也不画 Y 轴刻度', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('近 7 天调试与执行趋势')).toBeInTheDocument());

    expect(screen.getByText('统计沙箱内在线调试运行与正式执行的次数，含失败重试')).toBeInTheDocument();
    const card = cardOf('近 7 天调试与执行趋势');
    expect(within(card).queryByText('调试次数')).toBeNull();
    expect(within(card).queryByText('执行次数')).toBeNull();
    expect(card.querySelector('.recharts-yAxis')).toBeNull();
    expect(card.querySelector('.recharts-bar-label-list')).toBeNull();
  });
});
