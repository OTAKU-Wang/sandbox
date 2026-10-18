// PRD §3.2.1 补齐的两件事：任务状态筛选（全部/进行中/已结束）+ 卡片「执行」入口，
// 以及 §3.2.6 执行弹窗被任务卡预置（contract/workload）后的样子。
// 口径要求：状态桶只认后端 draft/archived，未登记状态既不归进行中也不归已结束；
// 「执行」只有拿到这条任务自己已发布进生产的那版负载才可点，否则禁用并给出既有权威文案。
// 只 mock 最外层 HTTP（services/api），services/flow 与页面逻辑都跑真代码。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useLocation } from 'react-router-dom';
import { renderWithProviders } from '../../../test/test-utils';
import FlowDevTasks from '../DevTasks';
import ComputeSchedule from '../ComputeSchedule';
import api from '../../../services/api';
import type { Notebook, Workload } from '../../../services/flow';
import { UNKNOWN_TEXT, WorkloadStatus } from '../../../services/flow';
import {
  DEV_TASK_STATE_OPTIONS,
  EMPTY_DEV_TASK_FILTERS,
  NO_CONTRACT_REASON,
  NO_PRODUCTION_WORKLOAD_REASON,
  buildDevTaskCard,
  buildDevTaskCards,
  devTaskStateLabel,
  filterDevTaskCards,
  notebookStateBucket,
} from '../devTasksModel';

// antd 响应式栅格与下拉依赖 window.matchMedia（jsdom 未实现）
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
const post = api.post as unknown as ReturnType<typeof vi.fn>;

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 100 });

function notebook(overrides: Partial<Notebook> = {}): Notebook {
  return {
    id: 'n-1',
    contract_id: 'c-1',
    title: '客单价分析',
    path: '/nb/a.ipynb',
    kernel: 'python3',
    language: 'python',
    content: null,
    version: 3,
    status: 'draft',
    session_id: null,
    execution_backend: null,
    last_executed_at: null,
    created_at: '2026-10-16T00:00:00Z',
    updated_at: '2026-10-16T02:00:00Z',
    ...overrides,
  };
}

function workload(overrides: Partial<Workload> = {}): Workload {
  return {
    id: 'w-1',
    name: '客单价',
    contract_id: 'c-1',
    source_notebook_id: null,
    language: 'python',
    entrypoint_hash: null,
    resource_spec: null,
    env: 'test',
    status: WorkloadStatus.Draft,
    version: 1,
    is_latest: true,
    test_run_id: null,
    published_at: null,
    created_at: '2026-10-16T00:00:00Z',
    ...overrides,
  };
}

const PUBLISHED_V2 = workload({ id: 'w-2', name: '客单价生产版', source_notebook_id: 'n-1', env: 'production', status: WorkloadStatus.Published, version: 2 });
const PUBLISHED_V3 = workload({ id: 'w-3', name: '客单价生产版', source_notebook_id: 'n-1', env: 'production', status: WorkloadStatus.Published, version: 3 });
const TEST_PUBLISHED = workload({ id: 'w-4', source_notebook_id: 'n-1', env: 'test', status: WorkloadStatus.Published });
const OTHER_TASK = workload({ id: 'w-5', source_notebook_id: 'other', env: 'production', status: WorkloadStatus.Published });

const CONTRACT = { id: 'c-1', contract_no: 'CT-0001', title: '客单价合约', status: 'active', sandbox_resource_spec: { memory_mb: 16384 }, product_ids: [] };
const SCHEDULE_ROW = {
  contract_id: 'c-1',
  contract_no: 'CT-0001',
  auto_compute: false,
  compute_frequency: 'daily',
  next_compute_at: null,
  last_computed_at: null,
  delivery_methods: ['dataset'],
};
const FINISHED_RUN = {
  id: 'r-1',
  contract_id: 'c-1',
  workload_id: 'w-9',
  trigger: 'manual',
  status: 'succeeded',
  scheduled_for: null,
  started_at: '2026-10-17T00:00:00Z',
  finished_at: '2026-10-17T00:05:00Z',
  output_rows: 12,
  output_files: [],
  error_message: null,
  session_id: null,
  resource_usage: { exit_code: 0 },
  params: null,
  resource_override: null,
};

