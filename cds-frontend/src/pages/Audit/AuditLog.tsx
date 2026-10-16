import { useState } from 'react';
import { Table, Tag, Typography, Space, Select, Button, Drawer, Descriptions, message } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { auditApi, type AuditRecord } from '../../services/auditApi';
import type { ColumnsType } from 'antd/es/table';
import { QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import AuditVerifyModal from '../../components/Audit/AuditVerifyModal';

const { Title } = Typography;

const actionColors: Record<string, string> = {
  login: 'blue', create: 'green', update: 'orange', delete: 'red',
  sign: 'purple', anchor: 'lime', access: 'cyan',
  output_inspection_pass: 'green', output_inspection_fail: 'red',
  unauthorized_access: 'red', dp_budget_exceeded: 'orange',
};

export default function AuditLog() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [actionFilter, setActionFilter] = useState<string | undefined>();
  const [selectedRecord, setSelectedRecord] = useState<AuditRecord | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);
  const [verifyTargetId, setVerifyTargetId] = useState<string | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['audit-records', page, actionFilter],
    queryFn: () => auditApi.list({ page, page_size: 20, action: actionFilter }),
  });

  const anchorMutation = useMutation({
    mutationFn: (ids: string[]) => auditApi.anchor(ids),
    onSuccess: (r) => {
      const height = r.block_number != null ? `, 区块高度: ${r.block_number}` : '';
      message.success(`已锚定 ${r.anchored} 条记录, Merkle Root: ${r.merkle_root?.slice(0, 16)}...${height}`);
      queryClient.invalidateQueries({ queryKey: ['audit-records'] });
      setSelectedRowKeys([]);
    },
    onError: () => message.error('锚定失败'),
  });

  const openVerifyModal = (id: string) => {
    setVerifyTargetId(id);
    setVerifyOpen(true);
  };

  const columns: ColumnsType<AuditRecord> = [
    { title: '时间', dataIndex: 'created_at', key: 'created_at', render: (v) => new Date(v).toLocaleString() },
    { title: '操作', dataIndex: 'action', key: 'action', render: (v) => <Tag color={actionColors[v] || 'default'}>{v}</Tag> },
    { title: '资源类型', dataIndex: 'resource_type', key: 'resource_type' },
    { title: '资源ID', dataIndex: 'resource_id', key: 'resource_id', render: (v) => v || '-' },
    { title: '用户', dataIndex: 'user_id', key: 'user_id', render: (v) => v ? v.slice(0, 8) + '...' : '-' },
    {
      title: '区块链', key: 'blockchain', render: (_, record) =>
        record.blockchain_tx_hash ? <Tag color="green">已锚定</Tag> : <Tag>未锚定</Tag>,
    },
    {
      title: '操作', key: 'action', render: (_, record) => (
        <Space>
          <a onClick={() => { setSelectedRecord(record); }}>详情</a>
          <a onClick={() => openVerifyModal(record.id)}>存证验真</a>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>审计日志</Title>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select placeholder="操作类型" value={actionFilter} onChange={setActionFilter} allowClear style={{ width: 200 }}
          options={Object.keys(actionColors).map(t => ({ label: t, value: t }))}
        />
        <Button
          type="primary"
          disabled={selectedRowKeys.length === 0}
          loading={anchorMutation.isPending}
          onClick={() => anchorMutation.mutate(selectedRowKeys)}
        >
          锚定选中记录 ({selectedRowKeys.length})
        </Button>
      </Space>

      {isError && (
        <QueryErrorAlert error={error} message="审计日志加载失败" onRetry={() => { void refetch(); }} />
      )}
      <Table
        columns={columns}
        dataSource={data?.items || []}
        rowKey="id"
        loading={isLoading}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as string[]),
          getCheckboxProps: (record) => ({ disabled: !!record.blockchain_tx_hash }),
        }}
        pagination={{ current: page, total: data?.total || 0, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />

      <Drawer title="审计记录详情" open={!!selectedRecord} onClose={() => setSelectedRecord(null)} width={600}>
        {selectedRecord && (
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="记录ID">{selectedRecord.id}</Descriptions.Item>
            <Descriptions.Item label="操作"><Tag color={actionColors[selectedRecord.action] || 'default'}>{selectedRecord.action}</Tag></Descriptions.Item>
            <Descriptions.Item label="资源类型">{selectedRecord.resource_type}</Descriptions.Item>
            <Descriptions.Item label="资源ID">{selectedRecord.resource_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="用户ID">{selectedRecord.user_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="会话ID">{selectedRecord.session_id || '-'}</Descriptions.Item>
            <Descriptions.Item label="IP地址">{selectedRecord.ip_address || '-'}</Descriptions.Item>
            <Descriptions.Item label="区块链TX">{selectedRecord.blockchain_tx_hash || '未锚定'}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{new Date(selectedRecord.created_at).toLocaleString()}</Descriptions.Item>
            <Descriptions.Item label="详情">
              <pre style={{ fontSize: 12, maxHeight: 200, overflow: 'auto' }}>{JSON.stringify(selectedRecord.detail, null, 2)}</pre>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>

      <AuditVerifyModal
        recordId={verifyTargetId}
        open={verifyOpen}
        onClose={() => setVerifyOpen(false)}
      />
    </div>
  );
}
