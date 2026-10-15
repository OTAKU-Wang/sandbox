import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../test/test-utils';
import ApiKeys from '../ApiKeys';
import { apiKeysApi, type ApiKey } from '../../services/apiKeysApi';

// antd Table/Drawer 的响应式断点依赖 window.matchMedia（jsdom 未实现）
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

vi.mock('../../services/apiKeysApi', () => ({
  apiKeysApi: {
    list: vi.fn(),
    create: vi.fn(),
    activity: vi.fn(),
    rotate: vi.fn(),
    patchStatus: vi.fn(),
    revoke: vi.fn(),
  },
}));

const READONLY_SCOPES = [
  'sessions', 'contracts', 'data', 'pipeline', 'audit', 'output',
  'infra', 'certs', 'federation', 'gateway', 'agent', 'meta',
].map((group) => `${group}:read`);

function makeKey(overrides: Partial<ApiKey> = {}): ApiKey {
  return {
    id: 'k-1',
    name: 'ETL 管线机器人',
    description: null,
    key_prefix: 'cds_ak_ab12',
    scopes: [...READONLY_SCOPES],
    status: 'active',
    rate_limit: 120,
    issued_by_username: 'admin',
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 90 * 86400000).toISOString(),
    last_used_at: new Date(Date.now() - 5 * 60000).toISOString(),
    revoked_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ApiKeys', () => {
  it('renders list row with monospace prefix, preset tag and status badge', async () => {
    vi.mocked(apiKeysApi.list).mockResolvedValue([makeKey()]);
    renderWithProviders(<ApiKeys />);
    expect(await screen.findByText('ETL 管线机器人')).toBeInTheDocument();
    expect(screen.getByText('cds_ak_ab12…')).toBeInTheDocument();
    expect(screen.getByText('只读')).toBeInTheDocument();
    expect(screen.getByText('生效')).toBeInTheDocument();
    expect(screen.getByText('5 分钟前')).toBeInTheDocument();
  });

  it('renders status badges for all lifecycle states (active/disabled/revoked/expired)', async () => {
    vi.mocked(apiKeysApi.list).mockResolvedValue([
      makeKey({ id: 'k-1', name: '密钥 A' }),
      makeKey({ id: 'k-2', name: '密钥 B', status: 'disabled' }),
      makeKey({
        id: 'k-3', name: '密钥 C', status: 'revoked',
        revoked_at: new Date().toISOString(),
      }),
      makeKey({
        id: 'k-4', name: '密钥 D',
        expires_at: new Date(Date.now() - 60000).toISOString(),
      }),
    ]);
    renderWithProviders(<ApiKeys />);
    expect(await screen.findByText('生效')).toBeInTheDocument();
    expect(screen.getByText('已禁用')).toBeInTheDocument();
    expect(screen.getByText('已吊销')).toBeInTheDocument();
    expect(screen.getByText('已过期')).toBeInTheDocument();
  });

  it('shows empty state with API-key explanation and create action', async () => {
    vi.mocked(apiKeysApi.list).mockResolvedValue([]);
    renderWithProviders(<ApiKeys />);
    expect(await screen.findByText(/什么是 API 密钥/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /创建第一把密钥/ })).toBeInTheDocument();
  });

  it('opens create dialog, expands custom scope matrix, and warns on full preset', async () => {
    vi.mocked(apiKeysApi.list).mockResolvedValue([]);
    renderWithProviders(<ApiKeys />);
    fireEvent.click(screen.getByRole('button', { name: /创建 API 密钥/ }));

    fireEvent.click(await screen.findByRole('radio', { name: /自定义/ }));
    expect(await screen.findByText('会话')).toBeInTheDocument();
    expect(screen.getByText('sessions')).toBeInTheDocument();
    expect(screen.getByText('计算管线')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: /全量/ }));
    expect(await screen.findByText(/可调用除用户\/密钥管理外的全部平台功能/)).toBeInTheDocument();
  });

  it('shows never-used hint when last_used_at is null', async () => {
    vi.mocked(apiKeysApi.list).mockResolvedValue([
      makeKey({ last_used_at: null }),
    ]);
    renderWithProviders(<ApiKeys />);
    expect(await screen.findByText('从未使用')).toBeInTheDocument();
  });
});
