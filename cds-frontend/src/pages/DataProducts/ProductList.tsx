import { useState } from 'react';
import { Table, Button, Space, Tag, Input, Select, Typography, message, Popconfirm } from 'antd';
import { PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { dataProductApi } from '../../services/dataProductApi';
import type { DataProduct } from '../../types/models';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, ROLE_GROUPS } from '../../utils/roles';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import type { ColumnsType } from 'antd/es/table';

const { Title } = Typography;

const productTypeColors: Record<string, string> = {
  structured: 'blue',
  unstructured: 'purple',
  'semi-structured': 'cyan',
};

const statusColors: Record<string, string> = {
  draft: 'default',
  reviewing: 'processing',
  approved: 'blue',
  published: 'green',
  suspended: 'orange',
  archived: 'orange',
};

const statusLabels: Record<string, string> = {
  draft: '草稿',
  reviewing: '审核中',
  approved: '已批准',
  published: '已发布',
  suspended: '已暂停',
  archived: '已归档',
};

const productTypeLabels: Record<string, string> = {
  structured: '结构化',
  unstructured: '非结构化',
  'semi-structured': '半结构化',
  api: 'API',
};

const securityLevelColors: Record<string, string> = {
  public: 'green',
  internal: 'blue',
  confidential: 'orange',
  secret: 'red',
};

export default function ProductList() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const canWriteProduct = hasAnyRole(user?.role, ROLE_GROUPS.productWriters);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>(searchParams.get('status') || undefined);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['data-products', page, search, typeFilter, statusFilter],
    queryFn: () => dataProductApi.list({ page, page_size: 20, q: search || undefined, product_type: typeFilter, status: statusFilter }),
  });

  const handleStatusChange = (value?: string) => {
    setStatusFilter(value);
    setPage(1);
    const nextParams = new URLSearchParams(searchParams);
    if (value) nextParams.set('status', value);
    else nextParams.delete('status');
    setSearchParams(nextParams, { replace: true });
  };

  const deleteMutation = useMutation({
    mutationFn: (id: string) => dataProductApi.delete(id),
    onSuccess: () => {
      message.success('删除成功');
      queryClient.invalidateQueries({ queryKey: ['data-products'] });
    },
    onError: () => message.error('删除失败'),
  });

  const archiveMutation = useMutation({
    mutationFn: (id: string) => dataProductApi.archive(id),
    onSuccess: () => {
      message.success('归档成功');
      queryClient.invalidateQueries({ queryKey: ['data-products'] });
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
    },
    onError: () => message.error('归档失败，请先确认没有活跃合约或沙箱会话'),
  });

  const columns: ColumnsType<DataProduct> = [
    { title: '名称', dataIndex: 'name', key: 'name', render: (text, record) => <a onClick={() => navigate(`/data-products/${record.id}`)}>{text}</a> },
    { title: '数据类型', dataIndex: 'product_type', key: 'product_type', render: (v) => <Tag color={productTypeColors[v]}>{productTypeLabels[v] || v}</Tag> },
    { title: '所属行业', dataIndex: 'industry', key: 'industry', render: (v) => <Tag>{v || '-'}</Tag> },
    { title: '安全等级', dataIndex: 'security_level', key: 'security_level', render: (v) => v ? <Tag color={securityLevelColors[v]}>{v}</Tag> : '-' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <Tag color={statusColors[v]}>{statusLabels[v] || v}</Tag> },
    { title: '行数', dataIndex: 'row_count', key: 'row_count' },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v) => new Date(v).toLocaleDateString() },
    {
      title: '操作', key: 'action', render: (_, record) => (
        <Space>
          <a onClick={() => navigate(`/data-products/${record.id}`)}>查看</a>
          {canWriteProduct && record.status === 'draft' && (
            <Popconfirm title="确认删除？" onConfirm={() => deleteMutation.mutate(record.id)}>
              <a style={{ color: '#ff4d4f' }}>删除</a>
            </Popconfirm>
          )}
          {canWriteProduct && record.status !== 'draft' && record.status !== 'archived' && (
            <Popconfirm title="确认归档？归档后将从目录下架。" onConfirm={() => archiveMutation.mutate(record.id)}>
              <a style={{ color: '#d48806' }}>归档</a>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>数据产品</Title>
        {canWriteProduct && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/data-products/create')}>
            创建产品
          </Button>
        )}
      </div>
      <Space style={{ marginBottom: 16 }}>
        <Input
          placeholder="搜索产品"
          prefix={<SearchOutlined />}
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          style={{ width: 240 }}
          allowClear
        />
        <Select
          placeholder="数据类型"
          value={typeFilter}
          onChange={(value) => { setTypeFilter(value); setPage(1); }}
          allowClear
          style={{ width: 160 }}
          options={[
            { label: '结构化', value: 'structured' },
            { label: '非结构化', value: 'unstructured' },
            { label: '半结构化', value: 'semi-structured' },
            { label: 'API', value: 'api' },
          ]}
        />
        <Select
          placeholder="状态"
          value={statusFilter}
          onChange={handleStatusChange}
          allowClear
          style={{ width: 150 }}
          options={[
            { label: '草稿', value: 'draft' },
            { label: '审核中', value: 'reviewing' },
            { label: '已批准', value: 'approved' },
            { label: '已发布', value: 'published' },
            { label: '已暂停', value: 'suspended' },
            { label: '已归档', value: 'archived' },
          ]}
        />
      </Space>

      {isError && (
        <QueryErrorAlert error={error} message="数据产品列表加载失败" onRetry={() => { void refetch(); }} />
      )}

      <Table
        columns={columns}
        dataSource={data?.items || []}
        rowKey="id"
        loading={isLoading}
        pagination={{
          current: page,
          total: data?.total || 0,
          pageSize: 20,
          onChange: setPage,
          showTotal: (total) => `共 ${total} 条`,
        }}
        locale={tableEmpty(search || typeFilter || statusFilter ? '没有匹配筛选条件的数据产品' : '暂无数据产品')}
      />
    </div>
  );
}
