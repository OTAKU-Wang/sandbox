// index 路由的落点分派：开发者工台 → 开发者首页（FlowDevHome），管理员工台 → 运营概览 Dashboard。
// 两张首页都 mock 掉挂载点（页面本体由别的用例覆盖），这里只钉「渲染的是哪一张」。
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { Suspense } from 'react';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import WorkspaceHome from '../WorkspaceHome';
import { useAuthStore } from '../../../stores/authStore';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import type { User } from '../../../types/models';

vi.mock('../../../pages/Dashboard', () => ({ default: () => <div>运营概览页面</div> }));
vi.mock('../../../pages/Flow/DevHome', () => ({ default: () => <div>开发者首页</div> }));

const providerUser: User = { id: 'u1', username: 'dev', email: 'd@test.com', role: 'data_provider', organization: null };
const operatorUser: User = { id: 'u2', username: 'op', email: 'op@test.com', role: 'operator', organization: null };

function renderHome() {
  // 两张首页都是 lazy 的；真产物里 App.tsx 在路由层挂了 Suspense，这里补同一层。
  return renderWithProviders(
    <Suspense fallback={null}>
      <WorkspaceHome />
    </Suspense>,
  );
}

describe('WorkspaceHome 首页分派', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null });
    useWorkspaceStore.setState({ workspace: null });
  });

  it('开发者工台的首页是开发者首页（不是空的运营概览）', async () => {
    useAuthStore.setState({ user: providerUser });
    renderHome();
    expect(await screen.findByText('开发者首页')).toBeInTheDocument();
    expect(screen.queryByText('运营概览页面')).not.toBeInTheDocument();
  });

  it('管理员工台的首页照旧是运营概览', async () => {
    useAuthStore.setState({ user: operatorUser });
    renderHome();
    expect(await screen.findByText('运营概览页面')).toBeInTheDocument();
  });

  it('运营方切到开发者工作台后首页仍是运营概览：够不到开发者首页的人回落，而不是 403 或自跳转', async () => {
    useAuthStore.setState({ user: operatorUser });
    useWorkspaceStore.setState({ workspace: 'developer' });
    renderHome();
    expect(await screen.findByText('运营概览页面')).toBeInTheDocument();
    expect(screen.queryByText('开发者首页')).not.toBeInTheDocument();
  });

  it('业务角色残留管理员工台偏好时按 resolveWorkspace 回落，仍落开发者首页', async () => {
    useAuthStore.setState({ user: providerUser });
    useWorkspaceStore.setState({ workspace: 'admin' });
    renderHome();
    expect(await screen.findByText('开发者首页')).toBeInTheDocument();
  });
});
