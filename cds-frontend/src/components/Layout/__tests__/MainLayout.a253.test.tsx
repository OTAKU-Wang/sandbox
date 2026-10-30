/**
 * A253：侧栏折回去之后，得有个地方能把它展开。
 *
 * `Sider` 收的是 `trigger={null}`（图上的折叠入口在顶栏，antd 自带那根底部把手条不要），
 * 而 Header/Sidebar 里没有任何 `collapsed` 的读端 ⇒ 折回去就再也拉不回宽态，导航永久锁死。
 * 修法是在折回态的侧栏底部补一个自己的展开件，不复活 antd 的 trigger。
 *
 * 为什么这里要把 uiStore 换成可控桩：`Sider` 带 `breakpoint="lg"`，挂载时 antd 的响应式回调
 * 会按 jsdom 的 matchMedia（一律 `matches:false`）反过来 `onCollapse(false)`，把测试设进去的
 * 折回态当场抹掉。被测对象是「折回态有没有长出可用的展开入口」，不是 antd 的断点逻辑，
 * 所以把 store 这一层桩住，MainLayout 本身的分支渲染仍走真代码。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { Route, Routes } from 'react-router-dom';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { renderWithProviders } from '../../../test/test-utils';
import type { User } from '../../../types/models';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }),
});

const uiState = {
  sidebarCollapsed: false,
  theme: 'light' as const,
  setSidebarCollapsed: vi.fn(),
  toggleSidebar: vi.fn(),
  setTheme: vi.fn(),
};

vi.mock('../../../stores/uiStore', () => ({
  useUIStore: (selector: (s: typeof uiState) => unknown) => selector(uiState),
}));
vi.mock('../DataplaneBanner', () => ({ default: () => null }));
vi.mock('../Sidebar', () => ({ default: () => <div data-testid="stub-sidebar" /> }));

import MainLayout from '../MainLayout';
import { useAuthStore } from '../../../stores/authStore';

const operatorUser: User = {
  id: 'u1', username: 'op', email: 'op@test.com', role: 'operator', organization: '城市数据运营中心',
};

/** 本池 import.meta.url 不是 file: scheme ⇒ 静态门从 cwd 往上找源文件。 */
function readSource(rel: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 6; depth += 1) {
    const candidate = join(dir, rel);
    if (existsSync(join(dir, 'package.json')) && existsSync(candidate)) return readFileSync(candidate, 'utf8');
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`找不到源文件 ${rel}`);
}

function renderLayout() {
  return renderWithProviders(
    <Routes>
      <Route path="/*" element={<MainLayout />}>
        <Route index element={<div>首页内容</div>} />
      </Route>
    </Routes>,
    { route: '/' },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  uiState.sidebarCollapsed = false;
  useAuthStore.getState().login(operatorUser, 'tok', 'refresh');
});

describe('A253：折回态的侧栏自带展开入口', () => {
  it('展开态一个控件都不多（原型真值：宽态底部是空白）', () => {
    renderLayout();
    expect(document.querySelector('.cds-sidebar-expand')).toBeNull();
  });

  it('折回态长出一个可命名、可聚焦的按钮，而不是只挂在 hover 上的图标', () => {
    uiState.sidebarCollapsed = true;
    renderLayout();
    const button = screen.getByRole('button', { name: '展开导航' });
    expect(button.tagName).toBe('BUTTON');
    expect(button).toHaveAttribute('type', 'button');
    expect(document.querySelector('.cds-sidebar-expand')).toBe(button);
  });

  it('点它写回 uiStore（顶栏入口读的是同一个 store，本地 state 拉不回宽态）', () => {
    uiState.sidebarCollapsed = true;
    renderLayout();
    fireEvent.click(screen.getByRole('button', { name: '展开导航' }));
    expect(uiState.setSidebarCollapsed).toHaveBeenCalledWith(false);
  });

  it('仍然不用 antd 自带的底部把手条（图上没有它；trigger={null} 是既有真值）', () => {
    uiState.sidebarCollapsed = true;
    renderLayout();
    expect(document.querySelector('.ant-layout-sider-trigger')).toBeNull();
  });

  it('样式挂在 .cds-sidebar-expand 上并且用可读的描边/字色（不是 A221 那种灰到看不见的对比度）', () => {
    const css = readSource('src/index.css');
    const block = css.slice(css.indexOf('.cds-sidebar .cds-sidebar-expand'));
    expect(block).not.toBe('');
    expect(block.slice(0, 800)).toMatch(/position:\s*absolute/);
    expect(block.slice(0, 800)).toMatch(/border:\s*1px solid #d4dae3/);
    expect(block.slice(0, 800)).not.toMatch(/color:\s*#a6adb8/);
  });
});
