import { Alert, Card, Collapse, Col, Descriptions, Divider, List, Progress, Row, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useQueryClient } from '@tanstack/react-query';
import { quotaApi, quotaNumber, useQuotaUsage } from '../../services/quotaApi';
import type { QuotaEndpointItem, QuotaSessionItem, QuotaSource } from '../../services/quotaApi';
import { QueryErrorAlert, CenteredLoading, tableEmpty } from '../../components/Feedback/QueryFeedback';
import { isLegacySandboxLevel, sandboxLevelLabel } from '../../types/enums';
import type { ColumnsType } from 'antd/es/table';

const { Title, Paragraph, Text } = Typography;

const statusColors: Record<string, string> = {
  provisioning: 'blue',
  ready: 'cyan',
  running: 'green',
  suspended: 'orange',
  completed: 'purple',
  failed: 'red',
  terminated: 'default',
  revoked: 'red',
  enabled: 'green',
  disabled: 'default',
};

function SourceTag({ source }: { source: QuotaSource }) {
  const trustworthy = source.backend === 'redis' || source.backend === 'database';
  return (
    <Tooltip title={source.note}>
      <Tag color={trustworthy ? 'green' : 'orange'}>{source.label}</Tag>
    </Tooltip>
  );
}

function UsageBar({ used, total }: { used: number | null | undefined; total: number }) {
  if (used === null || used === undefined) {
    return <Text type="secondary">未采集</Text>;
  }
  const percent = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return <Progress percent={percent} size="small" format={() => `${used.toLocaleString('zh-CN')} / ${total.toLocaleString('zh-CN')}`} />;
}

const endpointColumns: ColumnsType<QuotaEndpointItem> = [
  { title: '端点', dataIndex: 'name', render: (name, row) => (
    <Space direction="vertical" size={0}>
      <Text>{name}</Text>
      <Text type="secondary" code>{row.slug}</Text>
    </Space>
  ) },
  { title: '合约', dataIndex: 'contract_no', render: (no, row) => (
    <Space direction="vertical" size={0}>
      <Text>{no}</Text>
      <Text type="secondary">{row.role === 'provider' ? '我提供' : '我消费'}</Text>
    </Space>
  ) },
  { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={statusColors[s] ?? 'default'}>{s}</Tag> },
  {
    title: '已用 / 总额度',
    dataIndex: 'calls_used',
    width: 190,
    render: (_, row) => (
      <Space direction="vertical" size={0} style={{ width: '100%' }}>
        <Text>{quotaNumber(row.calls_used)} / {quotaNumber(row.quota_total)}</Text>
        {row.quota_total ? (
          <Progress
            percent={Math.min(100, Math.round((row.calls_used / row.quota_total) * 100))}
            size="small"
            status={row.calls_remaining === 0 ? 'exception' : 'active'}
          />
        ) : (
          <Text type="secondary">未设总额度</Text>
        )}
      </Space>
    ),
  },
  {
    title: '剩余',
    dataIndex: 'calls_remaining',
    width: 110,
    render: (v: number | null) => (v === null ? <Text type="secondary">未采集</Text> : <Text>{quotaNumber(v)}</Text>),
  },
  { title: '限流', dataIndex: 'rate_limit_per_minute', width: 110, render: (v: number) => `${v} 次/分` },
  { title: '最近调用', dataIndex: 'last_called_at', width: 170, render: (v: string | null) => v ?? <Text type="secondary">从未</Text> },
];

function SessionQuotaTable({ session }: { session: QuotaSessionItem }) {
  const columns: ColumnsType<QuotaSessionItem['quotas'][number]> = [
    { title: '计量项', dataIndex: 'label' },
    { title: '单位', dataIndex: 'unit', width: 120, render: (u: string) => <Text type="secondary">{u}</Text> },
    { title: '已用', dataIndex: 'usage', width: 130, render: (v: number | null) => quotaNumber(v) },
    {
      title: '限额',
      dataIndex: 'limit',
      width: 130,
      render: (v: number | null) => (v === null ? <Text type="secondary">未设限</Text> : quotaNumber(v)),
    },
  ];
  return (
    <Table
      size="small"
      rowKey="type"
      columns={columns}
      dataSource={session.quotas}
      pagination={false}
    />
  );
}

