// 布局骨架真值：顶栏横跨整页宽（蓝色实心条）+ 品牌块在顶栏；左栏 220 白底右分隔线；
// 内容区浅灰底上的一张圆角 8 白卡。菜单与 DataplaneBanner 不是本文件的真值，桩掉。
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { renderWithProviders } from '../../../test/test-utils';
import MainLayout from '../MainLayout';
import { useAuthStore } from '../../../stores/authStore';
import { useWorkspaceStore } from '../../../stores/workspaceStore';
import type { User } from '../../../types/models';

// antd 响应式栅格依赖 window.matchMedia（jsdom 未实现）
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

vi.mock('../DataplaneBanner', () => ({ default: () => null }));
vi.mock('../Sidebar', () => ({ default: () => <div data-testid="stub-sidebar" /> }));

const operatorUser: User = { id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: '城市数据运营中心' };

function renderLayout(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/*" element={<MainLayout />}>
        <Route index element={<div>首页内容</div>} />
        <Route path="monitoring" element={<div>内容页</div>} />
      </Route>
    </Routes>,
    { route },
  );
}

describe('MainLayout 布局骨架', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: operatorUser });
    useWorkspaceStore.setState({ workspace: null });
  });

  it('顶栏是 .cds-app 的直接子节点（横跨整页宽），左栏与内容在它下面', () => {
    renderLayout('/monitoring');
    expect(document.querySelector('.cds-app > .cds-header')).not.toBeNull();
    expect(document.querySelector('.cds-app > .ant-layout-has-sider')).not.toBeNull();
    // 顶栏不能嵌回内层 Layout（那会让蓝条被左栏挤掉一截）
    expect(document.querySelector('.cds-app > .ant-layout-has-sider .cds-header')).toBeNull();
  });

  it('品牌块在顶栏，左栏不再有 .cds-brand', () => {
    renderLayout('/monitoring');
    expect(document.querySelector('.cds-header .cds-brand')).not.toBeNull();
    expect(document.querySelector('.cds-sidebar .cds-brand')).toBeNull();
    expect(screen.getByTestId('stub-sidebar')).toBeInTheDocument();
  });

  it('左栏宽 220（原型右边界 x≈219）', () => {
    renderLayout('/monitoring');
    expect(document.querySelector('.cds-app .ant-layout-sider')).toHaveStyle({ width: '220px' });
  });

  it('原型导航里没有折叠件：钩子拆了，但窄屏自动收窄留着', () => {
    renderLayout('/monitoring');
    expect(document.querySelector('.ant-layout-sider-trigger')).toBeNull();
  });

  it('按原型逐屏收口的路由挂 cds-detail（界面细节层），其余屏不继承', () => {
    renderLayout('/flow/tasks');
    expect(document.querySelector('.cds-content-inner')).toHaveClass('cds-detail');
  });

  it('内容区白卡里渲染路由出口', () => {
    renderLayout('/monitoring');
    expect(screen.getByText('内容页')).toBeInTheDocument();
    expect(document.querySelector('.cds-content-inner > *')).not.toBeNull();
    expect(screen.queryByText('首页内容')).not.toBeInTheDocument();
  });
});
