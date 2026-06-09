import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import { CodeOutlined, FileProtectOutlined, LinkOutlined, StopOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { sandboxApi, type SessionExecuteResult } from '../../services/sandboxApi';
import { useAuthStore } from '../../stores/authStore';
import { UserRole } from '../../types/enums';
import { hasAnyRole } from '../../utils/roles';

const { Title, Paragraph, Text } = Typography;
const { TextArea } = Input;

interface NetworkPolicyFormValues {
  mode: 'deny_all' | 'allowlist';
  allowed_ips?: string;
  allowed_domains?: string;
  allowed_ports?: string;
  dns_proxy_enabled?: boolean;
  max_connections_per_second?: number;
  max_bandwidth_bytes_per_second?: number;
  active?: boolean;
}

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

const statusLabels: Record<string, string> = {
  pending: '等待中',
  key_distributing: '密钥分发中',
  ready: '已就绪',
  provisioning: '创建中',
  running: '运行中',
  suspended: '已暂停',
  completed: '已完成',
  failed: '失败',
  terminated: '已终止',
  revoked: '已撤销',
};

const modeLabels: Record<string, string> = {
  structured_query: '结构化查询',
  structured_modeling: '结构化建模',
  structured_app: '应用调用',
  llm_training: 'LLM 训练',
  product_dev: '产品开发',
  joint_federated: '联合计算',
};

const severityColors: Record<string, string> = {
  critical: 'red',
  high: 'orange',
  medium: 'gold',
  low: 'blue',
};

function shortId(id?: string | null) {
  return id ? `${id.slice(0, 8)}...${id.slice(-6)}` : '-';
}

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '-';
}

function renderJson(value: unknown) {
  if (!value || (typeof value === 'object' && Object.keys(value as Record<string, unknown>).length === 0)) return '-';
  return <pre className="cds-json-block">{JSON.stringify(value, null, 2)}</pre>;
}

