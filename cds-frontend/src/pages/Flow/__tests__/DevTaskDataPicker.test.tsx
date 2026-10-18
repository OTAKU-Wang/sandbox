// 图 2-2「关联数据」卡的取数面：筛选带（查询/重置）、双页签形态、分页带口径。
//
// 重点盯分页的诚实性：本节点页签走服务端分页，后端 total 必须落到分页器上，
// 否则屏上的「共 N 条」等于把「本页已载入」自称成全量（A3-6e 的缺陷）。
// 只 mock 最外层 HTTP（services/api），取数器本体、置灰规则与分页联动都跑真代码。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import DevTaskDataPicker, { DATA_PICKER_SCOPE_HINT, PICKER_PAGE_SIZE } from '../DevTaskDataPicker';
import type { ContractOption } from '../useContractOptions';
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

const CONTRACT: ContractOption = {
  id: 'c-1',
  title: '客单价合约',
  contract_no: 'CT-0001',
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
};

/** 后端在册 37 条：单页 10 条时「共 N 条」必须是 37，不能是本页行数。 */
const SERVER_TOTAL = 37;

function nodeProduct(index: number) {
  return {
    id: `p-node-${index}`,
    name: `本节点登记产品 ${index}`,
    description: null,
    product_type: 'tabular',
    status: 'published',
    resource_id: `r-node-${index}`,
  };
}

/** 记住每次打给 /data-products 的查询参数，用来证明换页/换页长真的重新取数。 */
let listParams: Record<string, unknown>[] = [];

function mockRoutes() {
  listParams = [];
  get.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/data-products/p-9') return Promise.resolve(GRANTED);
    if (url === '/data-products') {
      const params = config?.params ?? {};
      listParams.push(params);
      const skip = Number(params.skip ?? 0);
      const limit = Number(params.limit ?? PICKER_PAGE_SIZE);
      const count = Math.max(0, Math.min(limit, SERVER_TOTAL - skip));
      const items = Array.from({ length: count }, (_, offset) => nodeProduct(skip + offset + 1));
      return Promise.resolve({ items, total: SERVER_TOTAL, page: Math.floor(skip / limit) + 1, page_size: limit });
    }
    return Promise.resolve(undefined);
  });
}

function renderPicker(onChange = vi.fn()) {
  renderWithProviders(<DevTaskDataPicker contract={CONTRACT} value={null} onChange={onChange} />);
}

/** 分页条文本：antd 把它渲染成 .ant-pagination-total-text 一项。 */
function totalText(): string | undefined {
  return document.querySelector('.ant-pagination-total-text')?.textContent;
}

async function openNodeTab() {
  fireEvent.click(screen.getByTestId('task-tab-node'));
  await screen.findByText('本节点登记产品 1');
}

beforeEach(() => {
  vi.clearAllMocks();
  mockRoutes();
});

