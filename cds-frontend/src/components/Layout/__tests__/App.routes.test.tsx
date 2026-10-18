// App.tsx 的路由落点真值：index = 当前工台的首页（原型 A-1），/home = replace 重定向到 /。
// 说明：这里必须渲染整棵 App（BrowserRouter 读真实 window.history、Suspense + lazy 都在 App 里），
// 换 MemoryRouter 的 App 副本钉不住真产物；因此只桩掉外壳与页面挂载点，不断页面本体。
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import App from '../../../App';
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

vi.mock('../Sidebar', () => ({ default: () => <div data-testid="stub-sidebar" /> }));
vi.mock('../DataplaneBanner', () => ({ default: () => null }));
vi.mock('../../../pages/Dashboard', () => ({ default: () => <div>运营概览页面</div> }));
vi.mock('../../../pages/Flow/DevHome', () => ({ default: () => <div>开发者首页</div> }));
vi.mock('../../../pages/NotFound', () => ({ default: () => <div>页面不存在</div> }));

const providerUser: User = { id: 'u1', username: 'dev', email: 'd@test.com', role: 'data_provider', organization: null };
const operatorUser: User = { id: 'u2', username: 'op', email: 'op@test.com', role: 'operator', organization: null };

function renderAppAt(user: User, path: string) {
  // 登录态走 store（与全站用例同一写法），地址栏走真实 history：App 用的是 BrowserRouter。
  useAuthStore.setState({ user, token: 't', refreshToken: 'r', isAuthenticated: true });
  window.history.pushState({}, '', path);
  return render(<App />);
}

describe('App 路由落点', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, token: null, refreshToken: null, isAuthenticated: false });
    useWorkspaceStore.setState({ workspace: null });
    window.history.pushState({}, '', '/');
  });

  it('开发者工台的 / 挂开发者首页', async () => {
    renderAppAt(providerUser, '/');
    expect(await screen.findByText('开发者首页')).toBeInTheDocument();
    expect(screen.queryByText('运营概览页面')).not.toBeInTheDocument();
  });

  it('管理员工台的 / 挂运营概览', async () => {
    renderAppAt(operatorUser, '/');
    expect(await screen.findByText('运营概览页面')).toBeInTheDocument();
  });

  it('/home 重定向到 /（旧开发者首页链接不断）', async () => {
    renderAppAt(providerUser, '/home');
    expect(await screen.findByText('开发者首页')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/');
  });

  it('运营方的 /home 重定向后落运营概览（旧链接对管理员同样可达）', async () => {
    useWorkspaceStore.setState({ workspace: 'developer' });
    renderAppAt(operatorUser, '/home');
    expect(await screen.findByText('运营概览页面')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/');
  });

  it('未登录访问 / 仍然被ProtectedRoute 挡住（不会先渲染首页再跳）', async () => {
    // Login 页是 lazy 的，没 mock：这里只断言两张首页都没挂载。
    window.history.pushState({}, '', '/');
    render(<App />);
    expect(screen.queryByText('开发者首页')).toBeNull();
    expect(screen.queryByText('运营概览页面')).toBeNull();
  });

  it('未知路径照旧落 404 页', async () => {
    renderAppAt(providerUser, '/definitely-not-a-route');
    expect(await screen.findByText('页面不存在')).toBeInTheDocument();
  });
});
