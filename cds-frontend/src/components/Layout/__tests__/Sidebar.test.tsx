import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import Sidebar from '../Sidebar';
import { useAuthStore } from '../../../stores/authStore';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import type { User } from '../../../types/models';

// 原有用例把 useLocation/useNavigate 钉在 '/'；这里改成可改写的模块变量，
// 让选中态用例能直接控制当前路径（不靠真实 router 跳转）。
let mockedPathname = '/';
const navigateMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useLocation: () => ({ pathname: mockedPathname }),
    useNavigate: () => navigateMock,
  };
});

function at(pathname: string) {
  mockedPathname = pathname;
  navigateMock.mockClear();
}

describe('Sidebar', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null });
  });

  it('renders base menu items for unauthenticated user', () => {
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('首页')).toBeInTheDocument();
    expect(screen.queryByText('数据产品')).not.toBeInTheDocument();
    expect(screen.queryByText('沙箱管理')).not.toBeInTheDocument();
  });

  it('shows audit menu for operator role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('安全管理')).toBeInTheDocument();
  });

  it('hides audit menu for buyer role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'buyer', email: 'b@test.com', role: 'buyer', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('安全管理')).not.toBeInTheDocument();
  });

  it('shows audit menu for regulator role', () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'reg', email: 'reg@test.com', role: 'regulator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('安全管理')).toBeInTheDocument();
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
    // SS-18: 开发者工作台按原型 IA —— 沙箱会话列表已不是开发者口径, 开发闭环分组上位
    expect(screen.queryByText('沙箱管理')).not.toBeInTheDocument();
    // 原型 4+2 把开发者的「我的开发」限定为 flowDevUsers 那三个子项（旧口径靠 sandboxReaders
    // 的「计算调度」撑住父项）：运营方不在 flowDevUsers，所以父项整体缺席，计算调度也不在开发者口径。
    expect(screen.queryByText('我的开发')).not.toBeInTheDocument();
    expect(screen.queryByText('计算调度')).not.toBeInTheDocument();
    expect(screen.getByText('安全管理')).toBeInTheDocument();
    // 运营方不在 contractReaders: 结果集分组按角色门缺席（角色门不因原型改名而放宽）
    expect(screen.queryByText('我的结果集')).not.toBeInTheDocument();
  });

  it('业务角色残留管理员工台偏好时被强制回到开发者视图', () => {
    useAuthStore.setState({
      user: { id: 'u2', username: 'buyer', email: 'b@test.com', role: 'buyer', organization: null },
    });
    useWorkspaceStore.setState({ workspace: 'admin' });
    renderWithProviders(<Sidebar />);
    expect(screen.queryByText('K8s 运营')).not.toBeInTheDocument();
    expect(screen.getByText('首页')).toBeInTheDocument();
    expect(screen.queryByText('沙箱管理')).not.toBeInTheDocument();
  });
});

/** 顶层行（不含子菜单行）：分隔线记为 '---divider---'，其余取本行自己的标题文本。 */
function topLevelRows() {
  const root = document.querySelector('.ant-menu-root');
  if (!root) throw new Error('菜单根节点没渲染');
  return Array.from(root.querySelectorAll(':scope > li')).map((li) => {
    if (li.classList.contains('ant-menu-item-divider')) return '---divider---';
    const title =
      li.querySelector(':scope > .ant-menu-title-content') ??
      li.querySelector(':scope > .ant-menu-submenu-title .ant-menu-title-content');
    return title?.textContent ?? '';
  });
}

