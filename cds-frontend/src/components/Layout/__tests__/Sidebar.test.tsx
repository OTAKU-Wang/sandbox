import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import Sidebar from '../Sidebar';
import { useAuthStore } from '../../../stores/authStore';

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
});
