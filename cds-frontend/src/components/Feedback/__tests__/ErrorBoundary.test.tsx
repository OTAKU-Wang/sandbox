// 全站渲染错误兜底：没有它，任一懒加载页面在 render 期抛错（后端偶发 null 字段 → 空引用）
// 都会让 React 卸载整棵根树 —— 白屏、无原因、无恢复入口。
// 锁三件事：① 抛错时渲染可恢复面板而不是空白；② 原始错误必须 console.error（禁止静默吞异常）；
// ③ 「重试本页」清掉错误状态后子树重新渲染（可恢复，不是死路）。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { Component, type ReactNode } from 'react';
import { renderWithProviders } from '../../../test/test-utils';
import ErrorBoundary from '../ErrorBoundary';

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  consoleError.mockRestore();
});

/** 由用例控制开关的「坏掉的子组件」：boom=true 时在 render 期抛空引用类异常。 */
function makeFragileChild() {
  let boom = true;
  const fragile = {
    stop() {
      boom = false;
    },
    Component: function FragileChild(): ReactNode {
      if (boom) {
        // 复现真实缺陷形态：读后端返回的 null 字段。
        const row: { product_name: string } | null = null;
        return <div>{row!.product_name}</div>;
      }
      return <div>会话详情已恢复</div>;
    },
  };
  return fragile;
}

describe('ErrorBoundary 渲染错误兜底', () => {
  it('子树抛错时渲染可恢复面板，且原始错误进 console.error', () => {
    const fragile = makeFragileChild();
    renderWithProviders(
      <ErrorBoundary>
        <fragile.Component />
      </ErrorBoundary>,
    );

    const panel = screen.getByTestId('error-boundary');
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveTextContent('页面出错了');
    // 技术性短消息：异常名 + message，供排查
    expect(panel).toHaveTextContent("TypeError: Cannot read properties of null");
    expect(screen.queryByText('会话详情已恢复')).not.toBeInTheDocument();
    // 绝不静默吞掉：原始异常对象本身进了控制台
    expect(consoleError).toHaveBeenCalled();
    expect(consoleError.mock.calls[0][1]).toBeInstanceOf(TypeError);
  });

  it('点「重试本页」清掉错误状态并重新渲染子树', () => {
    const fragile = makeFragileChild();
    renderWithProviders(
      <ErrorBoundary>
        <fragile.Component />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('error-boundary')).toBeInTheDocument();

    // 模拟「数据已修好/刷新后字段不再为 null」，再点重试
    fragile.stop();
    fireEvent.click(screen.getByRole('button', { name: '重试本页' }));

    expect(screen.queryByTestId('error-boundary')).not.toBeInTheDocument();
    expect(screen.getByText('会话详情已恢复')).toBeInTheDocument();
  });

  it('兜底面板给出回首页的链接（SPA 之外的逃生门）', () => {
    const fragile = makeFragileChild();
    renderWithProviders(
      <ErrorBoundary title="导航出错了">
        <fragile.Component />
      </ErrorBoundary>,
    );
    expect(screen.getByText('导航出错了')).toBeInTheDocument();
    const home = screen.getByRole('link', { name: '回首页' });
    expect(home).toHaveAttribute('href', '/');
  });

  it('子树正常时不干预渲染（无异常不出现面板）', () => {
    class Good extends Component<{ children?: ReactNode }> {
      render() {
        return <div>正常内容</div>;
      }
    }
    renderWithProviders(
      <ErrorBoundary>
        <Good />
      </ErrorBoundary>,
    );
    expect(screen.queryByTestId('error-boundary')).not.toBeInTheDocument();
    expect(screen.getByText('正常内容')).toBeInTheDocument();
    expect(consoleError).not.toHaveBeenCalled();
  });
});