export default function FlowQuota() {
  const { data, isPending, isError, error, refetch, dataUpdatedAt } = useQuotaUsage();
  const queryClient = useQueryClient();

  if (isPending) return <CenteredLoading tip="加载配额用量" />;
  if (isError || !data) {
    return <QueryErrorAlert error={error} message="配额用量加载失败" onRetry={() => { void refetch(); }} />;
  }

  const { sandbox_sessions: sessions, tenant_resources: tenant, openapi_endpoints: openapi, sources, not_collected: notCollected } = data;
  const countersAreLocal = sources.sandbox_counters.backend !== 'redis';
  const tenantUsed = tenant.used;

  return (
    <div>
      <Space style={{ marginBottom: 8, width: '100%', justifyContent: 'space-between' }}>
        <Title level={4} style={{ margin: 0 }}>配额用量</Title>
        <Space>
          <Text type="secondary">
            更新于 {new Date(data.generated_at).toLocaleString('zh-CN')} · 统计口径：{data.scope.username}
          </Text>
          <a onClick={() => { void queryClient.invalidateQueries({ queryKey: ['quota-usage'] }); }}>
            <ReloadOutlined /> 刷新
          </a>
        </Space>
      </Space>
      <Paragraph type="secondary">{data.scope.note}</Paragraph>

      {countersAreLocal && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="会话计量当前来自进程内兜底，不是平台权威用量"
          description={sources.sandbox_counters.note}
        />
      )}

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic
              title="并发会话"
              value={sessions.active}
              suffix={`/ ${sessions.active_limit}`}
              valueStyle={{ color: sessions.active >= sessions.active_limit ? '#cf1322' : undefined }}
            />
            <Text type="secondary">还可用 {sessions.active_remaining} 个</Text>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic title="历史会话总数" value={sessions.total} />
            <Text type="secondary">{Object.entries(sessions.by_status).map(([k, v]) => `${k} ${v}`).join(' · ') || '暂无会话'}</Text>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic title="OpenAPI 端点" value={openapi.count} suffix={`启用 ${openapi.status_enabled}`} />
            <Text type="secondary">来源 <SourceTag source={sources.openapi_calls} /></Text>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic title="OpenAPI 已调用" value={openapi.totals.calls_used} />
            <Text type="secondary">
              剩余总额度 {quotaNumber(openapi.totals.calls_remaining)}
            </Text>
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        style={{ marginBottom: 16 }}
        title="租户资源"
        extra={<SourceTag source={sources.tenant_resources} />}
      >
        {!tenantUsed && <Alert type="info" showIcon style={{ marginBottom: 12 }} message="尚无会话占用过资源：以下占用为未采集，而非 0。" />}
        <Descriptions size="small" column={1} colon={false} labelStyle={{ width: 96 }}>
          <Descriptions.Item label="CPU 核"><UsageBar used={tenantUsed?.used_cpu_cores} total={tenant.limits.cpu_cores} /></Descriptions.Item>
          <Descriptions.Item label="内存 MB"><UsageBar used={tenantUsed?.used_memory_mb} total={tenant.limits.memory_mb} /></Descriptions.Item>
          <Descriptions.Item label="磁盘 MB"><UsageBar used={tenantUsed?.used_disk_mb} total={tenant.limits.disk_mb} /></Descriptions.Item>
        </Descriptions>
      </Card>

      <Card size="small" title="OpenAPI 结果通道额度" style={{ marginBottom: 16 }}>
        <Table<QuotaEndpointItem>
          size="small"
          rowKey="id"
          columns={endpointColumns}
          dataSource={openapi.items}
          pagination={false}
          locale={tableEmpty('尚无 OpenAPI 端点：在合约的交付方式里发布一个')}
        />
      </Card>

      <Card size="small" title="活跃会话计量" style={{ marginBottom: 16 }}>
        {sessions.per_session.length === 0 ? (
          <Text type="secondary">当前没有活跃会话。</Text>
        ) : (
          <Collapse
            size="small"
            items={sessions.per_session.map((session) => ({
              key: session.session_id,
              label: (
                <Space>
                  <Text code>{session.session_id.slice(0, 8)}</Text>
                  <Tag color={statusColors[session.status] ?? 'default'}>{session.status}</Tag>
                  <Tag color={isLegacySandboxLevel(session.sandbox_level) ? 'default' : 'geekblue'}>
                    {sandboxLevelLabel(session.sandbox_level)}
                  </Tag>
                  <Text type="secondary">创建于 {session.created_at ? new Date(session.created_at).toLocaleString('zh-CN') : '未知'}</Text>
                </Space>
              ),
              children: <SessionQuotaTable session={session} />,
            }))}
          />
        )}
        <Divider dashed style={{ margin: '12px 0' }} />
        <Text type="secondary">{sessions.note}</Text>
      </Card>

      <Card size="small" title="未采集项">
        <List
          size="small"
          dataSource={notCollected}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta title={item.label} description={item.reason} />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}
