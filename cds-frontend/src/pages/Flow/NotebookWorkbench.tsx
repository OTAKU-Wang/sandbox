import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  Modal,
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
  DeleteOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RocketOutlined,
  SaveOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import Editor from '@monaco-editor/react';
import './monacoSetup';
import { RuntimeStatusBanner } from './RuntimeStatusBanner';
import {
  canonicalizeNotebookContent,
  defaultNotebookPath,
  describeFlowError,
  parseNotebookContent,
  serializeNotebookContent,
  useCreateNotebook,
  useCreateWorkload,
  useDeleteNotebook,
  useExecuteCell,
  useJupyterStatus,
  useNotebook,
  useNotebooks,
  useRunNotebook,
  useStartDevSession,
  useUpdateNotebook,
} from '../../services/flow';
import type {
  CellExecuteResult,
  Notebook,
  NotebookDevSession,
  ParsedNotebook,
} from '../../services/flow';
import { CenteredLoading, QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import { backendFromCellResult, backendFromNotebook, toOutputPane } from './display';
import type { BackendView } from './display';
import { useContractOptions } from './useContractOptions';

const { Text, Title } = Typography;

interface CellState {
  running: boolean;
  result: CellExecuteResult | null;
}

const blankCellStates = (count: number): CellState[] =>
  Array.from({ length: Math.max(1, count) }, () => ({ running: false, result: null }));

function BackendStrip({ view, source }: { view: BackendView | null; source: string }) {
  if (!view) {
    return (
      <Alert
        type="info"
        showIcon
        icon={<ThunderboltOutlined />}
        message={
          <Space size={8} wrap>
            <Text strong>执行后端：尚未执行</Text>
            <Text type="secondary">运行任一单元格后，此处常驻显示实际后端（cds_kernel=有状态 / cds_stateless=降级）。</Text>
          </Space>
        }
        style={{ marginBottom: 12 }}
      />
    );
  }

  if (!view.known) {
    return (
      <Alert
        type="warning"
        showIcon
        message={
          <Space size={8} wrap>
            <Text strong>执行后端：{view.backend}</Text>
            <Text type="secondary">后端返回了未登记的后端标识，按原值展示，不推断其是否有状态。</Text>
          </Space>
        }
        style={{ marginBottom: 12 }}
      />
    );
  }

  if (!view.degraded) {
    return (
      <Alert
        type="success"
        showIcon
        message={
          <Space size={8} wrap>
            <Text strong>执行后端：cds_kernel（有状态）</Text>
            <Tag color="green">变量跨单元格保留</Tag>
            <Text type="secondary">来源：{source}</Text>
          </Space>
        }
        style={{ marginBottom: 12 }}
      />
    );
  }

  return (
    <Alert
      type="warning"
      showIcon
      message={
        <Space size={8} wrap>
          <Text strong>执行后端：cds_stateless（已降级）</Text>
          <Tag color="orange">无状态执行：变量与导入不跨单元格保留</Tag>
          <Text type="secondary">来源：{source}</Text>
        </Space>
      }
      description={
        <Space direction="vertical" size={2}>
          {view.reasons.map((reason) => (
            <Text key={reason} style={{ fontSize: 12 }}>
              降级原因：{reason}
            </Text>
          ))}
        </Space>
      }
      style={{ marginBottom: 12 }}
    />
  );
}

function OutputPane({ result }: { result: CellExecuteResult | null }) {
  const pane = toOutputPane(result);
  if (!pane) return null;

  return (
    <div style={{ marginTop: 8 }}>
      <Space size={4} wrap style={{ marginBottom: 6 }}>
        <Tag color={result?.ok ? 'green' : 'red'}>{result?.ok ? '执行成功' : '执行失败'}</Tag>
        <Tag color={result?.backend === 'cds_kernel' ? 'green' : 'orange'}>
          {result?.backend === 'cds_kernel'
            ? 'cds_kernel 有状态'
            : `${result?.backend ?? '未知后端'}${result?.stateful ? '' : ' 无状态'}`}
        </Tag>
        {typeof pane.exitCode === 'number' && <Tag>退出码 {pane.exitCode}</Tag>}
        {result?.kernel_error ? <Tag color="volcano">内核错误</Tag> : null}
      </Space>

      {pane.blocked && (
        <Alert
          type="error"
          showIcon
          message="输出已被输出管控策略阻断"
          description={pane.blockedReason ?? '后端未返回阻断原因（blocked_reason 为空）'}
          style={{ marginBottom: 8 }}
        />
      )}

      {result?.degraded_reason && (
        <Alert type="warning" showIcon message={`降级原因：${result.degraded_reason}`} style={{ marginBottom: 8 }} />
      )}

      {result?.kernel_error && (
        <Alert type="error" showIcon message="内核错误" description={result.kernel_error} style={{ marginBottom: 8 }} />
      )}

      {pane.repr && (
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            返回值 repr
          </Text>
          <pre className="cds-json-block">{pane.repr}</pre>
        </div>
      )}

      {pane.stdout && (
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            标准输出 stdout
          </Text>
          <pre className="cds-json-block">{pane.stdout}</pre>
        </div>
      )}

      {pane.stderr && (
        <div>
          <Text type="danger" style={{ fontSize: 12 }}>
            错误输出 stderr
          </Text>
          <pre className="cds-json-block" style={{ background: '#fff2f0', borderColor: '#ffccc7', color: '#820014' }}>
            {pane.stderr}
          </pre>
        </div>
      )}

      {!pane.stdout && !pane.stderr && !pane.repr && !pane.blocked && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          本次执行没有输出内容
        </Text>
      )}
    </div>
  );
}

