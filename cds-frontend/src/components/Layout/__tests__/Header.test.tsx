// 顶栏 = 原型 A-1 的那条蓝色实心条（1440×65，白字白图标）。
// 真值要点：左 = 白色方形 mark + 单行品牌白字；右 = 人像图标 + 「欢迎登录，<组织名>」+ 下拉箭头；
// 消息/帮助/设置类图标按钮：**无**；安全在线 Tag / 角色 Tag / 铃铛徽标一律不在顶栏出现。
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import AppHeader from '../Header';
import { useAuthStore } from '../../../stores/authStore';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import type { User } from '../../../types/models';

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

// 组织名取后端下发字段；这里的值是测试夹具，故意不用原型里的部署方示例名。
const orgUser: User = { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: '城市数据运营中心' };
const providerUser: User = { id: 'u2', username: 'dev', email: 'd@test.com', role: 'data_provider', organization: null };

function openUserMenu() {
  fireEvent.click(screen.getByText(/^欢迎登录，/));
}

describe('顶栏品牌条', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: orgUser });
    useWorkspaceStore.setState({ workspace: null });
    navigate.mockClear();
  });

  it('品牌白字单行，英文小字副标题去掉', () => {
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    expect(document.querySelector('.cds-header .cds-brand')).not.toBeNull();
    // 真值文案（strict-1-home.md §1）：「星信可信数据空间 · 数据沙箱开发平台」，无英文副标题。
    expect(screen.getByText('星信可信数据空间 · 数据沙箱开发平台')).toBeInTheDocument();
    expect(screen.queryByText('Confidential Data Sandbox')).not.toBeInTheDocument();
  });

  it('右侧是人像图标 + 「欢迎登录，<组织名>」，组织名取 user.organization', () => {
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    expect(screen.getByText('欢迎登录，城市数据运营中心')).toBeInTheDocument();
  });

  it('组织名缺失回落用户名，两者都缺失按「用户」口径', () => {
    useAuthStore.setState({ user: { ...providerUser, organization: null } });
    const { unmount } = renderWithProviders(<AppHeader />, { route: '/' });
    expect(screen.getByText('欢迎登录，dev')).toBeInTheDocument();
    unmount();

    useAuthStore.setState({ user: { ...providerUser, organization: '   ', username: '' } });
    renderWithProviders(<AppHeader />, { route: '/' });
    expect(screen.getByText('欢迎登录，用户')).toBeInTheDocument();
  });

  it('原型没有的东西不出现：徽标、安全在线、角色 Tag 都不在顶栏，只剩人像 + caret 两个图标', () => {
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    expect(screen.queryByText('安全在线')).not.toBeInTheDocument();
    expect(screen.queryByText('运营方')).not.toBeInTheDocument();
    expect(document.querySelector('.ant-badge')).toBeNull();
    expect(document.querySelectorAll('.cds-header .anticon')).toHaveLength(2);
  });
});

describe('顶栏不承载面包屑/页面标题（原型 A-1 顶栏 crop）', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: orgUser });
    useWorkspaceStore.setState({ workspace: null });
  });

  // 面包屑按原型在内容卡顶部（见 PageCrumb.test.tsx）；顶栏蓝条只有品牌 + 人像问候。
  // 旧实现把面包屑与页面标题画在蓝条里（实测 y=8、白字 0.78），与图不符，本轮搬出。
  for (const route of ['/', '/home', '/monitoring', '/flow/tasks', '/flow/tasks/new', '/notebooks', '/deliveries', '/audit']) {
    it(`${route} 的顶栏只有品牌条与人像问候，无面包屑/无页面标题`, () => {
      renderWithProviders(<AppHeader />, { route });
      expect(document.querySelector('.cds-header-context')).toBeNull();
      expect(document.querySelector('.cds-page-title')).toBeNull();
      expect(document.querySelector('.cds-header .ant-breadcrumb')).toBeNull();
      expect(document.querySelectorAll('.cds-header .anticon')).toHaveLength(2);
      expect(screen.getByText('星信可信数据空间 · 数据沙箱开发平台')).toBeInTheDocument();
    });
  }
});

describe('顶栏用户下拉（顶栏腾出来的能力都在这里）', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: orgUser });
    useWorkspaceStore.setState({ workspace: null });
    navigate.mockClear();
  });

  it('下拉里有只读角色显示与退出登录', async () => {
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    openUserMenu();
    expect(await screen.findByText('当前角色：运营方')).toBeInTheDocument();
    expect(screen.getByText('退出登录')).toBeInTheDocument();
  });

  it('退出登录清掉登录态并跳登录页', async () => {
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    openUserMenu();
    fireEvent.click(await screen.findByText('退出登录'));
    expect(useAuthStore.getState().user).toBeNull();
    expect(navigate).toHaveBeenCalledWith('/login');
  });

  it('管理员工台的人在下拉里切到开发者工作台，落点是开发者首页 /home', async () => {
    useWorkspaceStore.setState({ workspace: 'admin' });
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    openUserMenu();
    fireEvent.click(await screen.findByText('切到开发者工作台'));
    expect(useWorkspaceStore.getState().workspace).toBe('developer');
    expect(navigate).toHaveBeenCalledWith('/home');
  });

  it('已在开发者工作台的人切回管理员工台，落点是运营概览 /', async () => {
    useWorkspaceStore.setState({ workspace: 'developer' });
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    openUserMenu();
    fireEvent.click(await screen.findByText('切到管理员工台'));
    expect(useWorkspaceStore.getState().workspace).toBe('admin');
    expect(navigate).toHaveBeenCalledWith('/');
  });

  it('切不了工台的角色（数商）在下拉里也看不到切换项', async () => {
    useAuthStore.setState({ user: providerUser });
    renderWithProviders(<AppHeader />, { route: '/' });
    openUserMenu();
    await screen.findByText('退出登录');
    expect(screen.queryByText(/^切到/)).toBeNull();
  });

  it('Segmented 切工作台已移出顶栏：顶栏里没有任何按钮/分段控件', () => {
    renderWithProviders(<AppHeader />, { route: '/monitoring' });
    expect(document.querySelector('.cds-header .ant-segmented')).toBeNull();
    expect(document.querySelectorAll('.cds-header button')).toHaveLength(0);
  });
});
