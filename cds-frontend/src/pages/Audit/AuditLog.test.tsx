// 原型 4-1/4-2 页面级用例（只 mock services/auditApi 这一层）：
// ① 列表只有图片真值的 6 列，操作人用后端 join 出的 username，操作结果用后端派生的 outcome；
// ② 导出把「当前所选导出窗口」原样带给 GET /audit/export，且只带 days + format（写死 days 的实现必须红）；
// ③ 后端 require_roles 不含的角色只能看到禁用态，点击不发请求；
// ④ 类型页签发 event_type、操作类型下拉发后端真实 action 代码，两者不得混用；
// ⑤ 筛选四件套（关键字 / 操作结果 / 操作时间 / 重置）落成对应查询参数；
// ⑥ 详情弹窗按 720 弹窗形态取详情接口的超集字段；库里没有的字段宁可不显示，也不合成。
// ⑦ 第二轮逐屏对账（差距表 4-1.2 / 4-1.4 / 4-1.6 / 4-1.7 / 4-1.9 / 4-2.1 / 4-2.3 / 4-2.6）新增：
//    标题带两钮 40px 档、导出窗口不在筛选带、关键字用共用 .cds-filter-field 且无放大镜、
//    页签是原生 button（选中浅蓝底主色字）、时间三处统一 YYYY-MM-DD HH:mm:ss、
//    弹窗挂共用件 .cds-detail-modal 且三段 Descriptions 无格线、detail 原文默认收起。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../test/test-utils';
import AuditLog from './AuditLog';
import { auditApi, type AuditRecord, type AuditRecordDetail } from '../../services/auditApi';
import { AUDIT_TABS } from './auditDisplay';
import { useAuthStore } from '../../stores/authStore';
import { UserRole } from '../../types/enums';

// antd Table/Modal/Select 的响应式断点依赖 window.matchMedia（jsdom 未实现）
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

vi.mock('../../services/auditApi', () => ({
  auditApi: {
    list: vi.fn(),
    get: vi.fn(),
    anchor: vi.fn(),
    verify: vi.fn(),
    getMerkleProof: vi.fn(),
    export: vi.fn(),
  },
}));

function makeRecord(overrides: Partial<AuditRecord> = {}): AuditRecord {
  return {
    id: '9b75d339-0000-4000-8000-000000000001',
    user_id: 'user-8899aabb',
    username: '李娜',
    organization: '沈阳数据中心R',
    role: 'operator',
    display_no: 'LOG-20261017-020000-9b75',
    session_id: null,
    action: 'delivery.dataset.downloaded',
    resource_type: 'delivery_bundle',
    resource_id: 'bundle-2026-1017',
    detail: { bundle: 'bundle-2026-1017' },
    ip_address: '100.112.3.84',
    blockchain_tx_hash: null,
    created_at: '2026-10-17T02:00:00Z',
    outcome: 'success',
    event_type: 'export',
    ...overrides,
  };
}

/** 详情接口比列表多 related_contract（列表不 JOIN 合约表）。 */
function makeDetail(overrides: Partial<AuditRecordDetail> = {}): AuditRecordDetail {
  return {
    ...makeRecord({ event_type: 'export', action: 'delivery.dataset.downloaded', blockchain_tx_hash: '0x9f3a' }),
    related_contract: { contract_no: 'CDS-2026-0812-A1', title: '用户行为分析模型-合约A' },
    ...overrides,
  };
}

function setRole(role: UserRole) {
  useAuthStore.setState({
    user: { id: 'u-1', username: 'auditor', email: 'a@t.cn', role, organization: null },
  });
}

const clickedDownloads: string[] = [];
const createObjectURL = vi.fn(() => 'blob:audit-export');

