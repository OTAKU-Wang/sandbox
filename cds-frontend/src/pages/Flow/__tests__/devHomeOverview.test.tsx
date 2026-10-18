// 首页（原型 1、首页-平台总览.png = A-1 转录）的「逐区块照图」用例：区块标题/表头/说明行逐字，
// 数字只能来自 GET /flow/dashboard/home，缺数渲染「未记录」，内存卡不自称实测占用，
// 趋势卡不得出现原型没有的 y 轴刻度与柱顶数值。数据来源与诚实降级另见同目录 DevHome.test.tsx。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import FlowDevHome from '../DevHome';
import { homeOverviewFixture } from './homeFixtures';
import { useAuthStore } from '../../../stores/authStore';
import api from '../../../services/api';
import type { User } from '../../../types/models';
import type { HomeOverview } from '../../../services/flow';

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

function serve(overview: HomeOverview) {
  get.mockImplementation(() => Promise.resolve(overview));
}

function cardByTitle(title: string): HTMLElement {
  const host = screen.getByText(title).closest('.ant-card');
  if (!host) throw new Error(`找不到「${title}」所在卡片`);
  return host as HTMLElement;
}

beforeEach(() => {
  get.mockReset();
  serve(homeOverviewFixture());
  useAuthStore.setState({ user: { username: 'li-dev', role: 'buyer' } as User });
});

