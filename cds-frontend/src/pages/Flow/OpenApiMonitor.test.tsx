// 图 3-3「接口监控」（img-specs/group-C1.md 图 3 / group-C.md 图 C-3）：这张屏不画任何图表，
// 只有 3 张 KPI 卡（调用总次数（次）/最新调用时间/调用成功率，数值主色蓝）+ 一张 4 列调用流水表。
// 铁律不变：每个数字都只能来自后端返回；后端没下发成功/失败判定时成功率记「未记录」并给原因，
// 绝不允许推算成 100%（下面几条锁定用例逐条保留）。只 mock 最外层 HTTP（services/api），
// services/flow 与页面逻辑都跑真代码。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { renderWithProviders } from '../../test/test-utils';
import { OpenApiMonitorView } from './DeliveryCenter';
import { formatMinute } from './display';
import { useCrumbStackStore } from '../../stores/crumbStackStore';
import { formatDateTime } from '../../services/flow';
import type { ApiEndpoint, ApiEndpointCall } from '../../services/flow';
import api from '../../services/api';

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

vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const endpoint: ApiEndpoint = {
  id: 'ep-1',
  contract_id: 'c-1',
  workload_id: 'w-1',
  name: '流失评分查询接口',
  slug: 'churn-score',
  request_schema: null,
  rate_limit_per_minute: 60,
  quota_total: 500,
  status: 'enabled',
  path: '/open/v1/churn-score',
  published_at: '2026-08-07T10:24:59.000Z',
};

