import { useState } from 'react';
import { Card, Table, Tag, Typography, Space, Button, Modal, Form, Select, Input, message, Descriptions, Progress, Spin } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { federationApi } from '../../services/federationApi';
import type { TrustRelationship, SyncStatus } from '../../services/federationApi';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

const trustLevelColors: Record<string, string> = {
  none: 'default',
  basic: 'blue',
  verified: 'green',
  full: 'gold',
};

const trustStatusColors: Record<string, string> = {
  active: 'green',
  suspended: 'orange',
  revoked: 'red',
};

export default function CrossSpaceDashboard() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm();

  const { data: trusts, isLoading: trustsLoading } = useQuery({
    queryKey: ['federation-trusts'],
    queryFn: () => federationApi.listTrusts(),
  });

  const { data: syncStatus, isLoading: syncLoading } = useQuery({
    queryKey: ['catalog-sync-status'],
    queryFn: () => federationApi.getSyncStatus(),
  });

  const suspendMutation = useMutation({
    mutationFn: (id: string) => federationApi.suspendTrust(id),
    onSuccess: () => { message.success('已暂停'); queryClient.invalidateQueries({ queryKey: ['federation-trusts'] }); },
    onError: () => message.error('操作失败'),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => federationApi.revokeTrust(id),
    onSuccess: () => { message.success('已撤销'); queryClient.invalidateQueries({ queryKey: ['federation-trusts'] }); },
    onError: () => message.error('操作失败'),
  });

  const syncMutation = useMutation({
    mutationFn: ({ spaceId, mode }: { spaceId: string; mode: 'full' | 'incremental' }) =>
      federationApi.triggerSync(spaceId, mode),
    onSuccess: (data) => { message.success(`同步完成，${data.synced} 条记录`); queryClient.invalidateQueries({ queryKey: ['catalog-sync-status'] }); },
    onError: () => message.error('同步失败'),
  });

  const createMutation = useMutation({
    mutationFn: federationApi.establishTrust,
    onSuccess: () => { message.success('信任关系已建立'); setCreateOpen(false); form.resetFields(); queryClient.invalidateQueries({ queryKey: ['federation-trusts'] }); },
    onError: () => message.error('创建失败'),
  });

  const trustColumns: ColumnsType<TrustRelationship> = [
    { title: '信任ID', dataIndex: 'trust_id', key: 'trust_id', render: (v) => <Text code>{v.slice(0, 8)}...</Text> },
    { title: '远端空间', dataIndex: 'remote_space_name', key: 'remote_space_name' },
    { title: '信任等级', dataIndex: 'trust_level', key: 'trust_level', render: (v) => <Tag color={trustLevelColors[v]}>{v.toUpperCase()}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <Tag color={trustStatusColors[v]}>{v}</Tag> },
    { title: '允许操作', dataIndex: 'allowed_operations', key: 'ops', render: (ops: string[]) => ops?.map(o => <Tag key={o}>{o}</Tag>) },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v) => new Date(v).toLocaleString() },
    {
      title: '操作', key: 'action', width: 160,
      render: (_, record) => (
        <Space>
          {record.status === 'active' && (
            <Button size="small" onClick={() => suspendMutation.mutate(record.trust_id)}>暂停</Button>
          )}
          {record.status === 'suspended' && (
            <Button size="small" danger onClick={() => revokeMutation.mutate(record.trust_id)}>撤销</Button>
          )}
        </Space>
      ),
    },
  ];

  const syncColumns: ColumnsType<SyncStatus> = [
    { title: '空间', dataIndex: 'space_name', key: 'space_name' },
    { title: '同步条目', dataIndex: 'entry_count', key: 'entry_count' },
    { title: '最后同步', dataIndex: 'last_sync_at', key: 'last_sync_at', render: (v) => v ? new Date(v).toLocaleString() : '未同步' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <Tag color={v === 'synced' ? 'green' : 'orange'}>{v}</Tag> },
    {
      title: '操作', key: 'action', width: 200,
      render: (_, record) => (
        <Space>
          <Button size="small" loading={syncMutation.isPending} onClick={() => syncMutation.mutate({ spaceId: record.space_id, mode: 'incremental' })}>增量同步</Button>
          <Button size="small" loading={syncMutation.isPending} onClick={() => syncMutation.mutate({ spaceId: record.space_id, mode: 'full' })}>全量同步</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>跨空间联邦</Title>

      {/* Trust Relationships */}
      <Card title="信任关系" style={{ marginBottom: 16 }} extra={<Button type="primary" onClick={() => setCreateOpen(true)}>建立信任</Button>}>
        <Table columns={trustColumns} dataSource={trusts || []} rowKey="trust_id" loading={trustsLoading} pagination={false} />
      </Card>

      {/* Catalog Sync Status */}
      <Card title="目录同步状态">
        <Table columns={syncColumns} dataSource={syncStatus || []} rowKey="space_id" loading={syncLoading} pagination={false} />
      </Card>

      {/* Create Trust Modal */}
      <Modal title="建立信任关系" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()} confirmLoading={createMutation.isPending}>
        <Form form={form} layout="vertical" onFinish={(v) => createMutation.mutate(v)}>
          <Form.Item name="remote_space_id" label="远端空间ID" rules={[{ required: true }]}>
            <Input placeholder="输入远端空间 ID" />
          </Form.Item>
          <Form.Item name="trust_level" label="信任等级" rules={[{ required: true }]}>
            <Select options={[
              { label: 'BASIC — 只读目录', value: 'basic' },
              { label: 'VERIFIED — 数据读取', value: 'verified' },
              { label: 'FULL — 训练/写入', value: 'full' },
            ]} />
          </Form.Item>
          <Form.Item name="allowed_operations" label="允许操作" rules={[{ required: true }]}>
            <Select mode="multiple" options={[
              { label: 'read_catalog', value: 'read_catalog' },
              { label: 'search', value: 'search' },
              { label: 'read_data', value: 'read_data' },
              { label: 'write_data', value: 'write_data' },
              { label: 'train_model', value: 'train_model' },
            ]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