function JupyterPanel({ notebookId }: { notebookId: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useJupyterStatus(notebookId);

  if (isLoading) return <CenteredLoading tip="加载 Jupyter 可用性" />;
  if (isError) {
    return <QueryErrorAlert error={error} message="Jupyter 可用性查询失败" onRetry={() => { void refetch(); }} />;
  }
  if (!data) return null;

  return (
    <div style={{ marginBottom: 12 }}>
      {data.jupyter_available ? (
        <Alert
          type="success"
          showIcon
          message="Jupyter 原生界面可用"
          description={
            <Space direction="vertical" size={2}>
              <Text style={{ fontSize: 12 }}>服务路径：{data.jupyter_path ?? '后端未下发 jupyter_path'}</Text>
              <Text style={{ fontSize: 12 }}>
                代理入口：
                {data.proxy_enabled && data.url ? (
                  <a href={data.url} target="_blank" rel="noreferrer">
                    {data.url}
                  </a>
                ) : (
                  'proxy_enabled=false，未开放代理入口'
                )}
              </Text>
            </Space>
          }
        />
      ) : (
        <Alert
          type="warning"
          showIcon
          message="Jupyter 原生界面不可用"
          description={
            <Space direction="vertical" size={4}>
              <Text style={{ fontSize: 12 }}>
                后端如实返回 jupyter_available=false：当前环境未安装或未启用 Jupyter 服务，因此不提供 JupyterLab 直连入口。
              </Text>
              <Text style={{ fontSize: 12 }}>
                本页运行单元格不依赖 Jupyter：优先使用密态内核 cds_kernel；内核不可用时后端降级为 cds_stateless，并会在上方常驻提示。
              </Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                jupyter_path：{data.jupyter_path ?? 'null'} ｜ proxy_enabled：{String(data.proxy_enabled)} ｜ url：{data.url ?? 'null'}
              </Text>
            </Space>
          }
        />
      )}
      <Button
        size="small"
        icon={<ReloadOutlined />}
        loading={isFetching}
        style={{ marginTop: 8 }}
        onClick={() => {
          void refetch();
        }}
      >
        重新检测
      </Button>
    </div>
  );
}

function NotebookWorkspace({ notebook }: { notebook: Notebook }) {
  const navigate = useNavigate();
  const [parsed, setParsed] = useState<ParsedNotebook>(() => parseNotebookContent(notebook.content));
  const [cells, setCells] = useState<string[]>(() => (parsed.cells.length ? parsed.cells : ['']));
  const [cellStates, setCellStates] = useState<CellState[]>(() => blankCellStates(parsed.cells.length));
  const [savedContent, setSavedContent] = useState<string | null>(() => canonicalizeNotebookContent(notebook.content));
  const [persistOutput, setPersistOutput] = useState(true);
  const [lastBackend, setLastBackend] = useState<BackendView | null>(null);
  const [devSession, setDevSession] = useState<NotebookDevSession | null>(null);
  const [showJupyter, setShowJupyter] = useState(false);

  const saveMutation = useUpdateNotebook();
  const executeCellMutation = useExecuteCell();
  const runAllMutation = useRunNotebook();
  const devSessionMutation = useStartDevSession();
  const promoteMutation = useCreateWorkload();

  const language = (notebook.language || 'python').toLowerCase();
  const dirty = serializeNotebookContent(parsed, cells) !== (savedContent ?? '');
  const serverChanged = canonicalizeNotebookContent(notebook.content) !== (savedContent ?? '');
  const view = lastBackend ?? backendFromNotebook(notebook);

  const reloadFromServer = () => {
    const next = parseNotebookContent(notebook.content);
    setParsed(next);
    setCells(next.cells.length ? next.cells : ['']);
    setCellStates(blankCellStates(next.cells.length));
    setSavedContent(canonicalizeNotebookContent(notebook.content));
    message.success('已载入后端最新内容');
  };

  const save = () => {
    const content = serializeNotebookContent(parsed, cells);
    saveMutation.mutate(
      { id: notebook.id, payload: { content } },
      {
        onSuccess: (updated) => {
          setSavedContent(canonicalizeNotebookContent(updated.content ?? content));
          message.success(`已保存，当前版本 v${updated.version}`);
        },
        onError: (err) => message.error(describeFlowError(err, '保存失败')),
      },
    );
  };

  const runCell = (index: number) => {
    const code = cells[index] ?? '';
    if (!code.trim()) {
      message.warning(`第 ${index + 1} 个单元格为空`);
      return;
    }
    setCellStates((prev) => {
      const next = [...prev];
      while (next.length <= index) next.push({ running: false, result: null });
      next[index] = { ...next[index], running: true };
      return next;
    });
    executeCellMutation.mutate(
      { id: notebook.id, payload: { code, cell_index: index, persist: persistOutput } },
      {
        onSuccess: (result) => {
          setCellStates((prev) => {
            const next = [...prev];
            while (next.length <= index) next.push({ running: false, result: null });
            next[index] = { running: false, result };
            return next;
          });
          setLastBackend(backendFromCellResult(result));
          if (result.output_blocked) message.error('输出已被管控策略阻断');
          else if (!result.ok) message.warning(`第 ${index + 1} 个单元格执行失败（退出码 ${result.exit_code}）`);
          else message.success(`第 ${index + 1} 个单元格执行完成（${result.backend}）`);
        },
        onError: (err) => {
          setCellStates((prev) => {
            const next = [...prev];
            while (next.length <= index) next.push({ running: false, result: null });
            next[index] = { ...next[index], running: false };
            return next;
          });
          message.error(describeFlowError(err, '单元格执行失败'));
        },
      },
    );
  };

  const runAll = () => {
    runAllMutation.mutate(notebook.id, {
      onSuccess: (result) => {
        const byIndex = new Map<number, CellExecuteResult>();
        (result.results ?? []).forEach((item) => byIndex.set(item.cell_index, item));
        setCellStates((prev) =>
          Array.from({ length: Math.max(prev.length, byIndex.size, cells.length) }, (_, index) => ({
            running: false,
            result: byIndex.get(index) ?? prev[index]?.result ?? null,
          })),
        );
        const results = result.results ?? [];
        const last = results[results.length - 1];
        if (last) setLastBackend(backendFromCellResult(last));
        if (result.passed) message.success('全部单元格执行通过');
        else message.warning('存在未通过的单元格，请查看输出');
      },
      onError: (err) => message.error(describeFlowError(err, '运行全部失败')),
    });
  };

  const addCell = () => {
    setCells((prev) => [...prev, '']);
    setCellStates((prev) => [...prev, { running: false, result: null }]);
  };

  const removeCell = (index: number) => {
    if (cells.length <= 1) {
      message.warning('至少保留一个单元格');
      return;
    }
    setCells((prev) => prev.filter((_, cellIndex) => cellIndex !== index));
    setCellStates((prev) => prev.filter((_, cellIndex) => cellIndex !== index));
  };

  return (
    <Card
      size="small"
      title={
        <Space wrap>
          <Text strong>{notebook.title}</Text>
          <Tag>v{notebook.version}</Tag>
          {notebook.status ? <Tag>{notebook.status}</Tag> : null}
          {dirty && <Badge status="processing" text="有未保存修改" />}
        </Space>
      }
      extra={
        <Space wrap>
          <Tooltip title="执行时把单元格输出写回笔记内容（persist）">
            <Checkbox checked={persistOutput} onChange={(event) => setPersistOutput(event.target.checked)}>
              输出写入笔记
            </Checkbox>
          </Tooltip>
          <Button
            size="small"
            icon={<RocketOutlined />}
            loading={devSessionMutation.isPending}
            onClick={() =>
              devSessionMutation.mutate(notebook.id, {
                onSuccess: (session) => {
                  setDevSession(session);
                  message.success(session.created ? '测试沙箱已创建' : '已复用现有测试沙箱');
                },
                onError: (err) => message.error(describeFlowError(err, '启动测试沙箱失败')),
              })
            }
          >
            启动测试沙箱
          </Button>
          <Button size="small" icon={<ExperimentOutlined />} onClick={() => setShowJupyter((open) => !open)}>
            Jupyter 可用性
          </Button>
          <Button size="small" icon={<SaveOutlined />} disabled={!dirty} loading={saveMutation.isPending} onClick={save}>
            保存
          </Button>
          <Button size="small" type="primary" icon={<PlayCircleOutlined />} loading={runAllMutation.isPending} onClick={runAll}>
            运行全部
          </Button>
          <Button
            size="small"
            icon={<DeploymentUnitOutlined />}
            loading={promoteMutation.isPending}
            onClick={() =>
              promoteMutation.mutate(
                {
                  name: `${notebook.title} 负载`,
                  notebook_id: notebook.id,
                  contract_id: notebook.contract_id ?? undefined,
                },
                {
                  onSuccess: (workload) =>
                    Modal.confirm({
                      title: '已提升为负载',
                      content: `负载「${workload.name}」已按当前 Notebook v${notebook.version} 的单元格内容生成入口指纹${
                        workload.contract_id ? '' : '（未关联合约，发布前需在负载中心补齐）'
                      }。到「负载中心」发起测试，测试通过后才能发布到生产沙箱。`,
                      okText: '前往负载中心',
                      cancelText: '继续开发',
                      onOk: () => navigate('/workloads'),
                    }),
                  onError: (err) => message.error(describeFlowError(err, '提升为负载失败')),
                },
              )
            }
          >
            提升为负载
          </Button>
        </Space>
      }
    >
      <BackendStrip view={view} source={lastBackend ? '本次执行结果' : '笔记记录（尚未在本页执行）'} />

      {showJupyter && <JupyterPanel notebookId={notebook.id} />}

      {serverChanged && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="后端笔记内容已更新（可能由带输出的执行或其他会话保存引起）"
          description={
            <Space>
              <Text type="secondary" style={{ fontSize: 12 }}>
                为避免覆盖您的编辑，编辑器仍显示本地内容。
              </Text>
              <Button size="small" onClick={reloadFromServer}>
                载入最新内容
              </Button>
            </Space>
          }
        />
      )}

      {devSession && (
        <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }} title="测试沙箱">
          <Descriptions.Item label="环境">
            <Tag color="blue">{devSession.env || '未下发'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="沙箱级别">{devSession.sandbox_level || '-'}</Descriptions.Item>
          <Descriptions.Item label="沙箱模式">{devSession.sandbox_mode || '-'}</Descriptions.Item>
          <Descriptions.Item label="会话">
            {devSession.created ? '本次新建' : '复用已有'}{' '}
            <a onClick={() => navigate(`/sandbox-sessions/${devSession.session_id}`)}>
              {devSession.session_id.slice(0, 12)}…
            </a>
          </Descriptions.Item>
        </Descriptions>
      )}

      <Divider style={{ margin: '4px 0 12px' }} />

      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {cells.map((code, index) => {
          const state = cellStates[index] ?? { running: false, result: null };
          return (
            <Card
              key={index}
              size="small"
              type="inner"
              title={
                <Space>
                  <Text type="secondary">单元格 {index + 1}</Text>
                  {state.result ? (
                    <Tag color={state.result.backend === 'cds_kernel' ? 'green' : 'orange'}>{state.result.backend}</Tag>
                  ) : null}
                </Space>
              }
              extra={
                <Space size={4}>
                  <Button size="small" icon={<PlayCircleOutlined />} loading={state.running} onClick={() => runCell(index)}>
                    运行单元格
                  </Button>
                  <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeCell(index)} />
                </Space>
              }
            >
              <div style={{ border: '1px solid #dfe6ee', borderRadius: 6, overflow: 'hidden' }}>
                <Editor
                  height="160px"
                  language={language}
                  theme="vs"
                  value={code}
                  onChange={(value) => setCells((prev) => prev.map((cell, cellIndex) => (cellIndex === index ? (value ?? '') : cell)))}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbersMinChars: 3,
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
                    tabSize: 4,
                    wordWrap: 'on',
                  }}
                />
              </div>
              <OutputPane result={state.result} />
            </Card>
          );
        })}
      </Space>

      <Button size="small" icon={<PlusOutlined />} style={{ marginTop: 12 }} onClick={addCell}>
        新增单元格
      </Button>
    </Card>
  );
}

