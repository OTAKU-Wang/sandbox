import { useState } from 'react';
import {
  Typography,
  Card,
  Row,
  Col,
  Statistic,
  Table,
  Tag,
  Select,
  Space,
  Button,
  Modal,
  Input,
  Drawer,
  Descriptions,
  Badge,
  message,
} from 'antd';
import {
  BellOutlined,
  BlockOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  EyeOutlined,
  FileTextOutlined,
  ReloadOutlined,
  SafetyOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  monitoringApi,
  type Alert as AlertType,
  type AlertSeverity,
  type AlertStatus,
} from '../../services/monitoringApi';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

type DispositionAction = 'acknowledge' | 'resolve';

const actionLabels: Record<string, string> = {
  output_inspection_fail: 'DLP审查未通过',
  unauthorized_access: '未授权访问',
  dp_budget_exceeded: 'DP预算超限',
  contract_violation: '合约违规',
  sandbox_escape_attempt: '沙箱逃逸尝试',
};

const alertTypeLabels: Record<string, string> = {
  consecutive_rejection: '连续输出拒绝',
  dp_budget_exhaustion: 'DP预算耗尽',
  anomaly_burst: '访问突增',
  anomaly_off_hours: '非工作时段访问',
};

const actionColors: Record<string, string> = {
  output_inspection_fail: 'red',
  unauthorized_access: 'red',
  dp_budget_exceeded: 'orange',
  contract_violation: 'orange',
  sandbox_escape_attempt: 'red',
};

const severityLabels: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
};

const severityColors: Record<string, string> = {
  critical: 'red',
  high: 'volcano',
  medium: 'orange',
  low: 'blue',
};

const statusLabels: Record<string, string> = {
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已解决',
};

const statusColors: Record<string, string> = {
  open: 'red',
  acknowledged: 'gold',
  resolved: 'green',
};

const notificationLabels: Record<string, string> = {
  not_configured: '未配置',
  pending: '发送中',
  delivered: '已送达',
  failed: '失败',
};

const notificationBadgeStatus: Record<string, 'default' | 'processing' | 'success' | 'error'> = {
  not_configured: 'default',
  pending: 'processing',
  delivered: 'success',
  failed: 'error',
};

const alertTypeOptions = [
  { label: '连续输出拒绝', value: 'consecutive_rejection' },
  { label: 'DP预算耗尽', value: 'dp_budget_exhaustion' },
  { label: '访问突增', value: 'anomaly_burst' },
  { label: '非工作时段访问', value: 'anomaly_off_hours' },
];

const formatTime = (value?: string | null) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '-');

const getAlertLabel = (record: AlertType) => {
  const type = record.alert_type || record.action || '-';
  return alertTypeLabels[type] || actionLabels[type] || type;
};

const getAlertColor = (record: AlertType) => {
  if (record.severity) return severityColors[String(record.severity)] || 'default';
  return actionColors[record.action || ''] || 'default';
};

const getAlertMessage = (record: AlertType) => {
  if (record.message) return record.message;
  if (record.detail) return JSON.stringify(record.detail);
  return '-';
};

const getResourceText = (record: AlertType) => {
  const type = record.resource_type || (record.session_id ? 'sandbox_session' : '-');
  const id = record.resource_id || record.session_id || record.user_id || '-';
  return `${type} / ${id}`;
};

const isPersistentAlert = (record: AlertType) => Boolean(record.alert_type);

