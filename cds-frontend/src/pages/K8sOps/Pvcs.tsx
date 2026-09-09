import { useState } from 'react';
import { Card, Table, Tag, Alert, Button, Space, Typography } from 'antd';
import { ReloadOutlined, HddOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { listOpsPvcs, type OpsPvc } from '../../services/k8sOpsApi';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';

const { Text } = Typography;

const pvcColor: Record<string, string> = {
  Bound: 'green',
  Pending: 'blue',
  unavailable: 'default',
};

const pvcLabel: Record<string, string> = {
  Bound: '已绑定',
  Pending: '挂起',
  unavailable: '不可用',
};

export default function Pvcs() {
  const [refreshing, setRefreshing] = useState(false);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ops', 'pvcs'],
    queryFn: listOpsPvcs,
  });

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  };

  const columns: ColumnsType<OpsPvc> = [
    { title: '卷名', dataIndex: 'name', width: 160, render: (v: string) => <Text strong>{v}</Text> },
    { title: 'PVC Claim', dataIndex: 'claim_name', width: 220, render: (v: string) => <Text code>{v}</Text> },
    {
      title: '容量',
      dataIndex: 'size_limit_mb',
      width: 110,
      render: (value: number) => `${value} MB`,
    },
    {
      title: '只读',
      dataIndex: 'read_only',
      width: 90,
      render: (value: boolean) => (value ? <Tag color="orange">只读</Tag> : <Tag color="green">读写</Tag>),
    },
    {
      title: '关联会话',
      key: 'attachments',
      width: 110,
      render: (_, record) => record.attachments?.length || 0,
    },
    {
      title: 'PVC 状态',
      dataIndex: 'pvc_status',
      width: 110,
      render: (value: string) => <Tag color={pvcColor[value] || 'default'}>{pvcLabel[value] || value}</Tag>,
    },
    { title: '容量(实际)', dataIndex: 'pvc_capacity', width: 120, render: (v?: string | null) => v || '-' },
    { title: '存储类', dataIndex: 'pvc_storage_class', width: 130, render: (v?: string | null) => v || '-' },
  ];

  return (
    <Card
      title={
        <Space>
          <HddOutlined />
          <span>存储卷（PVCs）</span>
        </Space>
      }
      extra={<Button icon={<ReloadOutlined />} loading={refreshing} onClick={handleRefresh}>刷新</Button>}
    >
      {error ? <QueryErrorAlert error={error} onRetry={handleRefresh} /> : null}
      {data && !data.cluster_available ? (
        <Alert
          type="warning"
          showIcon
          message="集群不可用"
          description="无法连接 K8s 集群，PVC 实际绑定状态不可用（卷定义与挂载配置为权威数据，仍展示）。"
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <Table
        dataSource={data?.items || []}
        rowKey="volume_id"
        loading={isLoading}
        columns={columns}
        scroll={{ x: 1000 }}
        pagination={data?.items.length ? { pageSize: 20, showTotal: (t) => `共 ${t} 个卷` } : false}
        locale={tableEmpty('暂无共享卷')}
      />
    </Card>
  );
}
