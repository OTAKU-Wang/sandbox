// 首页（原型 1、首页-平台总览.png）的数据来源用例：每个数字要说得出它来自哪一次请求。
//
// 本文件钉住的是契约变更后的四条不变式（变更前那版首页在前端拼 6 个端点，已由后端聚合面取代）：
// · 整页只发一次 GET /flow/dashboard/home，旧拼装路径上的端点一个都不许再请求；
// · 后端给的 0 是合法值（照实显示），后端没给的数走「未记录」，两者不得互换；
// · 缺数/脏数据不画爆进度条、不出现 NaN 柱高、不拿 bundle_id 前缀冒充文件名；
// · 行序与入口链接由载荷决定，前端不再自行排序或编造跳转。
// 逐字照图（卡名/表头/说明行/反硬编码示例值）在同目录 __tests__/devHomeOverview.test.tsx。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../test/test-utils';
import FlowDevHome from './DevHome';
import { homeOverviewFixture } from './__tests__/homeFixtures';
import { useAuthStore } from '../../stores/authStore';
import api from '../../services/api';
import type { User } from '../../types/models';
import type { HomeOverview } from '../../services/flow';

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
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

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const LEGACY_ASSEMBLY_URLS = [
  '/notebooks',
  '/deliveries/openapi/endpoints',
  '/deliveries/quota-usage',
  '/deliveries/dataset/bundles',
  '/monitoring/task-trend',
  '/audit/records',
];

function serve(overview: HomeOverview) {
  get.mockImplementation(() => Promise.resolve(overview));
}

function requestedUrls(): string[] {
  return get.mock.calls.map(([url]) => String(url));
}

function barWidth(testId: string): string | undefined {
  const el = document.querySelector(`[data-testid="${testId}"] .ant-progress-bg`);
  return el ? (el as HTMLElement).style.width : undefined;
}

function barHeights(series: 'debug' | 'exec'): string[] {
  return Array.from(document.querySelectorAll(`[data-bar="${series}"]`)).map((el) => (el as HTMLElement).style.height);
}

function cardByTitle(title: string): HTMLElement {
  const host = screen.getByText(title).closest('.ant-card');
  if (!host) throw new Error(`找不到「${title}」所在卡片`);
  return host as HTMLElement;
}

async function renderHome(overview: HomeOverview = homeOverviewFixture()) {
  serve(overview);
  useAuthStore.setState({ user: { username: 'li-dev', role: 'buyer' } as User });
  renderWithProviders(<FlowDevHome />);
  await waitFor(() => expect(screen.getByTestId('kpi-tasks')).toBeInTheDocument());
}

beforeEach(() => {
  get.mockReset();
  navigate.mockReset();
});

describe('首页 · 数据来源与诚实降级', () => {
  it('整页只发一次聚合请求，旧的前端拼装端点一个都不再请求', async () => {
    await renderHome();

    expect(requestedUrls().filter((url) => url === '/flow/dashboard/home')).toHaveLength(1);
    for (const legacy of LEGACY_ASSEMBLY_URLS) {
      expect(requestedUrls()).not.toContain(legacy);
    }
  });

  it('四格主值逐字取载荷；进行中/已结束取后端计数而不是本页行数', async () => {
    const overview = homeOverviewFixture();
    expect(overview.recent_tasks).toHaveLength(3);
    await renderHome(overview);

    expect(within(screen.getByTestId('kpi-tasks')).getByText('11')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-tasks')).getByText('进行中 9 · 已结束 2')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-interfaces')).getByText('7')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-calls')).getByText('18')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-ops')).getByText('214')).toBeInTheDocument();
  });

  it('最近调用取 last_called_at；确实没有调用时大数字是真的 0，只有日期走未记录', async () => {
    const overview = homeOverviewFixture();
    overview.calls_today = { ...overview.calls_today, count: 0, failures: 0, last_called_at: null };
    await renderHome(overview);

    expect(within(screen.getByTestId('kpi-calls')).getByText('0')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-calls')).getByText('最近调用 未记录')).toBeInTheDocument();
  });

  it('操作日志可见性 self：可见行是真值的「失败 3」，口径写仅本账号、不冒充平台数', async () => {
    await renderHome(homeOverviewFixture());
    const kpi = screen.getByTestId('kpi-ops');
    expect(within(kpi).getByText('失败 3')).toBeInTheDocument();
    fireEvent.mouseEnter(within(kpi).getByText('今日操作日志'));
    await waitFor(() => expect(screen.getByText(/统计范围：仅本账号/)).toBeInTheDocument());
  });

  it('操作日志可见性 platform 时才写全平台', async () => {
    const platform = homeOverviewFixture();
    platform.ops_log_today = { ...platform.ops_log_today, visibility: 'platform' };
    await renderHome(platform);
    const kpi = screen.getByTestId('kpi-ops');
    fireEvent.mouseEnter(within(kpi).getByText('今日操作日志'));
    await waitFor(() => expect(screen.getByText(/统计范围：全平台/)).toBeInTheDocument());
  });

  it('后端给的 0 照实显示，不当成「未记录」隐藏起来', async () => {
    const overview = homeOverviewFixture();
    overview.export_interfaces = { ...overview.export_interfaces, total: 0, online: 0, offline: 0 };
    await renderHome(overview);

    expect(within(screen.getByTestId('kpi-interfaces')).getByText('0')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-interfaces')).getByText('在线 0 · 离线 0')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-interfaces')).queryByText('未记录')).toBeNull();
  });
});

