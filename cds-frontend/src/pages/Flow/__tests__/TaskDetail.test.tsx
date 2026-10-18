// 锁定 2-4 任务详情的图规格（卡序、表头逐列照图）与"数字只来自接口"这条底线：
// 夹具给真后端字段名（Notebook / DatasetBundleRow），断言的是页面上出现什么、没出现什么。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { renderWithProviders } from '../../../test/test-utils';
import api from '../../../services/api';
import { useAuthStore } from '../../../stores/authStore';
import type { User } from '../../../types/models';
import FlowTaskDetail from '../TaskDetail';
import {
  BUNDLE_EMPTY,
  BUNDLE_PACKAGED,
  CONTRACT,
  HOME_MEMORY_UNMEASURED,
  NOTEBOOK,
  PRODUCT,
  RUNNING_SESSION,
  USER_OPTIONS,
  emptyPage,
  pageOf,
} from './devFixtures';

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = api.get as ReturnType<typeof vi.fn>;
const post = api.post as ReturnType<typeof vi.fn>;

interface Fixture {
  notebook?: Record<string, unknown>;
  /** GET /contracts/{id}：undefined = 用默认合约，null = 取不回。 */
  contract?: Record<string, unknown> | null;
  bundles?: unknown[];
  /** null = 这份产品当前账号取不回。 */
  product?: Record<string, unknown> | null;
  sessions?: unknown[];
  orgOptions?: { id: string; username: string; role: string; organization: string | null }[];
}

function mockBackend(fx: Fixture = {}) {
  get.mockImplementation((url: string) => {
    if (url.startsWith('/notebooks/')) return Promise.resolve(fx.notebook ?? NOTEBOOK);
    if (url.startsWith('/contracts/')) {
      if (fx.contract === null) return Promise.reject(new Error('合约取不回'));
      return Promise.resolve(fx.contract ?? CONTRACT);
    }
    if (url.startsWith('/contracts')) return Promise.resolve(emptyPage);
    if (url.startsWith('/flow/dashboard/home')) return Promise.resolve(HOME_MEMORY_UNMEASURED);
    if (url.startsWith('/deliveries/dataset/bundles')) return Promise.resolve(pageOf(fx.bundles ?? [BUNDLE_PACKAGED, BUNDLE_EMPTY]));
    if (url.startsWith('/data-products/')) {
      return fx.product === null ? Promise.reject(new Error('无权查看')) : Promise.resolve(fx.product ?? PRODUCT);
    }
    if (url.startsWith('/users/options')) return Promise.resolve(fx.orgOptions ?? USER_OPTIONS);
    if (url.startsWith('/sandbox-sessions')) return Promise.resolve(pageOf(fx.sessions ?? []));
    if (url.startsWith('/workloads')) return Promise.resolve(pageOf([]));
    if (url.startsWith('/compute/runs')) return Promise.resolve(pageOf([]));
    return Promise.resolve(emptyPage);
  });
}

/** 合约下发授权清单的变体（product_ids 为 null 表示后端没下发这一项）。 */
function withContract(productIds: string[] | null, over: Record<string, unknown> = {}) {
  return { ...CONTRACT, product_ids: productIds, ...over };
}

function withNotebook(over: Record<string, unknown> = {}) {
  return { ...NOTEBOOK, ...over };
}

function renderDetail() {
  // useParams 要真拿到 taskId，所以套一层 Routes（否则查询被 disable，页面永远在 spin）
  return renderWithProviders(
    <Routes>
      <Route path="/flow/tasks/:taskId" element={<FlowTaskDetail />} />
    </Routes>,
    { route: '/flow/tasks/n-1' },
  );
}

function LocationProbe() {
  const location = useLocation();
  return <div>落在 {location.pathname}{location.search}</div>;
}