function route(over: { notebooks?: unknown[]; workloads?: unknown[] } = {}) {
  get.mockImplementation((url: string) => {
    if (url === '/notebooks') return Promise.resolve(page(over.notebooks ?? []));
    if (url === '/workloads') return Promise.resolve(page(over.workloads ?? []));
    if (url === '/contracts') return Promise.resolve(page([CONTRACT]));
    if (url === '/compute/schedule') return Promise.resolve({ items: [SCHEDULE_ROW], total: 1 });
    if (url === '/compute/runs') return Promise.resolve(page([]));
    return Promise.resolve(undefined);
  });
}

const textOf = (selector: string, root: ParentNode = document) =>
  Array.from(root.querySelectorAll(selector)).map((element) => element.textContent ?? '');

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

async function pickStateOption(label: string) {
  // 状态筛选下拉拿 className 定位（不靠 antd 内部结构猜哪个 select），选项只在未隐藏的下拉里找。
  const select = document.querySelector('.cds-filter-state') as Element;
  fireEvent.mouseDown(select.querySelector('.ant-select-selector') as Element);
  const option = await waitFor(() => {
    const hit = Array.from(
      document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content'),
    ).find((element) => element.textContent === label);
    if (!hit) throw new Error(`任务状态下拉里没有选项：${label}`);
    return hit;
  });
  fireEvent.click(option);
}

beforeEach(() => {
  vi.clearAllMocks();
  route();
});

describe('任务状态筛选（PRD §3.2.1）', () => {
  const cards = buildDevTaskCards(
    [
      notebook({ id: 'live', status: 'draft', task_no: 'TASK-20261016-001' }),
      notebook({ id: 'done', status: 'archived', task_no: 'TASK-20261016-002' }),
      notebook({ id: 'odd', status: 'paused_by_operator', task_no: 'TASK-20261016-003' }),
    ],
    [],
  );

  it('选项就是「进行中 / 已结束」，标签与卡片状态 Tag 同一份来源', () => {
    expect(DEV_TASK_STATE_OPTIONS.map((option) => option.label)).toEqual(['进行中', '已结束']);
    expect(devTaskStateLabel('ended')).toBe('已结束');
  });

  it('进行中只留未归档的，已结束只留归档的', () => {
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, state: 'active' }).map((card) => card.id)).toEqual(['live']);
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, state: 'ended' }).map((card) => card.id)).toEqual(['done']);
  });

  it('未登记的后端状态既不塞进进行中也不塞进已结束，只在「全部」下可见', () => {
    expect(notebookStateBucket(notebook({ status: 'paused_by_operator' }))).toBe('other');
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, state: 'active' }).map((card) => card.id)).not.toContain('odd');
    expect(filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, state: 'ended' }).map((card) => card.id)).not.toContain('odd');
    expect(cards.find((card) => card.id === 'odd')?.state.label).toBe('paused_by_operator');
  });

  it('「重置」清掉状态筛选后逐字回到全部（引用同一份，不吞行）', () => {
    const filtered = filterDevTaskCards(cards, { ...EMPTY_DEV_TASK_FILTERS, state: 'ended' });
    expect(filtered.map((card) => card.id)).toEqual(['done']);
    expect(filterDevTaskCards(cards, EMPTY_DEV_TASK_FILTERS)).toBe(cards);
  });

  it('生效筛选里点名是哪一档筛空的', () => {
    const filters = { ...EMPTY_DEV_TASK_FILTERS, state: 'ended' as const };
    expect(filterDevTaskCards(cards, { ...filters, keyword: 'live' })).toHaveLength(0);
    expect(cards.find((card) => card.id === 'live')?.state_bucket).toBe('active');
  });
});

