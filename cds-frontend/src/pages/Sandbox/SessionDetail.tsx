import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ExecStreamTerminal from '../../components/Sandbox/ExecStreamTerminal';
import ErrorCard from '../../components/Feedback/ErrorCard';
import ProofBundleDrawer from './ProofBundleDrawer';
import SessionStatusStrip from './SessionStatusStrip';
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
  List,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';import type { UploadProps } from 'antd';
import {
  CodeOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileProtectOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  LinkOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
  StopOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { ApiOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  sandboxApi,
  isSessionOperation,
  isTerminalOperation,
  type SessionExecuteResult,
  type ExecResult,
  type SessionLogEntry,
  type SessionFile,
  type SessionSnapshot,
  type SessionOperation,
} from '../../services/sandboxApi';
import { metaApi } from '../../services/metaApi';
import { useAuthStore } from '../../stores/authStore';
import { UserRole } from '../../types/enums';
import { hasAnyRole } from '../../utils/roles';
import { getErrorMessage, CenteredLoading, EmptyState, QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import SimulationBadge, { simulationStateForValue, type SimulationBadgeState } from '../../components/Feedback/SimulationBadge';
import { supportsWsStreaming, rowReviewStatus, parseW2Error } from './sessionWorkbench';

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

// PZ-16 ①：T5 行级状态高亮（/logs 条目 detail 带 inspection 字段时）
const rowReviewMeta: Record<string, { color: string; label: string; border: string; bg: string }> = {
  passed: { color: 'green', label: 'passed', border: '#52c41a', bg: '#f6ffed' },
  redacted: { color: 'orange', label: 'redacted', border: '#fa8c16', bg: '#fff7e6' },
  blocked: { color: 'red', label: 'blocked', border: '#ff4d4f', bg: '#fff1f0' },
};

const modeLabels: Record<string, string> = {
  structured_query: '结构化查询',
  structured_modeling: '结构化建模',
  structured_app: '应用调用',
  llm_training: 'LLM 训练',
  product_dev: '产品开发',
  joint_federated: '联合计算',
};

// SS-13: trust_root 徽标（level Tag 同行）——真实 TEE 显示硬件名，none/缺失诚实披露为裸 MicroVM
const trustRootLabels: Record<string, string> = {
  tdx: 'TDX 机密计算',
  sev_snp: 'SEV-SNP 机密计算',
  none: '无 TEE 硬件（裸 MicroVM）',
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

// PZ-14：后端披露字段（老响应可能缺失，防御式读取）
interface SecurityReportDisclosure {
  ner_engine?: string | null;
  signature_algorithm?: string | null;
}

function disclosureString(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined;
}

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

  // PZ-16 ③：文件审查预告（X-CDS-Output-Review 头解析结果，按文件名缓存）
  // 与 409 阻断发现摘要（不放开，仅展示 findings）
  const [fileReviews, setFileReviews] = useState<Record<string, { status: 'passed' | 'redacted' | 'blocked'; findings: number }>>({});
  const [blockedDownload, setBlockedDownload] = useState<{
    filename: string;
    findingsCount: number;
    findings: { severity?: string; type?: string; message?: string }[];
  } | null>(null);

  // --- Snapshots ---
  const [createSnapshotOpen, setCreateSnapshotOpen] = useState(false);
  const [snapshotDescription, setSnapshotDescription] = useState('');

  // PZ-16 ④：202 异步快照操作（轮询 GET /operations/{op_id}）+ OPERATION_LOCKED 锁定指示
  const [activeOp, setActiveOp] = useState<SessionOperation | null>(null);
  const [opLocked, setOpLocked] = useState<{ retryAfter: number } | null>(null);
  const { data: opData } = useQuery({
    queryKey: ['sandbox-session-operation', activeOp?.operation_id],
    queryFn: () => sandboxApi.getOperation(activeOp!.operation_id),
    enabled: !!activeOp && !isTerminalOperation(activeOp),
    refetchInterval: (q) => (isTerminalOperation(q.state.data) ? false : 2000),
    staleTime: 0,
  });

  const { data: session, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['sandbox-session', id],
    queryFn: () => sandboxApi.get(id!),
    enabled: !!id,
  });

  // PZ-16 ①：流式能力由 PZ-13 能力矩阵判定（stream_ws=real），不可用时回退 L0/k8s 白名单
  const { data: capabilityMatrix } = useQuery({
    queryKey: ['capability-matrix'],
    queryFn: () => metaApi.getCapabilityMatrix(),
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });
  const streamingSupported = !!session && supportsWsStreaming(session.sandbox_level, capabilityMatrix);
  const sessionIsActive = !!session && ['pending', 'provisioning', 'key_distributing', 'ready', 'running'].includes(session.status);

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

  const {
    data: networkPolicy,
    isError: networkIsError,
    error: networkError,
    refetch: refetchNetworkPolicy,
  } = useQuery({
    queryKey: ['sandbox-session-network-policy', id],
    queryFn: () => sandboxApi.getNetworkPolicy(id!),
    enabled: !!id && !!session,
  });

  // --- Usage ---
  const {
    data: usage,
    isFetching: usageFetching,
    isError: usageIsError,
    error: usageError,
    refetch: refetchUsage,
  } = useQuery({
    queryKey: ['sandbox-session-usage', id],
    queryFn: () => sandboxApi.getUsage(id!),
    enabled: !!id && !!session,
  });

  // --- Logs ---
  // PZ-16 ①：非 WS 级别（L1/L2/L3/L2'）轮询 /logs 审计尾（3s），显式"非实时"；WS 级别保留手动刷新
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
    refetchInterval: !streamingSupported && sessionIsActive ? 3000 : false,
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
    isError: filesIsError,
    error: filesError,
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
    isError: snapshotsIsError,
    error: snapshotsError,
    refetch: refetchSnapshots,
  } = useQuery({
    queryKey: ['sandbox-session-snapshots', id],
    queryFn: () => sandboxApi.listSnapshots(id!),
    enabled: !!id && !!session,
  });

  // 异步操作到达终态：刷新快照/文件/用量并清除进度指示
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!activeOp || !opData || !isTerminalOperation(opData)) return;
    if (opData.operation_id !== activeOp.operation_id) return;
    if (opData.status === 'succeeded') {
      message.success(opData.op_type === 'rollback' ? '快照回滚完成' : '快照创建完成');
    } else {
      message.error(`操作失败：${opData.error || '未知错误'}`);
    }
    void refetchSnapshots();
    void refetchFiles();
    void refetchUsage();
    setActiveOp(null);
  }, [opData, activeOp, refetchSnapshots, refetchFiles, refetchUsage]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // OPERATION_LOCKED：按 Retry-After 倒计时后自动解除锁定指示（503 无头时默认 5s）
  useEffect(() => {
    if (!opLocked) return;
    const t = setTimeout(() => setOpLocked(null), Math.max(1, opLocked.retryAfter) * 1000);
    return () => clearTimeout(t);
  }, [opLocked]);

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
    onSuccess: ({ blob, review }, filename) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      // PZ-16 ③：缓存 X-CDS-Output-Review 结果供文件表徽标展示（下载前预告）
      setBlockedDownload(null);
      if (review) {
        setFileReviews((prev) => ({ ...prev, [filename]: { status: review.status, findings: review.findings } }));
      }
      if (review?.status === 'redacted') {
        setDownloadRedactedFile(filename);
        message.info('该文件已通过输出审查，内容已脱敏');
      } else {
        setDownloadRedactedFile(null);
        message.success('下载成功');
      }
    },
    onError: (error: unknown, filename) => {
      const err = error as { status?: number; detail?: { findings_count?: number; error?: string; findings?: { severity?: string; type?: string; message?: string }[] } };
      if (err.status === 409) {
        // 409 输出审查阻断：不放开，展示发现摘要 + 联系管理员（PZ-16 ③）
        const count = err.detail?.findings_count ?? 0;
        const findings = err.detail?.findings ?? [];
        setFileReviews((prev) => ({ ...prev, [filename]: { status: 'blocked', findings: count } }));
        setBlockedDownload({ filename, findingsCount: count, findings });
        message.error(`下载失败：输出审查阻断（发现 ${count} 项风险，内容未释放）`);
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

  // PZ-16 ④：OPERATION_LOCKED（503）— 置锁定指示（busy，非 spinner），按 Retry-After 倒计时
  const handleOperationLocked = (error: unknown): boolean => {
    const w2 = parseW2Error(error);
    if (w2.code === 'OPERATION_LOCKED' || w2.code === 'HTTP_503' || /OPERATION_LOCKED/i.test(w2.message)) {
      setOpLocked({ retryAfter: 5 });
      return true;
    }
    return false;
  };

  // --- Snapshot create（202 异步 + 轮询） ---
  const createSnapshotMutation = useMutation({
    mutationFn: () => sandboxApi.createSnapshot(id!, { description: snapshotDescription || undefined }, true),
    onSuccess: (res) => {
      setCreateSnapshotOpen(false);
      setSnapshotDescription('');
      if (isSessionOperation(res)) {
        setActiveOp(res);
        message.info('快照创建已提交（异步执行中）');
      } else {
        message.success('快照已创建');
        void refetchSnapshots();
        void refetchUsage();
      }
    },
    onError: (error: unknown) => {
      if (!handleOperationLocked(error)) message.error(getErrorMessage(error, '创建快照失败'));
    },
  });

  // --- Snapshot rollback（202 异步 + 轮询；并发回滚 → OPERATION_LOCKED） ---
  const rollbackSnapshotMutation = useMutation({
    mutationFn: (snapshotId: string) => sandboxApi.rollbackSnapshot(id!, snapshotId, true),
    onSuccess: (res) => {
      if (isSessionOperation(res)) {
        setActiveOp(res);
        message.info('快照回滚已提交（异步执行中）');
      } else {
        message.success('已回滚到快照');
        void refetchSnapshots();
        void refetchFiles();
      }
    },
    onError: (error: unknown) => {
      if (!handleOperationLocked(error)) message.error(getErrorMessage(error, '回滚失败'));
    },
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

  if (isError) {
    // PZ-16 ⑥：主查询错误按 W2 码区分（404/403/410/500…），不再混同为"会话不存在或无权访问"；
    // 404/未知态同时给空态引导（PZ-16 ⑦：无会话 → 创建表单，仅链接，表单在会话列表页）
    const w2 = parseW2Error(error);
    return (
      <ErrorCard
        code={w2.code}
        message={w2.message}
        request_id={w2.requestId}
        status={w2.status}
        retryAction={() => void refetch()}
        actions={
          <>
            <Button onClick={() => navigate('/sandbox-sessions')}>返回列表</Button>
            <Button type="primary" onClick={() => navigate('/sandbox-sessions')}>创建会话</Button>
          </>
        }
      />
    );
  }
  if (isLoading) return <CenteredLoading tip="加载会话" />;
  if (!session) {
    return (
      <EmptyState
        description="暂无会话"
        action={<Button type="primary" onClick={() => navigate('/sandbox-sessions')}>前往创建会话</Button>}
      />
    );
  }

  const report = executeResult?.security_report;

  // PZ-14 诚实降级呈现：头部三元组（级别 / 运行时后端 / 证明级别）+ 执行结果区披露徽标。
  // 字段可能缺失（老响应），仅在字段存在时渲染。
  const sessionDisclosure = session as unknown as Record<string, unknown>;
  const sessionIsSimulation = sessionDisclosure.is_simulation === true;
  const sessionTeeMode = disclosureString(sessionDisclosure.tee_mode);
  const sessionBackend = disclosureString(sessionDisclosure.backend);
  const sessionProofLevel = disclosureString(sessionDisclosure.proof_level);
  const isL1SoftwarePath = session.sandbox_level === 'L1';

  const runtimeBackendBadge = (() => {
    if (sessionIsSimulation) {
      return (
        <SimulationBadge
          state="simulated"
          label={`运行时后端: ${sessionTeeMode || sessionBackend || 'software_confidential'}（模拟，FG-001）`}
          detail="当前运行时为软件模拟路径（诚实降级）：执行链路真实，隔离/证明硬件为模拟；FG-001 真实 TEE 硬件验收通过后转为真实。"
        />
      );
    }
    const backendValue = sessionTeeMode || sessionBackend;
    if (backendValue) {
      return (
        <SimulationBadge
          state={simulationStateForValue(backendValue) ?? 'real'}
          label={`运行时后端: ${backendValue}`}
          detail="会话运行时后端披露（tee_mode / backend 字段，PZ-14）。"
        />
      );
    }
    if (isL1SoftwarePath) {
      return (
        <SimulationBadge
          state="simulated"
          label="software_confidential（模拟，FG-001）"
          detail="L1 软件路径（诚实降级）：真实 TEE 硬件验收（FG-001）通过后转为真实。"
        />
      );
    }
    return null;
  })();

  const proofLevelBadge = (() => {
    if (sessionProofLevel) {
      const state: SimulationBadgeState =
        sessionProofLevel === 'hardware_tee' ? 'real'
        : sessionProofLevel === 'software_confidential' ? 'simulated'
        : (simulationStateForValue(sessionProofLevel) ?? 'real');
      return (
        <SimulationBadge
          state={state}
          label={`证明级别: ${proofLevelLabels[sessionProofLevel] || sessionProofLevel}`}
          detail={state === 'simulated'
            ? '软件密态证明（software_hash），非硬件 TEE 证明；FG-001 硬件验收通过后升级为硬件 TEE。'
            : '会话证明级别披露（proof_level 字段，PZ-14）。'}
        />
      );
    }
    if (isL1SoftwarePath) {
      return (
        <SimulationBadge
          state="simulated"
          label="证明级别: software_confidential（模拟，FG-001）"
          detail="L1 软件密态证明（software_hash）；FG-001 真实 TEE 硬件验收通过后升级为硬件 TEE。"
        />
      );
    }
    return null;
  })();

  const reportBadges: ReactNode[] = [];
  if (report) {
    if (report.dp_applied !== undefined) {
      reportBadges.push(
        report.dp_applied ? (
          <SimulationBadge key="dp" state="real" label="DP 已施加" detail="差分隐私（Laplace 加噪）已真实施加到本次输出（PZ-02）。" />
        ) : (
          <SimulationBadge key="dp" state="unsupported" label="DP 未施加" detail="本次输出未施加差分隐私（ε=0 或未配置预算）；输出不含 DP 噪声保护。" />
        ),
      );
    }
    const nerEngine = (report as SecurityReportDisclosure).ner_engine;
    const nerState = simulationStateForValue(nerEngine);
    if (nerEngine && nerState) {
      reportBadges.push(
        <SimulationBadge
          key="ner"
          state={nerState}
          label={`PII NER: ${nerEngine}`}
          detail={nerEngine === 'lac' ? 'ML NER 模型（LAC）真实执行。'
            : nerEngine === 'rule' ? 'ML 引擎不可用，由规则引擎执行 PII 识别（诚实披露，ner_engine=rule）。'
            : 'PII NER 未启用（仅正则层）。'}
        />,
      );
    }
    if (report.signature) {
      const sigAlgo = (report as SecurityReportDisclosure).signature_algorithm;
      // SM2 签名为 r||s（128 hex）；SM3 摘要为 64 hex —— 短即降级
      const downgraded = sigAlgo ? /sm3/i.test(sigAlgo) : /^[0-9a-f]{64}$/i.test(report.signature);
      reportBadges.push(
        downgraded ? (
          <SimulationBadge key="sig" state="simulated" label="签名降级: SM2→SM3" detail="SM2 签名不可用，已降级为 SM3 摘要（无签名不可否认性保证）；HSM/国密签名路径（FG-007）接入后恢复 SM2。" />
        ) : (
          <SimulationBadge key="sig" state="real" label="SM2 签名" detail="输出经 SM2 数字签名（GM/T 0009）。" />
        ),
      );
    }
  }

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Space wrap align="center">
            <Title level={4} style={{ margin: 0 }}>沙箱会话</Title>
            <Tag color={statusColors[session.status] ?? 'default'}>{statusLabels[session.status] ?? session.status}</Tag>
            <Tag>{session.sandbox_level}</Tag>
            <Tag color={session.trust_root && session.trust_root !== 'none' ? 'geekblue' : 'default'}>
              {trustRootLabels[session.trust_root ?? 'none'] ?? '无 TEE 硬件（裸 MicroVM）'}
            </Tag>
            <Tag>{modeLabels[session.sandbox_mode] || session.sandbox_mode}</Tag>
            {runtimeBackendBadge}
            {proofLevelBadge}
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
          <Button icon={<DownloadOutlined />} onClick={() => setProofOpen(true)}>
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

      {/* PZ-16 ②：状态机可视化（pending → key_distributing → ready → running → 终态 + 步骤耗时 + 失败态审计尾） */}
      <SessionStatusStrip session={session} auditTail={allLogs} />

      {session.error_message && (
        <Alert type="error" showIcon message="会话异常" description={session.error_message} style={{ marginBottom: 16 }} />
      )}

      {/* Usage summary line */}
      <Card size="small" style={{ marginBottom: 16 }} bodyStyle={{ padding: '8px 16px' }}>
        {usageIsError && <QueryErrorAlert error={usageError} message="用量加载失败" onRetry={() => { void refetchUsage(); }} />}
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
        {networkIsError && <QueryErrorAlert error={networkError} message="网络策略加载失败" onRetry={() => { void refetchNetworkPolicy(); }} />}
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
                  {reportBadges.length > 0 && (
                    <Space wrap>
                      {reportBadges}
                    </Space>
                  )}
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

      {/* --- W10/PZ-16 ①: 执行观察 — L0/k8s（能力矩阵 stream_ws=real）WS 流式；其余级别轮询 /logs（非实时） --- */}
      <Card
        title="执行观察"
        style={{ marginTop: 16 }}
        extra={
          streamingSupported
            ? <Tag color="green" icon={<ApiOutlined />}>实时 · WS 流式</Tag>
            : (
              <Tooltip title="该级别不支持 WS 流式执行（STREAM_UNSUPPORTED，能力矩阵）；执行观察来自 /logs 审计尾轮询（3s），非实时输出。">
                <Tag color="gold" icon={<InfoCircleOutlined />}>非实时 · 轮询</Tag>
              </Tooltip>
            )
        }
      >
        {streamingSupported ? (
          user?.id === session.user_id && session.status === 'running' ? (
            <ExecStreamTerminal sessionId={session.id} />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="会话所有者且状态为 running 时可用流式终端" />
          )
        ) : (
          <Alert
            type="info"
            showIcon
            message="该级别不支持 WS 流式执行"
            description={
              <span>
                {session.sandbox_level} 级别执行观察采用 <Text code>/logs</Text> 审计尾轮询（下方「审计日志」区，3s 刷新，显式"非实时"）；
                同步命令输出请走下方「终端命令」或「会话执行」。
              </span>
            }
          />
        )}
      </Card>

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
        {filesIsError && <QueryErrorAlert error={filesError} message="文件列表加载失败" onRetry={() => { void refetchFiles(); }} />}
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
        {blockedDownload && (
          // PZ-16 ③：409 输出审查阻断 — 发现摘要面板（不放开，联系管理员）
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              <span>
                输出审查阻断：文件 "{blockedDownload.filename}" 的下载被拒绝（发现 {blockedDownload.findingsCount} 项风险，原始内容未释放）
              </span>
            }
            description={
              <div>
                {blockedDownload.findings.length > 0 && (
                  <ul style={{ margin: '4px 0 8px', paddingLeft: 20 }}>
                    {blockedDownload.findings.map((f, i) => (
                      <li key={`${f.type}-${i}`} style={{ fontSize: 12 }}>
                        <Tag color={severityColors[f.severity ?? ''] ?? 'default'} style={{ fontSize: 11 }}>{f.severity || 'unknown'}</Tag>
                        {f.type}: {f.message || '-'}
                      </li>
                    ))}
                  </ul>
                )}
                <Text type="secondary">请勿尝试绕过输出审查（其他下载/导出通道同样受门禁）。如业务必须获取该内容，请联系管理员复核并走合规放行流程。</Text>
              </div>
            }
            closable
            onClose={() => setBlockedDownload(null)}
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
              render: (name: string, record: SessionFile) => {
                // PZ-16 ③：下载前 T5 结果预告 — 优先 list 的 output_review 字段（若有），
                // 其次消费上次下载的 X-CDS-Output-Review 头解析结果
                const review = fileReviews[name]
                  ?? (record.output_review?.status
                    ? { status: record.output_review.status as 'passed' | 'redacted' | 'blocked', findings: record.output_review.findings_count ?? 0 }
                    : undefined);
                return (
                  <Space>
                    <FileTextOutlined />
                    <Text code>{name}</Text>
                    {record.encrypted && <Tag color="gold">已加密</Tag>}
                    {review && (
                      <Tooltip title="T5 输出审查结果预告（X-CDS-Output-Review 语义：passed/redacted; findings=N）">
                        {review.status === 'passed' && <Tag color="green">审查通过</Tag>}
                        {review.status === 'redacted' && <Tag color="orange">已脱敏 · 发现 {review.findings} 项</Tag>}
                        {review.status === 'blocked' && <Tag color="red">已阻断 · 发现 {review.findings} 项</Tag>}
                      </Tooltip>
                    )}
                  </Space>
                );
              },
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

      {/* --- Snapshots Panel（PZ-16 ④：202 异步进度 + OPERATION_LOCKED 锁定态） --- */}
      <Card
        title="会话快照"
        style={{ marginTop: 16 }}
        extra={
          <Space>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              disabled={!canManageSnapshots || !!activeOp || !!opLocked}
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
        {snapshotsIsError && <QueryErrorAlert error={snapshotsError} message="快照列表加载失败" onRetry={() => { void refetchSnapshots(); }} />}
        {opLocked && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="操作锁定（OPERATION_LOCKED）：另一快照操作进行中"
            description={`约 ${opLocked.retryAfter}s 后可重新发起；回滚为同会话互斥操作，重复触发会被 503 拒绝。`}
          />
        )}
        {(activeOp || opData) && (() => {
          const op = opData ?? activeOp!;
          const percent = Math.min(100, Math.round((op.progress ?? 0) * 100));
          const statusTag = {
            pending: <Tag>待处理</Tag>,
            running: <Tag color="processing" style={{ marginInlineEnd: 0 }}>进行中</Tag>,
            succeeded: <Tag color="success" style={{ marginInlineEnd: 0 }}>已完成</Tag>,
            failed: <Tag color="error" style={{ marginInlineEnd: 0 }}>失败</Tag>,
          }[op.status] ?? <Tag>{op.status}</Tag>;
          return (
            <Alert
              type={op.status === 'failed' ? 'error' : 'info'}
              showIcon
              style={{ marginBottom: 12 }}
              message={
                <Space wrap>
                  <span>{op.op_type === 'rollback' ? '快照回滚' : '快照创建'}操作（异步）</span>
                  {statusTag}
                  <Text type="secondary" style={{ fontSize: 12 }}>operation_id: {op.operation_id.slice(0, 8)}…</Text>
                </Space>
              }
              description={
                <div style={{ maxWidth: 560 }}>
                  {/* 进度条（Progress）作为 busy 指示，不用 Spin 以免与列表 loading 混淆 */}
                  <Progress
                    percent={percent}
                    size="small"
                    status={op.status === 'failed' ? 'exception' : op.status === 'succeeded' ? 'success' : 'active'}
                  />
                  {op.status === 'failed' && op.error && (
                    <Text type="danger" style={{ fontSize: 12 }}>{op.error}</Text>
                  )}
                  {op.status === 'running' && (
                    <Text type="secondary" style={{ fontSize: 12 }}>快照为 202 异步操作，完成后自动刷新列表。</Text>
                  )}
                </div>
              }
            />
          );
        })()}
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
                      disabled={!canManageSnapshots || !!activeOp || !!opLocked}
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

      {/* --- Logs Viewer（PZ-16 ①：非 WS 级别轮询 + "非实时" 徽标 + T5 行级状态高亮） --- */}
      <Card
        title="审计日志"
        style={{ marginTop: 16 }}
        extra={
          <Space>
            {!streamingSupported && (
              <Tooltip title="该级别不支持 WS 流式，日志来自 /logs 审计尾轮询（3s），非实时输出。">
                <Tag color="gold" icon={<InfoCircleOutlined />}>非实时</Tag>
              </Tooltip>
            )}
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
            action={<Button size="small" onClick={() => void refetchLogs()}>重试</Button>}
            style={{ marginBottom: 12 }}
          />
        )}
        <List
          size="small"
          loading={logsFetching && allLogs.length === 0}
          dataSource={allLogs}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无日志" /> }}
          renderItem={(item) => {
            // T5 行级状态：inspection 字段存在时高亮输出行（passed/redacted/blocked）
            const rowStatus = rowReviewStatus(item.detail);
            const rowMeta = rowStatus ? rowReviewMeta[rowStatus] : null;
            return (
              <List.Item
                style={rowMeta ? { borderLeft: `3px solid ${rowMeta.border}`, paddingLeft: 12, background: rowMeta.bg } : undefined}
              >
                <List.Item.Meta
                  avatar={<Tag>{item.action}</Tag>}
                  title={
                    <Space>
                      <Text strong>{item.action}</Text>
                      {rowMeta && <Tag color={rowMeta.color} style={{ marginInlineEnd: 0 }}>{rowMeta.label}</Tag>}
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
            );
          }}
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

      {/* PZ-16 ⑤：证明包 — 一键生成/下载 + 披露分组（bundle_meta vs evidence，PZ-14 口径） */}
      <ProofBundleDrawer sessionId={session.id} open={proofOpen} onClose={() => setProofOpen(false)} />
    </div>
  );
}
