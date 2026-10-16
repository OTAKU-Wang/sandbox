import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import ErrorCard from '../ErrorCard';
import { useAuthStore } from '../../../stores/authStore';
import type { User } from '../../../types/models';

const operatorUser: User = { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null };
const buyerUser: User = { id: 'u2', username: 'buyer', email: 'b@test.com', role: 'buyer', organization: null };

describe('ErrorCard 能力矩阵入口', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null });
  });

  it('未登录不渲染能力矩阵入口', () => {
    renderWithProviders(<ErrorCard code="RUNTIME_UNAVAILABLE" message="运行时不可用" status={503} />);
    expect(screen.queryByText('查能力矩阵')).not.toBeInTheDocument();
  });

  it('业务角色（买方）不渲染无权访问的能力矩阵入口', () => {
    useAuthStore.setState({ user: buyerUser });
    renderWithProviders(<ErrorCard code="RUNTIME_UNAVAILABLE" message="运行时不可用" status={503} />);
    expect(screen.queryByText('查能力矩阵')).not.toBeInTheDocument();
  });

  it('管理态角色保留能力矩阵入口', () => {
    useAuthStore.setState({ user: operatorUser });
    renderWithProviders(<ErrorCard code="RUNTIME_UNAVAILABLE" message="运行时不可用" status={503} />);
    expect(screen.getByText('查能力矩阵')).toBeInTheDocument();
  });
});