describe('首页 · 任务 / 内存 / 趋势 / 结果集', () => {
  it('任务行序即载荷顺序，每行给出调试与详情入口', async () => {
    await renderHome();
    const card = cardByTitle('开发任务总览');
    const rows = Array.from(card.querySelectorAll('.ant-table-tbody tr.ant-table-row'));
    expect(rows.map((row) => row.querySelector('td')?.textContent)).toEqual(['客单价回归', '流失评分', '存量笔记本']);

    const links = (row: Element) => Array.from(row.querySelectorAll('a')).map((a) => a.getAttribute('href'));
    expect(links(rows[0])).toEqual(['/notebooks?task=n-2', '/compute', '/flow/tasks/n-2']);
    expect(links(rows[2])).toEqual(['/notebooks?task=n-3', '/compute', '/flow/tasks/n-3']);
  });

  it('没有任务时是真空态：0 行 + 真实 0，不补示例行', async () => {
    const overview = homeOverviewFixture();
    overview.recent_tasks = [];
    overview.dev_tasks = { ...overview.dev_tasks, total: 0, active: 0, ended: 0 };
    overview.task_filters = { all: 0, active: 0, ended: 0 };
    await renderHome(overview);

    const card = cardByTitle('开发任务总览');
    expect(card.querySelectorAll('.ant-table-tbody tr.ant-table-row')).toHaveLength(0);
    expect(card.textContent).toContain('还没有开发任务');
    expect(within(screen.getByTestId('kpi-tasks')).getByText('0')).toBeInTheDocument();
  });

  it('筛完为空只能说筛选为空，不能谎称「还没有开发任务」', async () => {
    const overview = homeOverviewFixture();
    // 把夹具里唯一一条 archived 任务抽掉 ⇒「已结束」页签计数为 0且行集为空。
    overview.recent_tasks = overview.recent_tasks.filter((task) => task.status !== 'archived');
    overview.dev_tasks = { ...overview.dev_tasks, ended: 0 };
    overview.task_filters = { ...overview.task_filters, ended: 0 };
    await renderHome(overview);

    const card = cardByTitle('开发任务总览');
    fireEvent.click(within(card).getByText('已结束 0'));
    await waitFor(() => expect(card.querySelector('.ant-empty')).not.toBeNull());
    expect(card.textContent).toContain('「已结束」里暂时没有任务');
    expect(card.textContent).not.toContain('还没有开发任务');
  });

  it('内存条宽度由载荷比值决定；任务档位上限不进分母', async () => {
    const overview = homeOverviewFixture();
    overview.memory.debug.reserved_mb = 3891;
    await renderHome(overview);

    expect(barWidth('memory-debug')).toBe('47%');
    expect(barWidth('memory-exec')).toBe('0%');
    // 32 G / 64 G 是单任务档位上限，不是这一格的分母
    expect(screen.queryByText(/32 G/)).toBeNull();
    expect(screen.queryByText(/64 G/)).toBeNull();
  });

  it('已分配超出配额时条子封顶 100%，不画负数也不画超界', async () => {
    const overview = homeOverviewFixture();
    overview.memory.debug.reserved_mb = 9999;
    await renderHome(overview);

    expect(barWidth('memory-debug')).toBe('100%');
  });

  it('趋势柱高按两组共用的 scaling 换算，计数 0 是 0px 而不是 NaN', async () => {
    await renderHome();

    // 满刻度 92px：原型 A-1 的图表带 y1020–1128（≈108 含轴标签），132px 会把整卡顶到 259 高
    expect(barHeights('debug')).toEqual(['0px', '92px', '18px', '0px', '0px', '74px', '37px']);
    expect(barHeights('exec')).toEqual(['0px', '37px', '0px', '0px', '0px', '0px', '18px']);
    for (const height of [...barHeights('debug'), ...barHeights('exec')]) {
      expect(height).not.toContain('NaN');
    }
  });

  it('全 0 序列不编造柱高，也不抛 NaN', async () => {
    const overview = homeOverviewFixture();
    overview.trend = {
      ...overview.trend,
      days: overview.trend.days.map((point) => ({ ...point, debug: 0, exec: 0 })),
    };
    await renderHome(overview);

    expect(barHeights('debug').every((height) => height === '0px')).toBe(true);
    expect(barHeights('exec').every((height) => height === '0px')).toBe(true);
  });

  it('结果集跳转按各自 contract；缺文件名时不拿包编号前缀冒充名字', async () => {
    await renderHome();
    const card = cardByTitle('最新结果集');
    const hrefs = Array.from(card.querySelectorAll('.ant-table-tbody a')).map((a) => a.getAttribute('href'));
    expect(hrefs).toEqual(['/deliveries?contract=c-1', '/deliveries?contract=c-2']);

    expect(within(card).getByText('delivery/b1a2c3d4.zip')).toBeInTheDocument();
    expect(within(card).queryByText(/b2c3d4e5/)).toBeNull();
    expect(within(card).getByText('8次')).toBeInTheDocument();
    expect(within(card).queryByText('0次')).toBeNull();
  });

  it('两个入口按钮跳整页任务页，不在首页内开抽屉', async () => {
    await renderHome();

    fireEvent.click(screen.getByRole('button', { name: /创建开发任务/ }));
    expect(navigate).toHaveBeenCalledWith('/flow/tasks/new');
    fireEvent.click(screen.getByRole('button', { name: /查看开发任务/ }));
    expect(navigate).toHaveBeenCalledWith('/flow/tasks');
  });
});