function renderWithLocation() {
  return renderWithProviders(
    <Routes>
      <Route path="/flow/tasks/:taskId" element={<FlowTaskDetail />} />
      <Route path="/flow/tasks" element={<div>任务列表</div>} />
      <Route path="/notebooks" element={<LocationProbe />} />
    </Routes>,
    { route: '/flow/tasks/n-1' },
  );
}

function cardOf(title: string): HTMLElement {
  const node = screen.getByText(title).closest('.ant-card');
  if (!node) throw new Error(`卡片不存在: ${title}`);
  return node as HTMLElement;
}

/** 逐列取表头文字（tooltip 包着的列名取的是 textContent，不受影响）。 */
function headerTitles(root: HTMLElement): string[] {
  return Array.from(root.querySelectorAll('thead th')).map((th) => th.textContent ?? '');
}

/** 逐列取第一行的文字（按 td 顺序）。 */
function firstRowCells(root: HTMLElement): string[] {
  const row = root.querySelector('tbody tr.ant-table-row');
  if (!row) return [];
  return Array.from(row.querySelectorAll('td')).map((td) => (td.textContent ?? '').trim());
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  vi.spyOn(window.console, 'warn').mockImplementation(() => undefined);
  useAuthStore.setState({ user: { id: 'u-1', username: 'buyer', email: '', role: 'buyer', organization: '沈阳数据中心R' } as User });
  mockBackend();
});

// antd v5 组件在 jsdom 里需要 matchMedia
beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      media: query,
      matches: false,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
});