describe('卡片字段：创建时间与沙箱引擎', () => {
  it('创建时间取后端 created_at，缺了就是「未记录」', () => {
    const card = buildDevTaskCard(notebook({ created_at: '2026-10-16T00:00:00Z' }));
    expect(card.created_text).not.toBe(UNKNOWN_TEXT);
    expect(card.created_text).toContain('2026');
    expect(buildDevTaskCard(notebook({ created_at: null as unknown as string })).created_text).toBe(UNKNOWN_TEXT);
  });

  it('沙箱引擎不在任务载荷里，恒「未记录」，不照原型示例值写 CubeSandbox MicroVM', () => {
    expect(buildDevTaskCard(notebook()).engine_text).toBe(UNKNOWN_TEXT);
  });
});

describe('执行入口的可用性（PRD §3.2.1 卡片 + §3.2.6 弹窗）', () => {
  it('这条任务有已发布的生产版本 → 可执行，并带上合约与这一版负载', () => {
    const card = buildDevTaskCards([notebook({ id: 'n-1' })], [PUBLISHED_V2, PUBLISHED_V3, TEST_PUBLISHED, OTHER_TASK])[0];
    expect(card.exec_disabled_reason).toBeNull();
    expect(card.exec_path).toBe('/compute?contract=c-1&workload=w-3');
  });

  it('只有测试环境版本或别的任务的版本 → 禁用，用执行弹窗里那句权威文案', () => {
    expect(buildDevTaskCard(notebook(), undefined, undefined).exec_disabled_reason).toBe(NO_PRODUCTION_WORKLOAD_REASON);
    const onlyTest = buildDevTaskCards([notebook({ id: 'n-1' })], [TEST_PUBLISHED, OTHER_TASK])[0];
    expect(onlyTest.exec_disabled_reason).toBe(NO_PRODUCTION_WORKLOAD_REASON);
    expect(onlyTest.exec_path).toBe('/compute');
  });

  it('没关联合约时优先说没合约（生产执行按合约调度）', () => {
    const card = buildDevTaskCards([notebook({ id: 'n-9', contract_id: null })], [PUBLISHED_V3])[0];
    expect(card.exec_disabled_reason).toBe(NO_CONTRACT_REASON);
  });
});