describe('开发者工作台菜单 = 原型 4+2（1、首页-平台总览.png）', () => {
  // 数商：flowDevUsers + contractReaders + sandboxReaders 都过，安全管理按角色门缺席
  //（auditReaders 只放 operator/regulator/admin，角色门不因原型改名而放宽）。
  const providerUser: User = { id: 'p1', username: 'dev', email: 'd@test.com', role: 'data_provider', organization: null };

  beforeEach(() => {
    at('/');
    useAuthStore.setState({ user: providerUser });
    useWorkspaceStore.setState({ workspace: 'developer' });
  });

  it('顶层恰好 4 项 + 分隔线 + 2 项灰字，文案逐字照原型', () => {
    renderWithProviders(<Sidebar />);
    expect(topLevelRows()).toEqual([
      '首页',
      '我的开发',
      '我的结果集',
      '---divider---',
      '开发指引文档',
      '环境与配额说明',
    ]);
    // 旧菜单里的「开发者工作台」（/home）已删；文档两项灰字弱化但仍是可点击菜单项。
    expect(screen.queryByText('开发者工作台')).not.toBeInTheDocument();
    expect(document.querySelector('.cds-menu-muted')).not.toBeNull();
  });

  it('两个子菜单默认折叠，展开后恰好是原型那几行', async () => {
    renderWithProviders(<Sidebar />);
    // 初始就是图上那 6 行：没有任何分组是展开的
    expect(document.querySelector('.ant-menu-submenu-open')).toBeNull();

    fireEvent.click(screen.getByText('我的开发'));
    await waitFor(() => expect(screen.getByText('任务列表')).toBeInTheDocument());
    const devRows = Array.from(
      document.querySelectorAll('.ant-menu-submenu-open li .ant-menu-title-content'),
    ).map((node) => node.textContent);
    expect(devRows).toEqual(['任务列表', '在线调试', '负载与发布']);
  });

  it('运营方在开发者工台展开「安全管理」恰好是操作日志 + 合规披露', async () => {
    useAuthStore.setState({
      user: { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    fireEvent.click(screen.getByText('安全管理'));
    await waitFor(() => expect(screen.getByText('合规披露')).toBeInTheDocument());
    const secRows = Array.from(
      document.querySelectorAll('.ant-menu-submenu-open li .ant-menu-title-content'),
    ).map((node) => node.textContent);
    expect(secRows).toEqual(['操作日志', '合规披露']);
  });

  it('原型 A-1：首页是本页唯一选中项，且子菜单全折叠', () => {
    at('/');
    renderWithProviders(<Sidebar />);
    const selected = Array.from(document.querySelectorAll('.ant-menu-item-selected'));
    expect(selected).toHaveLength(1);
    expect(selected[0].textContent).toContain('首页');
    expect(document.querySelector('.ant-menu-submenu-open')).toBeNull();
  });

  it('子路由 /flow/tasks/new 把「我的开发」父项一起点亮', () => {
    at('/flow/tasks/new');
    renderWithProviders(<Sidebar />);
    expect(document.querySelector('.ant-menu-item-selected')?.textContent).toContain('任务列表');
    expect(document.querySelector('.ant-menu-submenu-selected .ant-menu-submenu-title')?.textContent).toContain(
      '我的开发',
    );
  });

  it('文档两项点了照样跳转（灰字只是弱化，不是禁用）', () => {
    renderWithProviders(<Sidebar />);
    fireEvent.click(screen.getByText('开发指引文档'));
    expect(navigateMock).toHaveBeenCalledWith('/flow/guide');
  });
});

describe('管理员工台菜单不受原型 4+2 影响', () => {
  it('运营方在管理员工台照旧看到完整菜单（含计算调度与旧文案）', () => {
    at('/');
    // 不显式设偏好：运营方的默认工台就是管理员工台（resolveWorkspace 的默认口径）
    useWorkspaceStore.setState({ workspace: null });
    useAuthStore.setState({
      user: { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: null },
    });
    renderWithProviders(<Sidebar />);
    expect(screen.getByText('沙箱管理')).toBeInTheDocument();
    expect(screen.getByText('K8s 运营')).toBeInTheDocument();
    expect(screen.getByText('监控中心')).toBeInTheDocument();
    expect(screen.getByText('输出管控')).toBeInTheDocument();
    expect(screen.getByText('环境配额')).toBeInTheDocument();
    expect(screen.getByText('我的开发')).toBeInTheDocument();
    // 管理员工台的「我的开发」仍有 sandboxReaders 的计算调度（只有开发者工台按原型收成 3 行）
    fireEvent.click(screen.getByText('我的开发'));
    expect(screen.getByText('计算调度')).toBeInTheDocument();
    // 灰字弱化只属于开发者工台的原型菜单；自指的「开发者工作台」两项都删了
    expect(document.querySelector('.cds-menu-muted')).toBeNull();
    expect(screen.queryByText('开发者工作台')).not.toBeInTheDocument();
  });
});
