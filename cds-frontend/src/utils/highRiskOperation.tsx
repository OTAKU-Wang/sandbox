import { Input, Modal, Space, Typography, message } from 'antd';
import type { HighRiskOperationPayload } from '../services/identityApi';

const { Text } = Typography;

interface ConfirmHighRiskOperationOptions {
  title: string;
  content: string;
  okText: string;
  onConfirm: (payload: HighRiskOperationPayload) => void | Promise<void>;
}

export function confirmHighRiskOperation(options: ConfirmHighRiskOperationOptions) {
  let reason = '';
  let ticketId = '';

  Modal.confirm({
    title: options.title,
    content: (
      <Space direction="vertical" style={{ width: '100%' }}>
        <Text type="secondary">{options.content}</Text>
        <Input.TextArea
          autoFocus
          rows={3}
          maxLength={500}
          showCount
          placeholder="操作理由，至少 8 个字符"
          onChange={(event) => { reason = event.target.value; }}
        />
        <Input
          maxLength={128}
          placeholder="工单号，可选"
          onChange={(event) => { ticketId = event.target.value; }}
        />
      </Space>
    ),
    okText: options.okText,
    okType: 'danger',
    cancelText: '取消',
    onOk: () => {
      const trimmedReason = reason.trim();
      if (trimmedReason.length < 8) {
        message.error('操作理由至少 8 个字符');
        return Promise.reject(new Error('reason too short'));
      }
      return options.onConfirm({
        reason: trimmedReason,
        ticket_id: ticketId.trim() || null,
      });
    },
  });
}
