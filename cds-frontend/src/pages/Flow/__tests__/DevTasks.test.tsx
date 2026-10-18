// SS-18 M2/M2′ 页面级用例：卡片读视图（含存量行的「未记录」）、筛选只影响列表、
// 任务编号既不能编辑也不会进载荷、服务端 RESOURCE_OVERRIDE_EXCEEDED 要说人话。
// 只 mock 最外层 HTTP（services/api），services/flow 与页面逻辑都跑真代码。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import FlowDevTasks from '../DevTasks';
import { devTaskCreateHref } from '../devTasksModel';
import api from '../../../services/api';

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
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 100 });

const LEGACY = {
  id: 'legacy',
  contract_id: 'c-1',
  title: '存量笔记本',
  path: '/nb/legacy.ipynb',
  kernel: 'python3',
  language: 'python',
  content: null,
  version: 7,
  status: 'draft',
  session_id: null,
  execution_backend: 'cds_kernel',
  last_executed_at: null,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-02T00:00:00Z',
  task_no: null,
  debug_method: null,
  export_mode: null,
  debug_memory_mb: null,
  exec_memory_mb: null,
};

const NUMBERED = {
  ...LEGACY,
  id: 'n-2',
  title: '流失评分',
  path: '/nb/churn.ipynb',
  version: 3,
  task_no: 'TASK-20261016-001',
  debug_method: 'web_ide',
  export_mode: 'bundle',
  debug_memory_mb: 8192,
  exec_memory_mb: 16384,
};

const CONTRACT = {
  id: 'c-1',
  contract_no: 'CT-0001',
  title: '客单价合约',
  status: 'active',
  sandbox_resource_spec: { memory_mb: 16384 },
  product_ids: ['p-9'],
};

const GRANTED = {
  id: 'p-9',
  name: '授权零售表（PSI 可用）',
  product_type: 'tabular',
  status: 'published',
  row_count: 4800,
  security_level: 'L3',
};

function route(over: { notebooks?: unknown[]; contracts?: unknown[] } = {}) {
  get.mockImplementation((url: string) => {
    if (url === '/notebooks') return Promise.resolve(page(over.notebooks ?? [LEGACY, NUMBERED]));
    if (url === '/workloads') return Promise.resolve(page([]));
    if (url === '/compute/runs') return Promise.resolve(page([]));
    if (url === '/contracts') return Promise.resolve(page(over.contracts ?? [CONTRACT]));
    if (url === '/data-products/p-9') return Promise.resolve(GRANTED);
    return Promise.resolve(undefined);
  });
}

const textOf = (selector: string, root: ParentNode = document) =>
  Array.from(root.querySelectorAll(selector)).map((element) => element.textContent ?? '');

async function renderTasks() {
  renderWithProviders(<FlowDevTasks />);
  await screen.findByText('流失评分');
}



beforeEach(() => {
  vi.clearAllMocks();
  route();
});

