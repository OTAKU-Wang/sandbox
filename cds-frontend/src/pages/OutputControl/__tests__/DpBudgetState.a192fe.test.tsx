// OCR A192e 前端腿：DP 预算「读不到」在 UI 上必须说「未核实」，不许把 null 渲染成 0。
// 后端已把 GET /output-control/dp/budget/{id} 改成第三态（budget_state=unverified +
// epsilon_remaining=null）。这里钉三层：服务边界类型还表达得了 null 与三个新字段（静态腿）、
// 文案映射能区分「未核实」与「已耗尽」（纯函数腿）、页面真的上屏「未核实」（渲染腿）。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import InspectionPipeline, { dpBudgetView } from '../InspectionPipeline';
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
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;

const SESSION_ID = 'a192fe-budget-session-1';

function readSourceFromDisk(rel: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 5; depth += 1) {
    for (const base of [dir, join(dir, 'cds-frontend')]) {
      const candidate = join(base, rel);
      if (existsSync(candidate)) return readFileSync(candidate, 'utf8');
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`找不到源文件 ${rel}（cwd=${process.cwd()}）`);
}

function mockBudget(payload: Record<string, unknown>) {
  get.mockImplementation((url: string) => {
    if (url === '/sandbox-sessions') {
      return Promise.resolve({ items: [{ id: SESSION_ID, status: 'running', sandbox_level: 'l2c' }], total: 1 });
    }
    if (url.startsWith('/output-control/dp/budget/')) {
      return Promise.resolve({ session_id: SESSION_ID, ...payload });
    }
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
}

async function queryBudget() {
  const view = renderWithProviders(<InspectionPipeline />);
  const card = screen.getByText('DP预算管理').closest('.ant-card') as HTMLElement;
  fireEvent.mouseDown(card.querySelector('.ant-select-selector') as Element);
  const option = await waitFor(() => {
    const el = document.querySelector('.ant-select-item-option');
    if (!el) throw new Error('会话下拉没出来');
    return el as HTMLElement;
  });
  fireEvent.click(option);
  // 选中后 React 状态未落，立刻点「查询余额」会被页面自己的「请输入会话ID」卡住。
  await waitFor(() => expect(card.textContent).toContain(SESSION_ID.slice(0, 8)));
  fireEvent.click(within(card).getByRole('button', { name: '查询余额' }));
  return view;
}

/** antd Statistic 把读数拆成多个节点，只能整块取 textContent。 */
function statisticText(container: HTMLElement): string {
  const card = screen.getByText('DP预算管理').closest('.ant-card') as HTMLElement;
  return container.querySelector('.ant-card-body .ant-statistic')?.textContent
    ?? card.textContent ?? '';
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('A192e 服务边界类型', () => {
  it('getDPBudget 的类型表达得了「未知」与三个新字段', () => {
    const src = readSourceFromDisk('src/services/outputControlApi.ts');
    expect(src).toMatch(/epsilon_remaining:\s*number\s*\|\s*null/);
    expect(src).toMatch(/budget_state:/);
    expect(src).toMatch(/budget_source:/);
    expect(src).toMatch(/budget_authoritative:/);
  });
});

describe('A192e 预算文案映射', () => {
  it('未核实与已耗尽是两种不同文案，未知不落成 0', () => {
    const unverified = dpBudgetView('unverified', null);
    const exhausted = dpBudgetView('exhausted', 0);
    expect(unverified.display).toBe('未核实');
    expect(unverified.display).not.toBe(exhausted.display);
    expect(unverified.hint).not.toBeNull();
    expect(exhausted.display).toBe('0.00');
    expect(exhausted.hint).toContain('已耗尽');
    expect(dpBudgetView('unverified', null).display).not.toMatch(/^0(\.0+)?$/);
  });

  it('null 读数一律走未核实，哪怕后端漏发 budget_state', () => {
    expect(dpBudgetView(undefined, null).display).toBe('未核实');
    expect(dpBudgetView('sufficient', 3).display).toBe('3.00');
    expect(dpBudgetView('sufficient', 3, false).hint).toContain('未跨副本共享');
  });
});

describe('A192e 页面读数', () => {
  it('budget_state=unverified 时上屏「未核实」，不出现 0.00', async () => {
    mockBudget({
      epsilon_remaining: null,
      budget_state: 'unverified',
      budget_source: 'unverified',
      budget_authoritative: false,
    });
    const { container } = await queryBudget();

    await waitFor(() => expect(statisticText(container)).toContain('未核实'));
    expect(screen.getByText(/未知不是 0/)).toBeInTheDocument();
    expect(statisticText(container)).not.toContain('0.00');
    expect(document.body.textContent).not.toContain('已耗尽');
  });

  it('真·耗尽仍上屏 0.00 与「已耗尽」，与未核实分得开', async () => {
    mockBudget({
      epsilon_remaining: 0,
      budget_state: 'exhausted',
      budget_source: 'redis',
      budget_authoritative: true,
    });
    const { container } = await queryBudget();

    await waitFor(() => expect(statisticText(container)).toContain('0.00'));
    expect(screen.getByText(/已耗尽/)).toBeInTheDocument();
    expect(statisticText(container)).not.toContain('未核实');
  });
});
