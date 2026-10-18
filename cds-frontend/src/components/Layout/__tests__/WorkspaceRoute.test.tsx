import { describe, it, expect, beforeEach } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import WorkspaceRoute from '../WorkspaceRoute';
import RoleRoute from '../../Auth/RoleRoute';
import { ROLE_GROUPS } from '../../../utils/roles';
import { useAuthStore } from '../../../stores/authStore';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import type { User } from '../../../types/models';

// D-16（docs/defect-ledger-2026-10-16-portal-e2e.md）：双模式工作台过去只裁菜单不裁路由。
// 这些用例钉的是「角色够格但工作台不对」这一档：把 WorkspaceRoute 改成直通的变异实验里，
// 第 1、3、7 条变红（页面能直接进去），所以门一拆就有人叫。
const FORBIDDEN = /当前工作台不含此页面/;

const operatorUser: User = { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null };
const buyerUser: User = { id: 'u2', username: 'buyer', email: 'b@test.com', role: 'buyer', organization: null };

/** 与 App.tsx 同构：RoleRoute 在外管角色，WorkspaceRoute 在内管工作台。 */
function renderAdminOwnedRoute() {
  return renderWithProviders(
    <Routes>
      <Route
        path="/ops/deployments"
        element={
          <RoleRoute allowedRoles={ROLE_GROUPS.k8sOpsReaders}>
            <WorkspaceRoute workspace="admin">
              <div>部署列表</div>
            </WorkspaceRoute>
          </RoleRoute>
        }
      />
    </Routes>,
    { route: '/ops/deployments' },
  );
}

function renderDeveloperOwnedRoute() {
  return renderWithProviders(
    <Routes>
      <Route
        path="/notebooks"
        element={
          <RoleRoute allowedRoles={ROLE_GROUPS.flowDevUsers}>
            <WorkspaceRoute workspace="developer">
              <div>Notebook 开发</div>
            </WorkspaceRoute>
          </RoleRoute>
        }
      />
    </Routes>,
    { route: '/notebooks' },
  );
}

/** 两端共用：SS-18 把「安全管理」留在开发者工台（见 Sidebar.test.tsx），所以 /audit 标 developer。 */
function renderSharedRoute() {
  return renderWithProviders(
    <Routes>
      <Route
        path="/audit"
        element={
          <RoleRoute allowedRoles={ROLE_GROUPS.auditReaders}>
            <WorkspaceRoute workspace="developer">
              <div>操作日志</div>
            </WorkspaceRoute>
          </RoleRoute>
        }
      />
    </Routes>,
    { route: '/audit' },
  );
}

describe('WorkspaceRoute 工作台路由门（D-16）', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null });
    useWorkspaceStore.setState({ workspace: null });
  });

  it('运营方切到开发者工作台后手输 /ops/deployments 拿到无权访问态', () => {
    useAuthStore.setState({ user: operatorUser });
    useWorkspaceStore.setState({ workspace: 'developer' });
    renderAdminOwnedRoute();
    expect(screen.getByText(FORBIDDEN)).toBeInTheDocument();
    expect(screen.queryByText('部署列表')).not.toBeInTheDocument();
  });

  it('运营方在管理员工台正常打开 /ops/deployments', () => {
    useAuthStore.setState({ user: operatorUser });
    renderAdminOwnedRoute();
    expect(screen.getByText('部署列表')).toBeInTheDocument();
    expect(screen.queryByText(FORBIDDEN)).not.toBeInTheDocument();
  });

  it('不属于管理员工台的角色（买方）在管理态专属页拿不到页面', () => {
    useAuthStore.setState({ user: buyerUser });
    renderWithProviders(
      <WorkspaceRoute workspace="admin">
        <div>部署列表</div>
      </WorkspaceRoute>,
    );
    expect(screen.getByText(FORBIDDEN)).toBeInTheDocument();
    expect(screen.queryByText('部署列表')).not.toBeInTheDocument();
  });

  it('买方在开发者工台照旧打得开开发闭环路由', () => {
    useAuthStore.setState({ user: buyerUser });
    renderDeveloperOwnedRoute();
    expect(screen.getByText('Notebook 开发')).toBeInTheDocument();
  });

  it('运营方在两个工作台都打得开两端共用路由（管理员工台是超集）', () => {
    useAuthStore.setState({ user: operatorUser });
    useWorkspaceStore.setState({ workspace: 'admin' });
    renderSharedRoute();
    expect(screen.getByText('操作日志')).toBeInTheDocument();
  });

  it('运营方切到开发者工作台后仍打得开两端共用路由', () => {
    useAuthStore.setState({ user: operatorUser });
    useWorkspaceStore.setState({ workspace: 'developer' });
    renderSharedRoute();
    expect(screen.getByText('操作日志')).toBeInTheDocument();
  });

  it('切工台入口只对能切工台的角色出现', () => {
    useAuthStore.setState({ user: operatorUser });
    useWorkspaceStore.setState({ workspace: 'developer' });
    renderAdminOwnedRoute();
    expect(screen.getByRole('button', { name: '切到管理员工台' })).toBeInTheDocument();
  });

  it('不能切工台的角色不会看到一个点了没用的按钮', () => {
    useAuthStore.setState({ user: buyerUser });
    renderWithProviders(
      <WorkspaceRoute workspace="admin">
        <div>部署列表</div>
      </WorkspaceRoute>,
    );
    expect(screen.queryByRole('button', { name: '切到管理员工台' })).not.toBeInTheDocument();
  });
});