beforeEach(() => {
  vi.clearAllMocks();
  clickedDownloads.length = 0;
  // jsdom 既没有可用的 createObjectURL，也不会处理 a[download]：两处都接管掉，
  // 断言才落在「页面是否真的把 Blob 交给浏览器落盘」这件事上。
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    clickedDownloads.push(this.download);
  });
  setRole(UserRole.OPERATOR);
  vi.mocked(auditApi.list).mockResolvedValue({
    items: [makeRecord()],
    total: 186,
    page: 1,
    page_size: 10,
  });
});

async function renderPage() {
  renderWithProviders(<AuditLog />);
  await screen.findByText('操作记录');
  await waitFor(() => expect(auditApi.list).toHaveBeenCalled());
}

/** 按字段名定位下拉：筛选项文本互相包含（「全部」/「全部类型」），按下标或包含关系都会选错。 */
async function openFieldSelect(fieldLabel: string) {
  const trigger = await waitFor(() => {
    // 图 4-1 筛选带（y247–282）：灰字标签与控件同行，同处一个 .cds-filter-field。
    // 筛选项文本互相包含（「全部」/「全部类型」），按标签精确等值定位才不会选错。
    const field = Array.from(document.querySelectorAll('.cds-filter-field'))
      .find((node) => node.querySelector('.cds-filter-label')?.textContent?.trim() === fieldLabel);
    const host = field?.querySelector('.ant-select-selector');
    if (!host) throw new Error(`字段「${fieldLabel}」的下拉没渲染`);
    return host as Element;
  });
  fireEvent.mouseDown(trigger);
  return trigger;
}

async function openSelectByText(marker: string) {
  const trigger = await waitFor(() => {
    const found = Array.from(document.querySelectorAll('.ant-select')).find((node) =>
      node.textContent?.includes(marker));
    if (!found) throw new Error(`没有文本含「${marker}」的下拉`);
    return found.querySelector('.ant-select-selector') as Element;
  });
  fireEvent.mouseDown(trigger);
  return trigger;
}

async function clickOption(label: string) {
  const option = await waitFor(() => {
    const found = Array.from(
      document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content'),
    ).find((node) => node.textContent === label || node.textContent?.includes(label));
    if (!found) throw new Error('下拉里没有该选项');
    return found;
  });
  fireEvent.click(option);
}

async function modalBody() {
  return waitFor(() => {
    const element = document.querySelector('.ant-modal-body');
    if (!element) throw new Error('详情弹窗没打开');
    return element as HTMLElement;
  });
}

/** antd 会在两字按钮里插全角空格，按 textContent 去空格比对才稳。 */
function clickButton(label: string) {
  const button = Array.from(document.querySelectorAll('button'))
    .find((node) => node.textContent?.replace(/\s/g, '') === label);
  if (!button) throw new Error(`没有按钮「${label}」`);
  fireEvent.click(button);
}

async function modalRowReader() {
  const body = await modalBody();
  return (label: string) => {
    // 4-2 真值是竖排标签（PNG 4-2 x=30 纵切：y28..41 深色标签在上、y54..63 浅色值在下），
    // antd 竖排把 label 与 content 拆成相邻两个 tr（Row.js: vertical ⇒ label-tr + content-tr），
    // 所以不能再按「同一 tr 取整行」配对：按文档序把 label 格与 content 格一一对齐。
    // 两列数必须相等，不等就是 DOM 契约变了，报错而不是默默配错格。
    const labels = Array.from(body.querySelectorAll('.ant-descriptions-item-label')) as HTMLElement[];
    const contents = Array.from(body.querySelectorAll('.ant-descriptions-item-content')) as HTMLElement[];
    if (labels.length !== contents.length) {
      throw new Error(`详情弹窗标签格 ${labels.length} 个与值格 ${contents.length} 个不对齐`);
    }
    const i = labels.findIndex((node) => (node.textContent ?? '').trim() === label);
    if (i < 0) throw new Error(`详情弹窗里没有字段「${label}」`);
    return `${label}${contents[i]?.textContent ?? ''}`;
  };
}

