import { Alert, Button, Empty, Spin } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { ReactNode } from 'react';
import { getErrorMessage } from './errorMessage';

// P4-3（lint 清零·react-refresh）：getErrorMessage / tableEmpty 两个非组件导出已拆去
// ./errorMessage.ts 与 ./tableEmpty.tsx——组件文件混导出符号会让 HMR 整族失效。
// 消费方请直接从各自模块导入；本文件只导出组件。

export function QueryErrorAlert({
  error,
  message = '数据加载失败',
  onRetry,
}: {
  error: unknown;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <Alert
      type="error"
      showIcon
      message={message}
      description={getErrorMessage(error)}
      action={onRetry ? (
        <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
          重试
        </Button>
      ) : undefined}
      style={{ marginBottom: 16 }}
    />
  );
}

// antd v5 的 Spin 在 nested/fullscreen 模式下才渲染 tip，独立 <Spin tip> 的 tip 被静默忽略
// （antd 开发态直接告警 `tip` only work in nest or fullscreen pattern）——文案在组件里自己渲染。
export function CenteredLoading({ tip = '加载中' }: { tip?: string }) {
  return (
    <div style={{ margin: '100px auto', textAlign: 'center' }}>
      <Spin size="large" />
      <div style={{ marginTop: 8 }}>{tip}</div>
    </div>
  );
}

export function EmptyState({
  description,
  action,
}: {
  description: ReactNode;
  action?: ReactNode;
}) {
  return (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description}>
      {action}
    </Empty>
  );
}