export default function MonitoringDashboard() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<AlertStatus | undefined>();
  const [severityFilter, setSeverityFilter] = useState<AlertSeverity | undefined>();
  const [typeFilter, setTypeFilter] = useState<string | undefined>();
  const [selectedAlert, setSelectedAlert] = useState<AlertType | null>(null);
  const [disposition, setDisposition] = useState<{ action: DispositionAction; alert: AlertType } | null>(null);
  const [dispositionNote, setDispositionNote] = useState('');

  const {
    data: stats,
    isLoading: statsLoading,
    isError: statsIsError,
    error: statsError,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ['monitoring-stats'],
    queryFn: monitoringApi.getStats,
  });

  const hasAlertFilters = Boolean(statusFilter || severityFilter || typeFilter);

  const {
    data: alertsData,
    isLoading: alertsLoading,
    isFetching: alertsFetching,
    isError: alertsIsError,
    error: alertsError,
    refetch: refetchAlerts,
  } = useQuery({
    queryKey: ['monitoring-alerts', page, statusFilter, severityFilter, typeFilter],
    queryFn: () => monitoringApi.getAlerts({
      page,
      page_size: 20,
      status: statusFilter,
      severity: severityFilter,
      alert_type: typeFilter,
      include_legacy: !hasAlertFilters,
    }),
  });

  const closeDispositionModal = () => {
    setDisposition(null);
    setDispositionNote('');
  };

  const acknowledgeMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => monitoringApi.acknowledgeAlert(id, note),
    onSuccess: () => {
      message.success('告警已确认');
      queryClient.invalidateQueries({ queryKey: ['monitoring-alerts'] });
      closeDispositionModal();
    },
    onError: () => message.error('告警确认失败'),
  });

  const resolveMutation = useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) => monitoringApi.resolveAlert(id, note),
    onSuccess: () => {
      message.success('告警已解决');
      queryClient.invalidateQueries({ queryKey: ['monitoring-alerts'] });
      closeDispositionModal();
    },
    onError: () => message.error('告警解决失败'),
  });

  const resetFilters = () => {
    setStatusFilter(undefined);
    setSeverityFilter(undefined);
    setTypeFilter(undefined);
    setPage(1);
  };

  const openDispositionModal = (action: DispositionAction, alert: AlertType) => {
    setDisposition({ action, alert });
    setDispositionNote('');
  };

  const submitDisposition = () => {
    if (!disposition) return;
    const payload = {
      id: disposition.alert.id,
      note: dispositionNote.trim() || undefined,
    };
    if (disposition.action === 'acknowledge') {
      acknowledgeMutation.mutate(payload);
      return;
    }
    resolveMutation.mutate(payload);
  };

  const dispositionPending = acknowledgeMutation.isPending || resolveMutation.isPending;

  const alertColumns: ColumnsType<AlertType> = [
    {
      title: '等级',
      dataIndex: 'severity',
      width: 96,
      render: (value, record) => {
        if (!value) return <Tag color={getAlertColor(record)}>兼容</Tag>;
        const severity = String(value);
        return <Tag color={severityColors[severity] || 'default'}>{severityLabels[severity] || severity}</Tag>;
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (value) => {
        if (!value) return <Tag>只读</Tag>;
        const status = String(value);
        return <Tag color={statusColors[status] || 'default'}>{statusLabels[status] || status}</Tag>;
      },
    },
    {
      title: '类型',
      key: 'alert_type',
      width: 160,
      render: (_, record) => <Tag color={getAlertColor(record)}>{getAlertLabel(record)}</Tag>,
    },
    {
      title: '告警内容',
      key: 'message',
      render: (_, record) => (
        <Text style={{ maxWidth: 360 }} ellipsis={{ tooltip: getAlertMessage(record) }}>
          {getAlertMessage(record)}
        </Text>
      ),
    },
    {
      title: '资源',
      key: 'resource',
      width: 220,
      render: (_, record) => (
        <Text code style={{ whiteSpace: 'normal' }}>
          {getResourceText(record)}
        </Text>
      ),
    },
    {
      title: '次数',
      dataIndex: 'occurrence_count',
      width: 80,
      align: 'right',
      render: (value) => value ?? 1,
    },
    {
      title: '通知',
      dataIndex: 'notification_status',
      width: 110,
      render: (value) => {
        if (!value) return '-';
        const status = String(value);
        return (
          <Badge
            status={notificationBadgeStatus[status] || 'default'}
            text={notificationLabels[status] || status}
          />
        );
      },
    },
    {
      title: '最近发生',
      dataIndex: 'last_seen_at',
      width: 170,
      render: (value, record) => formatTime(value || record.created_at),
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 190,
      render: (_, record) => (
        <Space size="small">
          <Button size="small" icon={<EyeOutlined />} onClick={() => setSelectedAlert(record)}>
            详情
          </Button>
          {isPersistentAlert(record) && record.status === 'open' && (
            <Button size="small" icon={<BellOutlined />} onClick={() => openDispositionModal('acknowledge', record)}>
              确认
            </Button>
          )}
          {isPersistentAlert(record) && record.status !== 'resolved' && (
            <Button
              size="small"
              type="primary"
              icon={<CheckCircleOutlined />}
              onClick={() => openDispositionModal('resolve', record)}
            >
              解决
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Title level={4} style={{ marginBottom: 4 }}>监控中心</Title>
          <Text type="secondary">安全态势、审计统计与持久化告警处置</Text>
        </div>
        <Button icon={<ReloadOutlined />} loading={alertsFetching} onClick={() => { void refetchAlerts(); }}>
          刷新告警
        </Button>
      </div>

      {statsIsError && (
        <QueryErrorAlert error={statsError} message="监控统计加载失败" onRetry={() => { void refetchStats(); }} />
      )}
      {alertsIsError && (
        <QueryErrorAlert error={alertsError} message="安全告警加载失败" onRetry={() => { void refetchAlerts(); }} />
      )}

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
        <div className="cds-page-toolbar" style={{ marginBottom: 12 }}>
          <div>
            <Title level={5} style={{ margin: 0 }}>安全告警</Title>
            <Text type="secondary">持久化告警支持筛选、确认、解决与通知送达状态追踪</Text>
          </div>
          <Space wrap>
            <Select
              placeholder="状态"
              allowClear
              value={statusFilter}
              style={{ width: 130 }}
              onChange={(value) => { setStatusFilter(value); setPage(1); }}
              options={[
                { label: '待处理', value: 'open' },
                { label: '已确认', value: 'acknowledged' },
                { label: '已解决', value: 'resolved' },
              ]}
            />
            <Select
              placeholder="等级"
              allowClear
              value={severityFilter}
              style={{ width: 120 }}
              onChange={(value) => { setSeverityFilter(value); setPage(1); }}
              options={[
                { label: '严重', value: 'critical' },
                { label: '高', value: 'high' },
                { label: '中', value: 'medium' },
                { label: '低', value: 'low' },
              ]}
            />
            <Select
              placeholder="告警类型"
              allowClear
              value={typeFilter}
              style={{ width: 180 }}
              onChange={(value) => { setTypeFilter(value); setPage(1); }}
              options={alertTypeOptions}
            />
            <Button onClick={resetFilters}>重置</Button>
          </Space>
        </div>
        <Table
          dataSource={alertsData?.items || []}
          rowKey="id"
          loading={alertsLoading}
          columns={alertColumns}
          scroll={{ x: 1180 }}
          pagination={{
            current: page,
            total: alertsData?.total || 0,
            pageSize: 20,
            showTotal: (total) => `共 ${total} 条`,
            onChange: setPage,
          }}
          locale={tableEmpty('暂无安全告警')}
        />
      </Card>

      <Drawer
        title="告警详情"
        open={Boolean(selectedAlert)}
        onClose={() => setSelectedAlert(null)}
        width={680}
      >
        {selectedAlert && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="告警ID">{selectedAlert.id}</Descriptions.Item>
              <Descriptions.Item label="类型">
                <Tag color={getAlertColor(selectedAlert)}>{getAlertLabel(selectedAlert)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="等级">
                {selectedAlert.severity ? (
                  <Tag color={severityColors[String(selectedAlert.severity)] || 'default'}>
                    {severityLabels[String(selectedAlert.severity)] || selectedAlert.severity}
                  </Tag>
                ) : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {selectedAlert.status ? (
                  <Tag color={statusColors[String(selectedAlert.status)] || 'default'}>
                    {statusLabels[String(selectedAlert.status)] || selectedAlert.status}
                  </Tag>
                ) : '兼容审计告警'}
              </Descriptions.Item>
              <Descriptions.Item label="告警内容">{getAlertMessage(selectedAlert)}</Descriptions.Item>
              <Descriptions.Item label="用户ID">{selectedAlert.user_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="会话ID">{selectedAlert.session_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="资源">{getResourceText(selectedAlert)}</Descriptions.Item>
              <Descriptions.Item label="发生次数">{selectedAlert.occurrence_count ?? 1}</Descriptions.Item>
              <Descriptions.Item label="首次发生">{formatTime(selectedAlert.first_seen_at || selectedAlert.created_at)}</Descriptions.Item>
              <Descriptions.Item label="最近发生">{formatTime(selectedAlert.last_seen_at || selectedAlert.created_at)}</Descriptions.Item>
              <Descriptions.Item label="确认人">{selectedAlert.acknowledged_by || '-'}</Descriptions.Item>
              <Descriptions.Item label="确认时间">{formatTime(selectedAlert.acknowledged_at)}</Descriptions.Item>
              <Descriptions.Item label="解决人">{selectedAlert.resolved_by || '-'}</Descriptions.Item>
              <Descriptions.Item label="解决时间">{formatTime(selectedAlert.resolved_at)}</Descriptions.Item>
              <Descriptions.Item label="处置备注">{selectedAlert.resolution_note || '-'}</Descriptions.Item>
              <Descriptions.Item label="通知状态">
                {selectedAlert.notification_status ? (
                  <Badge
                    status={notificationBadgeStatus[String(selectedAlert.notification_status)] || 'default'}
                    text={notificationLabels[String(selectedAlert.notification_status)] || selectedAlert.notification_status}
                  />
                ) : '-'}
              </Descriptions.Item>
            </Descriptions>
            <div>
              <Title level={5}>元数据</Title>
              <pre className="cds-json-block">
                {JSON.stringify(selectedAlert.metadata || selectedAlert.detail || {}, null, 2)}
              </pre>
            </div>
            <div>
              <Title level={5}>通知结果</Title>
              <pre className="cds-json-block">
                {JSON.stringify(selectedAlert.notification_results || [], null, 2)}
              </pre>
            </div>
          </Space>
        )}
      </Drawer>

      <Modal
        title={disposition?.action === 'acknowledge' ? '确认告警' : '解决告警'}
        open={Boolean(disposition)}
        okText={disposition?.action === 'acknowledge' ? '确认' : '解决'}
        cancelText="取消"
        confirmLoading={dispositionPending}
        onOk={submitDisposition}
        onCancel={closeDispositionModal}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {disposition?.alert && (
            <div>
              <Text strong>{getAlertLabel(disposition.alert)}</Text>
              <br />
              <Text type="secondary">{getAlertMessage(disposition.alert)}</Text>
            </div>
          )}
          <Input.TextArea
            value={dispositionNote}
            onChange={(event) => setDispositionNote(event.target.value)}
            placeholder="填写处置备注，可留空"
            rows={4}
            maxLength={500}
            showCount
          />
        </Space>
      </Modal>
    </div>
  );
}
