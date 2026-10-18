// SS-18 M2′「关联数据」看板用例：三种「没有数据」必须说得不一样，取不回的行不能悄悄消失。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import ContractDataBoard from '../ContractDataBoard';
import { CONTRACT_DATA_RESOLVE_LIMIT, buildContractDataSummary } from '../contractDataModel';
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

const product = (id: string) => ({
  id,
  name: `产品-${id}`,
  description: null,
  product_type: 'tabular',
  industry: null,
  status: 'published',
  provider_id: 'p-1',
  resource_id: null,
  row_count: 1200,
  data_schema: null,
  security_level: 'L2',
  allowed_operations: null,
  output_constraints: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z',
});

const settled = (ids: string[], rejected: string[] = []): PromiseSettledResult<never>[] =>
  ids.map((id) => (rejected.includes(id)
    ? { status: 'rejected', reason: new Error('403') }
    : { status: 'fulfilled', value: product(id) }) as PromiseSettledResult<never>);

describe('buildContractDataSummary', () => {
  it('取不回的留位并单列计数，不混进已授权数量', () => {
    const summary = buildContractDataSummary(['a', 'b'], settled(['a', 'b'], ['b']));
    expect(summary.granted).toBe(2);
    expect(summary.resolved).toBe(1);
    expect(summary.invisible).toBe(1);
    expect(summary.omitted).toBe(0);
    expect(summary.items[1].product).toBeNull();
  });

  it('超出解析上限的部分记为未取回，不假装已全部展示', () => {
    const ids = Array.from({ length: CONTRACT_DATA_RESOLVE_LIMIT + 3 }, (_, i) => `p${i}`);
    const summary = buildContractDataSummary(ids, settled(ids.slice(0, CONTRACT_DATA_RESOLVE_LIMIT)));
    expect(summary.granted).toBe(CONTRACT_DATA_RESOLVE_LIMIT + 3);
    expect(summary.omitted).toBe(3);
  });
});

describe('ContractDataBoard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('没选合约：一句提示，不发请求', () => {
    renderWithProviders(<ContractDataBoard contractNo={null} productIds={null} />);
    expect(screen.getByText(/选择关联合约后/)).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it('合约未下发清单：说没下发，不当成「授权了 0 项」', () => {
    renderWithProviders(<ContractDataBoard contractNo="CT-0001" productIds={null} />);
    expect(screen.getByText(/未下发授权数据清单/)).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it('下发了但是空的：明说该合约没绑数据产品', () => {
    renderWithProviders(<ContractDataBoard contractNo="CT-0001" productIds={[]} />);
    expect(screen.getByText(/该合约未绑定数据产品/)).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  it('逐项取回：一项取不回就在表里标注无权查看，汇总写清已取回与无权查看', async () => {
    get.mockImplementation((url: string) => (url.includes('p2')
      ? Promise.reject(new Error('403 forbidden'))
      : Promise.resolve(product(url.slice(url.lastIndexOf('/') + 1)))));

    renderWithProviders(<ContractDataBoard contractNo="CT-0001" productIds={['p1', 'p2']} />);

    expect(await screen.findByText('产品-p1')).toBeInTheDocument();
    expect(screen.getByText('无权查看（未发布）')).toBeInTheDocument();
    expect(screen.getByText('授权数据')).toBeInTheDocument();
    expect(screen.getByText(/2 项（已取回 1，无权查看 1）/)).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.getByText('数据随合约整体授权进入沙箱（只读），任务不能单独勾选其中几份；能否离开开发环仍由出口网关与合约导出白名单把关。')).toBeInTheDocument();
  });
});
