import { Typography, Card, Row, Col, Statistic, Table, Tag, Spin, Alert } from 'antd';
import { CloudServerOutlined, FileTextOutlined, SafetyOutlined, BlockOutlined, DatabaseOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { monitoringApi, type Alert as AlertType } from '../../services/monitoringApi';
import type { ColumnsType } from 'antd/es/table';

const { Title } = Typography;

const actionLabels: Record<string, string> = {
  output_inspection_fail: 'DLP审查未通过',
  unauthorized_access: '未授权访问',
  dp_budget_exceeded: 'DP预算超限',
  contract_violation: '合约违规',
  sandbox_escape_attempt: '沙箱逃逸尝试',
};

const actionColors: Record<string, string> = {
  output_inspection_fail: 'red',
  unauthorized_access: 'red',
  dp_budget_exceeded: 'orange',
  contract_violation: 'orange',
  sandbox_escape_attempt: 'red',
};

export default function MonitoringDashboard() {
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['monitoring-stats'],
    queryFn: monitoringApi.getStats,
  });

  const { data: alertsData, isLoading: alertsLoading } = useQuery({
    queryKey: ['monitoring-alerts'],
    queryFn: () => monitoringApi.getAlerts({ page: 1, page_size: 20 }),
  });

  const alertColumns: ColumnsType<AlertType> = [
    {
      title: '类型',
      dataIndex: 'action',
      render: (v) => <Tag color={actionColors[v] || 'default'}>{actionLabels[v] || v}</Tag>,
    },
    { title: '资源类型', dataIndex: 'resource_type' },
    { title: '资源ID', dataIndex: 'resource_id', render: (v) => v || '-' },
    {
      title: '详情',
      dataIndex: 'detail',
      render: (v) => v ? <span style={{ fontSize: 12 }}>{JSON.stringify(v).slice(0, 80)}...</span> : '-',
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      render: (v) => v ? new Date(v).toLocaleString() : '-',
    },
  ];

  return (
    <div>
      <Title level={4}>监控中心</Title>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="活跃沙箱"
              value={stats?.active_sessions ?? '-'}
              prefix={<CloudServerOutlined />}
              loading={statsLoading}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="今日合约签署"
              value={stats?.today_contracts ?? '-'}
              prefix={<FileTextOutlined />}
              loading={statsLoading}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="审查通过率"
              value={stats?.inspection_pass_rate ?? '-'}
              suffix="%"
              prefix={<SafetyOutlined />}
              loading={statsLoading}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="链上存证"
              value={stats?.total_anchored ?? '-'}
              prefix={<BlockOutlined />}
              loading={statsLoading}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="数据产品总数"
              value={stats?.total_products ?? '-'}
              prefix={<DatabaseOutlined />}
              loading={statsLoading}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="审计记录总数"
              value={stats?.total_audit_records ?? '-'}
              loading={statsLoading}
            />
          </Card>
        </Col>
      </Row>

      <Card>
        <Title level={5}>安全告警</Title>
        <Table
          dataSource={alertsData?.items || []}
          rowKey="id"
          loading={alertsLoading}
          columns={alertColumns}
          pagination={{ total: alertsData?.total || 0, pageSize: 20 }}
          locale={{ emptyText: '暂无安全告警' }}
        />
      </Card>
    </div>
  );
}
