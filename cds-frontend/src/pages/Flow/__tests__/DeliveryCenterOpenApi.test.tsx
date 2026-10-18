// 图 3-1「接口列表」/ 图 3-2「新建API」（img-specs/group-C1.md 图 1、图 2 / group-C.md 图 C-1、C-2）。
// 锁的是图片真值：列名与顺序、状态在两页的不同呈现（列表页纯文字 / 监控页 chip）、3-2 的必填项
// 集合与默认态，以及「只把后端有的字段发出去、后端没有的一律标未记录」。
// 只 mock 最外层 HTTP（services/api），services/flow 与页面逻辑都跑真代码。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { renderWithProviders } from '../../../test/test-utils';
import { OpenApiExportTab } from '../DeliveryCenter';
import api from '../../../services/api';
import { useCrumbStackStore } from '../../../stores/crumbStackStore';
import type { ApiEndpoint } from '../../../services/flow';

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
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;
const patch = api.patch as unknown as ReturnType<typeof vi.fn>;
const del = api.delete as unknown as ReturnType<typeof vi.fn>;

const CONTRACT_ID = 'c-1';
const SERVER_URL = 'https://open.cds.local/';

const epA: ApiEndpoint = {
  id: 'e4b1a0c2-7c3f-4b6e-9a10-11ab22cc33dd',
  contract_id: CONTRACT_ID,
  workload_id: 'w-1',
  name: 'Qwen回答预测',
  slug: 'qwen-answer',
  request_schema: null,
  rate_limit_per_minute: 60,
  quota_total: null,
  status: 'enabled',
  path: '/open/v1/qwen-answer',
  published_at: '2026-08-07T10:24:59.000Z',
};

const epB: ApiEndpoint = {
  ...epA,
  id: 'a9f8e7d6-5c4b-3a21-8f70-6e5d4c3b2a19',
  name: 'Qwen模型预测',
  slug: 'qwen-model',
  status: 'disabled',
  published_at: null,
  path: '/open/v1/qwen-model',
};

const doc = {
  openapi: '3.1.0',
  info: { title: '合约 C-1 数据输出 API', version: '1' },
  servers: [{ url: SERVER_URL }],
  paths: {},
};

const workload = {
  id: 'w-1',
  name: '生产预测负载 v3',
  contract_id: CONTRACT_ID,
  source_notebook_id: null,
  language: 'python',
  entrypoint_hash: null,
  resource_spec: null,
  env: 'production',
  status: 'published',
  version: 3,
  is_latest: true,
  test_run_id: null,
  published_at: '2026-08-07T10:20:00.000Z',
  created_at: '2026-08-01T10:00:00.000Z',
};

function mockReads(items: ApiEndpoint[] = [epA, epB]) {
  get.mockImplementation((url: string) => {
    if (url === '/deliveries/openapi/endpoints') return Promise.resolve({ items, total: items.length });
    if (url === `/deliveries/openapi/contract/${CONTRACT_ID}`) return Promise.resolve(doc);
    if (url === '/workloads') return Promise.resolve({ items: [workload], total: 1, page: 1, page_size: 100 });
    return Promise.resolve(null);
  });
}

function renderTab() {
  return renderWithProviders(
    <ConfigProvider locale={zhCN}>
      <OpenApiExportTab contractId={CONTRACT_ID} />
    </ConfigProvider>,
  );
}

function headerTexts(): string[] {
  return Array.from(document.querySelectorAll('.ant-table-thead th')).map((th) => th.textContent ?? '');
}

/** 按表头文案定位整列单元格（列名或顺序变了就取不到，正是想让它红的那种断言）。 */
function columnCells(title: string): HTMLElement[] {
  const index = headerTexts().indexOf(title);
  if (index < 0) throw new Error(`没有这一列：${title}`);
  return Array.from(document.querySelectorAll('.ant-table-tbody tr.ant-table-row')).map(
    (row) => (row as HTMLTableRowElement).cells[index],
  );
}

