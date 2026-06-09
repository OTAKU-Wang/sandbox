import { useEffect, useState } from 'react';
import { Table, Tag, Typography, Space, Button, Select, Popconfirm, message, Modal, Form, InputNumber } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { sandboxApi } from '../../services/sandboxApi';
import { catalogApi } from '../../services/catalogApi';
import { contractApi } from '../../services/contractApi';
import type { SandboxSession } from '../../types/models';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, ROLE_GROUPS } from '../../utils/roles';
import type { ColumnsType } from 'antd/es/table';

const { Title } = Typography;

const statusColors: Record<string, string> = {
  pending: 'blue',
  key_distributing: 'cyan',
  ready: 'green',
  provisioning: 'blue',
  running: 'green',
  suspended: 'orange',
  completed: 'purple',
  failed: 'red',
  terminated: 'default',
  revoked: 'red',
};

const statusOptions = [
  'pending',
  'key_distributing',
  'ready',
  'provisioning',
  'running',
  'suspended',
  'completed',
  'failed',
  'terminated',
  'revoked',
];

export default function SessionList() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const canCreateSession = hasAnyRole(user?.role, ROLE_GROUPS.sandboxUsers);
  const [form] = Form.useForm();
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [createOpen, setCreateOpen] = useState(false);
  const requestedCreate = searchParams.get('create') === '1';
  const requestedProductId = searchParams.get('product_id');

  const { data, isLoading } = useQuery({
    queryKey: ['sandbox-sessions', page, statusFilter],
    queryFn: () => sandboxApi.list({ page, page_size: 20, status: statusFilter }),
  });

  const { data: catalog } = useQuery({
    queryKey: ['sandbox-catalog-products'],
    queryFn: () => catalogApi.search({ page: 1, page_size: 100 }),
    enabled: canCreateSession,
  });

  const { data: contracts } = useQuery({
    queryKey: ['sandbox-active-contracts'],
    queryFn: () => contractApi.list({ page: 1, page_size: 100, status: 'active' }),
    enabled: canCreateSession,
  });

  const terminateMutation = useMutation({
    mutationFn: (id: string) => sandboxApi.terminate(id),
    onSuccess: () => {
      message.success('已终止');
      queryClient.invalidateQueries({ queryKey: ['sandbox-sessions'] });
    },
    onError: () => message.error('终止失败'),
  });

  const createMutation = useMutation({
    mutationFn: (values: {
      data_product_id: string;
      sandbox_level: string;
      contract_id?: string;
      timeout_seconds?: number;
    }) => sandboxApi.create({
      data_product_id: values.data_product_id,
      sandbox_level: values.sandbox_level,
      contract_id: values.contract_id || null,
      timeout_seconds: values.timeout_seconds,
    }),
    onSuccess: (session) => {
      message.success('沙箱会话已创建');
      setCreateOpen(false);
      setSearchParams({}, { replace: true });
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: ['sandbox-sessions'] });
      navigate(`/sandbox-sessions/${session.id}`);
    },
    onError: () => message.error('创建沙箱会话失败'),
  });

  useEffect(() => {
    if (!canCreateSession || !requestedCreate) return;
    setCreateOpen(true);
    if (requestedProductId) {
      form.setFieldsValue({ data_product_id: requestedProductId });
    }
  }, [canCreateSession, form, requestedCreate, requestedProductId]);

  const closeCreateModal = () => {
    setCreateOpen(false);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('create');
    nextParams.delete('product_id');
    setSearchParams(nextParams, { replace: true });
  };

  const columns: ColumnsType<SandboxSession> = [
    { title: '会话ID', dataIndex: 'id', key: 'id', render: (v) => <a onClick={() => navigate(`/sandbox-sessions/${v}`)}>{v.slice(0, 8)}...</a> },
    { title: '沙箱级别', dataIndex: 'sandbox_level', key: 'sandbox_level', render: (v) => <Tag>{v}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <Tag color={statusColors[v] ?? 'default'}>{v}</Tag> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v) => new Date(v).toLocaleString() },
    {
      title: '操作', key: 'action', width: 120,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/sandbox-sessions/${record.id}`)}>详情</Button>
          {(record.status === 'running' || record.status === 'ready') && (
            <Popconfirm title="确认终止此会话？" onConfirm={() => terminateMutation.mutate(record.id)}>
              <Button size="small" danger>终止</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>沙箱会话</Title>
        {canCreateSession && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            创建会话
          </Button>
        )}
      </div>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="状态筛选" value={statusFilter} onChange={setStatusFilter} allowClear style={{ width: 160 }}
          options={statusOptions.map(s => ({ label: s, value: s }))}
        />
      </Space>
      <Table columns={columns} dataSource={data?.items || []} rowKey="id" loading={isLoading}
        pagination={{ current: page, total: data?.total || 0, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
      />

      <Modal
        title="创建沙箱会话"
        open={createOpen}
        onCancel={closeCreateModal}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={createMutation.mutate}
          initialValues={{ sandbox_level: 'L3', timeout_seconds: 3600 }}
        >
          <Form.Item name="data_product_id" label="数据产品" rules={[{ required: true, message: '请选择数据产品' }]}>
            <Select
              showSearch
              placeholder="选择已发布数据产品"
              optionFilterProp="label"
              options={(catalog?.items || []).map((product) => ({
                label: `${product.name}${product.industry ? ` / ${product.industry}` : ''}${product.security_level ? ` / ${product.security_level}` : ''}`,
                value: product.id,
              }))}
            />
          </Form.Item>
          <Form.Item name="contract_id" label="合约">
            <Select
              allowClear
              showSearch
              placeholder="选择 active 合约"
              optionFilterProp="label"
              options={(contracts?.items || []).map((contract) => ({
                label: `${contract.title} / ${contract.contract_no}`,
                value: contract.id,
              }))}
            />
          </Form.Item>
          <Form.Item name="sandbox_level" label="沙箱级别" rules={[{ required: true, message: '请选择沙箱级别' }]}>
            <Select options={[
              { label: 'L1 - TEE', value: 'L1' },
              { label: 'L2 - 软件增强', value: 'L2' },
              { label: 'L3 - 最小隔离', value: 'L3' },
              { label: 'K8s - 分布式 Pod', value: 'k8s' },
            ]} />
          </Form.Item>
          <Form.Item name="timeout_seconds" label="超时时间（秒）">
            <InputNumber min={60} max={86400} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
