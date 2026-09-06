import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { UploadProps } from 'antd';
import {
  CodeOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  LinkOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
  StopOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  sandboxApi,
  type SessionExecuteResult,
  type ExecResult,
  type SessionLogEntry,
  type SessionFile,
  type SessionSnapshot,
} from '../../services/sandboxApi';
import { useAuthStore } from '../../stores/authStore';
import { UserRole } from '../../types/enums';
import { hasAnyRole } from '../../utils/roles';
import { getErrorMessage } from '../../components/Feedback/QueryFeedback';

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
  expired: 'default',
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
  expired: '已过期',
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

const proofLevelLabels: Record<string, string> = {
  hardware_tee: '硬件 TEE',
  software_confidential: '软件密态',
  runtime_isolation: '运行时隔离',
};

const proofLevelColors: Record<string, string> = {
  hardware_tee: 'green',
  software_confidential: 'orange',
  runtime_isolation: 'blue',
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

function formatBytes(bytes?: number) {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
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
  const [proofOpen, setProofOpen] = useState(false);

  // --- Exec console state ---
  const [execCommand, setExecCommand] = useState('ls -la /workspace');
  const [execTimeout, setExecTimeout] = useState<number | null>(60);
  const [execResult, setExecResult] = useState<ExecResult | null>(null);

  // --- Logs viewer state ---
  const [logActionFilter, setLogActionFilter] = useState<string>('');
  const [logSince, setLogSince] = useState<string | undefined>();
  const [allLogs, setAllLogs] = useState<SessionLogEntry[]>([]);

  // --- Files ---
  const [downloadRedactedFile, setDownloadRedactedFile] = useState<string | null>(null);

  // --- Snapshots ---
  const [createSnapshotOpen, setCreateSnapshotOpen] = useState(false);
  const [snapshotDescription, setSnapshotDescription] = useState('');

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

  const canExec = !!session && user?.id === session.user_id && ['running', 'ready'].includes(session.status);
  const canPause = !!session && canOperate && ['ready', 'running'].includes(session.status);
  const canResume = !!session && canOperate && session.status === 'suspended';
  const canRefresh = !!session && canOperate && ['pending', 'provisioning', 'ready', 'running', 'suspended'].includes(session.status);
  const canManageFiles = !!session && canOperate && ['ready', 'running', 'suspended'].includes(session.status);
  const canManageSnapshots = !!session && canOperate && ['ready', 'running', 'suspended'].includes(session.status);

  const { data: networkPolicy } = useQuery({
    queryKey: ['sandbox-session-network-policy', id],
    queryFn: () => sandboxApi.getNetworkPolicy(id!),
    enabled: !!id && !!session,
  });

  // --- Usage ---
  const {
    data: usage,
    isFetching: usageFetching,
    refetch: refetchUsage,
  } = useQuery({
    queryKey: ['sandbox-session-usage', id],
    queryFn: () => sandboxApi.getUsage(id!),
    enabled: !!id && !!session,
  });

  // --- Logs ---
  const {
    data: logsData,
    isFetching: logsFetching,
    refetch: refetchLogs,
    isError: logsIsError,
    error: logsError,
  } = useQuery<import('../../services/sandboxApi').SessionLogsResponse>({
    queryKey: ['sandbox-session-logs', id, logActionFilter, logSince],
    queryFn: () => sandboxApi.getLogs(id!, {
      limit: 50,
      since: logSince,
      action: logActionFilter || undefined,
    }),
    enabled: !!id && !!session,
  });

  // Accumulate logs for "load more" pagination
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!logsData) return;
    if (logSince) {
      setAllLogs((prev) => [...prev, ...logsData.logs]);
    } else {
      setAllLogs(logsData.logs);
    }
  }, [logsData, logSince]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const loadMoreLogs = () => {
    if (logsData?.latest_created_at) {
      setLogSince(logsData.latest_created_at);
    }
  };

  const refreshLogs = () => {
    setLogSince(undefined);
    setAllLogs([]);
    void refetchLogs();
  };

  // --- Files ---
  const {
    data: filesData,
    isFetching: filesFetching,
    refetch: refetchFiles,
  } = useQuery({
    queryKey: ['sandbox-session-files', id],
    queryFn: () => sandboxApi.listFiles(id!),
    enabled: !!id && !!session,
  });

  // --- Snapshots ---
  const {
    data: snapshotsData,
    isFetching: snapshotsFetching,
    refetch: refetchSnapshots,
  } = useQuery({
    queryKey: ['sandbox-session-snapshots', id],
    queryFn: () => sandboxApi.listSnapshots(id!),
    enabled: !!id && !!session,
  });

  const {
    data: proofBundle,
    isFetching: proofFetching,
    isError: proofIsError,
    error: proofError,
    refetch: refetchProofBundle,
  } = useQuery({
    queryKey: ['sandbox-session-proof-bundle', id],
    queryFn: () => sandboxApi.getProofBundle(id!),
    enabled: false,
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

  const openProofBundle = () => {
    setProofOpen(true);
    void refetchProofBundle();
  };

  const downloadProofBundle = () => {
    if (!proofBundle) return;
    const blob = new Blob([JSON.stringify(proofBundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `cds-session-proof-${session?.id || id}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

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
    onError: (error: unknown) => {
      message.error(getErrorMessage(error, '执行失败'));
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
    onError: (error: unknown) => {
      message.error(getErrorMessage(error, '网络策略更新失败'));
    },
  });

  const invalidateSessionQueries = () => {
    queryClient.invalidateQueries({ queryKey: ['sandbox-session', id] });
    queryClient.invalidateQueries({ queryKey: ['sandbox-sessions'] });
  };

  // --- Pause / Resume / Refresh ---
  const pauseMutation = useMutation({
    mutationFn: () => sandboxApi.pauseSession(id!),
    onSuccess: () => {
      message.success('会话已暂停');
      invalidateSessionQueries();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '暂停失败')),
  });

  const resumeMutation = useMutation({
    mutationFn: () => sandboxApi.resumeSession(id!),
    onSuccess: () => {
      message.success('会话已恢复');
      invalidateSessionQueries();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '恢复失败')),
  });

  const refreshMutation = useMutation({
    mutationFn: () => sandboxApi.refreshSession(id!),
    onSuccess: () => {
      message.success('会话超时已延长');
      invalidateSessionQueries();
      void refetchUsage();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '续期失败')),
  });

  // --- Exec (shell) ---
  const execMutation = useMutation({
    mutationFn: () => sandboxApi.exec(id!, {
      command: execCommand,
      timeout_seconds: execTimeout ?? undefined,
    }),
    onSuccess: (result) => {
      setExecResult(result);
      if (result.output_blocked) {
        message.warning('输出已被安全审查阻断');
      }
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '执行失败')),
  });

  // --- File upload ---
  const uploadFileMutation = useMutation({
    mutationFn: (file: File) => sandboxApi.uploadFile(id!, file),
    onSuccess: () => {
      message.success('文件上传成功');
      void refetchFiles();
      void refetchUsage();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '文件上传失败')),
  });

  const handleUpload: UploadProps['beforeUpload'] = (file) => {
    uploadFileMutation.mutate(file as File);
    return false;
  };

  // --- File download ---
  const downloadFileMutation = useMutation({
    mutationFn: (filename: string) => sandboxApi.downloadFile(id!, filename),
    onSuccess: ({ blob, redacted }, filename) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      if (redacted) {
        setDownloadRedactedFile(filename);
        message.info('该文件已通过输出审查，内容已脱敏');
      } else {
        setDownloadRedactedFile(null);
        message.success('下载成功');
      }
    },
    onError: (error: unknown, filename) => {
      const err = error as { status?: number; detail?: { findings_count?: number; error?: string } };
      if (err.status === 409) {
        const count = err.detail?.findings_count ?? 0;
        message.error(`下载失败：输出审查阻断（发现 ${count} 项风险）`);
      } else {
        message.error(getErrorMessage(error, `${filename} 下载失败`));
      }
    },
  });

  // --- File delete ---
  const deleteFileMutation = useMutation({
    mutationFn: (filename: string) => sandboxApi.deleteFile(id!, filename),
    onSuccess: () => {
      message.success('文件已删除');
      void refetchFiles();
      void refetchUsage();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '删除失败')),
  });

  // --- Snapshot create ---
  const createSnapshotMutation = useMutation({
    mutationFn: () => sandboxApi.createSnapshot(id!, { description: snapshotDescription || undefined }),
    onSuccess: () => {
      message.success('快照已创建');
      setCreateSnapshotOpen(false);
      setSnapshotDescription('');
      void refetchSnapshots();
      void refetchUsage();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '创建快照失败')),
  });

  // --- Snapshot rollback ---
  const rollbackSnapshotMutation = useMutation({
    mutationFn: (snapshotId: string) => sandboxApi.rollbackSnapshot(id!, snapshotId),
    onSuccess: () => {
      message.success('已回滚到快照');
      void refetchSnapshots();
      void refetchFiles();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '回滚失败')),
  });

  // --- Snapshot delete ---
  const deleteSnapshotMutation = useMutation({
    mutationFn: (snapshotId: string) => sandboxApi.deleteSnapshot(id!, snapshotId),
    onSuccess: () => {
      message.success('快照已删除');
      void refetchSnapshots();
      void refetchUsage();
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, '删除快照失败')),
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
          <Button icon={<DownloadOutlined />} loading={proofFetching && proofOpen} onClick={openProofBundle}>
            证明包
          </Button>
          {canPause && (
            <Button icon={<PauseCircleOutlined />} loading={pauseMutation.isPending} onClick={() => pauseMutation.mutate()}>
              暂停
            </Button>
          )}
          {canResume && (
            <Button type="primary" icon={<PlayCircleOutlined />} loading={resumeMutation.isPending} onClick={() => resumeMutation.mutate()}>
              恢复
            </Button>
          )}
          {canRefresh && (
            <Button icon={<ReloadOutlined />} loading={refreshMutation.isPending} onClick={() => refreshMutation.mutate()}>
              续期
            </Button>
          )}
          {canOperate && ['running', 'ready', 'provisioning', 'suspended'].includes(session.status) && (
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

      {/* Usage summary line */}
      <Card size="small" style={{ marginBottom: 16 }} bodyStyle={{ padding: '8px 16px' }}>
        <Row gutter={[16, 8]} align="middle">
          <Col xs={12} sm={8} md={5}>
            <Text type="secondary">工作区：</Text>
            <Space size={4}>
              <Text strong>{usage?.workspace.files ?? '-'}</Text>
              <Text type="secondary">个文件</Text>
              <Text type="secondary">/</Text>
              <Text strong>{formatBytes(usage?.workspace.bytes)}</Text>
            </Space>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <Text type="secondary">上传：</Text>
            <Space size={4}>
              <Text strong>{usage?.uploaded_files.count ?? '-'}</Text>
              <Text type="secondary">个 /</Text>
              <Text strong>{formatBytes(usage?.uploaded_files.bytes)}</Text>
            </Space>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <Text type="secondary">快照：</Text>
            <Text strong>{usage?.snapshots.count ?? '-'}</Text>
            <Text type="secondary"> 个 / {formatBytes(usage?.snapshots.bytes)}</Text>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <Text type="secondary">超时：</Text>
            <Text strong>{usage?.timeout.timeout_seconds ?? '-'}</Text>
            <Text type="secondary"> 秒</Text>
            {usage && usage.timeout.extended_seconds > 0 && (
              <Tag color="green" style={{ marginLeft: 4 }}>+{usage.timeout.extended_seconds}s 续期</Tag>
            )}
          </Col>
          <Col xs={24} md={4} style={{ textAlign: 'right' }}>
            <Button size="small" icon={<ReloadOutlined />} loading={usageFetching} onClick={() => { void refetchUsage(); }}>
              刷新
            </Button>
          </Col>
        </Row>
      </Card>

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

      {/* --- Files Panel --- */}
      <Card
        title="工作区文件"
        style={{ marginTop: 16 }}
        extra={
          <Space>
            <Upload
              beforeUpload={handleUpload}
              showUploadList={false}
              disabled={!canManageFiles}
            >
              <Button
                type="primary"
                icon={<UploadOutlined />}
                loading={uploadFileMutation.isPending}
                disabled={!canManageFiles}
              >
                上传文件
              </Button>
            </Upload>
            <Button
              icon={<ReloadOutlined />}
              loading={filesFetching}
              onClick={() => { void refetchFiles(); }}
            >
              刷新
            </Button>
          </Space>
        }
      >
        {downloadRedactedFile && (
          <Alert
            type="info"
            showIcon
            message="输出审查说明"
            description={`文件 "${downloadRedactedFile}" 已通过输出审查，部分敏感内容已被脱敏。`}
            closable
            onClose={() => setDownloadRedactedFile(null)}
            style={{ marginBottom: 12 }}
          />
        )}
        <Table
          rowKey="name"
          size="small"
          loading={filesFetching}
          dataSource={filesData?.files || []}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无文件" /> }}
          pagination={false}
          columns={[
            {
              title: '文件名',
              dataIndex: 'name',
              key: 'name',
              render: (name: string, record: SessionFile) => (
                <Space>
                  <FileTextOutlined />
                  <Text code>{name}</Text>
                  {record.encrypted && <Tag color="gold">已加密</Tag>}
                </Space>
              ),
            },
            {
              title: '大小',
              dataIndex: 'size',
              key: 'size',
              width: 140,
              render: (size: number) => formatBytes(size),
            },
            {
              title: '操作',
              key: 'action',
              width: 180,
              render: (_: unknown, record: SessionFile) => (
                <Space>
                  <Button
                    type="link"
                    size="small"
                    icon={<DownloadOutlined />}
                    loading={downloadFileMutation.isPending && downloadFileMutation.variables === record.name}
                    onClick={() => downloadFileMutation.mutate(record.name)}
                    disabled={!canManageFiles}
                  >
                    下载
                  </Button>
                  <Popconfirm
                    title="确认删除此文件？"
                    description="删除后无法恢复"
                    onConfirm={() => deleteFileMutation.mutate(record.name)}
                    okButtonProps={{ danger: true }}
                  >
                    <Button
                      type="link"
                      danger
                      size="small"
                      icon={<DeleteOutlined />}
                      loading={deleteFileMutation.isPending && deleteFileMutation.variables === record.name}
                      disabled={!canManageFiles}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* --- Snapshots Panel --- */}
      <Card
        title="会话快照"
        style={{ marginTop: 16 }}
        extra={
          <Space>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={!canManageSnapshots}
              onClick={() => setCreateSnapshotOpen(true)}
            >
              创建快照
            </Button>
            <Button
              icon={<ReloadOutlined />}
              loading={snapshotsFetching}
              onClick={() => { void refetchSnapshots(); }}
            >
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          size="small"
          loading={snapshotsFetching}
          dataSource={snapshotsData?.snapshots || []}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无快照" /> }}
          pagination={false}
          columns={[
            {
              title: '快照 ID',
              dataIndex: 'id',
              key: 'id',
              width: 180,
              render: (v: string) => <Text code>{v.slice(0, 12)}...</Text>,
            },
            {
              title: '描述',
              dataIndex: 'description',
              key: 'description',
              render: (v?: string) => v || <Text type="secondary">-</Text>,
            },
            {
              title: '文件数',
              dataIndex: 'file_count',
              key: 'file_count',
              width: 100,
            },
            {
              title: '大小',
              dataIndex: 'size_bytes',
              key: 'size_bytes',
              width: 120,
              render: (v: number) => formatBytes(v),
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              key: 'created_at',
              width: 180,
              render: (v: string) => formatTime(v),
            },
            {
              title: '操作',
              key: 'action',
              width: 180,
              render: (_: unknown, record: SessionSnapshot) => (
                <Space>
                  <Popconfirm
                    title="确认回滚到此快照？"
                    description="回滚后当前工作区内容将被替换为快照状态，无法撤销。"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => rollbackSnapshotMutation.mutate(record.id)}
                  >
                    <Button
                      type="link"
                      size="small"
                      icon={<RollbackOutlined />}
                      disabled={!canManageSnapshots}
                    >
                      回滚
                    </Button>
                  </Popconfirm>
                  <Popconfirm
                    title="确认删除此快照？"
                    description="删除后无法恢复"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => deleteSnapshotMutation.mutate(record.id)}
                  >
                    <Button
                      type="link"
                      danger
                      size="small"
                      icon={<DeleteOutlined />}
                      disabled={!canManageSnapshots}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* --- Exec Console --- */}
      <Card
        title="终端命令"
        style={{ marginTop: 16 }}
        extra={<Tag color="blue">exec</Tag>}
      >
        {canExec ? (
          <>
            <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
              <Input
                value={execCommand}
                onChange={(e) => setExecCommand(e.target.value)}
                placeholder="输入 shell 命令，例如: ls -la /workspace"
                onPressEnter={() => execMutation.mutate()}
              />
              <InputNumber
                min={1}
                max={3600}
                value={execTimeout}
                onChange={(v) => setExecTimeout(v ?? null)}
                style={{ width: 130 }}
                addonBefore="超时(s)"
              />
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={execMutation.isPending}
                onClick={() => execMutation.mutate()}
              >
                运行
              </Button>
            </Space.Compact>
            {execResult && (
              <div
                style={{
                  background: '#0d1117',
                  borderRadius: 6,
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    padding: '8px 12px',
                    background: '#161b22',
                    borderBottom: '1px solid #30363d',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <Text type="secondary" style={{ color: '#8b949e', fontSize: 12 }}>
                    输出
                  </Text>
                  <Space size={4}>
                    <Tag color={execResult.exit_code === 0 ? 'green' : 'red'} style={{ margin: 0 }}>
                      exit {execResult.exit_code}
                    </Tag>
                    {execResult.duration_ms !== undefined && (
                      <Tag style={{ margin: 0 }}>{execResult.duration_ms}ms</Tag>
                    )}
                  </Space>
                </div>
                <div style={{ padding: 12, maxHeight: 400, overflow: 'auto' }}>
                  {execResult.output_blocked ? (
                    <Alert
                      type="error"
                      showIcon
                      message="输出已被安全审查阻断"
                      description={execResult.blocked_reason || '输出命中 critical 安全规则，原始内容未释放。'}
                    />
                  ) : (
                    <pre
                      style={{
                        margin: 0,
                        color: '#e6edf3',
                        fontFamily: 'Consolas, Monaco, monospace',
                        fontSize: 13,
                        lineHeight: 1.5,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-all',
                      }}
                    >
                      {execResult.output || (
                        <span style={{ color: '#6e7681' }}>（无输出）</span>
                      )}
                    </pre>
                  )}
                </div>
              </div>
            )}
          </>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="只有会话所有者可在 ready/running 状态执行命令"
          />
        )}
      </Card>

      {/* --- Logs Viewer --- */}
      <Card
        title="审计日志"
        style={{ marginTop: 16 }}
        extra={
          <Space>
            <Input
              placeholder="筛选动作"
              value={logActionFilter}
              onChange={(e) => {
                setLogActionFilter(e.target.value);
                setLogSince(undefined);
                setAllLogs([]);
              }}
              style={{ width: 160 }}
              allowClear
            />
            <Button
              icon={<ReloadOutlined />}
              loading={logsFetching}
              onClick={refreshLogs}
            >
              刷新
            </Button>
          </Space>
        }
      >
        {logsIsError && (
          <Alert
            type="error"
            showIcon
            message="日志加载失败"
            description={getErrorMessage(logsError)}
            style={{ marginBottom: 12 }}
          />
        )}
        <List
          size="small"
          loading={logsFetching && allLogs.length === 0}
          dataSource={allLogs}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无日志" /> }}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                avatar={<Tag>{item.action}</Tag>}
                title={
                  <Space>
                    <Text strong>{item.action}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {formatTime(item.created_at)}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      用户: {shortId(item.user_id)}
                    </Text>
                  </Space>
                }
                description={
                  item.detail && typeof item.detail === 'object' && Object.keys(item.detail as Record<string, unknown>).length > 0
                    ? <pre style={{ margin: 0, fontSize: 12, maxHeight: 80, overflow: 'auto' }}>{JSON.stringify(item.detail, null, 2)}</pre>
                    : <Text type="secondary">-</Text>
                }
              />
            </List.Item>
          )}
        />
        {logsData && logsData.logs.length > 0 && logsData.count > allLogs.length && (
          <div style={{ textAlign: 'center', marginTop: 12 }}>
            <Button onClick={loadMoreLogs} loading={logsFetching}>
              加载更多
            </Button>
          </div>
        )}
      </Card>

      {/* --- Create Snapshot Modal --- */}
      <Modal
        title="创建快照"
        open={createSnapshotOpen}
        onCancel={() => { setCreateSnapshotOpen(false); setSnapshotDescription(''); }}
        onOk={() => createSnapshotMutation.mutate()}
        confirmLoading={createSnapshotMutation.isPending}
        okText="创建"
      >
        <Form layout="vertical">
          <Form.Item label="描述（可选）">
            <Input.TextArea
              rows={3}
              value={snapshotDescription}
              onChange={(e) => setSnapshotDescription(e.target.value)}
              placeholder="输入快照描述，便于后续识别"
            />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title="会话证明包"
        open={proofOpen}
        onClose={() => setProofOpen(false)}
        width={760}
        extra={
          <Button icon={<DownloadOutlined />} disabled={!proofBundle} onClick={downloadProofBundle}>
            下载 JSON
          </Button>
        }
      >
        {proofIsError && (
          <Alert
            type="error"
            showIcon
            message="证明包生成失败"
            description={getErrorMessage(proofError, '未知错误')}
            style={{ marginBottom: 16 }}
          />
        )}
        {proofFetching && !proofBundle ? (
          <Spin size="large" style={{ display: 'block', margin: '80px auto' }} />
        ) : proofBundle ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="证明级别">
                <Tag color={proofLevelColors[proofBundle.runtime.proof_level] || 'default'}>
                  {proofLevelLabels[proofBundle.runtime.proof_level] || proofBundle.runtime.proof_level}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Bundle Hash">
                <Text code copyable>{proofBundle.integrity.bundle_hash}</Text>
              </Descriptions.Item>
              {proofBundle.integrity.evidence_hash && (
                <Descriptions.Item label="Evidence Hash">
                  <Text code copyable>{proofBundle.integrity.evidence_hash}</Text>
                </Descriptions.Item>
              )}
              <Descriptions.Item label="策略 Hash">
                <Text code copyable>{proofBundle.policy.combined_policy_hash || '-'}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Attestation">
                <Space direction="vertical" size={2}>
                  <Text>类型：{String(proofBundle.runtime.attestation.type || '-')}</Text>
                  <Text code copyable={!!proofBundle.runtime.attestation.quote_hash}>
                    quote_hash: {proofBundle.runtime.attestation.quote_hash || '-'}
                  </Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="输出审查">
                {proofBundle.output_security.available ? (
                  <Space direction="vertical" size={2}>
                    <Space wrap>
                      <Tag color={proofBundle.output_security.blocked ? 'red' : 'green'}>
                        {proofBundle.output_security.blocked ? '已阻断' : '已放行'}
                      </Tag>
                      <Tag>发现 {proofBundle.output_security.findings_count ?? 0} 项</Tag>
                    </Space>
                    {proofBundle.output_security.signature && (
                      <Text code copyable>{proofBundle.output_security.signature}</Text>
                    )}
                    {proofBundle.output_security.report_hash && (
                      <Text code copyable>report_hash: {proofBundle.output_security.report_hash}</Text>
                    )}
                  </Space>
                ) : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="审计事件">{proofBundle.audit.event_count ?? 0}</Descriptions.Item>
            </Descriptions>
            <Card title="JSON">
              <pre className="cds-json-block">{JSON.stringify(proofBundle, null, 2)}</pre>
            </Card>
          </Space>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无证明包" />
        )}
      </Drawer>
    </div>
  );
}
