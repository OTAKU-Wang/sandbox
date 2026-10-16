import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { FileSearchOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import {
  describeDeliveryMethod,
  describeFlowError,
  describeFrequency,
  formatDateTime,
  useCancelComputeRun,
  useComputeRuns,
  useComputeSchedule,
  useWorkloads,
} from '../../services/flow';
import type { ComputeRun, ComputeScheduleItem } from '../../services/flow';
import { useTriggerComputeRunDetailed } from '../../services/computeApi';
import type { ComputeRunView } from '../../services/computeApi';
import { sandboxApi } from '../../services/sandboxApi';
import { useQuery } from '@tanstack/react-query';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import { COMPUTE_RUN_STATUS_META, statusMeta } from './display';
import { useContractOptions } from './useContractOptions';

const { Text, Title, Paragraph } = Typography;

const RUN_POLL_INTERVAL_MS = 10_000;

function RunDetail({ run }: { run: ComputeRun }) {
  const files = run.output_files ?? [];
  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        运行 ID {run.id} ｜ 合约 {run.contract_id}
      </Text>

      {run.error_message ? (
        <Alert type="error" showIcon message="失败原因" description={<pre className="cds-json-block">{run.error_message}</pre>} />
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }}>
          后端未返回错误信息（error_message 为空）
        </Text>
      )}

      {run.output && (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            执行输出
          </Text>
          <pre className="cds-json-block">{run.output}</pre>
        </div>
      )}

      <div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          产出文件（{files.length}）
        </Text>
        {files.length ? (
          <pre className="cds-json-block">{files.join('\n')}</pre>
        ) : (
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              无产出文件记录
            </Text>
          </div>
        )}
      </div>
    </Space>
  );
}

const OVERRIDE_FIELDS: { key: string; label: string; unit: string }[] = [
  { key: 'cpu_cores', label: 'CPU 核数', unit: '核' },
  { key: 'memory_mb', label: '内存', unit: 'MB' },
  { key: 'timeout_seconds', label: '单次超时', unit: '秒' },
];

function specCeiling(spec: Record<string, unknown> | undefined, key: string): number | undefined {
  const value = spec?.[key];
  return typeof value === 'number' ? value : undefined;
}

// 后端 /compute/schedule 已下发 sandbox_resource_spec，flow.ts 的类型尚未跟进。
const scheduleSpec = (item: ComputeScheduleItem) =>
  (item as unknown as { sandbox_resource_spec?: Record<string, unknown> }).sandbox_resource_spec;

function ExecuteModal({
  contract,
  onClose,
  onDone,
}: {
  contract: ComputeScheduleItem;
  onClose: () => void;
  onDone: (run: ComputeRunView) => void;
}) {
  const workloadsQuery = useWorkloads({ contractId: contract.contract_id, env: 'production', status: 'published' });
  const workloads = workloadsQuery.data?.items ?? [];
  const [workloadId, setWorkloadId] = useState<string | null>(null);
  const [paramsText, setParamsText] = useState('');
  const [override, setOverride] = useState<Record<string, number>>({});
  const trigger = useTriggerComputeRunDetailed();

  const paramsError = (() => {
    const text = paramsText.trim();
    if (!text) return null;
    try {
      const parsed: unknown = JSON.parse(text);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return '运行参数必须是 JSON 对象';
      if (new Blob([text]).size > 8192) return '运行参数序列化后不得超过 8KB';
      return null;
    } catch {
      return 'JSON 解析失败，请检查语法';
    }
  })();

  const submit = () => {
    if (paramsError) {
      message.warning(paramsError);
      return;
    }
    const parsed = paramsText.trim() ? (JSON.parse(paramsText) as Record<string, unknown>) : null;
    trigger.mutate(
      {
        contractId: contract.contract_id,
        workload_id: workloadId,
        params: parsed,
        resource_override: Object.keys(override).length ? override : null,
      },
      {
        onSuccess: (run) => {
          onDone(run);
          onClose();
        },
        onError: (err) => message.error(describeFlowError(err, '触发生产执行失败')),
      },
    );
  };

  return (
    <Modal
      title={`在生产沙箱执行：${contract.contract_no ?? contract.contract_id.slice(0, 8)}`}
      open
      width={620}
      onCancel={onClose}
      onOk={submit}
      okText="开始执行"
      okButtonProps={{ disabled: !!paramsError, loading: trigger.isPending }}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="执行会在生产沙箱内跑完整个会话生命周期，产出按合约交付方式投递；上一轮未结束时本次会被跳过。"
        />
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            生产版本
          </Text>
          <Select
            style={{ width: '100%' }}
            placeholder="默认使用最新已发布版本"
            allowClear
            loading={workloadsQuery.isLoading}
            value={workloadId ?? undefined}
            onChange={(value?: string) => setWorkloadId(value ?? null)}
            options={workloads.map((workload) => ({ label: `v${workload.version} · ${workload.name}`, value: workload.id }))}
            notFoundContent={<Text type="secondary">该合约没有 published 的生产版本，需先在负载中心发布</Text>}
          />
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            运行参数（JSON 对象，沙箱内以 CDS_PARAMS 读取）
          </Text>
          <Input.TextArea
            rows={5}
            value={paramsText}
            placeholder='{"batch": "2026-10"}'
            onChange={(e) => setParamsText(e.target.value)}
          />
          {paramsError && <Text type="danger">{paramsError}</Text>}
        </div>
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            规格覆盖（只能向下收窄合约已批准的规格）
          </Text>
          <Space wrap size={12} style={{ marginTop: 6 }}>
            {OVERRIDE_FIELDS.map((field) => {
              const ceiling = specCeiling(scheduleSpec(contract), field.key);
              return (
                <Tooltip key={field.key} title={ceiling ? `合约上限 ${ceiling} ${field.unit}` : '合约未限定该规格项'}>
                  <InputNumber
                    style={{ width: 170 }}
                    placeholder={`${field.label}${ceiling ? ` ≤ ${ceiling}` : ''}`}
                    min={1}
                    max={ceiling}
                    value={override[field.key]}
                    onChange={(value) =>
                      setOverride((prev) => {
                        const next = { ...prev };
                        if (typeof value === 'number') next[field.key] = value;
                        else delete next[field.key];
                        return next;
                      })
                    }
                  />
                </Tooltip>
              );
            })}
          </Space>
        </div>
      </Space>
    </Modal>
  );
}

