// 图 2-2 整页「新增开发任务」用例。原 DevTasks.test.tsx 的 5 条创建用例在此承接
// （编号映射见 specs/ss-19 §9.2 台账）：编号只读不进载荷 / 内存必填 /
// RESOURCE_OVERRIDE_EXCEEDED 说人话 / 授权数据可见，另加本轮子集注入的 4 条。
// 只 mock 最外层 HTTP（services/api），services/flow、勾选器与页面逻辑都跑真代码。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import DevTaskCreatePage from '../DevTaskCreatePage';
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
const post = api.post as unknown as ReturnType<typeof vi.fn>;

const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 100 });

const CONTRACT = {
  id: 'c-1',
  contract_no: 'CT-0001',
  title: '客单价合约',
  status: 'active',
  purpose: '零售客单价建模',
  delivery_methods: ['dataset'],
  sandbox_resource_spec: { memory_mb: 16384 },
  product_ids: ['p-9'],
};

const GRANTED = {
  id: 'p-9',
  name: '授权零售表（PSI 可用）',
  description: '近 90 天门店流水',
  product_type: 'tabular',
  status: 'published',
  resource_id: 'r-9',
  row_count: 4800,
};

const NODE_ONLY = {
  id: 'p-77',
  name: '本节点登记但未授权表',
  description: null,
  product_type: 'tabular',
  status: 'published',
  resource_id: 'r-77',
};

function route() {
  get.mockImplementation((url: string) => {
    if (url === '/contracts') return Promise.resolve(page([CONTRACT]));
    if (url === '/data-products/p-9') return Promise.resolve(GRANTED);
    if (url === '/data-products') return Promise.resolve(page([GRANTED, NODE_ONLY]));
    return Promise.resolve(undefined);
  });
}

async function renderCreate() {
  renderWithProviders(<DevTaskCreatePage />);
  await screen.findByText('交付场景 / 数据沙箱开发 · 配置合约、资源与关联数据后即可启动调试');
}

/** antd Select 在 jsdom 里靠 mouseDown 开下拉；上一个下拉不会随动画收起，只能按选项文本认。 */
async function pickOption(fieldId: string, label: string) {
  const selector = await waitFor(() => {
    const element = document.getElementById(fieldId)?.closest('.ant-select')?.querySelector('.ant-select-selector');
    if (!element) throw new Error(`找不到 ${fieldId} 的下拉控件`);
    return element;
  });
  fireEvent.mouseDown(selector);
  const option = await waitFor(() => {
    const element = [...document.querySelectorAll('.ant-select-item-option-content')].find((node) => node.textContent === label);
    if (!element) throw new Error(`${fieldId} 的选项「${label}」没出现`);
    return element;
  });
  fireEvent.click(option);
}

async function fillBasics() {
  fireEvent.change(screen.getByPlaceholderText('例如：用户行为分析模型'), { target: { value: '客单价分析' } });
  await pickOption('contract_id', '客单价合约（CT-0001）');
  await pickOption('debug_method', 'Web IDE 在线调试');
  await pickOption('export_mode', '成果集打包导出');
}

async function fillMemory(debug: string, exec: string) {
  fireEvent.change(screen.getByPlaceholderText('如 8'), { target: { value: debug } });
  const execInput = await waitFor(() => {
    const element = document.getElementById('exec_memory_gib') as HTMLInputElement;
    if (!element) throw new Error('执行环境内存输入框没渲染');
    return element;
  });
  fireEvent.change(execInput, { target: { value: exec } });
}

/** 表格第一行的勾选框（rowSelection 生成，无 accessible name，只能按结构取）。 */
function firstRowCheckbox(): HTMLInputElement {
  const element = document.querySelector('.ant-table-tbody input[type="checkbox"]') as HTMLInputElement | null;
  if (!element) throw new Error('关联数据表格没渲染出勾选框');
  return element;
}

function submittedPayload(): Record<string, unknown> {
  const call = post.mock.calls.find((entry) => entry[0] === '/notebooks');
  if (!call) throw new Error('没有发出 POST /notebooks');
  return call[1] as Record<string, unknown>;
}

beforeEach(() => {
  vi.clearAllMocks();
  route();
});

