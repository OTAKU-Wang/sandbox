import { Button, Space, Tag, Typography, message } from 'antd';
import {
  AlertOutlined,
  CopyOutlined,
  DashboardOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import { hintForCode } from './errorHints';

const { Text } = Typography;

/**
 * PZ-15 通用错误卡片：code 徽章 + zh 用户提示（静态映射，镜像 docs/error-codes.md
 * "用户提示/下一步" 列）+ 操作按钮（重试 / 查能力矩阵 / 带 request_id 报告）。
 * 410 SESSION_TERMINATED 明确"会话已终止"且永不显示重试（GONE 契约：重试无效）。
 * 提示映射见 errorHints.ts（与 docs/error-codes.md 同步维护）。
 */
export interface ErrorCardInfo {
  code?: string;
  message: string;
  request_id?: string;
  retryable?: boolean;
  retryAction?: () => void;
}

export { ERROR_HINTS } from './errorHints';
export type { ErrorHint } from './errorHints';

export default function ErrorCard({
  code,
  message: serverMessage,
  request_id,
  retryable,
  retryAction,
  status,
  actions,
}: ErrorCardInfo & { status?: number; actions?: ReactNode }) {
  const base = hintForCode(code, status);
  // 调用方显式 retryable 优先；410 强制不可重试（GONE 契约）。
  const canRetry = status === 410 ? false : retryable ?? base.retryable;
  const warning = base.retryable && canRetry;

  const reportRequestId = async () => {
    const text = `CDS 错误报告\n错误码: ${code ?? 'UNKNOWN'}\n提示: ${base.hint}\n下一步: ${base.next}request_id: ${request_id ?? '（本次响应未携带，请从响应头 X-Request-ID 获取）'}`;
    try {
      await navigator.clipboard.writeText(text);
      message.success('报告内容已复制到剪贴板');
    } catch {
      message.info(text);
    }
  };

  return (
    <div
      style={{
        margin: '32px auto',
        maxWidth: 640,
        border: `1px solid ${warning ? '#ffe58f' : '#ffa39e'}`,
        borderRadius: 8,
        padding: 24,
        background: '#fff',
      }}
      data-testid="error-card"
      data-code={code ?? 'unknown'}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Space wrap align="center">
          {code && (
            <Tag color={warning ? 'gold' : status === 410 ? 'default' : 'red'}>
              {code}
            </Tag>
          )}
          {status === 410 && <AlertOutlined style={{ color: '#999' }} />}
          <Text strong style={{ fontSize: 16 }}>{base.hint}</Text>
        </Space>
        <Text type="secondary">{base.next}</Text>
        {serverMessage && serverMessage !== base.hint && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            服务端消息：{serverMessage}
          </Text>
        )}
        {request_id && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            request_id: <Text code copyable={{ text: request_id }}>{request_id}</Text>
          </Text>
        )}
        <Space wrap>
          {canRetry && retryAction && (
            <Button type="primary" icon={<ReloadOutlined />} onClick={retryAction}>
              重试
            </Button>
          )}
          {base.showCapability && (
            <Button
              icon={<DashboardOutlined />}
              href="/capability-matrix"
              target="_blank"
              rel="noreferrer"
            >
              查能力矩阵
            </Button>
          )}
          <Button icon={<CopyOutlined />} onClick={() => void reportRequestId()}>
            带 request_id 报告
          </Button>
          {actions}
        </Space>
      </Space>
    </div>
  );
}
