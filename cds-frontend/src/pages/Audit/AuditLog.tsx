import { useState } from 'react';
import { Table, Tag, Typography, Space, Select, Button, Drawer, Descriptions, message, Alert, Input } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { auditApi, type AuditRecord } from '../../services/auditApi';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

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
  const [verifyResult, setVerifyResult] = useState<{ verified: boolean; tx_hash: string | null } | null>(null);
  const [proofResult, setProofResult] = useState<{ record_hash: string; tx_hash: string } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['audit-records', page, actionFilter],
    queryFn: () => auditApi.list({ page, page_size: 20, action: actionFilter }),
  });

  const anchorMutation = useMutation({
    mutationFn: (ids: string[]) => auditApi.anchor(ids),
    onSuccess: (r) => {
      message.success(`已锚定 ${r.anchored} 条记录, Merkle Root: ${r.merkle_root?.slice(0, 16)}...`);
      queryClient.invalidateQueries({ queryKey: ['audit-records'] });
      setSelectedRowKeys([]);
    },
    onError: () => message.error('锚定失败'),
  });

  const verifyMutation = useMutation({
    mutationFn: (id: string) => auditApi.verify(id),
    onSuccess: (r) => {
      setVerifyResult(r);
      message.success(r.verified ? '区块链验证通过' : '区块链验证失败');
    },
    onError: () => message.error('验证请求失败'),
  });

  const proofMutation = useMutation({
    mutationFn: (id: string) => auditApi.getMerkleProof(id),
    onSuccess: (r) => {
      setProofResult(r);
    },
    onError: () => message.error('获取Merkle证明失败'),
  });

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
          <a onClick={() => { setSelectedRecord(record); setVerifyResult(null); setProofResult(null); }}>详情</a>
          {record.blockchain_tx_hash && (
            <a onClick={() => verifyMutation.mutate(record.id)}>验证</a>
          )}
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

      {verifyResult && (
        <Alert
          type={verifyResult.verified ? 'success' : 'error'}
          message={verifyResult.verified ? '区块链验证通过' : '区块链验证失败'}
          description={verifyResult.tx_hash ? `TX: ${verifyResult.tx_hash}` : '无链上记录'}
          closable
          onClose={() => setVerifyResult(null)}
          style={{ marginBottom: 16 }}
        />
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

        {selectedRecord?.blockchain_tx_hash && (
          <div style={{ marginTop: 16 }}>
            <Title level={5}>Merkle 证明</Title>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Button
                loading={proofMutation.isPending}
                onClick={() => proofMutation.mutate(selectedRecord.id)}
              >
                获取 Merkle 证明
              </Button>
              {proofResult && (
                <Descriptions bordered column={1} size="small">
                  <Descriptions.Item label="记录哈希">
                    <Text code copyable>{proofResult.record_hash}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="区块链TX">
                    <Text code copyable>{proofResult.tx_hash}</Text>
                  </Descriptions.Item>
                </Descriptions>
              )}
            </Space>
          </div>
        )}
      </Drawer>
    </div>
  );
}
