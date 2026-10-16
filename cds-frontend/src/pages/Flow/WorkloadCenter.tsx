import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudUploadOutlined,
  HistoryOutlined,
  RollbackOutlined,
  StopOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import {
  describeFlowError,
  formatDateTime,
  parseFlowError,
  useCreateWorkload,
  useNotebooks,
  usePublishWorkload,
  useRetireWorkload,
  useRollbackWorkload,
  useRunWorkloadTest,
  useWorkloadTestRuns,
  useWorkloads,
} from '../../services/flow';
import type { Workload, WorkloadTestCheck, WorkloadTestRun } from '../../services/flow';
import { RuntimeStatusBanner } from './RuntimeStatusBanner';
import { CenteredLoading, QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import {
  COMPUTE_RUN_STATUS_META,
  WORKLOAD_ENV_META,
  WORKLOAD_STATUS_META,
  checkSummary,
  evaluatePublishGate,
  publishGateReason,
  statusMeta,
} from './display';
import { useContractOptions } from './useContractOptions';

const { Text, Title } = Typography;

function CheckList({ checks }: { checks: WorkloadTestCheck[] }) {
  if (!checks.length) {
    return (
      <Text type="secondary" style={{ fontSize: 12 }}>
        后端未返回任何检查项
      </Text>
    );
  }
  return (
    <Table<WorkloadTestCheck>
      rowKey={(record) => `${record.name}-${record.passed ? 1 : 0}`}
      size="small"
      pagination={false}
      dataSource={checks}
      columns={[
        {
          title: '检查项',
          dataIndex: 'name',
          width: 220,
          render: (name: string, record) => (
            <Space size={6}>
              {record.passed ? (
                <CheckCircleOutlined style={{ color: '#52c41a' }} />
              ) : (
                <CloseCircleOutlined style={{ color: '#cf1322' }} />
              )}
              <Text>{name}</Text>
            </Space>
          ),
        },
        {
          title: '结果',
          dataIndex: 'passed',
          width: 90,
          render: (passed: boolean) => <Tag color={passed ? 'green' : 'red'}>{passed ? '通过' : '未通过'}</Tag>,
        },
        {
          title: '说明',
          dataIndex: 'detail',
          render: (detail: string | null) => detail || <Text type="secondary">后端未返回说明</Text>,
        },
      ]}
    />
  );
}

function TestRunDetail({ run, workloadName }: { run: WorkloadTestRun; workloadName: string }) {
  const summary = checkSummary(run);
  const verdict = run.passed === true ? 'pass' : run.passed === false ? 'fail' : 'unknown';

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Alert
        type={verdict === 'pass' ? 'success' : verdict === 'fail' ? 'error' : 'warning'}
        showIcon
        message={
          verdict === 'pass'
            ? `测试通过：${workloadName} 可发布到生产`
            : verdict === 'fail'
              ? `测试未通过：${workloadName} 暂不可发布`
              : `测试状态未确定（后端 status=${run.status}），以后端结果为准`
        }
        description={
          <Space size={8} wrap>
            <Text type="secondary" style={{ fontSize: 12 }}>
              测试编号 {run.test_run_id || '-'}
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              检查项 {summary.total} 项，未通过 {summary.failed.length} 项
            </Text>
            {run.session_id && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                测试会话 {run.session_id.slice(0, 12)}…
              </Text>
            )}
          </Space>
        }
      />
      <CheckList checks={run.checks} />
    </Space>
  );
}

function TestRunsDrawer({ workload, onClose }: { workload: Workload; onClose: () => void }) {
  const { data, isLoading, isError, error, refetch } = useWorkloadTestRuns(workload.id);
  const runs = data ?? [];
  const summary = checkSummary(runs[0]);

  return (
    <Drawer title={`测试记录 · ${workload.name}`} width={720} open onClose={onClose}>
      {isLoading && <CenteredLoading tip="加载测试记录" />}
      {isError && (
        <QueryErrorAlert
          error={error}
          message="测试记录加载失败"
          onRetry={() => {
            void refetch();
          }}
        />
      )}
      {!isLoading && !isError && !runs.length && (
        <Alert
          type="info"
          showIcon
          message="该负载还没有测试记录"
          description="生产发布受门禁约束：必须先发起一次测试并通过。"
        />
      )}
      {!isLoading && runs.length > 0 && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="负载状态">
              <Tag color={statusMeta(WORKLOAD_STATUS_META, workload.status).color}>
                {statusMeta(WORKLOAD_STATUS_META, workload.status).label}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="记录条数">
              {runs.length}（最近一次未通过 {summary.failed.length} 项）
            </Descriptions.Item>
          </Descriptions>
          {runs.map((run) => (
            <Card
              key={run.test_run_id || run.created_at}
              size="small"
              type="inner"
              title={
                <Space wrap>
                  <Text>{run.test_run_id ? `测试 ${run.test_run_id.slice(0, 12)}` : '测试记录'}</Text>
                  <Tag>{run.status}</Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {formatDateTime(run.created_at)}
                  </Text>
                </Space>
              }
            >
              <TestRunDetail run={run} workloadName={workload.name} />
            </Card>
          ))}
        </Space>
      )}
    </Drawer>
  );
}

function CreateWorkloadModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (workload: Workload) => void }) {
  const [form] = Form.useForm();
  const { data: contracts } = useContractOptions();
  const { data: notebooks } = useNotebooks(null);
  const createMutation = useCreateWorkload();

  const submit = () => {
    void form
      .validateFields()
      .then((values: { name: string; contract_id?: string; notebook_id?: string; entrypoint?: string; language?: string }) => {
        createMutation.mutate(
          {
            name: values.name.trim(),
            contract_id: values.contract_id || null,
            notebook_id: values.notebook_id || null,
            entrypoint: values.entrypoint?.trim() || undefined,
            language: values.language?.trim() || undefined,
          },
          {
            onSuccess: (workload) => {
              message.success(`负载已创建：${workload.name} v${workload.version}`);
              form.resetFields();
              onCreated(workload);
              onClose();
            },
            onError: (err) => message.error(describeFlowError(err, '创建负载失败')),
          },
        );
      })
      .catch(() => undefined);
  };

  return (
    <Modal title="从 Notebook 创建工作负载" open={open} onCancel={onClose} onOk={submit} confirmLoading={createMutation.isPending} okText="创建" width={560}>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="负载名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="例如：流失评分日计算" maxLength={120} />
        </Form.Item>
        <Form.Item name="contract_id" label="关联合约" tooltip="测试环境负载可先不挂合约，发布前必须绑定合约">
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="暂不关联"
            options={(contracts ?? []).map((contract) => ({ label: `${contract.title}（${contract.contract_no}）`, value: contract.id }))}
          />
        </Form.Item>
        <Form.Item name="notebook_id" label="来源 Notebook" tooltip="后端据此计算入口指纹 entrypoint_hash，测试通过后内容再改动会判定为哈希不一致">
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="选择已开发的 Notebook"
            options={(notebooks?.items ?? []).map((notebook) => ({ label: `${notebook.title}（v${notebook.version}）`, value: notebook.id }))}
          />
        </Form.Item>
        <Form.Item name="entrypoint" label="入口代码" tooltip="留空则取 Notebook 全部单元格">
          <Input.TextArea rows={3} placeholder="print('ok')" />
        </Form.Item>
        <Form.Item name="language" label="语言">
          <Input placeholder="python" maxLength={32} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default function WorkloadCenter() {
  const navigate = useNavigate();
  const [contractId, setContractId] = useState<string | null>(null);
  const [envFilter, setEnvFilter] = useState<'test' | 'production' | null>(null);
  const [drawerWorkload, setDrawerWorkload] = useState<Workload | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [latestRun, setLatestRun] = useState<WorkloadTestRun | null>(null);
  const [latestWorkloadId, setLatestWorkloadId] = useState<string | null>(null);
  const [gateError, setGateError] = useState<{ workloadId: string; text: string } | null>(null);

  const { data: contracts } = useContractOptions();
  const listQuery = useWorkloads({ contractId, env: envFilter ?? undefined });
  const testMutation = useRunWorkloadTest();
  const publishMutation = usePublishWorkload();
  const retireMutation = useRetireWorkload();
  const rollbackMutation = useRollbackWorkload();

  const runsForWorkload = (workload: Workload) => (latestRun && latestWorkloadId === workload.id ? latestRun : null);

  const runTest = (workload: Workload) => {
    setGateError(null);
    testMutation.mutate(workload.id, {
      onSuccess: (run) => {
        setLatestRun(run);
        setLatestWorkloadId(workload.id);
        setDrawerWorkload(workload);
        if (run.passed === true) message.success(`测试通过：${workload.name}`);
        else if (run.passed === false) message.warning('测试未通过，请查看检查项');
        else message.info(`测试结束，状态 ${run.status}`);
      },
      onError: (err) => message.error(describeFlowError(err, '发起测试失败')),
    });
  };

  const publish = (workload: Workload) => {
    setGateError(null);
    publishMutation.mutate(workload.id, {
      onSuccess: (updated) => {
        message.success(`已发布为生产版本 v${updated.version}`);
      },
      onError: (err) => {
        const info = parseFlowError(err);
        const text =
          info.httpStatus === 412 || info.code
            ? `${publishGateReason(info.code)}${info.detail ? `（后端说明：${info.detail}）` : ''}`
            : describeFlowError(err, '发布失败');
        setGateError({ workloadId: workload.id, text });
        message.error(text);
      },
    });
  };

  const columns: ColumnsType<Workload> = [
    {
      title: '负载',
      dataIndex: 'name',
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <Space size={6}>
            <Text strong>{name}</Text>
            <Tag>v{record.version}</Tag>
            {record.is_latest ? <Tag color="blue">最新</Tag> : <Tag>历史版本</Tag>}
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            入口指纹 {record.entrypoint_hash ? `${record.entrypoint_hash.slice(0, 12)}…` : '未计算'}
          </Text>
        </Space>
      ),
    },
    {
      title: '环境',
      dataIndex: 'env',
      width: 100,
      render: (env: string) => (
        <Tag color={statusMeta(WORKLOAD_ENV_META, env).color}>{statusMeta(WORKLOAD_ENV_META, env).label}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (status: string) => (
        <Tag color={statusMeta(WORKLOAD_STATUS_META, status).color}>{statusMeta(WORKLOAD_STATUS_META, status).label}</Tag>
      ),
    },
    {
      title: '关联合约',
      dataIndex: 'contract_id',
      width: 160,
      render: (contract_id: string | null) => {
        const hit = (contracts ?? []).find((contract) => contract.id === contract_id);
        return hit ? (
          <a onClick={() => navigate(`/contracts/${hit.id}`)}>{hit.contract_no}</a>
        ) : contract_id ? (
          <Text type="secondary">{contract_id.slice(0, 8)}…</Text>
        ) : (
          <Text type="secondary">未关联</Text>
        );
      },
    },
    {
      title: '测试',
      dataIndex: 'test_run_id',
      width: 130,
      render: (testRunId: string | null, record) => (
        <Button
          size="small"
          type="link"
          icon={<HistoryOutlined />}
          onClick={() => {
            setDrawerWorkload(record);
          }}
        >
          {testRunId ? '查看测试记录' : '无测试记录'}
        </Button>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 300,
      render: (_: unknown, record) => {
        const gate = evaluatePublishGate(record, runsForWorkload(record));
        const isTesting = testMutation.isPending && testMutation.variables === record.id;
        return (
          <Space wrap size={4}>
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              loading={isTesting}
              disabled={record.status === 'retired'}
              onClick={() => runTest(record)}
            >
              发起测试
            </Button>
            <Tooltip title={gate.canPublish ? '测试已通过，可发布为生产版本' : gate.reason}>
              <Button
                size="small"
                type="primary"
                icon={<CloudUploadOutlined />}
                disabled={!gate.canPublish || (publishMutation.isPending && publishMutation.variables === record.id)}
                loading={publishMutation.isPending && publishMutation.variables === record.id}
                onClick={() => publish(record)}
              >
                发布
              </Button>
            </Tooltip>
            <Popconfirm
              title="回滚到上一个生产版本？"
              description="当前生产版本会被停用，回滚不修改任何审计记录。"
              okText="确认回滚"
              cancelText="取消"
              onConfirm={() =>
                rollbackMutation.mutate(record.id, {
                  onSuccess: () => message.success('已回滚到上一个生产版本'),
                  onError: (err) => message.error(describeFlowError(err, '回滚失败')),
                })
              }
            >
              <Button size="small" icon={<RollbackOutlined />} disabled={record.env !== 'production'}>
                回滚
              </Button>
            </Popconfirm>
            <Popconfirm
              title="退役该负载？"
              description="退役后不可再测试或发布，历史版本仍保留。"
              okText="确认退役"
              cancelText="取消"
              onConfirm={() =>
                retireMutation.mutate(record.id, {
                  onSuccess: () => message.success('负载已退役'),
                  onError: (err) => message.error(describeFlowError(err, '退役失败')),
                })
              }
            >
              <Button size="small" danger icon={<StopOutlined />} disabled={record.status === 'retired'}>
                退役
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const items = listQuery.data?.items ?? [];
  const publishingError = gateError ? items.find((item) => item.id === gateError.workloadId) : null;

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Title level={4} style={{ margin: 0 }}>
            负载测试与发布
          </Title>
          <Text type="secondary">
            门禁：测试环境负载必须先通过测试才能发布为生产版本；发布被拒时后端返回 412 与错误码，此处按人话披露。
          </Text>
        </div>
        <Space wrap>
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 240 }}
            placeholder="全部合约"
            value={contractId ?? undefined}
            onChange={(value?: string) => setContractId(value ?? null)}
            options={(contracts ?? []).map((contract) => ({ label: `${contract.title}（${contract.contract_no}）`, value: contract.id }))}
          />
          <Select
            allowClear
            style={{ width: 140 }}
            placeholder="全部环境"
            value={envFilter ?? undefined}
            onChange={(value?: 'test' | 'production') => setEnvFilter(value ?? null)}
            options={[
              { label: '测试环境', value: 'test' },
              { label: '生产环境', value: 'production' },
            ]}
          />
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建负载
          </Button>
        </Space>
      </div>

      <RuntimeStatusBanner />

      {gateError && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={`发布被拒绝：${publishingError?.name ?? gateError.workloadId}`}
          description={gateError.text}
          onClose={() => setGateError(null)}
        />
      )}

      <Card size="small">
        {listQuery.isError && (
          <QueryErrorAlert
            error={listQuery.error}
            message="负载列表加载失败"
            onRetry={() => {
              void listQuery.refetch();
            }}
          />
        )}
        <Table<Workload>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={items}
          loading={listQuery.isLoading}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          locale={tableEmpty('还没有工作负载，先从 Notebook 创建一个')}
          expandable={{
            expandedRowRender: (record) => (
              <Row gutter={16}>
                <Col span={16}>
                  <Descriptions size="small" column={2} title="版本与来源">
                    <Descriptions.Item label="负载 ID">{record.id}</Descriptions.Item>
                    <Descriptions.Item label="来源 Notebook">
                      {record.source_notebook_id ? record.source_notebook_id.slice(0, 12) + '…' : '未记录'}
                    </Descriptions.Item>
                    <Descriptions.Item label="语言">{record.language || '-'}</Descriptions.Item>
                    <Descriptions.Item label="入口指纹">{record.entrypoint_hash || '未计算'}</Descriptions.Item>
                    <Descriptions.Item label="最近测试编号">{record.test_run_id || '无'}</Descriptions.Item>
                    <Descriptions.Item label="发布时间">{record.published_at ? new Date(record.published_at).toLocaleString('zh-CN', { hour12: false }) : '未发布'}</Descriptions.Item>
                  </Descriptions>
                </Col>
                <Col span={8}>
                  <Space direction="vertical" size={4}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      发布门禁
                    </Text>
                    {(() => {
                      const gate = evaluatePublishGate(record, runsForWorkload(record));
                      return (
                        <>
                          <Badge
                            status={gate.canPublish ? 'success' : 'default'}
                            text={gate.canPublish ? '满足发布条件' : gate.reason ?? '暂不可发布'}
                          />
                          {gate.warning && (
                            <Text type="warning" style={{ fontSize: 12 }}>
                              {gate.warning}
                            </Text>
                          )}
                        </>
                      );
                    })()}
                  </Space>
                </Col>
              </Row>
            ),
          }}
        />
      </Card>

      <Card size="small" style={{ marginTop: 16 }} title="状态说明">
        <Space wrap size={4}>
          {Object.entries(WORKLOAD_STATUS_META).map(([key, meta]) => (
            <Tag key={key} color={meta.color}>
              {meta.label}
            </Tag>
          ))}
          <Text type="secondary" style={{ fontSize: 12 }}>
            运行状态另有：
            {Object.values(COMPUTE_RUN_STATUS_META)
              .map((meta) => meta.label)
              .join(' / ')}
          </Text>
        </Space>
      </Card>

      {drawerWorkload && <TestRunsDrawer workload={drawerWorkload} onClose={() => setDrawerWorkload(null)} />}

      <CreateWorkloadModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(workload) => {
          setLatestWorkloadId(workload.id);
        }}
      />
    </div>
  );
}