// 整页 = 四卡 + antd 表格/双页签 + 3 个并发查询，是全应用最重的渲染之一。
// 本机全量并行跑时默认 5s 闸会偶发红（实测 5300ms）；这里抬的是闸值，
// 等待条件与断言一字未改，也没加任何 sleep。
describe('新增开发任务整页（图 2-2）', { timeout: 20_000 }, () => {
  it('四卡与底部条按图渲染；未勾选时说明按合约授权全量注入', async () => {
    await renderCreate();
    for (const card of ['基本信息', '我的合约', '资源配置', '关联数据']) {
      expect(screen.getByText(card)).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: /^完\s*成$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^取\s*消$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '← 返回列表' })).toBeInTheDocument();
    await fillBasics();
    expect(await screen.findByText(/未勾选时默认注入合约授权的全部 1 项数据/)).toBeInTheDocument();
  });

  it('任务编号不是输入框：只读展示、无表单控件、不进载荷', async () => {
    await renderCreate();
    await fillBasics();

    const readOnly = screen.getByLabelText('任务编号') as HTMLInputElement;
    expect(readOnly.disabled).toBe(true);
    expect(readOnly.value).toContain('系统自动生成');
    expect(document.querySelector('[name="task_no"], #task_no')).toBeNull();

    post.mockResolvedValue({ id: 'n-9', task_no: 'TASK-20261016-009', title: '客单价分析' });
    await fillMemory('8', '16');
    fireEvent.click(screen.getByRole('button', { name: /^完\s*成$/ }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/notebooks', expect.objectContaining({ title: '客单价分析' })));

    const payload = submittedPayload();
    expect(payload).not.toHaveProperty('task_no');
    expect(payload.contract_id).toBe('c-1');
    expect(payload.path).toBe('/nb/客单价分析.ipynb');
  });

  it('内存必填：没填调试/执行环境内存时提交不通过，也不会发请求', async () => {
    await renderCreate();
    await fillBasics();

    // 图 2-2 的灰字口径（范围就是后端 app/core/config.py 的档位上限）。
    expect(await screen.findByText(/范围 2G ~ 64G/)).toBeInTheDocument();
    // 图 A-3：label 是半角 `(G)`，框内右侧另有单位 `G`。单位是 .cds-unit-item 的 CSS ::after 画的，
    // jsdom 读不到伪元素，所以这里锁结构（两个档位都在）+ 锁「antd 开起来能用的 suffix 其实不渲染」。
    expect(screen.getByText('调试环境内存(G)')).toBeInTheDocument();
    expect(screen.getByText('执行环境内存(G)')).toBeInTheDocument();
    expect(document.querySelectorAll('.cds-unit-item')).toHaveLength(2);
    expect(document.querySelectorAll('.ant-input-number-suffix')).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: /^完\s*成$/ }));
    await waitFor(() => expect(screen.getByText('请输入调试环境内存')).toBeInTheDocument());
    expect(post).not.toHaveBeenCalled();
  });

  it('RESOURCE_OVERRIDE_EXCEEDED 呈现为「超出合约已批准规格（合约内存 16 GiB）」并归位到调试内存', async () => {
    await renderCreate();
    await fillBasics();
    await fillMemory('32', '40');
    post.mockRejectedValue({
      response: {
        status: 400,
        data: {
          code: 'RESOURCE_OVERRIDE_EXCEEDED',
          message: 'debug_memory_mb=32768 超出合约已批准的规格上限 16384',
          detail: { contract_memory_mb: 16384, effective_ceiling_mb: 16384 },
        },
      },
    });

    fireEvent.click(screen.getByRole('button', { name: /^完\s*成$/ }));
    await waitFor(() => expect(screen.getAllByText(/超出合约已批准规格（合约内存 16 GiB）/).length).toBeGreaterThanOrEqual(2));
    expect(document.querySelector('.ant-form-item-explain-error')?.textContent).toContain('超出合约已批准规格');
  });

  it('关联数据是双页签，未授权的本节点产品勾选框置灰并给出原因', async () => {
    await renderCreate();
    await fillBasics();

    expect(await screen.findByText('授权零售表（PSI 可用）')).toBeInTheDocument();
    // 图 2-2 的页签选中态是实心块，不再是 antd Tabs 的 ink-bar：改成首页同款按钮式页签后
    // 没有 role=tab，选中态靠 aria-pressed 上屏；可及名逐字未动，依旧按 role+name 点。
    fireEvent.click(screen.getByRole('button', { name: '本节点登记的数据产品' }));
    expect(await screen.findByText('本节点登记但未授权表')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '本节点登记的数据产品' })).toHaveAttribute('aria-pressed', 'true');

    const disabled = await waitFor(() => {
      const row = [...document.querySelectorAll('.ant-table-tbody tr')]
        .find((tr) => tr.textContent?.includes('本节点登记但未授权表'));
      const box = row?.querySelector('input[type="checkbox"]') as HTMLInputElement | null;
      if (!box) throw new Error('未授权产品那一行没渲染出来');
      if (!box.disabled) throw new Error('未授权产品的勾选框没有置灰');
      return box;
    });
    expect(disabled.disabled).toBe(true);
    // 取数口径那句从筛选带下方收进了「查询」按钮的 Tooltip（与 2-1 的查询钮同一口径）：
    // 指上去才在 portal 里出现，断言跟着改成 hover，文本一字未改。
    const query = screen.getByRole('button', { name: /^查\s*询$/ });
    expect(query).toHaveClass('ant-btn-primary');
    fireEvent.mouseOver(query);
    expect(await screen.findByText(/只有所选合约已授权的数据产品可勾选/)).toBeInTheDocument();
  });

  it('勾选授权数据 → 载荷只带这条数据资源 id', async () => {
    await renderCreate();
    await fillBasics();
    await screen.findByText('授权零售表（PSI 可用）');

    fireEvent.click(firstRowCheckbox());
    expect(await screen.findByText('已选择数据（1个）')).toBeInTheDocument();

    post.mockResolvedValue({ id: 'n-8', task_no: 'TASK-20261016-008', title: '客单价分析' });
    await fillMemory('8', '16');
    fireEvent.click(screen.getByRole('button', { name: /^完\s*成$/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(submittedPayload().data_resource_ids).toEqual(['r-9']);
  });

  it('清空已选 → 载荷带空数组，页面说清本任务不注入任何供数', async () => {
    await renderCreate();
    await fillBasics();
    await screen.findByText('授权零售表（PSI 可用）');

    fireEvent.click(firstRowCheckbox());
    fireEvent.click(await screen.findByRole('button', { name: '清空' }));
    expect(await screen.findByText(/已清空：本任务不注入任何供数/)).toBeInTheDocument();

    post.mockResolvedValue({ id: 'n-7', task_no: 'TASK-20261016-007', title: '客单价分析' });
    await fillMemory('8', '16');
    fireEvent.click(screen.getByRole('button', { name: /^完\s*成$/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(submittedPayload().data_resource_ids).toEqual([]);
  });

  it('没碰已选面板 → 载荷不带该字段（= 不限定，注入全部授权供数）', async () => {
    await renderCreate();
    await fillBasics();
    await screen.findByText('授权零售表（PSI 可用）');

    post.mockResolvedValue({ id: 'n-6', task_no: 'TASK-20261016-006', title: '客单价分析' });
    await fillMemory('8', '16');
    fireEvent.click(screen.getByRole('button', { name: /^完\s*成$/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(submittedPayload()).not.toHaveProperty('data_resource_ids');
  });

  it('任务描述进载荷；不填就不带该字段', async () => {
    await renderCreate();
    await fillBasics();
    fireEvent.change(screen.getByPlaceholderText('说明本任务要解决的问题、输入口径与期望产出。'), { target: { value: '按门店聚合近 90 天客单价' } });

    post.mockResolvedValue({ id: 'n-5', task_no: 'TASK-20261016-005', title: '客单价分析' });
    await fillMemory('8', '16');
    fireEvent.click(screen.getByRole('button', { name: /^完\s*成$/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(submittedPayload().description).toBe('按门店聚合近 90 天客单价');
  });

  it('地址带 contract_id 时默认就是那条合约（列表页筛选直传）', async () => {
    renderWithProviders(<DevTaskCreatePage />, { route: '/flow/tasks/new?contract_id=c-1' });
    await screen.findByText('交付场景 / 数据沙箱开发 · 配置合约、资源与关联数据后即可启动调试');
    expect(await screen.findByText('客单价合约（CT-0001）')).toBeInTheDocument();
    expect(await screen.findByText('授权零售表（PSI 可用）')).toBeInTheDocument();
  });
});