// 「已结束/进行中/重置」三段都要开 antd 下拉 + 等卡片重绘，是本文件最重的交互链。
// 单跑干净（16 passed，测试净耗时 4.54s），但全量并行跑时这条越 5s 默认闸（vitest-final.log：
// `Test timed out in 5000ms`，同轮 Duration 77.21s / import 693s 累计）。抬的只是闸值：
// 等待条件与断言一字未改，helper 里也只有 waitFor 轮询，没有 sleep。
describe('任务列表页接线', { timeout: 20_000 }, () => {
  const NOTEBOOKS = [
    notebook({ id: 'ready', title: '已发布任务', task_no: 'TASK-20261016-001' }),
    notebook({ id: 'drafting', title: '没发布的任务', task_no: 'TASK-20261016-002' }),
    notebook({ id: 'closed', title: '归档任务', task_no: 'TASK-20261016-003', status: 'archived' }),
  ];
  const WORKLOADS = [workload({ id: 'w-ready', name: '已发布生产版', source_notebook_id: 'ready', env: 'production', status: WorkloadStatus.Published, version: 4 })];

  async function renderTasks() {
    route({ notebooks: NOTEBOOKS, workloads: WORKLOADS });
    renderWithProviders(
      <>
        <FlowDevTasks />
        <LocationProbe />
      </>,
      { route: '/flow/tasks' },
    );
    await screen.findByText('已发布任务');
  }

  it('任务状态下拉真的过滤卡片，重置后三张卡原样回来', async () => {
    await renderTasks();
    // renderTasks 只等到第一张卡；三张卡来自多个查询汇齐后的筛选结果，
    // 直接同步断言就是拿「碰巧同时渲染完」当通过条件。先等第三张卡真正存再比顺序。
    await screen.findByText('归档任务');
    expect(textOf('.cds-task-card-title')).toEqual(['已发布任务', '没发布的任务', '归档任务']);

    await pickStateOption('已结束');
    await waitFor(() => expect(textOf('.cds-task-card-title')).toEqual(['归档任务']));
    // 真值 2-1 筛选卡只有四项 + 两钮：不追加「生效筛选」行，筛掉哪一档由空态文案点名（DevTasks.test.tsx）。
    expect(screen.queryByText(/生效筛选：/)).toBeNull();

    await pickStateOption('进行中');
    await waitFor(() => expect(textOf('.cds-task-card-title')).toEqual(['已发布任务', '没发布的任务']));

    fireEvent.click(screen.getByRole('button', { name: /重\s*置/ }));
    await waitFor(() => expect(textOf('.cds-task-card-title')).toEqual(['已发布任务', '没发布的任务', '归档任务']));
    expect(screen.queryByText(/生效筛选：/)).toBeNull();
  });

  it('卡片「执行」跳到计算调度并带上这条任务的合约与生产版本', async () => {
    await renderTasks();
    const card = document.querySelectorAll('.cds-task-card')[0];
    // 按钮名里antd会把图标 aria-label 算进来，因此按子串匹配。
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: /执\s*行/ }));
    expect(await screen.findByText('/compute?contract=c-1&workload=w-ready')).toBeInTheDocument();
  });

  it('没有已发布生产版本的任务：执行是禁用的，鼠标指上去给真原因', async () => {
    await renderTasks();
    const card = document.querySelectorAll('.cds-task-card')[1];
    const button = within(card as HTMLElement).getByRole('button', { name: /执\s*行/ });
    expect(button).toBeDisabled();
    fireEvent.mouseOver(button.parentElement as Element);
    expect(await screen.findByText(NO_PRODUCTION_WORKLOAD_REASON)).toBeInTheDocument();
  });

  it('卡片三枚按钮（图 2-1）：调试 / 执行 / 详情，卡内不再挂文字链接', async () => {
    await renderTasks();
    const card = document.querySelectorAll('.cds-task-card')[0];
    expect(within(card as HTMLElement).queryAllByRole('link')).toHaveLength(0);
    const buttons = within(card as HTMLElement).getAllByRole('button');
    // 图 2-1 钮面带图标（Bug/Play/详情）：antd 的两字自动空格只在纯文字钮生效，带图标后不再拆字。
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['调试', '执行', '详情']);
  });
});

describe('计算调度被任务卡预置（§3.2.6）', () => {
  it('带 contract/workload 进来：调度表按合约过滤，执行弹窗自动打开并预选这一版', async () => {
    route({ workloads: [workload({ id: 'w-9', name: '流失评分生产版', contract_id: 'c-1', env: 'production', status: WorkloadStatus.Published, version: 9 })] });
    renderWithProviders(<ComputeSchedule />, { route: '/compute?contract=c-1&workload=w-9' });

    expect(await screen.findByText('在生产沙箱执行：CT-0001')).toBeInTheDocument();
    expect(await screen.findByText('v9 · 流失评分生产版')).toBeInTheDocument();
    expect(
      get.mock.calls.some((call) => String(call[0]) === '/compute/schedule' && JSON.stringify(call[1] ?? '').includes('c-1')),
    ).toBe(true);

    post.mockResolvedValue(FINISHED_RUN);
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/compute/runs',
        expect.objectContaining({ contract_id: 'c-1', workload_id: 'w-9', params: null, resource_override: null }),
        expect.anything(),
      ),
    );
  });

  it('不带参数时行为不变：不会自己弹窗，手动点执行时版本选择仍留默认（最新已发布版）', async () => {
    route({ workloads: [workload({ id: 'w-9', name: '流失评分生产版', contract_id: 'c-1', env: 'production', status: WorkloadStatus.Published, version: 9 })] });
    renderWithProviders(<ComputeSchedule />, { route: '/compute' });

    await screen.findByText('CT-0001');
    expect(screen.queryByText('开始执行')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /执\s*行/ }));
    await screen.findByText('开始执行');
    expect(screen.getByText('默认使用最新已发布版本')).toBeInTheDocument();
  });
});