// 图上「关联任务：TEE模型预测」这一行只能来自后端负载接口，故意与端点名不同。
const workload = {
  id: 'w-1',
  name: '生产预测负载 v3',
  contract_id: 'c-1',
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

/** 明细行样板：字段名与后端 _call_dict 逐字一致，各用例只覆盖自己要验证的那几个。 */
function callRow(overrides: Partial<ApiEndpointCall> = {}): ApiEndpointCall {
  return {
    id: 'call-aaaa',
    endpoint_id: 'ep-1',
    slug: 'churn-score',
    status: 'succeeded',
    code: null,
    message: null,
    output_rows: 10,
    duration_ms: 120,
    request_sha256: 'sha-aaaa',
    caller_id: 'u-aaaa',
    client_ip: '203.0.113.9',
    created_at: '2026-10-16T01:00:00.000Z',
    ...overrides,
  };
}

const listOf = (items: ApiEndpointCall[], total: number) => ({ items, total, page: 1, page_size: 100 });

function renderMonitor() {
  return renderWithProviders(
    <ConfigProvider locale={zhCN}>
      <OpenApiMonitorView endpoint={endpoint} onClose={() => undefined} />
    </ConfigProvider>,
  );
}

/** 只读宫格的数值本体（不含标题），断言精确值而不是"页面上出现过这个数字"。 */
function tileValue(testId: string): string {
  const tile = screen.getByTestId(testId).querySelector('.ant-statistic-content-value');
  return tile?.textContent ?? '';
}

function headerTexts(): string[] {
  return Array.from(document.querySelectorAll('.ant-table-thead th')).map((th) => th.textContent ?? '');
}

/** 按表头取「调用时间」整列的文本（只盯时间口径，不靠“页面上出现过”）。 */
function timeCellTexts(): string[] {
  const index = headerTexts().indexOf('调用时间');
  if (index < 0) throw new Error('没有「调用时间」这一列');
  return Array.from(document.querySelectorAll('.ant-table-tbody tr.ant-table-row')).map(
    (row) => (row as HTMLTableRowElement).cells[index].textContent ?? '',
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

// 20_000 的理由：每条用例都要挂整页 antd（Table / Statistic / Tag）并等 react-query 落地。
// 本机实测（2026-10-17，vitest --reporter=verbose，见 fe-results-report.md）：11 条共 3.54s，
// 单条最慢 946ms（首条含模块冷启动）。等的一律是「查询落地后的 DOM」，没有固定 sleep；
// 抬高的只是默认 5s 上限，避免整台机器满载时误红而不是被测行为变了。
describe('接口监控页（图 3-3）', { timeout: 20_000 }, () => {
  it('按端点 ID 打真实的调用明细端点', async () => {
    get.mockResolvedValue(listOf([], 0));
    renderMonitor();
    await screen.findByText(/暂无调用明细/);
    expect(get).toHaveBeenCalledWith(
      '/deliveries/openapi/endpoints/ep-1/calls',
      expect.objectContaining({ params: expect.objectContaining({ page: 1 }) }),
    );
    // 图上没有筛选控件：PRD 版的状态下拉筛选撤下
    expect(screen.queryByText('全部状态')).not.toBeInTheDocument();
  });

  // 图片真值：明细表就是这 4 列（group-C1.md 图 3「恰 4 列表头」）。PRD 版的
  // 「来源 IP / 耗时」在图上不存在，这里把断言换成图片真值，用例本身保留。
  it('明细表列与顺序照图：序号 / 调用ID / 调用时间 / 调用状态', async () => {
    get.mockResolvedValue(listOf([callRow()], 1));
    renderMonitor();
    await screen.findByText('call-aaaa');
    expect(headerTexts()).toEqual(['序号', '调用ID', '调用时间', '调用状态']);
    for (const title of ['序号', '调用ID', '调用时间', '调用状态']) {
      expect(within(screen.getByRole('columnheader', { name: title })).getByText(title)).toBeInTheDocument();
    }
    expect(screen.queryByText('来源 IP')).not.toBeInTheDocument();
    expect(screen.queryByText('耗时')).not.toBeInTheDocument();
    // 撤下的两列不许改成"顺手把值塞进别的列"：后端下发的 IP/耗时都不上图
    expect(screen.queryByText('203.0.113.9')).not.toBeInTheDocument();
    expect(screen.queryByText('120 ms')).not.toBeInTheDocument();
  });

  it('三宫格与明细表渲染后端下发的真实值：总数取 total，最新时间取明细里最晚一条', async () => {
    // total 故意与本页行数不同；明细故意不按时间倒序摆，最新那条不在第一行。
    get.mockResolvedValue(
      listOf(
        [
          callRow({ id: 'call-1', created_at: '2026-10-16T01:00:00.000Z', duration_ms: 1234, client_ip: '203.0.113.9' }),
          callRow({ id: 'call-2', created_at: '2026-10-17T02:30:00.000Z', duration_ms: 22, client_ip: '203.0.113.10' }),
          callRow({
            id: 'call-3',
            created_at: '2026-10-15T09:00:00.000Z',
            status: 'failed',
            code: 'RATE_LIMITED',
            message: '超过每分钟 60 次限制',
            duration_ms: null,
            output_rows: null,
            client_ip: null,
          }),
        ],
        42,
      ),
    );
    renderMonitor();
    await screen.findByText('call-2');

    expect(tileValue('monitor-total')).toBe('42');
    expect(tileValue('monitor-latest')).toBe(formatDateTime('2026-10-17T02:30:00.000Z'));
    expect(tileValue('monitor-rate')).toBe('66.7%');
    expect(screen.getByText('调用总次数（次）')).toBeInTheDocument();
    expect(screen.getByText('最新调用时间')).toBeInTheDocument();
    expect(screen.getByText('调用成功率')).toBeInTheDocument();

    // 明细表：调用 ID 与状态判定原样出现在表里（图上只有这 4 列）
    expect(screen.getByText('call-1')).toBeInTheDocument();
    expect(screen.getAllByText('调用成功')).toHaveLength(2);
    expect(screen.getByText('限流阻断')).toBeInTheDocument();
    // 调用时间按图到分钟：与 formatMinute 等价，且不带秒段（不等于到秒的 formatDateTime）
    expect(timeCellTexts()).toEqual([
      formatMinute('2026-10-16T01:00:00.000Z'),
      formatMinute('2026-10-17T02:30:00.000Z'),
      formatMinute('2026-10-15T09:00:00.000Z'),
    ]);
    expect(timeCellTexts().every((text) => text.split(':').length === 2)).toBe(true);
    expect(timeCellTexts()).not.toContain(formatDateTime('2026-10-17T02:30:00.000Z'));
    // 图上没有图表：不许出现进度条/图形元素
    expect(document.querySelector('.ant-progress, canvas')).toBeNull();
    // 「数据更新至」取后端下发的最晚调用时间（同样到分钟），不用当前时间
    expect(
      screen.getByText(new RegExp(`数据更新至 ${formatMinute('2026-10-17T02:30:00.000Z')}`)),
    ).toBeInTheDocument();
  });

  it('明细未带成功/失败判定：成功率记「未记录」并说明原因，不推算成 100%', async () => {
    // 老数据形态：有 id/时间/耗时，但整批都没有成功或失败判定字段。
    get.mockResolvedValue(
      listOf(
        [
          callRow({ id: 'bare-1', status: undefined, code: undefined, message: undefined }),
          callRow({ id: 'bare-2', status: undefined, code: undefined, message: undefined, created_at: '2026-10-17T05:00:00.000Z' }),
        ],
        7,
      ),
    );
    renderMonitor();
    await screen.findByText('bare-2');

    expect(tileValue('monitor-rate')).toContain('未记录');
    expect(tileValue('monitor-rate')).not.toContain('%');
    expect(screen.getByText(/未带成功\/失败判定/)).toBeInTheDocument();
    // 整个页面里不许出现编造出来的满分
    expect(document.body.textContent).not.toContain('100%');
    // 总数与最新时间仍按后端下发的值渲染，不受成功率缺失影响
    expect(tileValue('monitor-total')).toBe('7');
    expect(tileValue('monitor-latest')).toBe(formatDateTime('2026-10-17T05:00:00.000Z'));
  });

  it('一条调用都没有：成功率与最新时间都记「未记录」，不写 0%、不写当前时间', async () => {
    get.mockResolvedValue(listOf([], 0));
    renderMonitor();
    // 先等数据落地再读宫格：空列表下「未记录」也会出现在加载中，不能拿它当数据到位的信号。
    await screen.findByText(/暂无调用明细/);

    expect(tileValue('monitor-total')).toBe('0');
    expect(tileValue('monitor-latest')).toBe('未记录');
    expect(tileValue('monitor-rate')).toBe('未记录');
    expect(screen.getByText(/暂无调用明细/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('100%');
    expect(document.body.textContent).not.toContain('0%');
    expect(screen.getByText(/数据更新至 未记录/)).toBeInTheDocument();
  });

  it('后端未下发 total 时按窗口条数兜底，并把口径写在宫格注释里', async () => {
    get.mockResolvedValue({ items: [callRow({ id: 'no-total-1' })], page: 1, page_size: 100 });
    renderMonitor();
    await screen.findByText('no-total-1');

    expect(tileValue('monitor-total')).toBe('1');
    expect(screen.getByText(/后端未下发总数/)).toBeInTheDocument();
  });

  it('KPI 数值照图取主色蓝，卡片带标题与单位后缀，卡间一条 1px 细竖线', async () => {
    get.mockResolvedValue(listOf([callRow()], 3));
    renderMonitor();
    await screen.findByText('call-aaaa');
    for (const testId of ['monitor-total', 'monitor-latest', 'monitor-rate']) {
      const value = screen.getByTestId(testId).querySelector('.ant-statistic-content') as HTMLElement;
      // 主色蓝＝原型像素探针值 #1B6EF3（页眉/当前页签/主按钮同一支蓝，pngprobe.py 10 屏同源）。
      expect(value.style.color).toBe('rgb(27, 110, 243)');
    }
    // 图上三张卡无独立底色，只靠卡间一条 1px #eef1f6 竖线分隔；首卡左边没线。
    // 竖线本轮起走共用件 .cds-kpi-divider（index.css）—— inline 样式压不过窄屏 media query；
    // jsdom 不加载全站 CSS，所以这里锁结构契约，像素值由真 Chrome 量（§15.3）。
    const tileBox = (testId: string) => screen.getByTestId(testId).parentElement as HTMLElement;
    expect(tileBox('monitor-total').className).not.toContain('cds-kpi-divider');
    for (const testId of ['monitor-latest', 'monitor-rate']) {
      expect(tileBox(testId).className).toContain('cds-kpi-divider');
    }
  });

  it('标题区照图：接口正常/已上线是带底色 chip（列表页才是纯文字），三个按钮齐且图标在位', async () => {
    get.mockResolvedValue(listOf([], 0));
    renderMonitor();
    await screen.findByText(/暂无调用明细/);
    for (const label of ['接口正常', '已上线']) {
      expect(screen.getByText(label).closest('.ant-tag')).not.toBeNull();
    }
    // 图 3-3（2026-10-17 PNG 复量 x2 目检）：两枚 chip 都带图标——绿勾选 + 蓝链形，
    // 早先小图误读成「已上线无图标」，以复量为准。
    expect(screen.getByText('接口正常').closest('.ant-tag')?.querySelector('.anticon-check-circle')).not.toBeNull();
    expect(screen.getByText('已上线').closest('.ant-tag')?.querySelector('.anticon-link')).not.toBeNull();
    // 带 icon 的按钮取不到可及名（简报第 4 条）：用文本回溯到按钮本体再查图标
    const back = screen.getByText('返回列表').closest('button') as HTMLElement;
    expect(back).not.toBeNull();
    expect(back.querySelector('.anticon-left')).not.toBeNull();
    const edit = screen.getByText('编辑接口').closest('button') as HTMLElement;
    expect(edit).not.toBeNull();
    expect(edit.querySelector('.anticon-edit')).not.toBeNull();
    expect(screen.getByRole('button', { name: /刷新数据/ })).toBeInTheDocument();
    // 图上没有「调用路径」这一行，apiUrl 只在 3-1 列表页出现
    expect(screen.queryByText('/open/v1/churn-score')).not.toBeInTheDocument();
  });

  it('监控视图钻取段 = OpenAPI导出 / 接口监控（壳层统一渲染，2026-10-17 全面包屑定稿）', async () => {
    get.mockResolvedValue(listOf([], 0));
    renderMonitor();
    await screen.findByText(/暂无调用明细/);
    expect(useCrumbStackStore.getState().stack).toEqual(['OpenAPI导出', '接口监控']);
  });

  it('「关联任务」取后端负载接口的名字，不拿端点名冒充', async () => {
    get.mockImplementation((url: string) => {
      if (url === '/workloads/w-1') return Promise.resolve(workload);
      if (url.endsWith('/calls')) return Promise.resolve(listOf([], 0));
      return Promise.resolve(null);
    });
    renderMonitor();
    await screen.findByText(/关联任务：生产预测负载 v3/);
    expect(get).toHaveBeenCalledWith('/workloads/w-1');
    expect(screen.getByText(/API ID：ep-1/)).toBeInTheDocument();
    expect(screen.getByText(/有效期：永不过期/)).toBeInTheDocument();
  });

  it('明细分页带照图：共 N 条 + 10 条/页 + 前往 __ 页', async () => {
    const rows = Array.from({ length: 12 }, (_, index) => callRow({ id: `call-${index}` }));
    get.mockResolvedValue(listOf(rows, 18));
    renderMonitor();
    await screen.findByText('call-0');
    expect(screen.getByText('共 12 条')).toBeInTheDocument();
    expect(screen.getByText(/10 条\/页/)).toBeInTheDocument();
    // 「前往 __ 页」是 antd quick-jumper 里的裸文本节点，只能整段取
    const jumper = document.querySelector('.ant-pagination-options-quick-jumper') as HTMLElement;
    expect(jumper.textContent).toContain('前往');
    expect(jumper.textContent).toContain('页');
    // 「共 12 条」是本次取回的窗口条数，后端总数 18 单独写明，不冒充全量
    expect(screen.getByText(/后端共 18 次调用/)).toBeInTheDocument();
  });
});