function CreateNotebookModal({
  open,
  onClose,
  contractId,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  contractId: string | null;
  onCreated: (notebook: Notebook) => void;
}) {
  const [form] = Form.useForm();
  const { data: contractOptions } = useContractOptions();
  const createMutation = useCreateNotebook();

  const submit = () => {
    void form
      .validateFields()
      .then((values: { title: string; path?: string; contract_id?: string }) => {
        createMutation.mutate(
          {
            title: values.title.trim(),
            path: values.path?.trim() || defaultNotebookPath(values.title),
            contract_id: values.contract_id || contractId || null,
            content: '# %%\nprint("hello cds")',
          },
          {
            onSuccess: (notebook) => {
              message.success(`Notebook 已创建：${notebook.title}`);
              form.resetFields();
              onCreated(notebook);
              onClose();
            },
            onError: (err) => message.error(describeFlowError(err, '创建 Notebook 失败')),
          },
        );
      })
      .catch(() => undefined);
  };

  return (
    <Modal title="新建 Notebook" open={open} onCancel={onClose} onOk={submit} confirmLoading={createMutation.isPending} okText="创建" width={560}>
      <Form form={form} layout="vertical">
        <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
          <Input placeholder="例如：客户流失特征探索" maxLength={120} />
        </Form.Item>
        <Form.Item name="path" label="沙箱内路径" tooltip="留空按标题自动生成 /notebooks/<slug>.ipynb">
          <Input placeholder="/notebooks/churn-explore.ipynb" />
        </Form.Item>
        <Form.Item name="contract_id" label="关联合约（可选）">
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="不关联合约"
            options={(contractOptions ?? []).map((contract) => ({
              label: `${contract.title}（${contract.contract_no}）`,
              value: contract.id,
            }))}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export default function NotebookWorkbench() {
  const [contractId, setContractId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const { data: contractOptions } = useContractOptions();
  const listQuery = useNotebooks(contractId);
  const detail = useNotebook(selectedId);
  const deleteMutation = useDeleteNotebook();

  const columns: ColumnsType<Notebook> = [
    {
      title: '名称',
      dataIndex: 'title',
      render: (title: string, record) => (
        <Space direction="vertical" size={0}>
          <a onClick={() => setSelectedId(record.id)}>{title}</a>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.path}
          </Text>
        </Space>
      ),
    },
    { title: '版本', dataIndex: 'version', width: 60, render: (version: number) => `v${version}` },
    {
      title: '后端',
      dataIndex: 'execution_backend',
      width: 128,
      render: (backend: string | null) =>
        backend ? (
          <Tag color={backend === 'cds_kernel' ? 'green' : 'orange'}>{backend}</Tag>
        ) : (
          <Text type="secondary">未记录</Text>
        ),
    },
  ];

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Title level={4} style={{ margin: 0 }}>
            Notebook 开发
          </Title>
          <Text type="secondary">在密态沙箱内逐单元格试算：执行后端与降级原因常驻显示，输出受管控策略约束，结果一律以后端返回为准。</Text>
        </div>
        <Space>
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 260 }}
            placeholder="全部合约"
            value={contractId ?? undefined}
            onChange={(value?: string) => setContractId(value ?? null)}
            options={(contractOptions ?? []).map((contract) => ({
              label: `${contract.title}（${contract.contract_no}）`,
              value: contract.id,
            }))}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              void listQuery.refetch();
            }}
          >
            刷新
          </Button>
        </Space>
      </div>

      <RuntimeStatusBanner />

      <Row gutter={16}>
        <Col xs={24} lg={8} style={{ marginBottom: 16 }}>
          <Card
            size="small"
            title="Notebook 列表"
            extra={
              <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                新建
              </Button>
            }
          >
            {listQuery.isError && (
              <QueryErrorAlert
                error={listQuery.error}
                message="Notebook 列表加载失败"
                onRetry={() => {
                  void listQuery.refetch();
                }}
              />
            )}
            <Table<Notebook>
              rowKey="id"
              size="small"
              columns={columns}
              dataSource={listQuery.data?.items ?? []}
              loading={listQuery.isLoading}
              pagination={false}
              locale={tableEmpty('还没有 Notebook，先新建一个')}
              onRow={(record) => (record.id === selectedId ? { style: { background: '#e6f4ff' } } : {})}
            />
          </Card>
          {selectedId && (
            <Button
              size="small"
              danger
              icon={<DeleteOutlined />}
              style={{ marginTop: 8 }}
              loading={deleteMutation.isPending}
              onClick={() =>
                deleteMutation.mutate(selectedId, {
                  onSuccess: () => {
                    message.success('Notebook 已归档');
                    setSelectedId(null);
                  },
                  onError: (err) => message.error(describeFlowError(err, '归档失败')),
                })
              }
            >
              归档当前 Notebook
            </Button>
          )}
        </Col>

        <Col xs={24} lg={16}>
          {!selectedId && (
            <Card size="small">
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="从左侧选择一个 Notebook 开始开发" />
            </Card>
          )}
          {selectedId && detail.isLoading && <CenteredLoading tip="加载 Notebook" />}
          {selectedId && detail.isError && (
            <QueryErrorAlert
              error={detail.error}
              message="Notebook 加载失败"
              onRetry={() => {
                void detail.refetch();
              }}
            />
          )}
          {selectedId && detail.data && <NotebookWorkspace key={detail.data.id} notebook={detail.data} />}
        </Col>
      </Row>

      <CreateNotebookModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        contractId={contractId}
        onCreated={(notebook) => {
          setSelectedId(notebook.id);
        }}
      />
    </div>
  );
}