async function pickWorkloadOption() {
  const selector = document.getElementById('workload_id')?.closest('.ant-select')?.querySelector('.ant-select-selector');
  if (!selector) throw new Error('关联任务下拉不存在');
  fireEvent.mouseDown(selector);
  // 下拉在 portal 里下一帧才出现，只能等它
  fireEvent.click(await screen.findByText('生产预测负载 v3'));
}

async function openCreateScreen() {
  await screen.findByText('Qwen回答预测');
  fireEvent.click(screen.getByRole('button', { name: /新建接口/ }));
  await screen.findByRole('button', { name: '新增接口' });
}

beforeEach(() => {
  vi.clearAllMocks();
  mockReads();
});

// 20_000 的理由：首屏并发 接口列表 + 合约文档 + 生产负载 三个 query，再挂整张 antd 表单。
// 本机实测（2026-10-17，vitest --reporter=verbose，见 fe-results-report.md）：这一组 7 条共 4.16s，
// 单条最慢 1.42s。等的一律是查询落地后的 DOM，没有固定 sleep；抬高的只是默认 5s 上限，
// 让满载机器不至于把机器负载误读成行为回归。
describe('图 3-1 接口列表', { timeout: 20_000 }, () => {
  it('列名与顺序照图，分页带写「共 N 个接口」与前往页', async () => {
    // 「前往 __ 页」只在后端总数超过一页时出现（rc-pagination: total > pageSize），
    // 所以这一条要让后端返回 12 个接口，而不是把 2 个接口的空列表当成图上的分页带。
    mockReads([epA, ...Array.from({ length: 11 }, (_, i) => ({ ...epA, id: `e-${i}`, name: `占位接口 ${i + 1}` }))]);
    renderTab();
    await screen.findByText('Qwen回答预测');
    expect(headerTexts()).toEqual(['接口名称', 'API ID', '接口描述', '有效期', 'apiUrl', '状态', '是否在线', '操作']);
    expect(screen.getByText('共 12 个接口')).toBeInTheDocument();
    expect(screen.getByText(/10 条\/页/)).toBeInTheDocument();
    const jumper = document.querySelector('.ant-pagination-options-quick-jumper') as HTMLElement;
    expect(jumper.textContent).toContain('前往');
  });

  it('状态与「是否在线」是彩色纯文字，不是带底色徽标（监控页才用 chip）', async () => {
    renderTab();
    await screen.findByText('Qwen回答预测');
    expect(columnCells('状态').map((cell) => cell.textContent)).toEqual(['正常', '已停用']);
    expect(columnCells('是否在线').map((cell) => cell.textContent)).toEqual(['上线', '离线']);
    for (const title of ['状态', '是否在线']) {
      for (const cell of columnCells(title)) {
        expect(cell.querySelector('.ant-tag')).toBeNull();
      }
    }
    // 颜色只按后端 status 上色：enabled 绿、disabled 灰
    expect(columnCells('状态')[0].firstElementChild?.getAttribute('style')).toContain('rgb(82, 196, 26)');
    expect(columnCells('状态')[1].firstElementChild?.getAttribute('style')).toContain('rgba(0, 0, 0, 0.45)');
  });

  it('apiUrl 用后端 servers+path 拼成截断链接，单元格里没有复制按钮', async () => {
    renderTab();
    await screen.findByText('Qwen回答预测');
    const cell = columnCells('apiUrl')[0];
    const link = within(cell).getByRole('link');
    const full = `${SERVER_URL}open/v1/qwen-answer`;
    expect(link.getAttribute('href')).toBe(full);
    expect(link.textContent).toBe(`${full.slice(0, 38)}…`); // 截断规则：38 字符 + 省略号
    expect(within(cell).queryByRole('button')).toBeNull();
    expect(cell.querySelector('.anticon-copy')).toBeNull();
  });

  it('后端没有描述字段 → 接口描述记「未记录」；无有效期字段 → 有效期写永不过期', async () => {
    renderTab();
    await screen.findByText('Qwen回答预测');
    expect(columnCells('接口描述').map((cell) => cell.textContent)).toEqual(['未记录', '未记录']);
    expect(columnCells('有效期').map((cell) => cell.textContent)).toEqual(['永不过期', '永不过期']);
  });

  it('筛选卡照图：标签与控件同行的三项筛选 + 查询/重置，查询按后端返回的名称过滤，重置还原', async () => {
    renderTab();
    await screen.findByText('Qwen回答预测');
    expect(screen.getByText('接口名称：')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('请输入接口名称')).toBeInTheDocument();
    expect(screen.getByText('状态：')).toBeInTheDocument();
    expect(screen.getByText('是否在线：')).toBeInTheDocument();
    // 图上本带只有一行（带高≈35）：灰字标签与控件同在一个共用件里，不再竖排
    for (const [label, control] of [
      ['接口名称：', 'input'],
      ['状态：', '.ant-select'],
      ['是否在线：', '.ant-select'],
    ] as const) {
      const field = screen.getByText(label).closest('.cds-filter-field') as HTMLElement;
      expect(field).not.toBeNull();
      expect(field.querySelector(control)).not.toBeNull();
    }
    // 取数口径从卡内第四行收进了「查询」的 Tooltip：hover 后原句一字不改地出全部
    fireEvent.mouseEnter(screen.getByRole('button', { name: /^查\s*询$/ }));
    const hint = await screen.findByText(/查询与重置在已取回的列表内筛选/);
    expect(hint.textContent).toContain('GET /deliveries/openapi/endpoints');
    expect(hint.textContent).toContain('没有名称/状态/上下线参数');

    fireEvent.change(screen.getByPlaceholderText('请输入接口名称'), { target: { value: '模型预测' } });
    fireEvent.click(screen.getByRole('button', { name: /^查\s*询$/ }));
    await screen.findByText('共 1 个接口');
    expect(screen.queryByText('Qwen回答预测')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^重\s*置$/ }));
    await screen.findByText('共 2 个接口');
    expect(screen.getByText('Qwen回答预测')).toBeInTheDocument();
  });

  // 收口判据「卡内元素右界 ≤ 所属卡右界」：jsdom 量不到像素，就钉住尺寸本身——
  // 前七列逐列 = 图 3-1 列簇宽（170/190/160/100/190/70/80）；合计必须等于容器宽 1130，
  // 否则 tableLayout=fixed 下富余会被按比例摊到每一列，墨迹列沿整体漂移（实测累计 15px）。
  it('表体宽度收口：前七列逐列照图 + 最右列钉住合计，列宽合计 = scroll.x = 1130', async () => {
    renderTab();
    await screen.findByText('Qwen回答预测');
    const widths = Array.from(document.querySelectorAll('.ant-table-content col')).map(
      (col) => Number((col as HTMLElement).style.width.replace('px', '')),
    );
    expect(widths).toEqual([170, 190, 160, 100, 190, 70, 80, 170]);
    expect(widths.reduce((sum, width) => sum + width, 0)).toBe(1130);
    const table = document.querySelector('.ant-table-content table') as HTMLElement;
    expect(parseInt(table.style.width, 10)).toBe(1130);
  });

  it('列表视图把钻取段交给壳层面包屑（2026-10-17 全面包屑定稿，页内不再自绘）', async () => {
    renderTab();
    await screen.findByText('Qwen回答预测');
    expect(useCrumbStackStore.getState().stack).toEqual(['OpenAPI导出', '接口列表']);
  });

  it('删除走真实 DELETE，编辑只发后端 PATCH 接受的那三个字段', async () => {
    del.mockResolvedValue(undefined);
    patch.mockResolvedValue(epA);
    renderTab();
    await screen.findByText('Qwen回答预测');

    fireEvent.click(within(columnCells('操作')[0]).getByText('编辑'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('每分钟限流')).toBeInTheDocument();
    expect(within(dialog).getByText(/后端 PATCH 只接受状态、每分钟限流与总配额/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('switch'));
    fireEvent.click(within(dialog).getByRole('button', { name: /^保\s*存$/ }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    const [patchUrl, patchPayload] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(patchUrl).toBe(`/deliveries/openapi/endpoints/${epA.id}`);
    expect(Object.keys(patchPayload).sort()).toEqual(['quota_total', 'rate_limit_per_minute', 'status']);
    expect(patchPayload.status).toBe('disabled');

    fireEvent.click(within(columnCells('操作')[1]).getByText('删除'));
    fireEvent.click(await screen.findByRole('button', { name: '确认删除' }));
    await waitFor(() => expect(del).toHaveBeenCalledWith(`/deliveries/openapi/endpoints/${epB.id}`));
  });
});

// 20_000 的理由同上一组；新建屏还要并发取生产负载并挂整张表单，本机实测这一组 6 条共 6.73s、单条最慢 1.88s。
describe('图 3-2 新建API', { timeout: 20_000 }, () => {
  it('必填项只有图上带 * 的那三项', async () => {
    renderTab();
    await openCreateScreen();
    const required = Array.from(document.querySelectorAll('.ant-form-item-required')).map((el) => el.textContent);
    expect(required).toEqual(['接口名称', '关联任务', '接口描述']);
    for (const label of ['接口状态', '接口有效期', '安全保障', '查询模式', '查询范围']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it('默认态照图：胶囊开关默认上线、有效期默认永不过期，关联任务取后端已发布生产负载', async () => {
    renderTab();
    await openCreateScreen();
    const sw = screen.getByRole('switch');
    expect(sw.className).toContain('ant-switch');
    expect(sw.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByText('关闭后接口不可被外部调用')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '永不过期' })).toBeChecked();
    expect(screen.getByRole('radio', { name: '选择日期' })).not.toBeChecked();
    expect(get).toHaveBeenCalledWith(
      '/workloads',
      expect.objectContaining({ params: expect.objectContaining({ env: 'production', status: 'published' }) }),
    );

    const selector = document.getElementById('workload_id')?.closest('.ant-select')?.querySelector('.ant-select-selector');
    fireEvent.mouseDown(selector as Element);
    expect(await screen.findByText('生产预测负载 v3')).toBeInTheDocument();
    // 后端没有 IP 白名单字段：按平台实际行为选「不限制」并锁住，不假装能配
    expect(screen.getByRole('radio', { name: '不限制' })).toBeChecked();
    expect(screen.getByRole('radio', { name: '不限制' })).toBeDisabled();
    expect(screen.getByRole('radio', { name: '开放查询' })).toBeChecked();
    expect(screen.getByRole('radio', { name: '最新结果集' })).toBeChecked();
    expect(screen.getByPlaceholderText(/支持输入IP地址，多个IP地址用逗号隔开/)).toBeDisabled();
  });

  it('提交只发后端有的字段，开关留在上线时不再补 PATCH', async () => {
    post.mockResolvedValue({ ...epA, slug: 'qwen-new' });
    renderTab();
    await openCreateScreen();
    fireEvent.change(screen.getByPlaceholderText('请输入接口名称'), { target: { value: 'Qwen回答预测' } });
    fireEvent.change(screen.getByPlaceholderText('请输入接口描述'), { target: { value: '问答结果集导出' } });
    await pickWorkloadOption();
    fireEvent.click(screen.getByRole('button', { name: '新增接口' }));

    await waitFor(() => expect(post).toHaveBeenCalled());
    const [url, payload] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(url).toBe('/deliveries/openapi/endpoints');
    expect(payload).toMatchObject({
      contract_id: CONTRACT_ID,
      workload_id: 'w-1',
      name: 'Qwen回答预测',
      description: '问答结果集导出',
    });
    // 后端没有的字段一律不许塞进请求体
    for (const absent of ['ip_whitelist', 'query_mode', 'expires_at', 'slug']) {
      expect(payload).not.toHaveProperty(absent);
    }
    expect(patch).not.toHaveBeenCalled();
  });

  it('把接口状态关到离线：创建后补一次真实 PATCH 落 status=disabled', async () => {
    post.mockResolvedValue({ ...epA, slug: 'qwen-new' });
    patch.mockResolvedValue({ ...epA, slug: 'qwen-new', status: 'disabled' });
    renderTab();
    await openCreateScreen();
    fireEvent.change(screen.getByPlaceholderText('请输入接口名称'), { target: { value: '离线接口' } });
    fireEvent.change(screen.getByPlaceholderText('请输入接口描述'), { target: { value: '离线导出' } });
    await pickWorkloadOption();
    fireEvent.click(screen.getByRole('switch'));
    fireEvent.click(screen.getByRole('button', { name: '新增接口' }));

    await waitFor(() => expect(patch).toHaveBeenCalled());
    const [patchUrl, patchPayload] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(patchUrl).toBe(`/deliveries/openapi/endpoints/${epA.id}`);
    expect(patchPayload).toEqual({ status: 'disabled' });
  });

  it('第 1 字段带照图是三列栅格：接口名称 / 关联任务 / 接口状态 同一行 md={8}', async () => {
    renderTab();
    await openCreateScreen();
    const rows = ['接口名称', '关联任务', '接口状态'].map((label) => {
      const item = screen.getByText(label).closest('.ant-form-item') as HTMLElement;
      expect(item).not.toBeNull();
      const col = item.closest('.ant-col') as HTMLElement;
      expect(col.className).toContain('ant-col-md-8');
      return col.parentElement as HTMLElement;
    });
    // 三个字段共一行：父级必须是同一个 .ant-row（接口状态不再是另起的整行）
    expect(rows[1]).toBe(rows[0]);
    expect(rows[2]).toBe(rows[0]);
    expect(rows[0].className).toContain('ant-row');
  });

  it('图上的固定披露文案按产品能力写全，结果集字段不拿原型示例填充', async () => {
    renderTab();
    await openCreateScreen();
    expect(screen.getByText('关闭后接口不可被外部调用')).toBeInTheDocument();
    // 原型原句全文到这里只保留产品做得到的那半句，后半段改成诚实版（白名单/查询范围不可配）
    expect(
      screen.getByText('接口创建后默认按「上线」状态发布，可在列表中修改状态（安全白名单与查询范围暂不可配置）'),
    ).toBeInTheDocument();
    expect(screen.getByText(/接口创建后默认按「上线」状态发布，可在列表中修改状态/)).toBeInTheDocument();
    expect(screen.getByText(/来自关联合约的结果集结构/)).toBeInTheDocument();
    // 图上「接口描述*」是必填红星，后端却没有这个字段：页面必须写明填了不落库
    expect(screen.getByText(/后端端点记录没有描述字段/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^取\s*消$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新增接口' })).toBeInTheDocument();
    expect(headerTexts()).toEqual(['字段名', '字段类型', '算子']);
    // 原型示例（query/STRING、result/OBJECT、POST）一条都不许落到 DOM 上
    for (const sample of ['query', 'STRING', 'result', 'OBJECT', 'POST']) {
      expect(screen.queryByText(sample)).not.toBeInTheDocument();
    }
    expect(screen.getByText(/后端未下发结果集字段结构/)).toBeInTheDocument();
    // 被丢掉的半句不许偷偷装回屏上，也不许悄悄消失：差异说明在 Tooltip 里给到原句
    const foot = screen.getByText(/接口创建后默认按「上线」状态发布/);
    fireEvent.mouseEnter(foot);
    const note = await screen.findByText(/原型此处原句为/);
    expect(note.textContent).toContain('、安全白名单与查询范围');
    expect(note.textContent).toContain('后端端点记录没有安全白名单与查询范围字段');
  });

  it('新建视图钻取段 = OpenAPI导出 / 新建API（段文交给壳层渲染）', async () => {
    renderTab();
    await openCreateScreen();
    expect(useCrumbStackStore.getState().stack).toEqual(['OpenAPI导出', '新建API']);
  });
});
