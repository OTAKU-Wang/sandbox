import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { Alert, Button, Space, Typography } from 'antd';

const { Text } = Typography;

/**
 * 渲染错误兜底（全站唯一一处挂载，见 App.tsx）：子树在 render 期抛错时 React 会卸载整棵根树，
 * 没有兜底就是一片空白 —— 用户回不来、看不到原因，后台字段偶发为 null 引起的空引用类缺陷
 * 因此被放大成「整个后台打不开」。
 *
 * 口径：
 * - 只兜「渲染期」抛错；事件回调 / Promise / Provider 之外的异步错误不在范围内（React 语义）。
 * - 原始错误 + componentStack 一律 console.error，绝不静默吞掉。
 * - 面板必须可恢复：「重试本页」清掉错误状态重挂子树；「回首页」是整页跳转的逃生门
 *   —— 被兜住的往往正是路由子树本身，SPA 内跳转不会重置本组件的 state，故用整页刷新，
 *   与同目录 ErrorCard 的 `href="/capability-matrix"` 同样是显式点击跳转（非自动重定向）。
 */
export interface ErrorBoundaryProps {
  children: ReactNode;
  /** 兜底面板标题：区分不同挂载点（默认「页面出错了」）。 */
  title?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    // throw 非 Error 值（字符串 / null）时也要能显示，统一包成 Error。
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 永不吞掉：控制台留原始异常与组件链，排查时不必依赖面板上的那一行摘要。
    console.error('[ErrorBoundary] 渲染期未捕获异常', error, info.componentStack);
  }

  private retry = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div style={{ margin: '32px auto', maxWidth: 640, padding: 24 }} data-testid="error-boundary">
        <Alert
          type="error"
          showIcon
          message={this.props.title ?? '页面出错了'}
          description={
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Text type="secondary">
                这一页在渲染时发生未捕获的异常，其它页面不受影响。可以重试本页，或先回首页。
              </Text>
              <Text type="danger" style={{ fontSize: 12 }}>
                {error.name}: {error.message || '（异常未提供 message）'}
              </Text>
            </Space>
          }
        />
        <Space wrap style={{ marginTop: 16 }}>
          <Button type="primary" onClick={this.retry}>
            重试本页
          </Button>
          <Button href="/">回首页</Button>
        </Space>
      </div>
    );
  }
}

export default ErrorBoundary;
