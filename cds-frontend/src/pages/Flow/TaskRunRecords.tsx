// SS-18 原型 2-4「任务详情 · 运行记录」卡：六列逐列照图
// （执行时间 / 操作类型 / 执行环境 / 状态 / 耗时 / 操作）。
//
// 诚实口径（项目铁律 honest degradation / 禁伪造）：这里只列**真发生过**的执行——
// ① 本页真实跑出来的调试结果（单元格 / 整本运行，含被输出策略拦截的那些）；
// ② 后端 compute run 里确实由本任务派生负载产生的正式执行记录（按 workload.source_notebook_id 归位）。
// 一条都没有就明说「暂无运行记录」，不放示例行、不编统计数；
// 后端没给的列（调试耗时、逐次内存占用、保留期字段）一律记破折号「—」或写明来源。
import { Card, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import {
  TASK_DEBUG_METHOD_OPTIONS,
  TASK_EXPORT_MODE_OPTIONS,
  UNKNOWN_TEXT,
  taskEnumLabel,
  useComputeRuns,
  useWorkloads,
} from '../../services/flow';
import type { ComputeRun, Notebook } from '../../services/flow';
import { EmptyState, QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import {
  NO_SOURCE_TEXT,
  RUN_RECORD_RETENTION_NOTE,
  RUN_RECORD_RETENTION_SOURCE,
  debugRunStatusMeta,
  formatDevTaskTime,
  formatPrototypeMemory,
  formatRunDuration,
  runRecordStatusMeta,
  statusTagTone,
} from './devTasksModel';
import type { StatusMeta } from './display';

const { Text } = Typography;

/** 一次真实发生过的调试执行（后端只在 notebook 上留最近一次执行时间，逐次明细在本页内存里）。 */
export interface DebugRunRecord {
  key: string;
  kind: 'cell' | 'notebook';
  label: string;
  /** 本页记录时刻（ISO）；不是后端时间戳，表下有一句说明。 */
  at: string;
  ok: boolean | null;
  backend?: string | null;
  exit_code?: number | null;
  blocked?: string | null;
  executed?: number | null;
  skipped?: number | null;
}

/** 页面上报一条调试记录时用的形状（key 由列表自己编）。 */
export type DebugRunRecordInput = Omit<DebugRunRecord, 'key'>;

/** 图 2-4 的运行记录行：一条记录 = 图上的六个格子。 */
interface RunRecordRow {
  key: string;
  /** 执行时间：调试行取本页记录时刻（口径见表下说明），正式执行取后端 started_at。 */
  time: string;
  time_note: string | null;
  /** 操作类型：图上只有「调试执行 / 正式执行」两种。 */
  type: string;
  /** 类型下的第二行小字：调试带单元格与实际后端，正式执行带后端触发方式。 */
  type_note: string | null;
  /** 执行环境：图上写作「调试环境 · N G / 执行环境 · N G」，内存取任务上后端下发的配额。 */
  env: string;
  status: StatusMeta;
  status_note: string | null;
  /** 耗时：正式执行按后端起止时间算；调试明细后端无起止时间，记「未记录」。 */
  duration: string;
  log_to: string | null;
}

function debugRow(record: DebugRunRecord, notebook: Notebook): RunRecordRow {
  const { meta, note } = debugRunStatusMeta(record);
  return {
    key: record.key,
    time: formatDevTaskTime(record.at) ?? UNKNOWN_TEXT,
    time_note: '本页记录时刻',
    type: '调试执行',
    type_note: record.backend ? `${record.label} · 后端 ${record.backend}` : record.label,
    env: `调试环境 · ${formatPrototypeMemory(notebook.debug_memory_mb)}`,
    status: meta,
    status_note: note,
    duration: NO_SOURCE_TEXT,
    log_to: `/notebooks?task=${encodeURIComponent(notebook.id)}`,
  };
}

function productionRow(run: ComputeRun, notebook: Notebook): RunRecordRow {
  return {
    key: `run:${run.id}`,
    time: formatDevTaskTime(run.started_at ?? run.scheduled_for) ?? UNKNOWN_TEXT,
    time_note: run.started_at ? null : '后端只下发计划时刻，尚无开始时间',
    type: '正式执行',
    type_note: run.trigger ? `触发方式 ${run.trigger}` : null,
    env: `执行环境 · ${formatPrototypeMemory(notebook.exec_memory_mb)}`,
    status: runRecordStatusMeta(run.status),
    status_note: run.error_message ?? null,
    duration: formatRunDuration(run.started_at, run.finished_at),
    log_to: '/compute',
  };
}

export interface TaskRunRecordsProps {
  notebook: Notebook;
  /** 本页真实执行得到的调试记录（没有就传空数组，面板自己说「暂无」）。 */
  debugRecords: DebugRunRecord[];
}

export default function TaskRunRecords({ notebook, debugRecords }: TaskRunRecordsProps) {
  const contractId = notebook.contract_id ?? null;
  // 没关联合约时无处归位，两个查询直接关掉（不发无谓请求），空态文案自己说清原因。
  const workloads = useWorkloads({ contractId }, contractId !== null);
  const runs = useComputeRuns({ contractId }, undefined, contractId !== null);

  // 正式执行记录挂在负载上：只有 source_notebook_id 指向本任务的负载，其 run 才算这个任务的记录。
  const ownWorkloadIds = new Set(
    (workloads.data?.items ?? [])
      .filter((workload) => workload.source_notebook_id === notebook.id)
      .map((workload) => workload.id),
  );
  const productionRuns = (runs.data?.items ?? []).filter(
    (run) => !!run.workload_id && ownWorkloadIds.has(run.workload_id),
  );

  const rows: RunRecordRow[] = [
    ...debugRecords.map((record) => debugRow(record, notebook)),
    ...productionRuns.map((run) => productionRow(run, notebook)),
  ];

  const loading = contractId !== null && (workloads.isPending || runs.isPending);
  const failed = contractId !== null && (workloads.isError || runs.isError);

  const emptyDescription = contractId === null
    ? `本任务${UNKNOWN_TEXT}关联合约，正式执行记录无处归位；本页也还没有跑过调试。`
    : '暂无运行记录：本页尚未执行，后端也没有本任务派生负载的正式执行记录。';

  const columns: ColumnsType<RunRecordRow> = [
    {
      title: '执行时间',
      dataIndex: 'time',
      width: 200,
      render: (value: string, row) => (
        <Space direction="vertical" size={0}>
          <Text>{value}</Text>
          {row.time_note && <Text type="secondary" style={{ fontSize: 12 }}>{row.time_note}</Text>}
        </Space>
      ),
    },
    {
      title: '操作类型',
      dataIndex: 'type',
      width: 200,
      render: (value: string, row) => (
        <Space direction="vertical" size={0}>
          <Text>{value}</Text>
          {row.type_note && <Text type="secondary" style={{ fontSize: 12 }}>{row.type_note}</Text>}
        </Space>
      ),
    },
    {
      title: '执行环境',
      dataIndex: 'env',
      width: 190,
      render: (value: string) => (
        <Tooltip title="环境名后的内存取任务创建时填写的配额：后端 compute run 不下发逐次内存占用。">
          <span>{value}</span>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 150,
      render: (status: StatusMeta, row) => (
        <Space direction="vertical" size={0}>
          <Tooltip title={row.status_note ?? status.label}>
            <Tag className={`t24-tag ${statusTagTone(status.color)}`}>{status.label}</Tag>
          </Tooltip>
          {row.status_note && <Text type="secondary" style={{ fontSize: 12 }}>{row.status_note}</Text>}
        </Space>
      ),
    },
    { title: '耗时', dataIndex: 'duration', width: 110 },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, row) => (row.log_to
        ? <Link to={row.log_to}>查看日志</Link>
        : <Text type="secondary">{UNKNOWN_TEXT}</Text>),
    },
  ];

  return (
    <Card
      className="t24-card"
      size="small"
      title={<span className="t24-ht"><span className="t24-bar" />运行记录<ClockCircleOutlined className="t24-hicon" /><span className="t24-hnote">{RUN_RECORD_RETENTION_NOTE}</span></span>}
      extra={<Link to="/compute">查看全部记录<span className="t24-arrow">&gt;</span></Link>}
    >
      {/* 表永远带着六列表头渲染（图上那一列带一直在）：没行时在表体里给空态，
          而不是把整张表换成空盒子——那样六个列头会整体消失，与图不符。 */}
      {failed && (
        <QueryErrorAlert
          error={workloads.error ?? runs.error}
          message="运行记录加载失败"
          onRetry={() => { void workloads.refetch(); void runs.refetch(); }}
        />
      )}
      <Table<RunRecordRow>
        className="t24-table"
        size="small"
        rowKey="key"
        pagination={false}
        loading={loading && !failed}
        dataSource={rows}
        columns={columns}
        locale={{ emptyText: <EmptyState description={emptyDescription} /> }}
      />
      <Space direction="vertical" size={2} style={{ marginTop: 12 }}>
        <Text className="t24-table-foot" style={{ display: 'block' }}>
          调试执行的时间取本页记录时刻（后端只在任务上保存最近一次执行时间）；正式执行的时间、状态与结果均来自后端 compute run。
        </Text>
        <Text className="t24-table-foot" style={{ display: 'block' }}>
          本任务运行参数：调试方法 {taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, notebook.debug_method)} · 导出方式{' '}
          {taskEnumLabel(TASK_EXPORT_MODE_OPTIONS, notebook.export_mode)} · 调试内存 {formatPrototypeMemory(notebook.debug_memory_mb)} ·
          执行内存 {formatPrototypeMemory(notebook.exec_memory_mb)} · 执行后端 {notebook.execution_backend ?? UNKNOWN_TEXT}；
          调试执行没有耗时字段，故该格记{NO_SOURCE_TEXT}。
        </Text>
        <Text className="t24-table-foot" style={{ display: 'block' }}>{RUN_RECORD_RETENTION_SOURCE}</Text>
      </Space>
    </Card>
  );
}