describe('首页逐区块照图', () => {
  it('首页不自带大标题（顶带 = 面包屑），欢迎带两按钮、四张 KPI 的卡名与副标题逐字，数字全取响应', async () => {
    renderWithProviders(<FlowDevHome />);
    // strict-1-home.md §2 行 48 / §5 行 152：顶部那条带是面包屑（墨高 14 + 前置图标），
    // 由 PageCrumb 统一渲染，页面自身不得再画 24px 「首页」标题；首块 = C1 欢迎卡。
    await waitFor(() => expect(screen.getByTestId('home-welcome')).toBeInTheDocument());
    expect(screen.queryByTestId('home-title')).toBeNull();
    expect(screen.queryByText('首页')).toBeNull();

    const welcome = screen.getByTestId('home-welcome');
    expect(within(welcome).getByText('欢迎回来，li-dev')).toBeInTheDocument();
    expect(within(welcome).getByText('查看开发任务')).toBeInTheDocument();
    expect(within(welcome).getByText('创建开发任务')).toBeInTheDocument();

    expect(within(screen.getByTestId('kpi-tasks')).getByText('开发任务总数')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-tasks')).getByText('11')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-tasks')).getByText('进行中 9 · 已结束 2')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-interfaces')).getByText('结果集导出接口')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-interfaces')).getByText('7')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-interfaces')).getByText('在线 4 · 离线 3')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-calls')).getByText('今日数据调用次数')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-calls')).getByText('18')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-calls')).getByText('最近调用 2026-09-30')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-ops')).getByText('今日操作日志')).toBeInTheDocument();
    expect(within(screen.getByTestId('kpi-ops')).getByText('214')).toBeInTheDocument();
    // 可见副行按真值只有「失败 3」；口径那句（审计明细端点对本角色 403，这一格不是平台数）
    // 收进卡名 Tooltip，文案本身由 homeModel.test.ts 钉住。
    expect(within(screen.getByTestId('kpi-ops')).getByText('失败 3')).toBeInTheDocument();
  });

  it('原型示例值一个都不在屏上（证明数字不是抄图上的假数据）', async () => {
    renderWithProviders(<FlowDevHome />);
    await waitFor(() => expect(screen.getByTestId('kpi-tasks')).toBeInTheDocument());
    for (const slop of ['张伟', '1,204 次', '2026-08-12', '2026-08-12 10:42', '沈阳数据中心', '用户行为分析模型']) {
      expect(screen.queryByText(slop)).toBeNull();
    }
  });

  it('开发任务总览：表头逐字、筛选标签计数取后端、点「已结束」只剩归档行', async () => {
    renderWithProviders(<FlowDevHome />);
    const card = await waitFor(() => cardByTitle('开发任务总览'));
    expect(within(card).getByText('仅展示最近创建的 5 个任务')).toBeInTheDocument();
    expect(within(card).getByText('查看全部任务')).toBeInTheDocument();
    for (const head of ['任务名称', '状态', '调试方法', '创建时间', '操作']) {
      expect(within(card).getAllByText(head).length).toBeGreaterThan(0);
    }
    expect(within(card).getByText('全部 11')).toBeInTheDocument();
    expect(within(card).getByText('进行中 9')).toBeInTheDocument();
    expect(within(card).getByText('已结束 2')).toBeInTheDocument();

    expect(within(card).getByText('客单价回归')).toBeInTheDocument();
    expect(within(card).getAllByText('进行中')).toHaveLength(2);
    // 原型时间写法 `2026-08-12 10:30`：ISO 短横 + 分钟，不是 toLocaleString 的斜杠式
    expect(within(card).getAllByText((text) => /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(text))).toHaveLength(3);
    fireEvent.click(within(card).getByText('已结束 2'));
    await waitFor(() => expect(within(card).queryByText('客单价回归')).toBeNull());
    expect(within(card).getByText('存量笔记本')).toBeInTheDocument();
    expect(within(card).getAllByText('已结束')).toHaveLength(1);
  });

  it('内存卡说真话：分子是已分配的 3.8 G，分母是 8 G 配额，不出现「实测」，16 G 档标未记录', async () => {
    renderWithProviders(<FlowDevHome />);
    const card = await waitFor(() => cardByTitle('沙箱内存使用'));
    expect(within(card).queryByText(/实时占用/)).toBeNull();

    const debug = screen.getByTestId('memory-debug');
    expect(within(debug).getByText('调试内存使用')).toBeInTheDocument();
    expect(within(debug).getByText('3.8 G')).toBeInTheDocument();
    expect(within(debug).getByText(/\/ 8 G 配额/)).toBeInTheDocument();
    expect(within(debug).getByText('占用中')).toBeInTheDocument();
    expect(within(debug).getByText('0 G')).toBeInTheDocument();
    expect(within(debug).getByText('4 G')).toBeInTheDocument();
    expect(within(debug).getByText('8 G')).toBeInTheDocument();

    const exec = screen.getByTestId('memory-exec');
    expect(within(exec).getByText('待执行')).toBeInTheDocument();
    // 真值 §5 的执行段只有 标题/徽标/大数字/配额/空条/刻度：「运行中·排队」与「16 G 独立配额未记录」
    // 都不再占版面，但一字不丢 —— 全文在配额数字的 Tooltip 里。
    fireEvent.mouseOver(within(exec).getByText(/\/ 8 G 配额/));
    await waitFor(() => expect(screen.getByText(/运行中 0 · 排队 2/)).toBeInTheDocument());
    expect(screen.getByText(/16 G 独立配额未记录/)).toBeInTheDocument();

    // 可见副标题收成与真值同长的一句，完整口径与后端 reason 在 Tooltip 里
    expect(within(card).getByText('调试与执行环境内存占用')).toBeInTheDocument();
    fireEvent.mouseOver(within(card).getByText('调试与执行环境内存占用'));
    await waitFor(() => expect(screen.getByText(/非进程实测占用/)).toBeInTheDocument());

    expect(within(card).getByText('调试内存随调试会话占用，退出调试环境后自动释放')).toBeInTheDocument();
    expect(within(card).getByText('执行内存在任务正式执行时占用，执行完成后自动释放')).toBeInTheDocument();
  });

  it('趋势卡：两组柱各 7 根、x 轴只有 MM/DD，没有原型图上不存在的数值标注', async () => {
    const { container } = renderWithProviders(<FlowDevHome />);
    const card = await waitFor(() => cardByTitle('沙箱运行与执行次数趋势'));
    expect(within(card).getByText('统计沙箱内在线调试运行与正式执行的次数，含失败重试')).toBeInTheDocument();
    expect(within(card).getByText('调试次数')).toBeInTheDocument();
    expect(within(card).getByText('执行次数')).toBeInTheDocument();

    const chart = screen.getByTestId('trend-chart');
    expect(within(chart).getAllByTestId('trend-x-label')).toHaveLength(7);
    expect(within(chart).getByText('10/12')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-bar="debug"]')).toHaveLength(7);
    expect(container.querySelectorAll('[data-bar="exec"]')).toHaveLength(7);
    expect(within(chart).queryByText('5')).toBeNull();
    expect(within(chart).queryByText('2')).toBeNull();
  });

  it('最新结果集：表头逐字，调用次数带口径，缺文件名/缺状态渲染未记录', async () => {
    renderWithProviders(<FlowDevHome />);
    const card = await waitFor(() => cardByTitle('最新结果集'));
    expect(within(card).getByText('按合约约定通过 OpenApi 导出的结果集')).toBeInTheDocument();
    expect(within(card).getByText('前往我的结果集')).toBeInTheDocument();
    for (const head of ['结果集名称', '生成时间', '调用次数', '状态', '操作']) {
      expect(within(card).getAllByText(head).length).toBeGreaterThan(0);
    }
    expect(within(card).getByText('delivery/b1a2c3d4.zip')).toBeInTheDocument();
    expect(within(card).getByText('8次')).toBeInTheDocument();
    expect(within(card).getByText('正常')).toBeInTheDocument();
    expect(within(card).getAllByText('API详情')).toHaveLength(2);

    const unrecorded = within(card).getAllByText('未记录');
    expect(unrecorded.length).toBeGreaterThanOrEqual(4);
    // 卡底脚注真值图上没有（§6：卡底即表格）⇒ 整句仍在，只是搬进标题的 Tooltip，一字不丢
    fireEvent.mouseOver(within(card).getByText('最新结果集'));
    await waitFor(() =>
      expect(
        screen.getByText('调用次数口径：该合约下 OpenAPI 接口的累计调用次数；今日操作日志与本页所有数字均为当前账号口径。'),
      ).toBeInTheDocument(),
    );
  });

  it('配额取不到时画空条并写「未记录 配额」，不假装还有 8 G 可用', async () => {
    const fixture = homeOverviewFixture();
    fixture.memory = { ...fixture.memory, quota_mb: null };
    serve(fixture);

    renderWithProviders(<FlowDevHome />);
    await waitFor(() => expect(screen.getByTestId('memory-debug')).toBeInTheDocument());
    expect(screen.getAllByText(/未记录 配额/)).toHaveLength(2);
    expect(screen.getAllByText('配额刻度未记录')).toHaveLength(2);
  });

  it('接口失败时整页走错误态并给重试，不渲染半成品', async () => {
    get.mockImplementation(() => Promise.reject({ code: 'HTTP_403', message: 'Insufficient permissions', detail: null, request_id: 'r-1' }));
    renderWithProviders(<FlowDevHome />);
    await waitFor(() => expect(screen.getByText('首页数据加载失败')).toBeInTheDocument());
    expect(screen.queryByTestId('kpi-tasks')).toBeNull();
  });
});
