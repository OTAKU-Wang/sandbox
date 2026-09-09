import { useState } from 'react';
import { Card, Table, Tag, Badge, Alert, Button, Space, Typography } from 'antd';
import { ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { listOpsNetworkPolicies, type OpsNetworkPolicy } from '../../services/k8sOpsApi';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';

const { Text } = Typography;

const modeColor: Record<string, string> = {
  deny_all: 'red',
  allowlist: 'blue',
};

const modeLabel: Record<string, string> = {
  deny_all: '全部拒绝',
  allowlist: '白名单',
};

export default function NetworkPolicies() {
  const [page, setPage] = useState(1);
  const [refreshing, setRefreshing] = useState(false);
  const pageSize = 20;
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['ops', 'network-policies', page],
    queryFn: () => listOpsNetworkPolicies((page - 1) * pageSize, pageSize),
  });

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  };

  const columns: ColumnsType<OpsNetworkPolicy> = [
    {
      title: '会话 ID',
      dataIndex: 'session_id',
      width: 200,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: '策略模式',
      dataIndex: 'mode',
      width: 120,
      render: (value: string) => <Tag color={modeColor[value] || 'default'}>{modeLabel[value] || value}</Tag>,
    },
    {
      title: '允许 IP',
      dataIndex: 'allowed_ips',
      width: 220,
      render: (value: string[]) => value?.length ? value.join(', ') : '-',
    },
    {
      title: '允许域名',
      dataIndex: 'allowed_domains',
      width: 220,
      render: (value: string[]) => value?.length ? value.join(', ') : '-',
    },
    {
      title: '端口',
      dataIndex: 'allowed_ports',
      width: 120,
      render: (value: number[]) => value?.length ? value.join(', ') : '-',
    },
    {
      title: 'DNS 代理',
      dataIndex: 'dns_proxy_enabled',
      width: 110,
      render: (value: boolean) => (value ? <Tag color="cyan">开启</Tag> : <Tag>关闭</Tag>),
    },
    {
      title: 'Pod 存活',
      dataIndex: 'live_pod',
      width: 110,
      render: (value: boolean) => (value ? <Badge status="processing" text="在线" /> : <Badge status="default" text="无" />),
    },
  ];

  return (
    <Card
      title={
        <Space>
          <SafetyCertificateOutlined />
          <span>网络策略（NetworkPolicies）</span>
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
          description="无法连接 K8s 集群，Pod 存活状态无法核实（策略定义为权威数据，仍展示）。"
          style={{ marginBottom: 16 }}
        />
      ) : null}
      <Table
        dataSource={data?.items || []}
        rowKey="session_id"
        loading={isLoading}
        columns={columns}
        scroll={{ x: 1100 }}
        pagination={{
          current: page,
          total: data?.total || 0,
          pageSize,
          showTotal: (t) => `共 ${t} 条`,
          onChange: setPage,
        }}
        locale={tableEmpty('暂无网络策略')}
      />
    </Card>
  );
}
