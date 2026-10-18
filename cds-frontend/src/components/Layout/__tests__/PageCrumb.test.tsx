// 全站面包屑契约（用户 2026-10-17 定稿「平台全面使用面包屑导航 但是要考虑通用性」）：
// 段 = 路由注册表（routeCrumb.ts）+ 页内钻取栈（crumbStackStore）；样式只有斜杠一种：
// 描边图标 + 灰前段 + ` / ` + 黑当前段（图 4-1 §1.2）。
// 开发系四屏那条页签带按用户 2026-10-18 裁定删除，本文件末尾锁住它不会回来。
import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '../../../test/test-utils';
import PageCrumb from '../PageCrumb';
import { useCrumbSegments } from '../useCrumbSegments';
import { useCrumbStackStore } from '../../../stores/crumbStackStore';

const crumbTexts = () =>
  [...document.querySelectorAll('.cds-crumb-slash > span:not(.cds-crumb-sep):not(.cds-crumb-lead)')].map((el) => el.textContent);

const ROUTES = [
  ['/flow/tasks', ['首页', '我的开发']],
  ['/flow/tasks/new', ['首页', '我的开发', '新增开发任务']],
  ['/flow/tasks/6f1c2c0a-0000-4111-8111-000000000001', ['首页', '我的开发', '任务详情']],
  ['/notebooks', ['首页', '我的开发', '调试环境']],
  ['/audit', ['安全管理', '操作日志']],
  ['/deliveries', ['我的结果集']],
] as const;

const Drill = ({ segs }: { segs: string[] }) => {
  useCrumbSegments(segs);
  return null;
};

describe('平台面包屑 = 注册表段 + 钻取栈', () => {
  beforeEach(() => {
    useCrumbStackStore.setState({ stack: [] });
  });

  for (const [route, segs] of ROUTES) {
    it(`${route} 面包屑逐字 ${segs.join(' / ')}`, () => {
      renderWithProviders(<PageCrumb />, { route });
      expect(crumbTexts()).toEqual([...segs]);
      expect(screen.getByTestId('crumb-current').textContent).toBe(segs[segs.length - 1]);
    });
  }

  it('钻取段追加在注册表段之后', () => {
    renderWithProviders(
      <>
        <PageCrumb />
        <Drill segs={['OpenAPI导出', '新建API']} />
      </>,
      { route: '/deliveries' },
    );
    expect(crumbTexts()).toEqual(['我的结果集', 'OpenAPI导出', '新建API']);
  });

  it('钻取组件卸载后栈自动清空', async () => {
    const { rerender } = renderWithProviders(
      <>
        <PageCrumb />
        <Drill segs={['OpenAPI导出', '新建API']} />
      </>,
      { route: '/deliveries' },
    );
    expect(useCrumbStackStore.getState().stack).toEqual(['OpenAPI导出', '新建API']);
    rerender(
      <>
        <PageCrumb />
      </>,
    );
    await Promise.resolve();
    expect(useCrumbStackStore.getState().stack).toEqual([]);
  });

  it('首页也渲染斜杠面包屑，单段「首页」（strict-1-home.md §2 行 48 / §5 行 152）', () => {
    renderWithProviders(<PageCrumb />, { route: '/home' });
    const band = document.querySelector('.cds-crumb-slash');
    expect(band).not.toBeNull();
    expect(band!.textContent).toContain('首页');
    // 墨迹真值：前置图标 245,90,13,11 + 文字 269,89,28,14（14px），所以不能回退成 24px 页面大标题。
    expect(band!.querySelector('.cds-crumb-lead')).not.toBeNull();
    expect(band!.querySelectorAll('[data-testid="crumb-current"]')).toHaveLength(1);
  });

  it('页签带已删除：任何路由都不渲染 .cds-page-tabs', () => {
    for (const [route] of ROUTES) {
      const { unmount } = renderWithProviders(<PageCrumb />, { route });
      expect(document.querySelector('.cds-page-tabs')).toBeNull();
      expect(document.querySelector('.cds-crumb-slash')).not.toBeNull();
      unmount();
    }
  });
});
