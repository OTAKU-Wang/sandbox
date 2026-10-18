// 首页（原型 A-1）开发任务 KPI 带 + 趋势卡的落地用例：
// ① 三个卡名逐字照图；②「今日操作日志」与 4-1 审计页同源（同一天区间 + 同一个 total，page_size 只要 1）；
// ③ 403 写「无权限」而不是 0；④ 趋势数据点只能来自 /monitoring/task-trend 真序列，
//    且不带原型上没有的 Y 轴刻度与柱顶数值，空序列走空态而不是补 0。
//
// 放在 Flow/__tests__ 下（文件名匹配 devTask*）：这是本次任务允许的测试目录，被测对象是首页 A-1 的
// 开发任务 KPI 带，与 pages/Dashboard/__tests__/Dashboard.test.tsx 的其余用例互补、不重复。
//
// jsdom 里 recharts 的 ResponsiveContainer 永远量不到尺寸（0×0，画不出可断言的 SVG），
// 所以这里把 recharts 换成记录型替身：断言落在「画了哪些图部件、喂进去的数据是不是后端那份」
// 与页面真实文本上，不假装量到了像素。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import dayjs from 'dayjs';
import { renderWithProviders } from '../../../test/test-utils';
import Dashboard from '../../Dashboard';
import api from '../../../services/api';

const chartSpy = vi.hoisted(() => ({
  charts: [] as Array<{ name: string; data: Array<Record<string, unknown>>; parts: string[]; barLabel: unknown }>,
}));

vi.mock('recharts', async () => {
  const React = await import('react');
  const make = (name: string) => {
    const Comp = (props: { children?: import('react').ReactNode; data?: unknown; label?: unknown }) => {
      if (name === 'BarChart' || name === 'LineChart' || name === 'PieChart' || name === 'AreaChart') {
        chartSpy.charts.push({
          name,
          data: Array.isArray(props.data) ? (props.data as Array<Record<string, unknown>>) : [],
          parts: [],
          barLabel: undefined,
        });
      }
      const current = chartSpy.charts[chartSpy.charts.length - 1];
      if (current) {
        current.parts.push(name);
        if (name === 'Bar') current.barLabel = props.label;
      }
      return React.createElement('div', { 'data-chart-part': name }, props.children ?? null);
    };
    Comp.displayName = name;
    return Comp;
  };
  const names = ['XAxis', 'YAxis', 'CartesianGrid', 'Tooltip', 'ResponsiveContainer', 'Legend',
    'PieChart', 'Pie', 'Cell', 'BarChart', 'Bar', 'LineChart', 'Line', 'AreaChart', 'Area'];
  const mod: Record<string, unknown> = {};
  for (const name of names) mod[name] = make(name);
  return mod;
});

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
/** 后端真下发的每日序列（合并计数，空白日由后端补 0 后一并下发）。 */
const TREND = [
  { date: '2026-08-06', count: 0 },
  { date: '2026-08-07', count: 2 },
  { date: '2026-08-08', count: 5 },
  { date: '2026-08-09', count: 1 },
  { date: '2026-08-10', count: 0 },
  { date: '2026-08-11', count: 3 },
  { date: '2026-08-12', count: 7 },
];
const FORBIDDEN = { code: 'HTTP_403', message: 'Insufficient permissions', detail: 'Insufficient permissions', request_id: 'r-403' };

function okRoutes(url: string) {
  const path = String(url);
  if (path === '/audit/records') return Promise.resolve({ items: [], total: 186, page: 1, page_size: 1 });
  if (path === '/notebooks') return Promise.resolve({ items: [], total: 12, page: 1, page_size: 1 });
  if (path === '/deliveries/openapi/endpoints') {
    return Promise.resolve({ items: [{ id: 'e-1', status: 'enabled' }, { id: 'e-2', status: 'disabled' }], total: 2, page: 1, page_size: 100 });
  }
  if (path === '/monitoring/stats') return Promise.resolve(STATS);
  if (path === '/monitoring/task-trend') return Promise.resolve(TREND);
  if (path === '/monitoring/policy-rejection') return Promise.resolve({ categories: [], total: 0, pass_rate: 100 });
  if (path === '/monitoring/security-distribution') return Promise.resolve([]);
  if (path === '/monitoring/recent-events') return Promise.resolve([]);
  return Promise.resolve({ items: [], total: 0, page: 1, page_size: 20 });
}

function cardOf(title: string): HTMLElement {
  const card = screen.getByText(title).closest('.ant-card');
  if (!card) throw new Error(`没有找到「${title}」所在的卡片`);
  return card as HTMLElement;
}

