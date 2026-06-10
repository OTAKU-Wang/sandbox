import { Typography, Card, Row, Col, Statistic, Table, Tag, Space, Button, Tooltip as AntTooltip, Alert } from 'antd';
import {
  DatabaseOutlined,
  FileTextOutlined,
  CloudServerOutlined,
  SafetyOutlined,
  BlockOutlined,
  AuditOutlined,
  CloudUploadOutlined,
  KeyOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SearchOutlined,
  CheckCircleOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer,
  PieChart, Pie, Cell, BarChart, Bar, Legend,
} from 'recharts';
import { monitoringApi } from '../../services/monitoringApi';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, ROLE_GROUPS } from '../../utils/roles';
import { UserRole } from '../../types/enums';
import { EmptyState, QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import dayjs from 'dayjs';

const { Title } = Typography;

const COLORS = ['#1677ff', '#52c41a', '#faad14', '#ff4d4f'];

const ACTION_LABELS: Record<string, string> = {
  'sandbox.create': '创建沙箱',
  'sandbox.start': '启动沙箱',
  'sandbox.stop': '停止沙箱',
  'task.execute': '执行任务',
  'task.complete': '完成任务',
  'task.fail': '任务失败',
  'policy.deny': '策略拒绝',
  'output_inspection_fail': '审查失败',
  'contract_violation': '合约违规',
};

const LEVEL_COLORS: Record<string, string> = {
  L1: '#52c41a',
  L2: '#faad14',
  L3: '#ff4d4f',
  k8s: '#1677ff',
};

const POSTURE_STATUS_LABELS: Record<string, string> = {
  verified: '已验证',
  configured: '已配置',
  software: '软件模式',
  not_configured: '未配置',
  risk: '风险',
};

const POSTURE_STATUS_COLORS: Record<string, string> = {
  verified: 'green',
  configured: 'blue',
  software: 'orange',
  not_configured: 'default',
  risk: 'red',
};

const RELEASE_RECOMMENDATION: Record<string, { label: string; color: string }> = {
  go: { label: 'Go', color: 'green' },
  conditional_go: { label: 'Conditional Go', color: 'orange' },
  no_go: { label: 'No-Go', color: 'red' },
};

export default function Dashboard() {
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const role = user?.role;
  const canViewSecurityPosture = hasAnyRole(role, ROLE_GROUPS.monitoringReaders);

  const {
    data: stats,
    isLoading,
    isError: statsIsError,
    error: statsError,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ['monitoring-stats'],
    queryFn: monitoringApi.getStats,
  });

  const {
    data: taskTrend,
    isError: taskTrendIsError,
    error: taskTrendError,
    refetch: refetchTaskTrend,
  } = useQuery({
    queryKey: ['task-trend'],
    queryFn: () => monitoringApi.getTaskTrend(7),
  });

  const {
    data: policyRejection,
    isError: policyRejectionIsError,
    error: policyRejectionError,
    refetch: refetchPolicyRejection,
  } = useQuery({
    queryKey: ['policy-rejection'],
    queryFn: monitoringApi.getPolicyRejection,
  });

  const {
    data: securityDist,
    isError: securityDistIsError,
    error: securityDistError,
    refetch: refetchSecurityDist,
  } = useQuery({
    queryKey: ['security-distribution'],
    queryFn: monitoringApi.getSecurityDistribution,
  });

  const {
    data: recentEvents,
    isLoading: recentEventsLoading,
    isError: recentEventsIsError,
    error: recentEventsError,
    refetch: refetchRecentEvents,
  } = useQuery({
    queryKey: ['recent-events'],
    queryFn: () => monitoringApi.getRecentEvents(10),
    refetchInterval: 30000, // Refresh every 30s
  });

  const {
    data: securityPosture,
    isError: securityPostureIsError,
    error: securityPostureError,
    refetch: refetchSecurityPosture,
  } = useQuery({
    queryKey: ['security-posture'],
    queryFn: monitoringApi.getSecurityPosture,
    enabled: canViewSecurityPosture,
    refetchInterval: 60000,
  });

  const pieData = policyRejection?.categories?.map((c) => ({
    name: ACTION_LABELS[c.category] || c.category,
    value: c.count,
  })) || [];

  const barData = securityDist?.map((d) => ({
    level: d.level,
    count: d.count,
  })) || [];

  const eventColumns = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      dataIndex: 'action',
      key: 'action',
      render: (v: string) => <Tag>{ACTION_LABELS[v] || v}</Tag>,
    },
    {
      title: '资源',
      dataIndex: 'resource_type',
      key: 'resource_type',
    },
  ];

  const roleActions = [
    ...(hasAnyRole(role, [UserRole.DATA_PROVIDER]) ? [
      { title: '上传资源', icon: <CloudUploadOutlined />, path: '/data-resources' },
      { title: '创建产品', icon: <PlusOutlined />, path: '/data-products/create' },
      { title: '产品发布', icon: <DatabaseOutlined />, path: '/data-products' },
    ] : []),
    ...(hasAnyRole(role, [UserRole.BUYER]) ? [
      { title: '浏览目录', icon: <SearchOutlined />, path: '/catalog' },
      { title: '创建沙箱', icon: <CloudServerOutlined />, path: '/sandbox-sessions?create=1' },
      { title: '训练任务', icon: <PlayCircleOutlined />, path: '/training' },
    ] : []),
    ...(hasAnyRole(role, [UserRole.OPERATOR, UserRole.ADMIN]) ? [
      { title: '产品审核', icon: <CheckCircleOutlined />, path: '/data-products?status=reviewing' },
      { title: '输出审查', icon: <SafetyOutlined />, path: '/output-control' },
      { title: '证书密钥', icon: <KeyOutlined />, path: '/identity/keys' },
    ] : []),
    ...(hasAnyRole(role, [UserRole.REGULATOR]) ? [
      { title: '审计追踪', icon: <AuditOutlined />, path: '/audit' },
      { title: '安全监控', icon: <SafetyOutlined />, path: '/monitoring' },
      { title: '合约核验', icon: <FileTextOutlined />, path: '/contracts' },
    ] : []),
  ];

  return (
    <div>
      <Title level={4}>运营概览</Title>

      {roleActions.length > 0 && (
        <Card style={{ marginBottom: 16 }}>
          <Space wrap>
            {roleActions.map((action) => (
              <Button key={action.path} icon={action.icon} onClick={() => navigate(action.path)}>
                {action.title}
              </Button>
            ))}
          </Space>
        </Card>
      )}

      {statsIsError && (
        <QueryErrorAlert error={statsError} message="运营统计加载失败" onRetry={() => { void refetchStats(); }} />
      )}
      {policyRejectionIsError && (
        <QueryErrorAlert error={policyRejectionError} message="策略拒绝统计加载失败" onRetry={() => { void refetchPolicyRejection(); }} />
      )}

      {canViewSecurityPosture && (
        <Card
          title="安全态势"
          style={{ marginBottom: 16 }}
          extra={
            securityPosture && (
              <Tag color={RELEASE_RECOMMENDATION[securityPosture.release_recommendation]?.color || 'default'}>
                {RELEASE_RECOMMENDATION[securityPosture.release_recommendation]?.label || securityPosture.release_recommendation}
              </Tag>
            )
          }
        >
          {securityPostureIsError ? (
            <QueryErrorAlert error={securityPostureError} message="安全态势加载失败" onRetry={() => { void refetchSecurityPosture(); }} />
          ) : securityPosture ? (
            <Space wrap size={[8, 8]}>
              {securityPosture.capabilities.map((item) => (
                <AntTooltip
                  key={item.id}
                  title={
                    <div>
                      <div>{item.summary}</div>
                      <div style={{ marginTop: 4 }}>{item.action}</div>
                      {item.evidence?.length > 0 && (
                        <div style={{ marginTop: 4 }}>证据: {item.evidence.join(' / ')}</div>
                      )}
                    </div>
                  }
                >
                  <Tag color={POSTURE_STATUS_COLORS[item.status] || 'default'} style={{ padding: '4px 8px', lineHeight: '22px' }}>
                    {item.name}: {POSTURE_STATUS_LABELS[item.status] || item.status}
                  </Tag>
                </AntTooltip>
              ))}
            </Space>
          ) : (
            <Alert type="info" showIcon message="安全态势加载中" />
          )}
        </Card>
      )}

      {/* Stats Cards */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="数据产品"
              value={stats?.total_products ?? '-'}
              prefix={<DatabaseOutlined />}
              loading={isLoading}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="今日合约签署"
              value={stats?.today_contracts ?? '-'}
              prefix={<FileTextOutlined />}
              loading={isLoading}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="运行沙箱"
              value={stats?.active_sessions ?? '-'}
              prefix={<CloudServerOutlined />}
              loading={isLoading}
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
              loading={isLoading}
            />
          </Card>
        </Col>
      </Row>

      {/* Role-specific stats */}
      {hasAnyRole(role, ROLE_GROUPS.monitoringReaders) && (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title="链上存证"
                value={stats?.total_anchored ?? '-'}
                prefix={<BlockOutlined />}
                loading={isLoading}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title="审计记录"
                value={stats?.total_audit_records ?? '-'}
                loading={isLoading}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title="策略拒绝率"
                value={policyRejection ? (100 - policyRejection.pass_rate).toFixed(1) : '-'}
                suffix="%"
                valueStyle={{ color: (policyRejection?.pass_rate ?? 100) < 95 ? '#ff4d4f' : '#52c41a' }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* Charts Row */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        {/* Task Trend */}
        <Col xs={24} lg={12}>
          <Card title="任务趋势 (7天)">
            {taskTrendIsError ? (
              <QueryErrorAlert error={taskTrendError} message="任务趋势加载失败" onRetry={() => { void refetchTaskTrend(); }} />
            ) : taskTrend?.length ? (
              <ResponsiveContainer width="100%" height={250}>
                <LineChart data={taskTrend}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(v) => dayjs(v).format('MM/DD')}
                  />
                  <YAxis />
                  <RechartsTooltip
                    labelFormatter={(v) => dayjs(v).format('YYYY-MM-DD')}
                  />
                  <Line
                    type="monotone"
                    dataKey="count"
                    stroke="#1677ff"
                    strokeWidth={2}
                    name="任务数"
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 250, display: 'grid', placeItems: 'center' }}>
                <EmptyState description="暂无任务趋势数据" />
              </div>
            )}
          </Card>
        </Col>

        {/* Policy Rejection Pie */}
        <Col xs={24} lg={12}>
          <Card title="策略拒绝分布">
            {policyRejectionIsError ? (
              <QueryErrorAlert error={policyRejectionError} message="策略拒绝分布加载失败" onRetry={() => { void refetchPolicyRejection(); }} />
            ) : pieData.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    labelLine={false}
                    label={({ name, percent = 0 }) => `${name} ${(percent * 100).toFixed(0)}%`}
                    outerRadius={80}
                    fill="#8884d8"
                    dataKey="value"
                  >
                    {pieData.map((_, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <RechartsTooltip />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 250, display: 'grid', placeItems: 'center' }}>
                <EmptyState description="暂无拒绝记录" />
              </div>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        {/* Security Level Distribution */}
        <Col xs={24} lg={12}>
          <Card title="安全等级分布">
            {securityDistIsError ? (
              <QueryErrorAlert error={securityDistError} message="安全等级分布加载失败" onRetry={() => { void refetchSecurityDist(); }} />
            ) : barData.length ? (
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={barData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="level" />
                  <YAxis />
                  <RechartsTooltip />
                  <Legend />
                  <Bar dataKey="count" name="产品数" fill="#1677ff">
                    {barData.map((entry) => (
                      <Cell key={entry.level} fill={LEVEL_COLORS[entry.level] || '#1677ff'} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ height: 250, display: 'grid', placeItems: 'center' }}>
                <EmptyState description="暂无安全等级数据" />
              </div>
            )}
          </Card>
        </Col>

        {/* Recent Events */}
        <Col xs={24} lg={12}>
          <Card title="实时事件流" extra={<span style={{ fontSize: 12, color: '#999' }}>自动刷新</span>}>
            {recentEventsIsError && (
              <QueryErrorAlert error={recentEventsError} message="实时事件加载失败" onRetry={() => { void refetchRecentEvents(); }} />
            )}
            <Table
              dataSource={recentEvents || []}
              columns={eventColumns}
              rowKey="id"
              size="small"
              loading={recentEventsLoading}
              pagination={false}
              scroll={{ y: 210 }}
              locale={tableEmpty('暂无实时事件')}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