function splitList(value?: string) {
  return (value || '')
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function splitPorts(value?: string) {
  return splitList(value).map((item) => Number(item)).filter((item) => Number.isInteger(item));
}

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [networkForm] = Form.useForm<NetworkPolicyFormValues>();
  const [code, setCode] = useState("print('hello confidential sandbox')");
  const [language, setLanguage] = useState('python');
  const [executeResult, setExecuteResult] = useState<SessionExecuteResult | null>(null);

  const { data: session, isLoading } = useQuery({
    queryKey: ['sandbox-session', id],
    queryFn: () => sandboxApi.get(id!),
    enabled: !!id,
  });

  const canOperate = !!session && (
    user?.id === session.user_id || hasAnyRole(user?.role, [UserRole.OPERATOR, UserRole.ADMIN])
  );
  const canExecute = !!session && user?.id === session.user_id && session.status === 'running';
  const canUpdateNetwork = !!session && canOperate && ['running', 'ready'].includes(session.status);

  const { data: networkPolicy } = useQuery({
    queryKey: ['sandbox-session-network-policy', id],
    queryFn: () => sandboxApi.getNetworkPolicy(id!),
    enabled: !!id && !!session,
  });

  useEffect(() => {
    if (!networkPolicy) return;
    networkForm.setFieldsValue({
      mode: networkPolicy.mode,
      allowed_ips: (networkPolicy.allowed_ips || []).join('\n'),
      allowed_domains: (networkPolicy.allowed_domains || []).join('\n'),
      allowed_ports: (networkPolicy.allowed_ports || []).join(','),
      dns_proxy_enabled: networkPolicy.dns_proxy_enabled,
      max_connections_per_second: networkPolicy.max_connections_per_second,
      max_bandwidth_bytes_per_second: networkPolicy.max_bandwidth_bytes_per_second,
      active: networkPolicy.active,
    });
  }, [networkForm, networkPolicy]);

  const networkMode = Form.useWatch('mode', networkForm);

  const terminateMutation = useMutation({
    mutationFn: () => sandboxApi.terminate(id!),
    onSuccess: () => {
      message.success('已终止');
      queryClient.invalidateQueries({ queryKey: ['sandbox-session', id] });
      queryClient.invalidateQueries({ queryKey: ['sandbox-sessions'] });
    },
    onError: () => message.error('终止失败'),
  });

  const executeMutation = useMutation({
    mutationFn: () => sandboxApi.execute(id!, code, language),
    onSuccess: (result) => {
      setExecuteResult(result);
      if (result.output_blocked) message.warning('输出已被安全审查阻断');
      else message.success('执行完成');
    },
    onError: (error: any) => {
      const detail = error?.detail;
      const text = typeof detail === 'string' ? detail : detail?.error || '执行失败';
      message.error(text);
    },
  });

  const updateNetworkMutation = useMutation({
    mutationFn: (values: NetworkPolicyFormValues) => sandboxApi.updateNetworkPolicy(id!, {
      mode: values.mode,
      allowed_ips: splitList(values.allowed_ips),
      allowed_domains: splitList(values.allowed_domains),
      allowed_ports: splitPorts(values.allowed_ports),
      dns_proxy_enabled: values.dns_proxy_enabled,
      max_connections_per_second: values.max_connections_per_second,
      max_bandwidth_bytes_per_second: values.max_bandwidth_bytes_per_second,
      active: values.active,
    }),
    onSuccess: (result) => {
      message.success('网络策略已更新');
      queryClient.setQueryData(['sandbox-session-network-policy', id], result.network_policy);
    },
    onError: (error: any) => {
      const detail = error?.detail;
      const text = Array.isArray(detail)
        ? detail.map((item: any) => item.msg || item.message || JSON.stringify(item)).join('; ')
        : detail || '网络策略更新失败';
      message.error(text);
    },
  });

  if (isLoading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  if (!session) return <Empty description="会话不存在或无权访问" />;

  const report = executeResult?.security_report;

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Space wrap align="center">
            <Title level={4} style={{ margin: 0 }}>沙箱会话</Title>
            <Tag color={statusColors[session.status] ?? 'default'}>{statusLabels[session.status] ?? session.status}</Tag>
            <Tag>{session.sandbox_level}</Tag>
            <Tag>{modeLabels[session.sandbox_mode] || session.sandbox_mode}</Tag>
          </Space>
          <Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
            <Text code>{session.id}</Text>
          </Paragraph>
        </div>
        <Space wrap>
          <Button icon={<LinkOutlined />} onClick={() => navigate(`/data-products/${session.data_product_id}`)}>
            数据产品
          </Button>
          {session.contract_id && (
            <Button icon={<FileProtectOutlined />} onClick={() => navigate(`/contracts/${session.contract_id}`)}>
              合约
            </Button>
          )}
          {canOperate && ['running', 'ready', 'provisioning'].includes(session.status) && (
            <Button danger icon={<StopOutlined />} loading={terminateMutation.isPending} onClick={() => terminateMutation.mutate()}>
              终止
            </Button>
          )}
          <Button onClick={() => navigate('/sandbox-sessions')}>返回列表</Button>
        </Space>
      </div>

      {session.error_message && (
        <Alert type="error" showIcon message="会话异常" description={session.error_message} style={{ marginBottom: 16 }} />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card title="会话信息">
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="会话 ID"><Text code>{session.id}</Text></Descriptions.Item>
              <Descriptions.Item label="用户 ID"><Text code>{shortId(session.user_id)}</Text></Descriptions.Item>
              <Descriptions.Item label="数据产品"><Text code>{shortId(session.data_product_id)}</Text></Descriptions.Item>
              <Descriptions.Item label="关联合约">{session.contract_id ? <Text code>{shortId(session.contract_id)}</Text> : '-'}</Descriptions.Item>
              <Descriptions.Item label="容器 ID">{session.container_id ? <Text code>{session.container_id}</Text> : '-'}</Descriptions.Item>
              <Descriptions.Item label="会话密钥">{session.session_key_id ? <Text code>{shortId(session.session_key_id)}</Text> : '-'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatTime(session.created_at)}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{formatTime(session.updated_at)}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{formatTime(session.started_at)}</Descriptions.Item>
              <Descriptions.Item label="结束时间">{formatTime(session.ended_at)}</Descriptions.Item>
              <Descriptions.Item label="资源限制" span={2}>{renderJson(session.resource_limits)}</Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Row gutter={[16, 16]}>
            <Col span={24}>
              <Card>
                <Statistic title="超时时间" value={session.timeout_seconds} suffix="秒" />
              </Card>
            </Col>
            <Col span={24}>
              <Card>
                <Statistic title="输出审查" value={report ? (report.blocked ? '阻断' : '已审查') : '待执行'} />
              </Card>
            </Col>
          </Row>
        </Col>
      </Row>

      <Card title="网络策略" style={{ marginTop: 16 }}>
        <Form
          form={networkForm}
          layout="vertical"
          onFinish={updateNetworkMutation.mutate}
          initialValues={{
            mode: 'deny_all',
            allowed_ports: '443,80',
            dns_proxy_enabled: true,
            max_connections_per_second: 10,
            max_bandwidth_bytes_per_second: 0,
            active: true,
          }}
        >
          <Row gutter={16}>
            <Col xs={24} md={6}>
              <Form.Item name="mode" label="模式">
                <Select
                  disabled={!canUpdateNetwork}
                  options={[
                    { label: '默认拒绝', value: 'deny_all' },
                    { label: '白名单放行', value: 'allowlist' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item name="active" label="启用" valuePropName="checked">
                <Switch disabled={!canUpdateNetwork} />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item name="dns_proxy_enabled" label="DNS 代理" valuePropName="checked">
                <Switch disabled={!canUpdateNetwork || networkMode !== 'allowlist'} />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item name="max_connections_per_second" label="连接速率/秒">
                <InputNumber min={0} max={10000} style={{ width: '100%' }} disabled={!canUpdateNetwork} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="allowed_ips" label="允许 CIDR">
                <TextArea rows={3} placeholder="10.0.0.0/8" disabled={!canUpdateNetwork || networkMode !== 'allowlist'} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="allowed_domains" label="允许域名">
                <TextArea rows={3} placeholder="api.example.com&#10;*.internal.example.com" disabled={!canUpdateNetwork || networkMode !== 'allowlist'} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="allowed_ports" label="允许端口">
                <TextArea rows={3} placeholder="443,80" disabled={!canUpdateNetwork || networkMode !== 'allowlist'} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16} align="bottom">
            <Col xs={24} md={8}>
              <Form.Item name="max_bandwidth_bytes_per_second" label="带宽上限（字节/秒，0 为不限）">
                <InputNumber min={0} style={{ width: '100%' }} disabled={!canUpdateNetwork} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Button
                type="primary"
                htmlType="submit"
                loading={updateNetworkMutation.isPending}
                disabled={!canUpdateNetwork}
              >
                更新网络策略
              </Button>
            </Col>
          </Row>
        </Form>
      </Card>

      <Card title="会话执行" style={{ marginTop: 16 }}>
        {canExecute ? (
          <>
            <Space style={{ marginBottom: 12 }}>
              <Select
                value={language}
                onChange={setLanguage}
                style={{ width: 140 }}
                options={[
                  { label: 'Python', value: 'python' },
                  { label: 'SQL', value: 'sql' },
                  { label: 'Shell', value: 'bash' },
                ]}
              />
              <Button type="primary" icon={<CodeOutlined />} loading={executeMutation.isPending} onClick={() => executeMutation.mutate()}>
                执行
              </Button>
            </Space>
            <TextArea value={code} onChange={(event) => setCode(event.target.value)} rows={8} />
          </>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="只有会话所有者可在 running 状态执行代码" />
        )}
      </Card>

      {executeResult && (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} lg={14}>
            <Card
              title="执行输出"
              extra={<Tag color={executeResult.exit_code === 0 ? 'green' : 'red'}>exit {executeResult.exit_code}</Tag>}
            >
              {executeResult.output_blocked ? (
                <Alert type="error" showIcon message="输出已阻断" description="输出命中 critical 安全规则，原始内容未释放。" />
              ) : executeResult.output ? (
                <pre className="cds-json-block">{executeResult.output}</pre>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无输出" />
              )}
            </Card>
          </Col>
          <Col xs={24} lg={10}>
            <Card title="输出安全报告">
              {report ? (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Space wrap>
                    <Tag color={report.blocked ? 'red' : report.passed ? 'green' : 'orange'}>
                      {report.blocked ? '已阻断' : report.passed ? '通过' : '已脱敏'}
                    </Tag>
                    <Tag>发现 {report.findings_count ?? report.findings?.length ?? 0} 项</Tag>
                    {report.dp_applied && <Tag color="blue">DP</Tag>}
                  </Space>
                  {report.watermark && <Text code>watermark: {report.watermark}</Text>}
                  {report.signature && <Text code>signature: {report.signature.slice(0, 24)}...</Text>}
                  {(report.findings || []).map((finding, index) => (
                    <Alert
                      key={`${finding.type}-${index}`}
                      type={finding.severity === 'critical' ? 'error' : 'warning'}
                      showIcon
                      message={<Space><Tag color={severityColors[finding.severity] ?? 'default'}>{finding.severity}</Tag>{finding.type}</Space>}
                      description={finding.message}
                    />
                  ))}
                </Space>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无报告" />
              )}
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
}
