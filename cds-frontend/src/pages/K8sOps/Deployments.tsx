import { useState } from 'react';
import { Card, Table, Tag, Badge, Alert, Button, Space, Typography } from 'antd';
import { ReloadOutlined, ClusterOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { listDeployments, type OpsDeployment } from '../../services/k8sOpsApi';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';

const { Text } = Typography;

const statusColor: Record<string, string> = {
  running: 'green',
  provisioning: 'blue',
  failed: 'red',
  completed: 'default',
};

const statusLabel: Record<string, string> = {
  running: '运行中',
  provisioning: '创建中',
  failed: '失败',
  completed: '已完成',
};

export default function Deployments() {
  const [refreshing, setRefreshing] = useState(false);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ops', 'deployments'],
    queryFn: listDeployments,
  });

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  };

  const columns: ColumnsType<OpsDeployment> = [
    {
      title: 'Pod',
      dataIndex: 'pod_name',
      width: 220,
      render: (value: string) => <Text strong>{value}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'cds_status',
      width: 110,
      render: (value: string) => (
        <Tag color={statusColor[value] || 'default'}>{statusLabel[value] || value}</Tag>
      ),
    },
    {
      title: '就绪',
      dataIndex: 'ready',
      width: 90,
      render: (value: boolean) => (value ? <Badge status="success" text="就绪" /> : <Badge status="error" text="未就绪" />),
    },
    { title: 'Phase', dataIndex: 'phase', width: 110 },
    { title: 'Pod IP', dataIndex: 'pod_ip', width: 130 },
    { title: '会话 ID', dataIndex: 'session_id', width: 180, ellipsis: true },
    { title: '用户 ID', dataIndex: 'user_id', width: 160, ellipsis: true },
    {
      title: '原因',
      dataIndex: 'reason',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
  ];

  return (
    <Card
      title={
        <Space>
          <ClusterOutlined />
          <span>K8s 部署（沙箱 Pod）</span>
        </Space>
      }
      extra={
        <Space>
          <Text type="secondary">命名空间 {data?.namespace || '-'}</Text>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={handleRefresh}>刷新</Button>
        </Space>
      }
    >
      {error ? <QueryErrorAlert error={error} onRetry={handleRefresh} /> : null}
      {data && !data.cluster_available ? (
        <Alert
          type="warning"
          showIcon
          message="集群不可用"
          description="无法连接 K8s 集群（python client / kubectl 不可达），当前展示为空。请确认集群已就绪（scripts/install-k3s.sh + e2e-k8s.sh）。"
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <Table
        dataSource={data?.items || []}
        rowKey="pod_name"
        loading={isLoading}
        columns={columns}
        scroll={{ x: 1200 }}
        pagination={data?.items.length ? { pageSize: 20, showTotal: (t) => `共 ${t} 个 Pod` } : false}
        locale={tableEmpty(
          data?.cluster_available === false ? '集群不可用，无部署数据' : '暂无沙箱 Pod',
        )}
      />
    </Card>
  );
}
