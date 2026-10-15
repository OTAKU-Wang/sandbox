import { Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { catalogApi } from '../services/catalogApi';
import { dataPipelineApi, type PipelineTaskSummary } from '../services/dataPipelineApi';
import { dataProductApi } from '../services/dataProductApi';
import { dataResourceApi } from '../services/dataResourceApi';
import { QueryErrorAlert, tableEmpty } from '../components/Feedback/QueryFeedback';

const { Title, Text } = Typography;

const TASK_STATUS_COLORS: Record<string, string> = {
  pending: 'blue',
  running: 'processing',
  completed: 'green',
  failed: 'red',
};

interface StageQueryState {
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

interface Stage {
  key: string;
  title: string;
  statTitle: string;
  total?: number;
  link?: string;
  linkLabel?: string;
  q: StageQueryState;
}

export default function DataPipeline() {
  const navigate = useNavigate();

  const resources = useQuery({
    queryKey: ['data-resources', 1, 1],
    queryFn: () => dataResourceApi.list({ page: 1, page_size: 1 }),
  });
  const products = useQuery({
    queryKey: ['data-products', 1, 1],
    queryFn: () => dataProductApi.list({ page: 1, page_size: 1 }),
  });
  const catalog = useQuery({
    queryKey: ['catalog', 1, 1],
    queryFn: () => catalogApi.search({ page: 1, page_size: 1 }),
  });
  const tasks = useQuery({
    queryKey: ['data-pipeline-tasks', 1],
    queryFn: () => dataPipelineApi.listTasks({ page: 1, page_size: 10 }),
  });

  const stages: Stage[] = [
    {
      key: 'resources',
      title: '① 数据资源',
      statTitle: '已登记资源',
      total: resources.data?.total,
      link: '/data-resources',
      linkLabel: '前往资源页',
      q: resources,
    },
    {
      key: 'products',
      title: '② 数据产品',
      statTitle: '产品总数',
      total: products.data?.total,
      link: '/data-products',
      linkLabel: '前往产品页',
      q: products,
    },
    {
      key: 'catalog',
      title: '③ 数据目录',
      statTitle: '目录产品',
      total: catalog.data?.total,
      link: '/catalog',
      linkLabel: '前往目录页',
      q: catalog,
    },
    {
      key: 'tasks',
      title: '④ 我的管线任务',
      statTitle: '任务总数',
      total: tasks.data?.total,
      q: tasks,
    },
  ];

  const taskColumns: ColumnsType<PipelineTaskSummary> = [
    {
      title: '任务 ID',
      dataIndex: 'task_id',
      key: 'task_id',
      render: (v: string) => <Text style={{ fontFamily: 'monospace', fontSize: 12 }}>{v}</Text>,
    },
    { title: '类型', dataIndex: 'task_type', key: 'task_type', width: 100 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (v: string) => <Tag color={TASK_STATUS_COLORS[v] ?? 'default'}>{v}</Tag>,
    },
    { title: '输入路径', dataIndex: 'input_path', key: 'input_path', ellipsis: true },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 100,
      render: (v: number | null) => (v != null ? `${v} ms` : '—'),
    },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180 },
  ];

  return (
    <div>
      <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>数据管线</Title>
        <Text type="secondary">
          数据闭环工作流总览：资源登记 → 产品化 → 目录上架 → 沙箱/管线执行（输出经审查后才出沙箱）。
        </Text>
      </Space>

      <Row gutter={[16, 16]}>
        {stages.map((stage) => (
          <Col key={stage.key} xs={24} sm={12} lg={6}>
            <Card size="small" title={stage.title}>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {stage.q.isError ? (
                  <QueryErrorAlert
                    error={stage.q.error}
                    message={`${stage.title} 加载失败`}
                    onRetry={() => { void stage.q.refetch(); }}
                  />
                ) : (
                  <Statistic title={stage.statTitle} value={stage.total ?? '—'} />
                )}
                {stage.link && (
                  <Button size="small" type="link" style={{ paddingLeft: 0 }} onClick={() => navigate(stage.link as string)}>
                    {stage.linkLabel}
                  </Button>
                )}
              </Space>
            </Card>
          </Col>
        ))}
      </Row>

      <Card size="small" title="我的管线任务（最近 10 条）" style={{ marginTop: 16 }}>
        {tasks.isError ? (
          <QueryErrorAlert
            error={tasks.error}
            message="管线任务加载失败"
            onRetry={() => { void tasks.refetch(); }}
          />
        ) : (
          <Table<PipelineTaskSummary>
            rowKey="task_id"
            columns={taskColumns}
            dataSource={tasks.data?.items ?? []}
            loading={tasks.isLoading}
            pagination={false}
            size="small"
            locale={tableEmpty('暂无管线任务')}
          />
        )}
      </Card>
    </div>
  );
}
