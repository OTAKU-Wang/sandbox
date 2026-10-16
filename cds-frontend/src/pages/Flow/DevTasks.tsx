import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography, message } from 'antd';
import { PlusOutlined, ProjectOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { defaultNotebookPath, flowApi } from '../../services/flow';
import type { NotebookCreatePayload } from '../../services/flow';
import { CenteredLoading, QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import { useContractOptions } from './useContractOptions';
import { RuntimeStatusBanner } from './RuntimeStatusBanner';
import { buildDevTaskRows } from './devTasksModel';
import type { DevTaskRow } from './devTasksModel';

const KIND_LABEL: Record<DevTaskRow['kind'], string> = {
  notebook: '笔记本',
  workload: '负载',
  run: '执行',
};

function formatTime(value: string | null): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false });
}

interface CreateTaskForm {
  title: string;
  contract_id: string;
}

export default function DevTasks() {
  const navigate = useNavigate();
  const [contractId, setContractId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm<CreateTaskForm>();

  const contracts = useContractOptions();
  const contractLabel = useMemo(() => {
    const map = new Map((contracts.data ?? []).map((item) => [item.id, `${item.contract_no} ${item.title}`]));
    return (id: string | null) => (id ? (map.get(id) ?? id) : '—');
  }, [contracts.data]);

  const notebooks = useQuery({
    queryKey: ['flow', 'dev-tasks', 'notebooks'],
    queryFn: () => flowApi.listNotebooks({ page: 1, page_size: 50 }),
  });
  const workloads = useQuery({
    queryKey: ['flow', 'dev-tasks', 'workloads'],
    queryFn: () => flowApi.listWorkloads({ page: 1, page_size: 50 }),
  });
  const runs = useQuery({
    queryKey: ['flow', 'dev-tasks', 'runs'],
    queryFn: () => flowApi.listRuns({ page: 1, page_size: 50 }),
  });

  const createTask = useMutation({
    mutationFn: (values: CreateTaskForm) => {
      const payload: NotebookCreatePayload = {
        title: values.title.trim(),
        path: defaultNotebookPath(values.title),
        contract_id: values.contract_id,
      };
      return flowApi.createNotebook(payload);
    },
    onSuccess: async () => {
      setCreating(false);
      form.resetFields();
      message.success('开发任务已创建，正在打开 Notebook 开发环境');
      await Promise.all([notebooks.refetch(), workloads.refetch(), runs.refetch()]);
      navigate('/notebooks');
    },
  });

  const loading = notebooks.isPending || workloads.isPending || runs.isPending;
  const failed = notebooks.isError || workloads.isError || runs.isError;

  const rows = useMemo(() => {
    const all = buildDevTaskRows({
      notebooks: notebooks.data?.items ?? [],
      workloads: workloads.data?.items ?? [],
      runs: runs.data?.items ?? [],
    });
    return contractId ? all.filter((row) => row.contract_id === contractId) : all;
  }, [notebooks.data, workloads.data, runs.data, contractId]);

  const options = (contracts.data ?? []).map((item) => ({
    value: item.id,
    label: `${item.contract_no} ${item.title}`,
  }));

  const activeOptions = (contracts.data ?? [])
    .filter((item) => item.status === 'active')
    .map((item) => ({ value: item.id, label: `${item.contract_no} ${item.title}（${item.contract_no}）` }));

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <RuntimeStatusBanner />
      <Card
        title={
          <Space>
            <ProjectOutlined />
            <span>我的开发</span>
          </Space>
        }
        extra={
          <Space>
            <Select
              allowClear
              style={{ minWidth: 280 }}
              placeholder="全部合约"
              value={contractId ?? undefined}
              options={options}
              onChange={(value?: string) => setContractId(value ?? null)}
            />
            <Button icon={<ReloadOutlined />} onClick={() => Promise.all([notebooks.refetch(), workloads.refetch(), runs.refetch()])}>
              刷新
            </Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
              新建开发任务
            </Button>
          </Space>
        }
      >
        {failed ? (
          <QueryErrorAlert
            error={notebooks.error ?? workloads.error ?? runs.error}
            onRetry={() => Promise.all([notebooks.refetch(), workloads.refetch(), runs.refetch()])}
          />
        ) : null}
        {loading && !failed ? <CenteredLoading /> : null}
        {!loading && !failed ? (
          <Table<DevTaskRow>
            rowKey="key"
            size="small"
            dataSource={rows}
            pagination={{ pageSize: 20, showSizeChanger: true }}
            locale={tableEmpty('还没有开发任务，点右上角新建一个')}
            columns={[
              { title: '名称', dataIndex: 'name', ellipsis: true },
              {
                title: '类型',
                dataIndex: 'kind',
                width: 90,
                render: (kind: DevTaskRow['kind']) => KIND_LABEL[kind],
              },
              {
                title: '阶段',
                dataIndex: 'stage',
                width: 150,
                render: (stage: DevTaskRow['stage']) => <Tag color={stage.color}>{stage.label}</Tag>,
              },
              { title: '合约', dataIndex: 'contract_id', ellipsis: true, render: (id: string | null) => contractLabel(id) },
              { title: '最近更新', dataIndex: 'at', width: 180, render: (value: string | null) => formatTime(value) },
              {
                title: '操作',
                width: 110,
                render: (_: unknown, row: DevTaskRow) => (
                  <Typography.Link onClick={() => navigate(row.target)}>进入</Typography.Link>
                ),
              },
            ]}
          />
        ) : null}
      </Card>

      <Modal
        title="新建开发任务"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => form.submit()}
        confirmLoading={createTask.isPending}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={(values) => createTask.mutate(values)}>
          <Form.Item name="title" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}>
            <Input placeholder="例如：客单价分析" maxLength={120} />
          </Form.Item>
          <Form.Item name="contract_id" label="关联合约" rules={[{ required: true, message: '请选择合约' }]}
            extra="开发任务在合约约定的沙箱资源与交付约束内运行，不能自选。">
            <Select showSearch optionFilterProp="label" placeholder="选择已生效的合约" options={activeOptions} />
          </Form.Item>
        </Form>
        {createTask.isError ? <QueryErrorAlert error={createTask.error} /> : null}
      </Modal>
    </Space>
  );
}
