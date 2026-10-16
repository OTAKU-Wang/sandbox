import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import Sidebar from '../Sidebar';
import { useAuthStore } from '../../../stores/authStore';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import type { User } from '../../../types/models';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useLocation: () => ({ pathname: '/' }),
    useNavigate: () => vi.fn(),
  };
});

describe('Sidebar', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null });
  });

  it('renders base menu items for unauthenticated user', () => {
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('仪表盘')).toBeInTheDocument();
    expect(screen.queryByText('数据产品')).not.toBeInTheDocument();
    expect(screen.queryByText('沙箱管理')).not.toBeInTheDocument();
  });

  it('shows audit menu for operator role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('审计中心')).toBeInTheDocument();
  });

  it('hides audit menu for buyer role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'buyer', email: 'b@test.com', role: 'buyer', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('审计中心')).not.toBeInTheDocument();
  });

  it('shows audit menu for regulator role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'reg', email: 'reg@test.com', role: 'regulator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('审计中心')).toBeInTheDocument();
  });

  it('shows K8s 运营 group for operator role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('K8s 运营')).toBeInTheDocument();
  });

  it('hides K8s 运营 group for buyer role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'buyer', email: 'b@test.com', role: 'buyer', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('K8s 运营')).not.toBeInTheDocument();
  });

  it('shows API 密钥 menu for admin role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'admin', email: 'a@test.com', role: 'admin', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('用户管理')).toBeInTheDocument();
    expect(screen.getByText('API 密钥')).toBeInTheDocument();
  });

  it('hides API 密钥 menu for non-admin roles', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('API 密钥')).not.toBeInTheDocument();
  });
});

describe('双模式工作台菜单过滤', () => {
  const operatorUser: User = { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null };

  beforeEach(() => {
    useWorkspaceStore.setState({ workspace: null });
  });

  it('运营方默认落在管理员工台，管理态分组可见', () => {
    useAuthStore.setState({ user: operatorUser });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('K8s 运营')).toBeInTheDocument();
    expect(screen.getByText('监控中心')).toBeInTheDocument();
    expect(screen.getByText('沙箱管理')).toBeInTheDocument();
  });

  it('运营方切到开发者工作台后只保留开发闭环分组', () => {
    useAuthStore.setState({ user: operatorUser });
    useWorkspaceStore.setState({ workspace: 'developer' });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('K8s 运营')).not.toBeInTheDocument();
    expect(screen.queryByText('监控中心')).not.toBeInTheDocument();
    expect(screen.queryByText('身份管理')).not.toBeInTheDocument();
    expect(screen.getByText('沙箱管理')).toBeInTheDocument();
    expect(screen.getByText('审计中心')).toBeInTheDocument();
  });

  it('业务角色残留管理员工台偏好时被强制回到开发者视图', () => {
    useAuthStore.setState({
      user: { id: 'u2', username: 'buyer', email: 'b@test.com', role: 'buyer', organization: null },
    });
    useWorkspaceStore.setState({ workspace: 'admin' });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('K8s 运营')).not.toBeInTheDocument();
    expect(screen.getByText('沙箱管理')).toBeInTheDocument();
  });
});