/** 趋势图 = 喂了带 date 的那份序列的图件（安全等级分布那张也用 BarChart，按数据区分）。 */
function trendCharts() {
  return chartSpy.charts.filter((chart) => chart.data.length > 0 && 'date' in chart.data[0]);
}

beforeEach(() => {
  chartSpy.charts.length = 0;
  get.mockReset();
  get.mockImplementation(okRoutes);
});

describe('首页 A-1 开发任务 KPI 带与趋势卡', () => {
  it('三个 KPI 卡名逐字照图，值取后端 total', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('开发任务总数')).toBeInTheDocument());
    expect(screen.getByText('今日操作日志')).toBeInTheDocument();
    expect(screen.getByText('结果集导出接口')).toBeInTheDocument();

    await waitFor(() => expect(within(cardOf('今日操作日志')).getByText('186')).toBeInTheDocument());
    await waitFor(() => expect(within(cardOf('开发任务总数')).getByText('12')).toBeInTheDocument());
  });

  it('今日操作日志与 4-1 同源：当天零点 date_from + limit 1，值就是响应 total，不去调统计端点凑数', async () => {
    const dayStart = dayjs().startOf('day').toISOString();
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(within(cardOf('今日操作日志')).getByText('186')).toBeInTheDocument());

    const params = get.mock.calls
      .filter(([url]) => String(url) === '/audit/records')
      .map(([, config]) => (config as { params: Record<string, unknown> }).params);
    expect(params.length).toBeGreaterThanOrEqual(1);
    for (const one of params) {
      // 与 4-1 审计页同一条口径：date_from = 当天零点（浏览器本地日切，ISO 带时区偏移），limit 只要 1 行
      expect(one.date_from).toBe(dayStart);
      expect(one.limit).toBe(1);
    }
    // 不许另找一个统计端点凑这个数
    expect(get.mock.calls.some(([url]) => /statistics|summary|count/i.test(String(url)))).toBe(false);
  });

  it('审计与开发任务端点回 403 时写「无权限」，既不写 0 也不混进「未记录」', async () => {
    get.mockImplementation((url: string) => {
      const path = String(url);
      if (path === '/audit/records' || path === '/notebooks') return Promise.reject(FORBIDDEN);
      return okRoutes(path);
    });

    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(within(cardOf('今日操作日志')).getByText('无权限')).toBeInTheDocument());
    const auditCard = cardOf('今日操作日志');
    expect(within(auditCard).queryByText('0')).toBeNull();
    expect(within(auditCard).queryByText('未记录')).toBeNull();
    await waitFor(() => expect(within(cardOf('开发任务总数')).getByText('无权限')).toBeInTheDocument());
  });

  it('趋势图画后端真序列，且不带头图里没有的 Y 轴刻度与柱顶数值', async () => {
    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('近 7 天调试与执行趋势')).toBeInTheDocument());
    await waitFor(() => expect(trendCharts().length).toBe(1));

    const chart = trendCharts()[0];
    // 数据点就是 /monitoring/task-trend 返回的那份序列，原样喂进去，前端不增删不改值
    expect(chart.data).toEqual(TREND);
    // 原型图只有日期刻度与系列名：没有 Y 轴，也没有柱顶数值
    expect(chart.parts).not.toContain('YAxis');
    expect(chart.barLabel).toBeUndefined();
    expect(chart.parts).toContain('XAxis');
    // 图上这条图例是 `调试次数`/`执行次数` 两块（group-A.md 趋势卡），但后端
    // /monitoring/task-trend 只下发一份合并日计数 → 拆两块就是造假序列；
    // 口径由图自己的副标题承载，图例位不放自造的字符串。
    expect(chart.parts.filter((part) => part === 'Bar')).toHaveLength(1);
    expect(screen.getByText('统计沙箱内在线调试运行与正式执行的次数，含失败重试')).toBeInTheDocument();
  });

  it('后端返回空序列时趋势卡走空态，不拿 0 拼一条出来', async () => {
    get.mockImplementation((url: string) => {
      const path = String(url);
      if (path === '/monitoring/task-trend') return Promise.resolve([]);
      return okRoutes(path);
    });

    renderWithProviders(<Dashboard />);
    await waitFor(() => expect(screen.getByText('近 7 天调试与执行趋势')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/这 7 天没有趋势数据/)).toBeInTheDocument());
    expect(trendCharts()).toHaveLength(0);
  });
});