describe('任务详情页按原型渲染', () => {
  it('八张卡自上而下逐张照图（含卡题旁的灰字说明）', async () => {
    mockBackend({ notebook: withNotebook({ contract_id: null }) });
    renderDetail();
    await screen.findByRole('heading', { name: '销售分析' });
    const heads = Array.from(document.querySelectorAll('.ant-card-head-title'))
      .map((node) => node.textContent ?? '')
      // 「资源使用情况」是资源配置卡里的内层小卡，单独断言
      .filter((text) => text !== '资源使用情况');
    expect(heads).toEqual([
      '基本信息',
      '我的合约合约用于约束模型输出格式与导出通道',
      '资源配置内存配额将应用于调试与执行阶段',
      '关联数据已授权到沙箱的数据产品，详情信息为只读',
      '运行记录记录调试与正式执行的历史，最多保留 90 天',
      '结果集按合约约定，结果集已通过 OpenApi 导出至「我的结果集」；',
    ]);
    expect(screen.getByText('资源使用情况')).toBeInTheDocument();
  });

  it('卡 A：返回列表白底描边灰字钮、进入调试蓝 ghost 描边钮（1:1 裁读），执行任务才是实心主钮', async () => {
    renderDetail();
    const back = (await screen.findByText('返回列表')).closest('button');
    const debug = screen.getByText('进入调试').closest('button');
    const run = screen.getByText('执行任务').closest('button');
    expect(back).not.toBeNull();
    // 图上两颗副钮都有描边框：返回是无色描边，调试是蓝 ghost（antd primary-ghost）
    expect(back!).not.toHaveClass('ant-btn-text');
    expect(back!).not.toHaveClass('ant-btn-primary');
    expect(debug).toHaveClass('ant-btn-primary');
    expect(debug).toHaveClass('ant-btn-background-ghost');
    expect(run).toHaveClass('ant-btn-primary');
    expect(run!).not.toHaveClass('ant-btn-background-ghost');
    // 图标按图：返回左箭头，调试是方框箭头（Import），执行是实心播放三角
    expect(back!.querySelector('.anticon-arrow-left')).not.toBeNull();
    expect(debug!.querySelector('.anticon-import')).not.toBeNull();
    expect(run!.querySelector('.anticon-caret-right')).not.toBeNull();
  });

  it('进入调试去带 task 的调试台，返回列表回 /flow/tasks', async () => {
    mockBackend({ notebook: withNotebook({ contract_id: null }) });
    const first = renderWithLocation();
    await screen.findByRole('heading', { name: '销售分析' });
    fireEvent.click(screen.getByText('进入调试'));
    expect(await screen.findByText('落在 /notebooks?task=n-1')).toBeInTheDocument();
    first.unmount();

    renderWithLocation();
    fireEvent.click(await screen.findByText('返回列表'));
    expect(await screen.findByText('任务列表')).toBeInTheDocument();
  });

  it('执行任务真调 POST /notebooks/{id}/run，并把这次运行如实记进运行记录', async () => {
    post.mockResolvedValue({
      results: [
        { ok: true, backend: 'cds_kernel', stateful: true, stdout: '', stderr: '', repr: null, exit_code: 0, output_blocked: false, cell_index: 0 },
        { ok: true, backend: 'cds_kernel', stateful: true, stdout: '', stderr: '', repr: null, exit_code: 0, output_blocked: false, cell_index: 1 },
      ],
      passed: true,
    });
    renderDetail();
    fireEvent.click(await screen.findByText('执行任务'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/notebooks/n-1/run', undefined, expect.anything()));
    const table = await within(cardOf('运行记录')).findByText('整本运行（2 个单元格） · 后端 cds_kernel');
    expect(table).toBeInTheDocument();
    expect(within(cardOf('运行记录')).getByText('执行成功')).toBeInTheDocument();
  });

  it('执行任务失败：409 NOTEBOOK_NO_CELLS 原样提示，不吞错', async () => {
    post.mockRejectedValue({
      response: { status: 409, data: { code: 'NOTEBOOK_NO_CELLS', message: '任务里还没有可运行的单元格', detail: null, request_id: 'r-1' } },
    });
    renderDetail();
    fireEvent.click(await screen.findByText('执行任务'));
    // 错语留在卡 A 里（瞬时 toast 不能当唯一披露），且 code 原样带上
    expect(await screen.findByText(/执行任务失败：任务里还没有可运行的单元格（NOTEBOOK_NO_CELLS）/)).toBeInTheDocument();
  });

  it('四个统计小卡按图排序，数值取后端真值', async () => {
    renderDetail();
    await screen.findByRole('heading', { name: '销售分析' });
    const titles = Array.from(document.querySelectorAll('.t24-tile .ant-statistic-title')).map((node) => node.textContent);
    expect(titles).toEqual(['任务状态', '模型调试方法', '关联数据产品', '产出结果集']);
    const stats = Array.from(document.querySelectorAll('.t24-tile .ant-statistic-content'));
    expect(stats[0].textContent).toBe('进行中');
    expect(stats[1].textContent).toBe('未记录'); // notebook.debug_method=null → 不替它默认 Python
    expect(stats[2].textContent).toBe('1个'); // 合约授权 1 个数据产品
    expect(stats[3].textContent).toBe('2个'); // 交付包接口 total=2
  });

  it('基本信息四项逐字照图；所属节点取登录主体的组织；图上无 info 图标（口径进悬浮层）', async () => {
    mockBackend({ notebook: withNotebook({ task_no: 'TASK-20261017-013', description: '按周聚合门店流水', debug_method: 'jupyter' }) });
    renderDetail();
    const info = await waitFor(() => cardOf('基本信息'));
    for (const label of ['任务名称', '任务编号', '所属节点', '任务描述']) {
      expect(within(info).getByText(label)).toBeInTheDocument();
    }
    expect(within(info).getByText('TASK-20261017-013')).toBeInTheDocument();
    expect(within(info).getByText('按周聚合门店流水')).toBeInTheDocument();
    expect(within(info).getByText('沈阳数据中心R')).toBeInTheDocument();
    // 图 C 无任何 info 图标（2-4-measured §12.4「多图标」判为偏差）：口径改成 label 悬浮，卡内图标数钉死为 0
    expect(info.querySelectorAll('.anticon-info-circle')).toHaveLength(0);
    expect(within(info).queryByText(/沙箱引擎/)).not.toBeInTheDocument();
    expect(within(info).queryByText(/执行后端/)).not.toBeInTheDocument();
  });

  it('基本信息缺字段照样渲染：缺的记「未记录」，编号退回 notebook.id', async () => {
    renderDetail();
    const info = await waitFor(() => cardOf('基本信息'));
    expect(within(info).getByText('销售分析')).toBeInTheDocument();
    expect(within(info).getAllByText('未记录').length).toBe(2); // 任务编号 + 任务描述
    expect(await screen.findByText(/# n-1 · 创建时间/)).toBeInTheDocument();
    expect(await screen.findByText(/最近执行 未记录/)).toBeInTheDocument();
  });

  it('资源配置：配额是后端真值（灰底只读盒 + G 片），内存使用零填充 + 右侧「未计量 / 配额」', async () => {
    renderDetail();
    const card = await waitFor(() => cardOf('资源配置'));
    const labels = Array.from(card.querySelectorAll('.t24-quota-label')).map((n) => n.textContent);
    expect(labels).toEqual(['调试环境内存（G）', '执行环境内存（G）']);
    const boxes = Array.from(card.querySelectorAll('.t24-quota-box'));
    expect(boxes.map((n) => n.textContent)).toEqual(['8G', '16G']);
    expect(boxes[0].querySelector('.t24-quota-g')?.textContent).toBe('G');
    expect(within(card).getByText('用于在线调试、断点运行与日志查看')).toBeInTheDocument();
    expect(within(card).getByText('用于全量任务正式执行环境')).toBeInTheDocument();
    expect(within(card).getByText('资源使用情况')).toBeInTheDocument();
    expect(within(card).getByText('调试内存使用')).toBeInTheDocument();
    expect(within(card).getByText('执行内存使用')).toBeInTheDocument();
    // 进度条按图画两条，但一条填充都没有：使用量没有采样源，画 47.5% 就是伪造
    const bars = card.querySelectorAll('.ant-progress');
    expect(bars).toHaveLength(2);
    expect(Array.from(card.querySelectorAll('.ant-progress-bg')).every((bg) => (bg as HTMLElement).style.width === '0%')).toBe(true);
    expect(within(card).queryByText(/47\.5%/)).not.toBeInTheDocument();
    expect(within(card).getByText('未计量 / 8 G')).toBeInTheDocument();
    expect(within(card).getByText('未计量 / 16 G')).toBeInTheDocument();
    // 后端为什么没有分子：悬浮层里是后端原话（home.not_collected: memory.measured），不是本页自己编的理由
    fireEvent.mouseEnter(card.querySelectorAll('.ant-progress')[0] as Element);
    expect(await screen.findByText(/get_storage_metrics/)).toBeInTheDocument();
  });

  it('「沙箱环境运行中」只认真查到的 running 会话，没有就说没有', async () => {
    mockBackend({ notebook: withNotebook({ session_id: 's-1' }), sessions: [RUNNING_SESSION] });
    const { unmount } = renderDetail();
    const card = await waitFor(() => cardOf('资源配置'));
    expect(within(card).getByText('沙箱环境运行中，退出调试后自动释放内存')).toBeInTheDocument();
    expect(within(card).queryByText(/当前无活跃调试会话/)).not.toBeInTheDocument();
    unmount();

    mockBackend({ notebook: withNotebook({ session_id: 's-1' }), sessions: [] });
    renderDetail();
    const idle = await waitFor(() => cardOf('资源配置'));
    expect(within(idle).getByText('当前无活跃调试会话，没有待释放的内存')).toBeInTheDocument();
    expect(within(idle).queryByText(/沙箱环境运行中/)).not.toBeInTheDocument();
  });

  it('合约不在列表第一页也要按 id 取回（现网 total 22 > 列表上限 20，靠列表会误判成没合约）', async () => {
    renderDetail();
    const card = await waitFor(() => cardOf('我的合约'));
    expect(get).toHaveBeenCalledWith('/contracts/c-1');
    expect(within(card).getByText('零售流水合约')).toBeInTheDocument();
    // 结果导出方式取合约 flow-config 的 delivery_methods（三枚只读盒，不是表）
    const boxes = card.querySelectorAll('.t24-field-box');
    expect(boxes).toHaveLength(3);
    expect(boxes[2].textContent).toBe('API 导出');
  });

  it('结果导出方式：flow-config 未下发记「未记录」，下发了但为空记「—」', async () => {
    mockBackend({ contract: withContract(['p-1'], { delivery_methods: null }) });
    const { unmount } = renderDetail();
    const card = await waitFor(() => cardOf('我的合约'));
    expect(card.querySelectorAll('.t24-field-box')[2].textContent).toBe('未记录');
    unmount();

    mockBackend({ contract: withContract(['p-1'], { delivery_methods: [] }) });
    renderDetail();
    const blank = await waitFor(() => cardOf('我的合约'));
    expect(blank.querySelectorAll('.t24-field-box')[2].textContent).toBe('—');
  });

  it('关联数据七列逐字照图：组织名称按 provider_id 解析，产品类型给人读名', async () => {
    renderDetail();
    const card = await waitFor(() => cardOf('关联数据'));
    expect(headerTitles(card).filter((text) => text !== '' && !text.startsWith('EMPTY'))).toEqual([
      '数据产品名称', '产品简介', '产品类型', '交付方式', '交付的使用场景', '组织名称', '操作',
    ]);
    expect(await within(card).findByText('零售交易明细')).toBeInTheDocument();
    expect(firstRowCells(card)).toEqual(['零售交易明细', '脱敏后的门店流水', '结构化数据', 'API 导出', '模型训练与效果验证', '沈阳数据中心R', '详情']);
    // 名称与详情都是蓝色链接（图上两列都是链接）
    expect(within(card).getAllByText('零售交易明细')[0].closest('a')).not.toBeNull();
    expect(within(card).getByText('详情').closest('a')).not.toBeNull();
    expect(within(card).getByText('共 1 个数据产品')).toBeInTheDocument();
    expect(within(card).getByText('已授权到沙箱的数据产品，详情信息为只读')).toBeInTheDocument();
  });

  it('组织名称解析不到就置破折号，不从 provider_id 反推名字', async () => {
    mockBackend({ orgOptions: [{ id: 'u-9', username: 'other', role: 'data_provider', organization: null }] });
    renderDetail();
    const card = await waitFor(() => cardOf('关联数据'));
    await within(card).findByText('零售交易明细');
    expect(firstRowCells(card)[5]).toBe('—');
  });

  it('合约没下发授权清单时明说没有下发，不替它编数字', async () => {
    mockBackend({ contract: withContract(null) });
    renderDetail();
    const card = await waitFor(() => cardOf('我的合约'));
    expect(within(card).getByText('零售流水合约')).toBeInTheDocument();
    expect(await screen.findByText(/未下发授权数据清单，不替它编一个数字出来/)).toBeInTheDocument();
    expect(screen.queryByText('共 0 个数据产品')).not.toBeInTheDocument();
    expect(screen.getByText('授权清单未记录')).toBeInTheDocument();
    // 统计小卡同理：授权清单未下发时小卡记「未记录」，不是 0 个
    // 按图重建后四枚小卡同入一张白卡（.t24-tile）：断言改按单枚小卡钉，契约不变
    const tile = screen.getByText('关联数据产品').closest('.t24-tile') as HTMLElement;
    expect(tile).toBeTruthy();
    expect(within(tile).getByText('未记录')).toBeInTheDocument();
    expect(within(tile).queryByText('0')).not.toBeInTheDocument();
  });

  it('成果包列读后端下发的 file_name/行数/大小，导出状态整列置破折号', async () => {
    renderDetail();
    const card = await waitFor(() => cardOf('结果集'));
    expect(await within(card).findByText('user_behavior_result_20260812.csv')).toBeInTheDocument();
    expect(within(card).getByText('db_empty')).toBeInTheDocument(); // 无 file_name 的旧包退回 bundle_id
    expect(within(card).getByText('36 行')).toBeInTheDocument();
    expect(within(card).getByText('DLP 审查通过')).toBeInTheDocument();
    expect(within(card).getByText('已嵌入水印')).toBeInTheDocument();
    // 图上「已导出」这一档后端没有对应字段 → 不许出现
    expect(within(card).queryByText('已导出')).not.toBeInTheDocument();
    expect(within(card).getAllByText('—').length).toBeGreaterThanOrEqual(2);
    expect(within(card).getByText('前往我的结果集')).toBeInTheDocument();
  });

  it('全页没有分页器，数据一次取全（单次上限 100）', async () => {
    renderDetail();
    await screen.findByText('user_behavior_result_20260812.csv');
    expect(document.querySelector('.ant-pagination')).toBeNull();
    expect(get).toHaveBeenCalledWith('/deliveries/dataset/bundles', expect.objectContaining({ params: expect.objectContaining({ page_size: 100 }) }));
  });

  it('三张表逐列照图，且表里没有「示例」字样', async () => {
    renderDetail();
    const granted = await waitFor(() => cardOf('关联数据'));
    await within(granted).findByText('零售交易明细');
    expect(headerTitles(granted).filter((text) => text !== '' && !text.startsWith('EMPTY'))).toEqual([
      '数据产品名称', '产品简介', '产品类型', '交付方式', '交付的使用场景', '组织名称', '操作',
    ]);
    const results = cardOf('结果集');
    expect(headerTitles(results).filter((text) => text !== '' && !text.startsWith('EMPTY'))).toEqual([
      '结果集名称', '生成时间', '数据行数', '文件大小', '导出状态', '操作',
    ]);
    const runs = cardOf('运行记录');
    expect(headerTitles(runs).filter((text) => text !== '' && !text.startsWith('EMPTY'))).toEqual([
      '执行时间', '操作类型', '执行环境', '状态', '耗时', '操作',
    ]);
    expect(document.body.textContent).not.toMatch(/示例|模拟|样例/);
    // 后端没给的格子统一是破折号，不是 0 也不是编出来的数
    expect(document.body.textContent).toContain('—');
  });

  it('卡片灰字说明逐字照图', async () => {
    renderDetail();
    await screen.findByRole('heading', { name: '销售分析' });
    expect(screen.getByText('合约用于约束模型输出格式与导出通道')).toBeInTheDocument();
    // 图：块题后是 ⓘ 图标（无文字），说明本体是纯灰字
    const quotaCard = cardOf('资源配置');
    expect(within(quotaCard).getByText('内存配额将应用于调试与执行阶段')).toBeInTheDocument();
    expect(quotaCard.querySelector('.t24-ht .anticon-info-circle')).not.toBeNull();
    expect(screen.getByText('已授权到沙箱的数据产品，详情信息为只读')).toBeInTheDocument();
    expect(screen.getByText('按合约约定，结果集已通过 OpenApi 导出至「我的结果集」；')).toBeInTheDocument();
    // 运行记录那张的灰字在 TaskRunRecords 自己的用例里锁
    expect(screen.getByText('记录调试与正式执行的历史，最多保留 90 天')).toBeInTheDocument();
  });

  it('取数失败走统一错误态，不出现 spin 卡死', async () => {
    get.mockImplementation((url: string) => (url.startsWith('/notebooks/') ? Promise.reject(new Error('boom')) : Promise.resolve(emptyPage)));
    const { container } = renderDetail();
    expect(await screen.findByText('数据加载失败')).toBeInTheDocument();
    await waitFor(() => expect(container.querySelector('.ant-spin-spinning')).toBeNull());
  });
});
