// 合约详情页本地契约（A316/A317，全部离线跑）：
// ① A316：激活/终止失败必须把后端 W2 信封里的原因（message/detail）带进提示
//    （api.ts 拦截器已 reject error.response.data；页面侧统一走 flow.ts 的 describeFlowError），
//    固定「激活失败/终止失败」四个字让运维无法自服务。
// ② A317：后端 DP 消费流水只回最近 20 条（app/api/contracts.py:297 `ledger[:20]`），
//    body 里没有 total/has_more —— 满 20 条时页面必须显式披露这一上限，不许把 20 行当全量台账。
// 只 mock 最外层 HTTP 与 antd 静态 message（jsdom 下 v5 static toast 不落 DOM，
// 只能接管参数断言）；高危确认弹窗用替身直接回调（static Modal 同属不落 DOM 的那一类）。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { message } from 'antd';
import { renderWithProviders } from '../../../test/test-utils';
import ContractDetail from '../ContractDetail';
import api from '../../../services/api';
import { useAuthStore } from '../../../stores/authStore';

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
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return {
    ...actual,
    message: { ...actual.message, error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } as typeof actual.message,
  };
});
vi.mock('../../../utils/highRiskOperation', () => ({
  // 只替身弹窗本身：点「终止合约」后直接以合法 payload 回调，页面侧的
  // mutation → onError → message.error 链路仍是真代码。
  confirmHighRiskOperation: (opts: { onConfirm: (p: { reason: string; ticket_id: null }) => void }) => {
    opts.onConfirm({ reason: '回收误创建的会话', ticket_id: null });
  },
}));

const get = api.get as unknown as ReturnType<typeof vi.fn>;
const post = api.post as unknown as ReturnType<typeof vi.fn>;

const PROVIDER = { id: 'u-prov', username: 'provider1', role: 'admin' };

function contractFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 'ct-1',
    contract_no: 'CT-0001',
    contract_type: 'data_query',
    status: 'signed',
    buyer_id: 'u-buyer',
    provider_id: PROVIDER.id,
    product_ids: ['p-1'],
    title: '零售合作合约',
    purpose: null,
    purpose_scope: null,
    terms: null,
    allowed_sandbox_levels: 'l2c',
    allowed_sandbox_modes: ['query'],
    allowed_operations: 'read,analyze',
    max_duration_hours: 24,
    dp_epsilon_budget: 1,
    max_output_rows: 1000,
    allowed_output_formats: 'csv,json',
    inspection_rule_set: null,
    provider_signed_at: null,
    buyer_signed_at: null,
    provider_signature: null,
    buyer_signature: null,
    signed_at: null,
    created_at: '2026-10-01T00:00:00',
    updated_at: null,
    ...overrides,
  };
}

/** W2 信封（api.ts 拦截器原样 reject 的 error.response.data 形状）。 */
function envelope(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    code: 'CONTRACT_STATE_INVALID',
    message: 'Cannot activate contract in draft status',
    detail: 'Cannot activate contract in draft status',
    request_id: 'req-1a2b3c4d',
    ...overrides,
  };
}

function renderDetail() {
  return renderWithProviders(
    <Routes>
      <Route path="/contracts/:id" element={<ContractDetail />} />
    </Routes>,
    { route: '/contracts/ct-1' },
  );
}

function findButton(label: string): HTMLButtonElement {
  const button = Array.from(document.querySelectorAll('button'))
    .find((node) => node.textContent?.replace(/\s/g, '') === label);
  if (!button) throw new Error(`按钮没渲染: ${label}`);
  return button as HTMLButtonElement;
}

const dpEntry = (i: number) => ({
  session_id: `s-${i}`,
  operation: 'query',
  epsilon_consumed: 0.01,
  timestamp: `2026-10-0${(i % 8) + 1}T00:00:00`,
});

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  vi.mocked(message.error).mockReset();
  useAuthStore.setState({ user: PROVIDER as never, isAuthenticated: true });
});

describe('激活/终止失败 — 透传后端 W2 原因（A316）', () => {
  it('激活失败：提示里带出后端 message，而不是干巴巴的「激活失败」', async () => {
    get.mockImplementation((url: string) =>
      url === '/contracts/ct-1' ? Promise.resolve(contractFixture({ status: 'signed' })) : Promise.resolve(undefined));
    post.mockImplementation((url: string) =>
      url === '/contracts/ct-1/activate' ? Promise.reject(envelope()) : Promise.reject(new Error(`unexpected post ${url}`)));

    renderDetail();
    fireEvent.click(await waitFor(() => findButton('激活')));

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    const shown = vi.mocked(message.error).mock.calls[0][0] as string;
    expect(shown).toContain('Cannot activate contract in draft status');
  });

  it('终止失败：提示里带出后端 message', async () => {
    const reason = 'Contract already terminated by provider';
    get.mockImplementation((url: string) =>
      url === '/contracts/ct-1' ? Promise.resolve(contractFixture({ status: 'active' })) : Promise.resolve(undefined));
    post.mockImplementation((url: string) =>
      url === '/contracts/ct-1/terminate' ? Promise.reject(envelope({ message: reason, detail: reason, code: 'CONTRACT_ALREADY_TERMINATED' })) : Promise.reject(new Error(`unexpected post ${url}`)));

    renderDetail();
    fireEvent.click(await waitFor(() => findButton('终止合约')));

    await waitFor(() => expect(post).toHaveBeenCalledWith('/contracts/ct-1/terminate', expect.objectContaining({ reason: '回收误创建的会话' })));
    await waitFor(() => expect(message.error).toHaveBeenCalled());
    const shown = vi.mocked(message.error).mock.calls[0][0] as string;
    expect(shown).toContain(reason);
  });
});

describe('DP 消费流水 — 满页截断必须披露（A317）', () => {
  it('entries 满 20 条（后端 ledger[:20] 上限）→ 渲染截断披露，且不把 20 行当全量', async () => {
    const entries = Array.from({ length: 20 }, (_, i) => dpEntry(i));
    get.mockImplementation((url: string) => {
      if (url === '/contracts/ct-1') return Promise.resolve(contractFixture({ status: 'active' }));
      if (url === '/contracts/ct-1/dp-budget') {
        return Promise.resolve({ total_epsilon: 1, remaining_epsilon: 0.8, consumed_epsilon: 0.2, entries });
      }
      return Promise.resolve(undefined);
    });

    renderDetail();
    const caption = await screen.findByTestId('dp-budget-truncated');
    expect(caption).toBeInTheDocument();
    await waitFor(() => expect(document.querySelectorAll('.ant-table-tbody tr.ant-table-row')).toHaveLength(20));
  });

  it('entries 未满上限（3 条 = 后端确定的全量）→ 不出现截断披露', async () => {
    const entries = [dpEntry(0), dpEntry(1), dpEntry(2)];
    get.mockImplementation((url: string) => {
      if (url === '/contracts/ct-1') return Promise.resolve(contractFixture({ status: 'active' }));
      if (url === '/contracts/ct-1/dp-budget') {
        return Promise.resolve({ total_epsilon: 1, remaining_epsilon: 0.8, consumed_epsilon: 0.2, entries });
      }
      return Promise.resolve(undefined);
    });

    renderDetail();
    await waitFor(() => expect(document.querySelectorAll('.ant-table-tbody tr.ant-table-row')).toHaveLength(3));
    expect(screen.queryByTestId('dp-budget-truncated')).toBeNull();
  });
});