describe('关联数据取数器（图 2-2 关联数据卡）', () => {
  it('页签是首页同款实心块形态（data-testid 挂 task-tab- 前缀），默认仍停在已购买页签', async () => {
    renderPicker();
    expect(await screen.findByText('授权零售表（PSI 可用）')).toBeInTheDocument();

    const purchased = screen.getByTestId('task-tab-purchased');
    const node = screen.getByTestId('task-tab-node');
    expect(purchased.tagName).toBe('BUTTON');
    expect(purchased).toHaveAttribute('aria-pressed', 'true');
    expect(node).toHaveAttribute('aria-pressed', 'false');
    // 不再是 antd Tabs 的 ink-bar：页签带里没有 role=tab，也没有 .ant-tabs-ink-bar。
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    expect(document.querySelector('.ant-tabs-ink-bar')).toBeNull();
    // 图 2-2 y1067..1097 实采：选中药签是实心蓝块白字（#1b6ff2），不是首页的浅底药签。
    expect(purchased.style.background).toBe('rgb(27, 111, 242)');
  });

  it('本节点页签的分页带用后端 total，不再把本页行数当全量', async () => {
    renderPicker();
    await screen.findByText('授权零售表（PSI 可用）');
    // 「我购买的数据产品」逐项取回、后端不给总数：只声明本页已载入。
    expect(await screen.findByText('本页已载入 1 条')).toBeInTheDocument();

    await openNodeTab();
    expect(totalText()).toBe('后端共 37 条，本页载入 10 条');
    // 37 条 / 每页 10 → 4 页，第 4 页的页码必须存在（旧写法只会渲染出 1 页）。
    expect(document.querySelector('.ant-pagination-item-4')).not.toBeNull();
    expect(document.querySelector('.cds-task-pager')).not.toBeNull();
  });

  it('分页带补齐 10条/页 与 前往 X 页；换页/换页长按后端那一页取数', async () => {
    renderPicker();
    await openNodeTab();

    const sizeChanger = document.querySelector('.ant-pagination-options-size-changer');
    const jumper = document.querySelector('.ant-pagination-options-quick-jumper input');
    expect(sizeChanger).not.toBeNull();
    expect(sizeChanger?.textContent).toContain(String(PICKER_PAGE_SIZE));
    expect(jumper).not.toBeNull();

    fireEvent.click(document.querySelector('.ant-pagination-item-2') as Element);
    await screen.findByText('本节点登记产品 11');
    await waitFor(() =>
      expect(listParams).toContainEqual(expect.objectContaining({ skip: 10, limit: PICKER_PAGE_SIZE })),
    );
    expect(totalText()).toBe('后端共 37 条，本页载入 10 条');
  });

  it('筛选带按图排序：重置在左、查询（蓝实心，口径进 Tooltip）在右', async () => {
    renderPicker();
    await screen.findByText('授权零售表（PSI 可用）');

    // 判据：group-A.md「图 A-3」筛选行 y998–1035 =「按钮 `重置`、`查询`」（图 A-2/C-4 同序）。
    const band = screen.getAllByRole('button', { name: /^(查\s*询|重\s*置)$/ });
    expect(band.map((button) => button.textContent?.trim())).toEqual(['重 置', '查 询']);
    const query = screen.getByRole('button', { name: /^查\s*询$/ });
    expect(query).toHaveClass('ant-btn-primary');

    // 取数口径不再常驻一行灰字，指到查询钮上照样读得到。
    expect(screen.queryByText(DATA_PICKER_SCOPE_HINT)).toBeNull();
    fireEvent.mouseOver(query);
    expect(await screen.findByText(DATA_PICKER_SCOPE_HINT)).toBeInTheDocument();
  });

  it('查询把关键词带给后端并回到第 1 页；重置清掉关键词与页码', async () => {
    renderPicker();
    await openNodeTab();

    fireEvent.change(screen.getByPlaceholderText('请输入数据产品名称'), { target: { value: '产品 3' } });
    fireEvent.click(screen.getByRole('button', { name: /^查\s*询$/ }));
    // 换筛选条件会先把当页清空重取（那一瞬分页器还没页码可点），等这一页真的渲染出来再往下走。
    await screen.findByText('本节点登记产品 1');
    await waitFor(() => expect(listParams).toContainEqual(expect.objectContaining({ q: '产品 3', skip: 0 })));

    fireEvent.click(document.querySelector('.ant-pagination-item-2') as Element);
    await waitFor(() => expect(listParams).toContainEqual(expect.objectContaining({ q: '产品 3', skip: 10 })));

    fireEvent.click(screen.getByRole('button', { name: /^重\s*置$/ }));
    // 重置后重新取的那一跳必须同时回到 q=空 与第 1 页（看最后录到的一条，不是历史上有没有过）。
    await waitFor(() =>
      expect(listParams.at(-1)).toEqual(expect.objectContaining({ q: undefined, skip: 0 })),
    );
    expect((screen.getByPlaceholderText('请输入数据产品名称') as HTMLInputElement).value).toBe('');
  });
});
