import { useState } from 'react';
import { Alert, Button, Card, Popconfirm, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { PlayCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
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
  useTriggerComputeRun,
} from '../../services/flow';
import type { ComputeRun, ComputeScheduleItem } from '../../services/flow';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import { COMPUTE_RUN_STATUS_META, statusMeta } from './display';
import { useContractOptions } from './useContractOptions';

const { Text, Title } = Typography;

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

export default function ComputeSchedule() {
  const navigate = useNavigate();
  const [contractId, setContractId] = useState<string | null>(null);
  const { data: contracts } = useContractOptions();
  const scheduleQuery = useComputeSchedule(contractId);
  const runsQuery = useComputeRuns({ contractId }, RUN_POLL_INTERVAL_MS);
  const triggerMutation = useTriggerComputeRun();
  const cancelMutation = useCancelComputeRun();

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
        <Popconfirm
          title="立即触发一次计算？"
          description="将在该合约绑定的沙箱内执行工作负载，上一轮未结束时本次会被跳过。"
          okText="触发"
          cancelText="取消"
          onConfirm={() =>
            triggerMutation.mutate(record.contract_id, {
              onSuccess: (run) => {
                if (run.status === 'skipped_overlap') message.warning('上一轮计算尚未结束，本次已跳过');
                else message.success(`已触发计算（${run.id.slice(0, 8)}…，状态 ${run.status}）`);
              },
              onError: (err) => message.error(describeFlowError(err, '手动触发失败')),
            })
          }
        >
          <Button size="small" icon={<PlayCircleOutlined />} loading={triggerMutation.isPending && triggerMutation.variables === record.contract_id}>
            手动触发
          </Button>
        </Popconfirm>
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
          scroll={{ x: 1200 }}
          expandable={{ expandedRowRender: (record) => <RunDetail run={record} /> }}
        />
      </Card>
    </div>
  );
}
