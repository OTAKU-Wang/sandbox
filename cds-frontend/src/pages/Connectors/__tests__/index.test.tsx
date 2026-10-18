// 连接器页本地契约（A313 页面半程 + A318，全部离线跑）：
// ① 游标可达：服务端声明还有下一页（X-Next-Cursor）时，UI 必须给出「加载更多」并用游标真的续拉，
//    第 101 条连接器要能出现在表里；
// ② 游标丢失也要披露：后端 total=null（还有更多）而游标头没透传过来时，界面必须显式说「列表可能不完整」；
// ③ A318：后端心跳 latency_ms 恒为 0（connectors.py get_connector_heartbeat_status 写死），
//    不是实测值 ⇒ 心跳面板不许把「0 ms」当测量值渲染。
// 只 mock 最外层 HTTP（services/api），服务层归一化与页面取数都跑真代码。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import ConnectorsPage from '../index';
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

const row = (id: string) => ({
  id: `c-${id}`,
  space_id: `sp-${id}`,
  space_name: `CONN-${id}`,
  space_url: `https://sp-${id}.example/api`,
  status: 'active',
  is_healthy: true,
  last_heartbeat: null,
  created_at: '2026-10-01T00:00:00',
});

function mockConnectorPages(pagesByCursor: Record<string | 'first', unknown>) {
  get.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/connectors/') {
      const cursor = config?.params?.cursor;
      const key = cursor == null ? 'first' : String(cursor);
      return Promise.resolve(pagesByCursor[key] ?? { items: [], total: 0, __next_cursor: null });
    }
    if (url.endsWith('/heartbeat')) {
      return Promise.resolve({ connector_id: 'c-1', status: 'healthy', last_seen: '2026-10-18T00:00:00', latency_ms: 0 });
    }
    return Promise.resolve(undefined);
  });
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe('连接器列表 — 截断必须可达或披露（A313）', () => {
  it('还有下一页时给出「加载更多」，点击后用游标真的续拉，新行出现在表里', async () => {
    mockConnectorPages({
      first: { items: [row('1')], total: null, __next_cursor: 'cursor-2' },
      'cursor-2': { items: [row('2')], total: null, __next_cursor: null },
    });
    renderWithProviders(<ConnectorsPage />);
    await screen.findByText('CONN-1');

    const more = await screen.findByTestId('connectors-load-more');
    fireEvent.click(more);

    await screen.findByText('CONN-2');
    const secondCall = get.mock.calls.filter((call) => call[0] === '/connectors/')[1];
    expect(secondCall?.[1]).toMatchObject({ params: expect.objectContaining({ cursor: 'cursor-2' }) });
    await waitFor(() => expect(screen.queryByTestId('connectors-load-more')).toBeNull());
  });

  it('后端声明还有更多但游标头丢失 → 显式披露「可能不完整」，而不是静默当全量', async () => {
    mockConnectorPages({ first: { items: [row('1')], total: null, __next_cursor: null } });
    renderWithProviders(<ConnectorsPage />);
    await screen.findByText('CONN-1');
    expect(await screen.findByTestId('connectors-truncated-warning')).toBeInTheDocument();
    expect(screen.queryByTestId('connectors-load-more')).toBeNull();
  });

  it('全量取回（total 为数字且无游标）→ 不渲染任何截断控件/警示', async () => {
    mockConnectorPages({ first: { items: [row('1'), row('2')], total: 2, __next_cursor: null } });
    renderWithProviders(<ConnectorsPage />);
    await screen.findByText('CONN-2');
    expect(screen.queryByTestId('connectors-truncated-warning')).toBeNull();
    expect(screen.queryByTestId('connectors-load-more')).toBeNull();
  });
});

describe('心跳面板 — 不许把后端的常量 0 当实测延迟（A318）', () => {
  it('心跳弹窗渲染，但不出现「延迟」测量行（后端 latency_ms 恒 0，非实测）', async () => {
    mockConnectorPages({ first: { items: [row('1')], total: 1, __next_cursor: null } });
    renderWithProviders(<ConnectorsPage />);
    await screen.findByText('CONN-1');

    const heartbeatButton = await screen.findByRole('button', { name: /心\s*跳/ });
    fireEvent.click(heartbeatButton);

    await waitFor(() => expect(get).toHaveBeenCalledWith('/connectors/c-1/heartbeat'));
    // 弹窗真的渲染了（正向锚点），但伪造的测量值不在
    await waitFor(() => expect(document.querySelector('.ant-modal')).not.toBeNull());
    expect(screen.queryByText('延迟')).toBeNull();
    expect(screen.queryByText(/0\s*ms/)).toBeNull();
  });
});
