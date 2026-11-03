import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import TeamManagement from '../TeamManagement';
import { teamApi, type Team } from '../../../services/teamApi';
import { usersApi } from '../../../services/usersApi';

// antd Table/Drawer/Modal 的响应式断点依赖 window.matchMedia（jsdom 未实现）
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    listener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

vi.mock('../../../services/teamApi', () => ({
  teamApi: {
    fetchTeams: vi.fn(),
    fetchTeam: vi.fn(),
    createTeam: vi.fn(),
    updateTeam: vi.fn(),
    addTeamMember: vi.fn(),
    removeTeamMember: vi.fn(),
  },
}));

vi.mock('../../../services/usersApi', () => ({
  usersApi: { options: vi.fn() },
}));

function makeTeam(overrides: Partial<Team> = {}): Team {
  return {
    id: 't-1',
    name: '金融风控创新实验室',
    org_id: 'org-fin-01',
    description: '跨机构风控建模',
    status: 'active',
    cpu_quota_cores: 32,
    memory_quota_gb: 128,
    storage_quota_gb: 1000,
    members: [
      {
        user_id: 'u-dev',
        username: 'risk_dev',
        role: 'core_developer',
        permissions: ['view', 'run'],
        added_at: new Date().toISOString(),
        added_by: 'u-admin',
      },
    ],
    member_count: 1,
    role_permissions: {},
    created_by: 'u-admin',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

function listResult(items: Team[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(usersApi.options).mockResolvedValue([]);
});

describe('TeamManagement', () => {
  it('列表按信封渲染团队、配额与成员数', async () => {
    vi.mocked(teamApi.fetchTeams).mockResolvedValue(listResult([makeTeam()]));
    renderWithProviders(<TeamManagement />);

    expect(await screen.findByText('金融风控创新实验室')).toBeInTheDocument();
    expect(screen.getByText('32 核')).toBeInTheDocument();
    expect(screen.getByText('128 GB')).toBeInTheDocument();
    expect(screen.getByText('1000 GB')).toBeInTheDocument();
    expect(screen.getByText('1 人')).toBeInTheDocument();
    expect(screen.getByText('启用')).toBeInTheDocument();
    expect(screen.getByText('org-fin-01')).toBeInTheDocument();
  });

  it('成员抽屉展示成员、团队角色与权限点', async () => {
    const team = makeTeam();
    vi.mocked(teamApi.fetchTeams).mockResolvedValue(listResult([team]));
    vi.mocked(teamApi.fetchTeam).mockResolvedValue(team);
    renderWithProviders(<TeamManagement />);

    fireEvent.click(await screen.findByText('成员管理'));
    expect(await screen.findByText('risk_dev')).toBeInTheDocument();
    expect(screen.getByText('核心开发者')).toBeInTheDocument();
    expect(screen.getByText('view')).toBeInTheDocument();
    expect(screen.getByText('run')).toBeInTheDocument();
    expect(teamApi.fetchTeam).toHaveBeenCalledWith('t-1');
  });

  it('配额调整带出现有配额，保存时按 PATCH 下发', async () => {
    const team = makeTeam();
    vi.mocked(teamApi.fetchTeams).mockResolvedValue(listResult([team]));
    vi.mocked(teamApi.updateTeam).mockResolvedValue({ ...team, cpu_quota_cores: 64 });
    renderWithProviders(<TeamManagement />);

    fireEvent.click(await screen.findByText('配额调整'));
    expect(await screen.findByDisplayValue('32')).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue('32'), { target: { value: '64' } });
    fireEvent.click(screen.getByRole('button', { name: '保 存' }));

    await waitForCall(() => vi.mocked(teamApi.updateTeam).mock.calls.length > 0);
    expect(teamApi.updateTeam).toHaveBeenCalledWith(
      't-1',
      expect.objectContaining({ name: '金融风控创新实验室', cpu_quota_cores: 64 }),
    );
  });

  it('新建团队用默认配额下发 POST', async () => {
    vi.mocked(teamApi.fetchTeams).mockResolvedValue(listResult([]));
    vi.mocked(teamApi.createTeam).mockResolvedValue(makeTeam());
    renderWithProviders(<TeamManagement />);

    fireEvent.click(await screen.findByText('新建团队'));
    fireEvent.change(await screen.findByPlaceholderText('例如：金融风控创新实验室'), {
      target: { value: '新算法团队' },
    });
    fireEvent.click(screen.getByRole('button', { name: '创建团队' }));

    await waitForCall(() => vi.mocked(teamApi.createTeam).mock.calls.length > 0);
    expect(teamApi.createTeam).toHaveBeenCalledWith(
      expect.objectContaining({ name: '新算法团队', cpu_quota_cores: 16, memory_quota_gb: 64, storage_quota_gb: 500 }),
    );
  });
});

/** antd 表单提交是异步的（validateFields 走 Promise）；轮询到调用发生为止，上限 2s。 */
async function waitForCall(predicate: () => boolean) {
  const deadline = Date.now() + 2000;
  while (!predicate() && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  expect(predicate()).toBe(true);
}