describe('开发任务列表（M2）', () => {
  it('卡片三带（图 2-1）：图标角标+名称+状态胶囊，规格只剩调试方法与创建时间，平台字段归 2-4 详情页', async () => {
    await renderTasks();
    // strict-2-1.md §2 卡内栅格无编号行（标题→元信息行之间只有状态药丸）；编号仍留在模型里供详情页用。
    expect(document.querySelectorAll('.cds-task-card-no')).toHaveLength(0);
    expect(textOf('.cds-task-card-title')).toEqual(['存量笔记本', '流失评分']);

    // 图标维度判据：角标按调试方法推导——未登记走中性灰档（禁伪造：不编彩色图标）。
    const icos = document.querySelectorAll('.cds-task-card .cds-task-ico');
    expect(icos).toHaveLength(2);
    expect(icos[0]).toHaveClass('is-gray');
    expect(icos[1]).toHaveClass('is-blue');
    expect(textOf('.cds-state-chip')).toEqual(['进行中', '进行中']);
    // strict-2-1.md §2/§6：胶囊内是 10×10 实心圆点 + 文字，不是环形/减号图标。
    expect(document.querySelectorAll('.cds-state-chip .cds-state-dot')).toHaveLength(2);
    expect(document.querySelectorAll('.cds-state-chip .anticon')).toHaveLength(0);

    // 原型卡内只有「模型调试方法 + 创建时间」两行；平台多出来的字段不再堆灰字行，
    // 但「未记录」口径照旧：后端没下发就不补示例值。
    const legacyCard = document.querySelectorAll('.cds-task-card')[0];
    expect(document.querySelectorAll('.cds-task-card-meta')).toHaveLength(0);
    const legacySpecs = textOf('.cds-task-card-specs dd', legacyCard);
    expect(legacySpecs).toHaveLength(2);
    expect(legacySpecs[0]).toBe('未记录');
    expect(legacySpecs.join('|')).not.toMatch(/\b0 GiB\b/);

    const numberedCard = document.querySelectorAll('.cds-task-card')[1];
    expect(textOf('.cds-task-card-specs dd', numberedCard)[0]).toContain('Web IDE 在线调试');
  });

  it('筛选只影响列表：筛空了会点名筛选条件，清空后存量行原样回来', async () => {
    await renderTasks();
    const keyword = screen.getByPlaceholderText('请输入任务名称');

    fireEvent.change(keyword, { target: { value: '不存在的关键字' } });
    await screen.findByText(/在筛选（关键词“不存在的关键字”）下没有命中/);
    expect(document.querySelectorAll('.cds-task-card')).toHaveLength(0);

    fireEvent.change(keyword, { target: { value: '' } });
    await waitFor(() => expect(document.querySelectorAll('.cds-task-card')).toHaveLength(2));

    // PNG 2-1 筛选带收口为四项：多余的「只看已编号任务」开关随关联合约/导出方式一并退场。
    expect(screen.queryByText('只看已编号任务')).toBeNull();
    expect(screen.queryByText(/刷\s*新/)).toBeNull();
    expect(screen.queryByText('关联合约')).toBeNull();
    expect(screen.queryByText('导出方式')).toBeNull();

    fireEvent.change(keyword, { target: { value: '不存在' } });
    await screen.findByText(/在筛选（关键词“不存在”）下没有命中/);
    // 真值 2-1 没「生效筛选」行，空态自己给一颗「清空筛选」。
    fireEvent.click(screen.getAllByRole('button', { name: '清空筛选' })[0]);
    await waitFor(() => expect(document.querySelectorAll('.cds-task-card')).toHaveLength(2));
  });

  it('真值 2-1 上不存在的额外控件已退场：无包装卡/无第二张表/分页是独立一条', async () => {
    await renderTasks();
    expect(screen.queryByText('开发任务')).toBeNull();
    expect(screen.queryByText(/当前显示/)).toBeNull();
    expect(screen.queryByText('关联负载与生产执行')).toBeNull();
    expect(screen.queryByText(/生效筛选：/)).toBeNull();
    expect(document.querySelector('.cds-task-pager-band .cds-task-pager')).not.toBeNull();
  });

  it('卡片三枚按钮（调试 / 执行 / 详情）带着这条任务的上下文跳转', async () => {
    await renderTasks();
    const card = document.querySelectorAll('.cds-task-card')[1];
    const buttons = within(card as HTMLElement).getAllByRole('button');
    // 带图标的 antd 钮不拆字（无图标才排「调 试」），PNG 三钮就是带图标的。
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['调试', '执行', '详情']);

    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: /调\s*试/ }));
    expect(navigate).toHaveBeenCalledWith('/notebooks?task=n-2');

    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: /详\s*情/ }));
    expect(navigate).toHaveBeenCalledWith('/flow/tasks/n-2');
  });
});

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

describe('新建开发任务的入口（整页化后）', () => {
  it('按钮跳整页 /flow/tasks/new', async () => {
    renderWithProviders(<FlowDevTasks />);
    // antd 的图标按钮在 dom-accessibility-api 里取不到名字，这里按文案定位再点它的按钮壳。
    const create = (await screen.findByText('创建任务')).closest('button');
    fireEvent.click(create!);
    expect(navigate).toHaveBeenCalledWith('/flow/tasks/new');
  });

  it('整页地址口径：无筛选=直进整页，带合约筛选=把该合约带成默认合约', () => {
    expect(devTaskCreateHref(null)).toBe('/flow/tasks/new');
    expect(devTaskCreateHref('c-1')).toBe('/flow/tasks/new?contract_id=c-1');
  });

  it('列表页不再内联创建表单（创建逻辑归 DevTaskCreatePage）', async () => {
    const source = (await import('../DevTasks.tsx?raw')).default;
    expect(source).not.toContain('DevTaskCreate');
    expect(source).toContain('devTaskCreateHref');
  });
});
