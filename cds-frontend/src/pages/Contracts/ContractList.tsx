import { useState } from 'react';
import { Table, Button, Space, Tag, Input, Select, Typography } from 'antd';
import { PlusOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { contractApi } from '../../services/contractApi';
import type { Contract } from '../../types/models';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, ROLE_GROUPS } from '../../utils/roles';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import type { ColumnsType } from 'antd/es/table';

const { Title } = Typography;

const statusColors: Record<string, string> = {
  draft: 'default', negotiating: 'blue', signed: 'green', active: 'lime',
  suspended: 'orange', completed: 'purple', violated: 'red', archived: 'gray',
};

const typeLabels: Record<string, string> = {
  data_query: '数据查询', model_training: '模型训练', data_application: '数据应用',
  api_service: 'API服务', joint_compute: '联合计算', product_dev: '产品开发', data_modeling: '数据建模',
};

export default function ContractList() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const canWriteContract = hasAnyRole(user?.role, ROLE_GROUPS.contractWriters);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string | undefined>();

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['contracts', page, statusFilter],
    queryFn: () => contractApi.list({ page, page_size: 20, status: statusFilter }),
  });

  const columns: ColumnsType<Contract> = [
    { title: '标题', dataIndex: 'title', key: 'title', render: (text, record) => <a onClick={() => navigate(`/contracts/${record.id}`)}>{text}</a> },
    { title: '类型', dataIndex: 'contract_type', key: 'contract_type', render: (v) => typeLabels[v] || v },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <Tag color={statusColors[v]}>{v}</Tag> },
    { title: '沙箱级别', dataIndex: 'allowed_sandbox_levels', key: 'allowed_sandbox_levels', render: (v) => v || '-' },
    { title: 'DP预算(ε)', dataIndex: 'dp_epsilon_budget', key: 'dp_epsilon_budget', render: (v) => v ?? '-' },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v) => new Date(v).toLocaleDateString() },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>合约管理</Title>
        {canWriteContract && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/contracts/create')}>创建合约</Button>
        )}
      </div>
      <Space style={{ marginBottom: 16 }}>
        <Select placeholder="状态筛选" value={statusFilter} onChange={setStatusFilter} allowClear style={{ width: 160 }}
          options={['draft', 'negotiating', 'signed', 'active', 'completed', 'terminated'].map(s => ({ label: s, value: s }))}
        />
      </Space>

      {isError && (
        <QueryErrorAlert error={error} message="合约列表加载失败" onRetry={() => { void refetch(); }} />
      )}

      <Table columns={columns} dataSource={data?.items || []} rowKey="id" loading={isLoading}
        pagination={{ current: page, total: data?.total || 0, pageSize: 20, onChange: setPage, showTotal: (t) => `共 ${t} 条` }}
        locale={tableEmpty(statusFilter ? '没有匹配状态的合约' : '暂无合约')}
      />
    </div>
  );
}