function RunLogDrawer({ run, onClose }: { run: ComputeRunView; onClose: () => void }) {
  const navigate = useNavigate();
  const logsQuery = useQuery({
    queryKey: ['compute-run-logs', run.id, run.session_id],
    queryFn: () => sandboxApi.getLogs(run.session_id as string, { limit: 200 }),
    enabled: !!run.session_id,
    refetchInterval: 10_000,
  });
  const usage = run.resource_usage ?? {};
  const files = (run.output_files ?? []) as ({ name: string; size_bytes?: number } | string)[];

  return (
    <Drawer title={`运行日志 · ${run.id.slice(0, 8)}…`} width={880} open onClose={onClose}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="状态">
            <Tag color={statusMeta(COMPUTE_RUN_STATUS_META, run.status).color}>{statusMeta(COMPUTE_RUN_STATUS_META, run.status).label}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="退出码">{typeof usage.exit_code === 'number' ? usage.exit_code : '后端未上报'}</Descriptions.Item>
          <Descriptions.Item label="产出行数">{run.output_rows ?? 0}</Descriptions.Item>
          <Descriptions.Item label="出口审查">
            {usage.output_blocked ? (
              <Tag color="red">产出被 DLP 规则拦截</Tag>
            ) : (
              <Tag color="green">未被拦截</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="沙箱会话" span={2}>
            {run.session_id ? (
              <a onClick={() => navigate(`/sandbox-sessions/${run.session_id}`)}>{run.session_id}</a>
            ) : (
              <Text type="secondary">本次运行未创建沙箱会话（无真实执行）</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="运行参数" span={2}>
            <pre className="cds-json-block">{run.params ? JSON.stringify(run.params, null, 2) : '未提交参数'}</pre>
          </Descriptions.Item>
          <Descriptions.Item label="规格覆盖" span={2}>
            <pre className="cds-json-block">{run.resource_override ? JSON.stringify(run.resource_override, null, 2) : '未覆盖'}</pre>
          </Descriptions.Item>
        </Descriptions>

        {run.error_message && <Alert type="error" showIcon message="失败原因" description={<pre className="cds-json-block">{run.error_message}</pre>} />}
        {usage.manifest_error && <Alert type="warning" showIcon message={`产出清单不可用：${usage.manifest_error}`} />}

        <div>
          <Text strong>产出文件（{files.length}）</Text>
          {files.length ? (
            <pre className="cds-json-block">{files.map((file) => (typeof file === 'string' ? file : `${file.name}${typeof file.size_bytes === 'number' ? ` (${file.size_bytes} B)` : ''}`)).join('\n')}</pre>
          ) : (
            <Paragraph type="secondary" style={{ marginTop: 6 }}>
              无产出文件记录
            </Paragraph>
          )}
        </div>

        <div>
          <Text strong>沙箱操作日志</Text>
          {!run.session_id && (
            <Paragraph type="secondary" style={{ marginTop: 6 }}>
              没有会话就没有执行日志——此处不伪造记录。
            </Paragraph>
          )}
          {run.session_id && logsQuery.isError && (
            <QueryErrorAlert error={logsQuery.error} message="日志加载失败" onRetry={() => void logsQuery.refetch()} />
          )}
          {run.session_id && (
            <Table
              size="small"
              rowKey="id"
              loading={logsQuery.isLoading}
              dataSource={logsQuery.data?.logs ?? []}
              pagination={{ pageSize: 20 }}
              locale={tableEmpty('该会话暂无操作日志')}
              columns={[
                { title: '时间', dataIndex: 'created_at', width: 180, render: (value: string) => formatDateTime(value) },
                { title: '动作', dataIndex: 'action', width: 200 },
                {
                  title: '详情',
                  dataIndex: 'detail',
                  render: (detail: unknown) =>
                    detail ? <pre className="cds-json-block" style={{ margin: 0 }}>{typeof detail === 'string' ? detail : JSON.stringify(detail, null, 2)}</pre> : <Text type="secondary">-</Text>,
                },
              ]}
            />
          )}
        </div>
      </Space>
    </Drawer>
  );
}

export default function ComputeSchedule() {
  const navigate = useNavigate();
  const [contractId, setContractId] = useState<string | null>(null);
  const { data: contracts } = useContractOptions();
  const scheduleQuery = useComputeSchedule(contractId);
  const runsQuery = useComputeRuns({ contractId }, RUN_POLL_INTERVAL_MS);
  const cancelMutation = useCancelComputeRun();
  const [executeContract, setExecuteContract] = useState<ComputeScheduleItem | null>(null);
  const [logRun, setLogRun] = useState<ComputeRunView | null>(null);

  const contractNo = (id: string) => (contracts ?? []).find((contract) => contract.id === id)?.contract_no ?? `${id.slice(0, 8)}…`;

  const scheduleColumns: ColumnsType<ComputeScheduleItem> = [
    {
      title: '合约',
      dataIndex: 'contract_no',
      render: (contractNoValue: string | null, record) => (
        <a onClick={() => navigate(`/contracts/${record.contract_id}`)}>{contractNoValue || contractNo(record.contract_id)}</a>
      ),
    },
    {
      title: '自动计算',
      dataIndex: 'auto_compute',
      width: 100,
      render: (auto: boolean) => (auto ? <Tag color="green">已开启</Tag> : <Tag>已关闭</Tag>),
    },
    {
      title: '频率',
      dataIndex: 'compute_frequency',
      width: 110,
      render: (frequency: string) => describeFrequency(frequency),
    },
    {
      title: '下次计算时间',
      dataIndex: 'next_compute_at',
      width: 190,
      render: (value: string | null, record) =>
        value ? (
          <Tag color="processing">{formatDateTime(value)}</Tag>
        ) : record.auto_compute ? (
          <Text type="warning">自动计算已开启但后端未下发 next_compute_at</Text>
        ) : (
          <Text type="secondary">无排程（仅手动）</Text>
        ),
    },
    {
      title: '上次计算完成',
      dataIndex: 'last_computed_at',
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '交付方式',
      dataIndex: 'delivery_methods',
      width: 200,
      render: (methods: string[] | null) =>
        methods?.length ? (
          <Space size={4} wrap>
            {methods.map((method) => (
              <Tag key={method}>{describeDeliveryMethod(method)}</Tag>
            ))}
          </Space>
        ) : (
          <Text type="secondary">未配置</Text>
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_: unknown, record) => (
        <Button size="small" type="primary" ghost icon={<PlayCircleOutlined />} onClick={() => setExecuteContract(record)}>
          执行
        </Button>
      ),
    },
  ];

  const runColumns: ColumnsType<ComputeRun> = [
    {
      title: '运行',
      dataIndex: 'id',
      width: 130,
      render: (id: string) => <Text copyable={{ text: id }}>{id.slice(0, 8)}…</Text>,
    },
    {
      title: '合约',
      dataIndex: 'contract_id',
      width: 150,
      render: (id: string) => <a onClick={() => navigate(`/contracts/${id}`)}>{contractNo(id)}</a>,
    },
    {
      title: '触发方式',
      dataIndex: 'trigger',
      width: 100,
      render: (trigger: string | null) =>
        trigger === 'manual' ? <Tag color="blue">手动</Tag> : trigger === 'schedule' ? <Tag>定时</Tag> : <Text type="secondary">{trigger ?? '-'}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 160,
      render: (status: string) => (
        <Tag color={statusMeta(COMPUTE_RUN_STATUS_META, status).color}>{statusMeta(COMPUTE_RUN_STATUS_META, status).label}</Tag>
      ),
    },
    { title: '计划时间', dataIndex: 'scheduled_for', width: 170, render: (value: string | null) => formatDateTime(value) },
    { title: '开始', dataIndex: 'started_at', width: 170, render: (value: string | null) => formatDateTime(value) },
    { title: '结束', dataIndex: 'finished_at', width: 170, render: (value: string | null) => formatDateTime(value) },
    {
      title: '输出行数',
      dataIndex: 'output_rows',
      width: 100,
      render: (rows: number | null) => (typeof rows === 'number' ? rows : <Text type="secondary">-</Text>),
    },
    {
      title: '日志',
      key: 'logs',
      width: 90,
      render: (_: unknown, record) => (
        <Button size="small" type="text" icon={<FileSearchOutlined />} onClick={() => setLogRun(record as unknown as ComputeRunView)}>
          日志
        </Button>
      ),
    },
    {
      title: '取消',
      key: 'cancel',
      width: 90,
      render: (_: unknown, record) => {
        const cancellable = record.status === 'pending' || record.status === 'running';
        return (
          <Tooltip title={cancellable ? '取消排队/执行中的计算' : '仅排队中或执行中的运行可取消'}>
            <Popconfirm
              title="取消这次计算？"
              okText="确认取消"
              cancelText="返回"
              disabled={!cancellable}
              onConfirm={() =>
                cancelMutation.mutate(record.id, {
                  onSuccess: () => message.success('已请求取消'),
                  onError: (err) => message.error(describeFlowError(err, '取消失败')),
                })
              }
            >
              <Button size="small" danger type="text" icon={<StopOutlined />} disabled={!cancellable}>
                取消
              </Button>
            </Popconfirm>
          </Tooltip>
        );
      },
    },
  ];

  const scheduleItems = scheduleQuery.data?.items ?? [];

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Title level={4} style={{ margin: 0 }}>
            计算调度
          </Title>
          <Text type="secondary">按合约查看自动计算排程与运行记录；下一次计算时间以后端下发的 next_compute_at 为准。</Text>
        </div>
        <Space wrap>
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 260 }}
            placeholder="全部合约"
            value={contractId ?? undefined}
            onChange={(value?: string) => setContractId(value ?? null)}
            options={(contracts ?? []).map((contract) => ({ label: `${contract.title}（${contract.contract_no}）`, value: contract.id }))}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              void scheduleQuery.refetch();
              void runsQuery.refetch();
            }}
          >
            刷新
          </Button>
        </Space>
      </div>

      <Card size="small" title="调度表" style={{ marginBottom: 16 }}>
        {scheduleQuery.isError && (
          <QueryErrorAlert
            error={scheduleQuery.error}
            message="调度表加载失败"
            onRetry={() => {
              void scheduleQuery.refetch();
            }}
          />
        )}
        <Table<ComputeScheduleItem>
          rowKey="contract_id"
          size="small"
          columns={scheduleColumns}
          dataSource={scheduleItems}
          loading={scheduleQuery.isLoading}
          pagination={false}
          locale={tableEmpty('还没有配置自动计算的合约')}
        />
      </Card>

      <Card
        size="small"
        title={
          <Space>
            <span>运行记录</span>
            <Text type="secondary" style={{ fontSize: 12 }}>
              每 {RUN_POLL_INTERVAL_MS / 1000} 秒自动刷新
            </Text>
          </Space>
        }
      >
        {runsQuery.isError && (
          <QueryErrorAlert
            error={runsQuery.error}
            message="运行记录加载失败"
            onRetry={() => {
              void runsQuery.refetch();
            }}
          />
        )}
        <Table<ComputeRun>
          rowKey="id"
          size="small"
          columns={runColumns}
          dataSource={runsQuery.data?.items ?? []}
          loading={runsQuery.isLoading}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          locale={tableEmpty('还没有计算运行记录')}
          scroll={{ x: 1320 }}
          expandable={{ expandedRowRender: (record) => <RunDetail run={record} /> }}
        />
      </Card>

      {executeContract && (
        <ExecuteModal
          contract={executeContract}
          onClose={() => setExecuteContract(null)}
          onDone={(run) => {
            if (run.status === 'skipped_overlap') message.warning('上一轮计算尚未结束，本次已跳过');
            else if (run.status === 'succeeded') message.success(`执行完成（${run.id.slice(0, 8)}…）`);
            else message.warning(`执行已结束，状态 ${run.status}`);
            setLogRun(run);
          }}
        />
      )}
      {logRun && <RunLogDrawer run={logRun} onClose={() => setLogRun(null)} />}
    </div>
  );
}