describe('AuditLog（原型 4-1 / 4-2）', { timeout: 20_000 }, () => {
  // 本页首屏并发列表 + 角色判定 + 弹窗详情三段 query，默认 5s 在全量并行下会偶发超时
  it('列表按图片真值只有 6 列，操作人用后端 join 的名字、操作结果用后端派生值', async () => {
    await renderPage();
    const tbody = within(document.querySelector('.ant-table-tbody') as HTMLElement);
    await tbody.findByText('结果集导出');
    const thead = within(document.querySelector('.ant-table-thead') as HTMLElement);
    const headers = thead.getAllByText(/操作|来源 IP/).map((th) => th.textContent);
    expect(headers).toEqual(['操作时间', '操作类型', '操作人', '来源 IP', '操作结果', '操作']);

    const tr = (tbody.getByText('李娜').closest('tr'))!;
    expect(tr.textContent).toContain('李娜');
    expect(tr.textContent).not.toContain('user-8899aabb');
    expect(tr.textContent).toContain('成功');
    expect(tr.textContent).not.toContain('未记录');
    expect(screen.getByText('共 186 条记录')).toBeTruthy();
    // 后两者的中文文案来自 App 根部 ConfigProvider 的 zh_CN locale，测试装配层不挂 locale，
    // 因此这里只断言页面确实开启了这两个控件，「10条/页 / 前往 __ 页」的中文在 Chrome 实机复验。
    expect(document.querySelector('.ant-pagination-options-size-changer')).toBeTruthy();
    expect(document.querySelector('.ant-pagination-options-quick-jumper')).toBeTruthy();
  });

  it('导出按固定 30 天窗口调 /audit/export（图上无窗口选择件），并按后端同名文件落盘', async () => {
    vi.mocked(auditApi.export).mockResolvedValue({
      blob: new Blob(['id,action\n'], { type: 'text/csv' }),
      filename: 'audit_export_90d.csv',
      contentType: 'text/csv',
    });
    await renderPage();

    fireEvent.click(within(document.querySelector('.ant-card-head') as HTMLElement).getByText('数据调用'));
    await waitFor(() => expect(auditApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ event_type: 'call' }),
    ));

    clickButton('导出日志');

    // 图 4-1 §1.3：页头只有导出日志+刷新，导出固定 30 天；后端只认 days+format，
    // 列表页签条件不得混进导出参数（多带的筛选后端会直接忽略）。
    await waitFor(() => expect(auditApi.export).toHaveBeenCalledWith({ days: 30, format: 'csv' }));
    expect(auditApi.export).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(clickedDownloads).toEqual(['audit_export_90d.csv']));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it('后端 require_roles 之外的角色：导出按钮禁用且点击不发请求', async () => {
    setRole(UserRole.BUYER);
    await renderPage();

    const button = screen.getByRole('button', { name: /导出日志/ });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(auditApi.export).not.toHaveBeenCalled();

    setRole(UserRole.REGULATOR);
    await waitFor(() => expect(screen.getByRole('button', { name: /导出日志/ })).toBeEnabled());
  });

  it('类型页签发 event_type，操作类型下拉发真实 action 代码，刷新重发列表请求', async () => {
    await renderPage();
    const listCalls = vi.mocked(auditApi.list).mock.calls.length;

    const tabHead = within(document.querySelector('.ant-card-head') as HTMLElement);
    expect(AUDIT_TABS.map((tab) => tab.label)).toEqual(['创建调试沙箱', '进入生产环境', '结果集导出', '数据调用']);
    expect(tabHead.getByText('全部')).toBeTruthy();

    fireEvent.click(tabHead.getByText('创建调试沙箱'));
    await waitFor(() => expect(auditApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, page_size: 10, event_type: 'dev_sandbox' }),
    ));

    await openFieldSelect('操作类型');
    await clickOption('compute.run.requested');
    clickButton('查询');
    await waitFor(() => expect(auditApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'compute.run.requested' }),
    ));

    clickButton('刷新');
    await waitFor(() => expect(vi.mocked(auditApi.list).mock.calls.length).toBeGreaterThan(listCalls));
  });

  it('筛选四件套落成查询参数（关键字回车即提交），重置把参数清空', async () => {
    await renderPage();

    const keyword = screen.getByPlaceholderText('操作对象 / 操作人');
    fireEvent.change(keyword, { target: { value: 'bundle-2026-1017' } });
    // 关键字是普通 Input（图上无放大镜）：回车就该提交，不靠 Input.Search 的 onSearch
    fireEvent.keyDown(keyword, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(auditApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ keyword: 'bundle-2026-1017' }),
    ));

    await openFieldSelect('操作结果');
    await clickOption('失败');
    clickButton('查询');

    await waitFor(() => expect(auditApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ keyword: 'bundle-2026-1017', outcome: 'failure' }),
    ));

    clickButton('重置');
    await waitFor(() => expect(auditApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ keyword: undefined, outcome: undefined, event_type: undefined }),
    ));
  });

  it('详情用详情接口的超集字段：日志编号/操作人/关联合约/操作结果都取后端下发值', async () => {
    const detail = makeDetail({
      detail: { bundle: 'bundle-2026-1017', env: 'env-16g-20260812-095840', sandbox_level: 'L3', duration_ms: 1280 },
    });
    vi.mocked(auditApi.get).mockResolvedValue(detail);
    await renderPage();

    fireEvent.click(screen.getByText('详情'));
    await screen.findByText('操作日志详情');
    await waitFor(() => expect(auditApi.get).toHaveBeenCalledWith(detail.id));

    const content = await waitFor(() => {
      const element = document.querySelector('.ant-modal-content');
      if (!element) throw new Error('详情弹窗没打开');
      return element as HTMLElement;
    });
    await waitFor(() => expect(content.textContent).toContain('用户行为分析模型-合约A'));
    expect(content.textContent).toContain('LOG-20261017-020000-9b75');
    expect(content.textContent).not.toContain(`#${detail.id}`);
    expect(content.textContent).toContain('李娜（沈阳数据中心R / 运营方）');
    expect(content.textContent).toContain('成功');
    expect(content.textContent).toContain('L3');
    expect(content.textContent).toContain('1.28s');
    expect(content.textContent).not.toContain('未记录');
  });

  it('详情接口没给字段时整行不显示，页底点名缺了哪几项，绝不合成单位/耗时', async () => {
    const bare = makeDetail({
      username: null,
      display_no: null,
      detail: { endpoint: '/open/v1/dataset' },
    });
    vi.mocked(auditApi.get).mockResolvedValue(bare);
    await renderPage();

    fireEvent.click(screen.getByText('详情'));
    const rowOf = await modalRowReader();
    await waitFor(() => expect(rowOf('操作对象')).toContain('bundle-2026-1017'));

    const body = await modalBody();
    expect(Array.from(body.querySelectorAll('.ant-descriptions-item-label'))
      .map((node) => node.textContent)).not.toContain('环境标识');
    expect(body.textContent).toContain('未随附：环境标识 / 关联数据 / 耗时');
    expect(rowOf('操作人')).toContain('未记录');
    expect(body.textContent).not.toContain('沈阳数据中心R');
    expect(body.textContent).not.toContain('1.28s');

    const content = document.querySelector('.ant-modal-content') as HTMLElement;
    expect(content.textContent).toContain(bare.id);
  });

  // ⑦-1 4-1.6 + 4-1.2：导出窗口属于导出、不属于筛选；标题带两钮走 40px 档
  it('页头卡右侧只有「导出日志 + 刷新」两颗钮（图 4-1 真值），筛选带只剩图上六件套', async () => {
    await renderPage();

    const filterCard = screen.getByPlaceholderText('操作对象 / 操作人').closest('.ant-card') as HTMLElement;
    // antd 给两字按钮插全角空格（DOM 里是「重 置」），比字串前先压空白
    const bandText = (filterCard.textContent ?? '').replace(/\s+/g, '');
    expect(bandText).not.toContain('导出近');
    for (const piece of ['操作类型', '关键字', '操作结果', '操作时间', '重置', '查询']) {
      expect(bandText).toContain(piece);
    }

    const headCard = document.querySelector('.cds-audit-pagehead') as HTMLElement;
    expect(headCard).toBeTruthy();
    // 图 4-1 §1.3：页头右侧就是下载钮+实心刷新两颗，屏上无第三个控件（导出窗口选择件不在图上）。
    expect(headCard.querySelector('.ant-select')).toBeNull();
    expect(headCard.querySelectorAll('button')).toHaveLength(2);
    expect(headCard.contains(filterCard as HTMLElement)).toBe(false);

    // 图上「刷新 97×40 / 导出日志 124×40」都是 large 档
    for (const name of [/导出日志/, /刷新/]) {
      expect(screen.getByRole('button', { name }).className).toContain('ant-btn-lg');
    }
  });

  // ⑦-2 4-1.4：关键字字段有共用件灰字标签，且不是带放大镜的 Input.Search
  it('关键字字段用共用件 .cds-filter-field/.cds-filter-label，筛选带里没有放大镜按钮', async () => {
    await renderPage();

    const input = screen.getByPlaceholderText('操作对象 / 操作人');
    const field = input.closest('.cds-filter-field') as HTMLElement;
    expect(field).toBeTruthy();
    expect(field.querySelector('.cds-filter-label')?.textContent).toBe('关键字');
    expect(document.querySelector('.ant-input-search')).toBeNull();
    expect(document.querySelector('.ant-input-search-button')).toBeNull();
  });

  // ⑦-3 4-1.7：页签不是 CheckableTag 的实心蓝底白字，而是首页同一套 tabStyle
  it('类型页签是原生 button：选中浅蓝底主色字，未选中白底 1px 灰边，筛选行为不变', async () => {
    await renderPage();

    const head = document.querySelector('.ant-card-head') as HTMLElement;
    const all = screen.getByTestId('task-tab-all');
    const dev = screen.getByTestId('task-tab-dev_sandbox');
    expect(all.tagName).toBe('BUTTON');
    expect(dev.tagName).toBe('BUTTON');
    expect(head.querySelectorAll('.ant-tag')).toHaveLength(0);
    expect(head.querySelectorAll('[data-testid^="task-tab-"]')).toHaveLength(5);

    expect(all.style.background).toBe('var(--cds-primary-soft)');
    expect(all.style.color).toBe('var(--cds-primary)');
    expect(dev.style.background).toBe('rgb(255, 255, 255)');
    expect(dev.style.border).toBe('1px solid rgb(223, 230, 238)');
    expect(dev.style.color).toBe('rgb(107, 114, 128)');

    fireEvent.click(dev);
    await waitFor(() => expect(auditApi.list).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, event_type: 'dev_sandbox' }),
    ));
    expect(screen.getByTestId('task-tab-dev_sandbox').style.background).toBe('var(--cds-primary-soft)');
    expect(screen.getByTestId('task-tab-all').style.background).toBe('rgb(255, 255, 255)');

    // 再点一次选中的页签回到「全部」（与原先 CheckableTag 的反选行为等价）
    fireEvent.click(screen.getByTestId('task-tab-dev_sandbox'));
    await waitFor(() => expect(auditApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ event_type: undefined }),
    ));
    expect(screen.getByTestId('task-tab-all').style.background).toBe('var(--cds-primary-soft)');
  });

  // ⑦-4 4-1.9 + 4-2.1：列表、弹窗标题、基础信息三处同一个时间口径
  it('时间三处统一 YYYY-MM-DD HH:mm:ss（连字符、补零、秒级），不是 2026/8/12 斜杠形态', async () => {
    const stamp = '2026-08-12T10:42:13';
    vi.mocked(auditApi.list).mockResolvedValue({
      items: [makeRecord({ created_at: stamp })], total: 186, page: 1, page_size: 10,
    });
    vi.mocked(auditApi.get).mockResolvedValue(makeDetail({ created_at: stamp }));
    await renderPage();

    const tbody = within(document.querySelector('.ant-table-tbody') as HTMLElement);
    const cell = await tbody.findByText('2026-08-12 10:42:13');
    expect(cell.textContent).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);

    fireEvent.click(screen.getByText('详情'));
    const header = await waitFor(() => {
      const node = screen.getByText('操作日志详情').closest('.ant-modal-header');
      if (!node?.textContent?.includes('生成时间')) throw new Error('弹窗标题还没带生成时间');
      return node as HTMLElement;
    });
    expect(header.textContent).toContain('生成时间 2026-08-12 10:42:13');

    const rowOf = await modalRowReader();
    expect(rowOf('操作时间')).toContain('2026-08-12 10:42:13');
    expect(rowOf('操作时间')).not.toMatch(/\d{4}\/\d/);
  });

  // ⑦-5 4-2.3 + 4-2.6 + 4-2.7：弹窗共用件、无网格线、图外的 detail 原文默认收起
  it('详情弹窗挂共用件 .cds-detail-modal，三段字段无格线，detail 原文默认收起', async () => {
    vi.mocked(auditApi.get).mockResolvedValue(makeDetail({
      detail: { bundle: 'bundle-2026-1017', env: 'env-16g-20260812-095840' },
    }));
    await renderPage();

    fireEvent.click(screen.getByText('详情'));
    const scoped = await waitFor(() => {
      const node = document.querySelector('.cds-detail-modal');
      if (!node?.querySelector('.ant-modal-footer')) throw new Error('弹窗没带上共用件类名或 footer');
      return node as HTMLElement;
    });
    expect(within(scoped).getByText('操作日志详情')).toBeTruthy();

    await waitFor(() => expect(scoped.querySelectorAll('.ant-descriptions')).toHaveLength(3));
    expect(scoped.querySelectorAll('.ant-descriptions-bordered')).toHaveLength(0);
    // footer 三钮同排同高（图上 footer 只有 32px 一档的「关闭」）
    expect(scoped.querySelectorAll('.ant-modal-footer .ant-btn-sm')).toHaveLength(0);

    const body = scoped.querySelector('.ant-modal-body') as HTMLElement;
    expect(body.textContent).not.toContain('"bundle":');
    fireEvent.click(body.querySelector('.ant-collapse-header') as HTMLElement);
    await waitFor(() => expect(body.textContent).toContain('"bundle": "bundle-2026-1017"'));
  });

  // ⑦-6 4-1.3：下拉 10 项都是后端真写过的 action，每项都有中文名（不再「code (code)」）
  it('操作类型下拉的 10 项都有中文名，且不掺后端没有的动作', async () => {
    await renderPage();
    await openFieldSelect('操作类型');

    const options = await waitFor(() => {
      const found = Array.from(document.querySelectorAll(
        '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content',
      ));
      expect(found).toHaveLength(10);
      return found;
    });
    const codes: string[] = [];
    for (const option of options) {
      const text = option.textContent ?? '';
      expect(text).not.toMatch(/^(\S+) \(\1\)$/);
      const parsed = /^(.+?) \((.+\..+)\)$/.exec(text);
      expect(parsed, `选项「${text}」不是「中文名 (code)」形态`).toBeTruthy();
      codes.push(parsed![2]);
    }
    expect([...codes].sort()).toEqual([
      'compute.run.requested', 'contract.sign', 'data_product.publish',
      'delivery.dataset.downloaded', 'notebook.dev_session_created', 'openapi.call',
      'retention.purge', 'sandbox.execute', 'user.login', 'user.login_failed',
    ]);
  });
});
