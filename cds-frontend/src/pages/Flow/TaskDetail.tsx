// SS-18 原型 2-4「我的开发-任务详情」（图规格 = 机器转录 img-specs/group-B.md §图 2）：
// 八张卡纵向单列，逐张照图（任务标题 → 4 个统计小卡 → 基本信息 → 我的合约 → 资源配置 → 关联数据 → 运行记录 → 结果集），
// 全页无分页器（图二次放大确认），一次取全（后端 page_size 上限内；取不全时如实写出未取回条数）。
//
// 诚实口径（项目铁律 honest degradation / 禁伪造）：
// - 每个数字都取后端下发值；后端字段存在但没下发 → 「未记录」，整列/整项没有来源 → 「—」（图上自己也用破折号）；
// - 内存实测占用后端根本没有采样源（home.not_collected 的 memory.measured），使用量写「未计量」，
//   进度条只画空槽，不画 47.5% 这类假填充；
// - 「沙箱环境运行中…」这句只在 GET /sandbox-sessions 里真查到本任务的 running 会话时说；
// - 成果包在数据模型里按**合约 + 会话**归集（`delivery_bundles` 没有任务维度外键），
//   所以「产出结果集」如实按合约归集并写明口径，不假装这一条任务独产。
import { useState } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Modal,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ArrowLeftOutlined,
  CaretRightFilled,
  CheckCircleFilled,
  ClockCircleOutlined,
  CodeOutlined,
  DatabaseOutlined,
  ExportOutlined,
  ImportOutlined,
  InfoCircleOutlined,
  LockOutlined,
  ProfileOutlined,
  ReloadOutlined,
  RiseOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { ReactNode } from 'react';
import TaskRunRecords from './TaskRunRecords';
import type { DebugRunRecordInput } from './TaskRunRecords';
import { bundleReviewMeta, bundleWatermarkMeta } from './display';
import {
  BUNDLE_EXPORT_STATUS_HINT,
  BUNDLE_NAME_HINT,
  DEV_TASK_NODE_HINT,
  MEMORY_USAGE_UNMEASURED,
  NO_ACTIVE_DEBUG_SESSION_NOTE,
  NO_SOURCE_TEXT,
  PRODUCT_ORG_HINT,
  RESULT_SET_SCOPE_HINT,
  SANDBOX_MEMORY_RUNNING_NOTE,
  formatDevTaskTime,
  formatPrototypeMemory,
  memoryUnmeasuredReason,
  notebookStateMeta,
  productTypeLabel,
  statusTagTone,
  taskNodeText,
} from './devTasksModel';
import { useContractById } from './useContractOptions';
import type { ContractOption } from './useContractOptions';
import { CONTRACT_DATA_RESOLVE_LIMIT, buildContractDataSummary } from './contractDataModel';
import type { ContractDataItem } from './contractDataModel';
import { dataProductApi } from '../../services/dataProductApi';
import { usersApi } from '../../services/usersApi';
import { sandboxApi } from '../../services/sandboxApi';
import { useAuthStore } from '../../stores/authStore';
import {
  TASK_DEBUG_METHOD_OPTIONS,
  TASK_EXPORT_MODE_OPTIONS,
  UNKNOWN_TEXT,
  describeDeliveryMethod,
  describeFlowError,
  formatBytes,
  formatDateTime,
  taskEnumLabel,
  useDatasetBundles,
  useHomeOverview,
  useNotebook,
  useRunNotebook,
} from '../../services/flow';
import type { DatasetBundleRow } from '../../services/flow';
import { CenteredLoading, QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import './taskDetail.css';

const { Text, Title } = Typography;

/** 图 2-4「资源配置」左栏两句固定说明。 */
const DEBUG_MEMORY_CAPTION = '用于在线调试、断点运行与日志查看';
const EXEC_MEMORY_CAPTION = '用于全量任务正式执行环境';
const MEMORY_QUOTA_NOTE = '内存配额将应用于调试与执行阶段';
/** 图 2-4「关联数据」「结果集」两句固定说明（照图原文）。 */
const GRANTED_DATA_NOTE = '已授权到沙箱的数据产品，详情信息为只读';
const RESULT_SET_NOTE = '按合约约定，结果集已通过 OpenApi 导出至「我的结果集」；';
/** 一页取全的批量：后端各表 page_size 上限都在 100 以内（1000 会 422）。 */
const FULL_PAGE_SIZE = 100;

const footnoteStyle = { fontSize: 12 } as const;

/** 统计小卡右侧那种「彩色底 + 白色图形」图标（图：紫底圆柱、橙底柱状图文档）。 */
function TintedIcon({ icon, background }: { icon: ReactNode; background: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 24,
        height: 24,
        borderRadius: 4,
        background,
        color: '#fff',
      }}
    >
      {icon}
    </span>
  );
}

export default function FlowTaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const notebookQuery = useNotebook(taskId ?? null);
  const organization = useAuthStore((state) => state.user?.organization);
  const [detailProduct, setDetailProduct] = useState<ContractDataItem | null>(null);
  const [debugRecords, setDebugRecords] = useState<DebugRunRecordInput[]>([]);
  /** 执行任务的失败原因：除了 toast，还在卡 A 里留一行错语（瞬时提示不能当唯一披露，也不能吞）。 */
  const [runError, setRunError] = useState<string | null>(null);
  const runNotebook = useRunNotebook();

  const notebook = notebookQuery.data;
  const contractId = notebook?.contract_id ?? null;
  const sessionId = notebook?.session_id ?? null;

  // 合约按 id 取回：列表接口 limit 上限 20，靠列表命中会把「不在第一页」误判成「没有合约」。
  const contractQuery = useContractById(contractId);
  const contract: ContractOption | null = contractQuery.data ?? null;

  // 内存使用量的唯一权威披露：home.not_collected 的 memory.measured（后端自己说没有采样源）。
  const home = useHomeOverview();
  const bundles = useDatasetBundles(contractId, 1, FULL_PAGE_SIZE);

  // 关联数据 = 合约授权进沙箱的数据产品清单；null（未下发）与 []（确实没绑）是两件事。
  const grantedIds = contract?.product_ids ?? [];
  const products = useQuery({
    queryKey: ['flow', 'task-detail', 'contract-data-products', grantedIds],
    enabled: grantedIds.length > 0,
    queryFn: async () => {
      const head = grantedIds.slice(0, CONTRACT_DATA_RESOLVE_LIMIT);
      const settled = await Promise.allSettled(head.map((id) => dataProductApi.get(id)));
      return buildContractDataSummary(grantedIds, settled);
    },
  });
  // 组织名称：按 provider_id 到账号选项里解析 organization（上限 100，后端 le=100）。
  const orgOptions = useQuery({
    queryKey: ['flow', 'task-detail', 'user-options'],
    queryFn: () => usersApi.options({ limit: FULL_PAGE_SIZE }),
  });
  // 「沙箱环境运行中」这句只认真查到的 running 会话（按 session_id 归位，退回按合约归位）。
  const runningSessions = useQuery({
    queryKey: ['flow', 'task-detail', 'running-sessions'],
    queryFn: () => sandboxApi.list({ page: 1, page_size: FULL_PAGE_SIZE, status: 'running' }),
  });

  if (notebookQuery.isPending) return <CenteredLoading />;
  if (notebookQuery.isError) return <QueryErrorAlert error={notebookQuery.error} />;
  if (!notebook) return <Alert type="warning" showIcon message="没有找到这条开发任务" />;

  const state = notebookStateMeta(notebook);
  const workbenchPath = `/notebooks?task=${encodeURIComponent(notebook.id)}`;
  const memoryQuotaText = {
    debug: formatPrototypeMemory(notebook.debug_memory_mb),
    exec: formatPrototypeMemory(notebook.exec_memory_mb),
  };
  const grantedCount = contract?.product_ids ? contract.product_ids.length : null;
  const bundleTotal = bundles.data?.total ?? null;
  const unmeasuredReason = memoryUnmeasuredReason(home.data?.not_collected);

  const sessionRows = runningSessions.data?.items ?? [];
  const activeSession
    = sessionRows.find((row) => row.id === sessionId)
      ?? (contractId ? sessionRows.find((row) => row.contract_id === contractId) ?? null : null);

  const orgNameById = new Map((orgOptions.data ?? []).map((option) => [option.id, option.organization]));
  const organizationOf = (item: ContractDataItem): string => {
    const providerId = item.product?.provider_id;
    if (!providerId) return NO_SOURCE_TEXT;
    return orgNameById.get(providerId)?.trim() || NO_SOURCE_TEXT;
  };

  /** 执行任务：真调 POST /notebooks/{id}/run，失败（含 409 NOTEBOOK_NO_CELLS）原样提示，不吞。 */
  const runThisTask = () => {
    runNotebook.mutate(notebook.id, {
      onSuccess: (result) => {
        setRunError(null);
        const failed = result.results.find((cell) => !cell.ok);
        setDebugRecords((prev) => [
          ...prev,
          {
            kind: 'notebook',
            label: `整本运行（${result.results.length} 个单元格）`,
            at: new Date().toISOString(),
            ok: result.passed,
            backend: result.results[0]?.backend ?? null,
            exit_code: failed ? failed.exit_code : 0,
            blocked: failed?.blocked_reason ?? null,
            executed: result.results.length,
            skipped: 0,
          },
        ]);
        if (result.passed) {
          message.success(`整本运行通过：${result.results.length} 个单元格`);
        } else {
          message.warning(`整本运行未通过：${result.results.length} 个单元格里有失败，见运行记录`);
        }
      },
      onError: (error) => {
        const reason = describeFlowError(error, '执行任务失败');
        setRunError(reason);
        message.error(reason);
      },
    });
  };

  // ---------- 我的合约（图 D）----------
  // 1:1 裁读（2-4-contract-zone）：这一屏是三个灰底只读盒（基本信息同款），不是表 ——
  // 旧「通栏表」是缩放视图误读，已按图重建；口径仍挂 label 悬浮层。
  const exportModeText = contract?.delivery_methods === null || contract?.delivery_methods === undefined
    ? UNKNOWN_TEXT
    : contract.delivery_methods.length
      ? contract.delivery_methods.map(describeDeliveryMethod).join('、')
      : NO_SOURCE_TEXT;
  const EXPORT_MODE_SCOPE_HINT = `取合约的交付方式白名单（flow-config.delivery_methods）；任务自身的成果导出方式：${notebook.export_mode ? taskEnumLabel(TASK_EXPORT_MODE_OPTIONS, notebook.export_mode) : UNKNOWN_TEXT}`;
  /** 配额盒：图上值是裸数字 + 右端白底 G 小片；formatPrototypeMemory 带的「 G」尾缀拆进芯片，不重复画。 */
  const quotaParts = (text: string) => (text.endsWith(' G') ? { value: text.slice(0, -2), unit: true } : { value: text, unit: false });

  // ---------- 关联数据（图 F：七列逐列照图）----------
  const grantedColumns: ColumnsType<ContractDataItem> = [
    {
      title: '数据产品名称',
      key: 'name',
      render: (_: unknown, row) => (row.product
        ? <Typography.Link onClick={() => setDetailProduct(row)}>{row.product.name}</Typography.Link>
        : <Text type="warning">未取回（未发布或本账号无权查看）· {row.id}</Text>),
    },
    {
      title: '产品简介',
      key: 'description',
      render: (_: unknown, row) => row.product?.description ?? UNKNOWN_TEXT,
    },
    {
      title: '产品类型',
      key: 'product_type',
      width: 130,
      render: (_: unknown, row) => productTypeLabel(row.product?.product_type),
    },
    {
      title: (
        <Tooltip title="数据产品载荷没有交付方式字段：这一列取关联合约 flow-config 的 delivery_methods（对整份授权生效）。">
          <span>交付方式</span>
        </Tooltip>
      ),
      key: 'delivery_methods',
      width: 140,
      render: () => (contract?.delivery_methods?.length
        ? contract.delivery_methods.map(describeDeliveryMethod).join('、')
        : NO_SOURCE_TEXT),
    },
    {
      title: (
        <Tooltip title="同样取合约的用途（contracts.purpose）：交付的使用场景按合约约定，数据产品载荷里没有这一项。">
          <span>交付的使用场景</span>
        </Tooltip>
      ),
      key: 'purpose',
      width: 170,
      render: () => contract?.purpose ?? NO_SOURCE_TEXT,
    },
    {
      title: (
        <Tooltip title={PRODUCT_ORG_HINT}>
          <span>组织名称</span>
        </Tooltip>
      ),
      key: 'organization',
      width: 130,
      render: (_: unknown, row) => organizationOf(row),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_: unknown, row) => <Typography.Link onClick={() => setDetailProduct(row)}>详情</Typography.Link>,
    },
  ];

  // ---------- 结果集（图 H：六列逐列照图）----------
  const bundleColumns: ColumnsType<DatasetBundleRow> = [
    {
      title: '结果集名称',
      key: 'name',
      render: (_: unknown, row) => (
        <Tooltip title={BUNDLE_NAME_HINT}>
          <Text code>{row.file_name?.trim() ? row.file_name : row.bundle_id}</Text>
        </Tooltip>
      ),
    },
    {
      title: '生成时间',
      dataIndex: 'created_at',
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: '数据行数',
      dataIndex: 'row_count',
      width: 110,
      render: (value: number | null | undefined) => (typeof value === 'number' ? `${value} 行` : NO_SOURCE_TEXT),
    },
    {
      title: '文件大小',
      dataIndex: 'size_bytes',
      width: 110,
      render: (value: number | null | undefined) => (typeof value === 'number' ? formatBytes(value) : NO_SOURCE_TEXT),
    },
    {
      title: '导出状态',
      key: 'export_status',
      width: 120,
      render: (_: unknown, row) => {
        const review = bundleReviewMeta(row.t5_review_status);
        const watermark = bundleWatermarkMeta(row.watermark_status);
        return (
          <Space direction="vertical" size={2}>
            <Tooltip title={BUNDLE_EXPORT_STATUS_HINT}>
              <span>{NO_SOURCE_TEXT}</span>
            </Tooltip>
            <Space size={4} wrap>
              <Text type="secondary" style={footnoteStyle}>出域审查</Text>
              <Tag color={review.color}>{review.label}</Tag>
              <Text type="secondary" style={footnoteStyle}>水印</Text>
              <Tag color={watermark.color}>{watermark.label}</Tag>
            </Space>
          </Space>
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: () => (
        <Link to={`/deliveries?contract=${encodeURIComponent(contractId ?? '')}`}>查看结果集</Link>
      ),
    },
  ];

  /** 「资源使用情况」一行：图上标签与值同一行（值右端），下一行 6px 进度槽；分子没有采样源就不画填充。 */
  const memoryUsageRow = (label: string, quotaText: string) => (
    <>
      <div className="t24-mem-row">
        <span>{label}</span>
        <Tooltip title={unmeasuredReason}>
          <span className="t24-mem-value">{`${MEMORY_USAGE_UNMEASURED} / ${quotaText}`}</span>
        </Tooltip>
      </div>
      <div className="t24-mem-bar">
        <Tooltip title={unmeasuredReason}>
          <Progress percent={0} showInfo={false} />
        </Tooltip>
      </div>
    </>
  );

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }} className="cds-t24">
      {/* A 任务标题：图 y138..249 —— 白卡 h112 内缩 24，三钮 h38 右置 */}
      <div className="cds-page-toolbar t24-head">
        <div className="t24-head-main">
          <div className="t24-head-row">
            <h4 className="t24-title">{notebook.title}</h4>
            <Tag className={`t24-tag ${statusTagTone(state.color)}`}>{state.label}</Tag>
          </div>
          <div className="t24-head-sub">
            # {notebook.task_no ?? notebook.id} · 创建时间 {formatDevTaskTime(notebook.created_at) ?? UNKNOWN_TEXT} · 最近执行{' '}
            {formatDevTaskTime(notebook.last_executed_at) ?? UNKNOWN_TEXT}
          </div>
        </div>
        <div className="t24-head-actions">
          {/* 1:1 裁读（2-4-head-btns-x2）：返回列表白底描边灰字，进入调试白底蓝描边蓝字（ghost）+方框箭头图标，
              执行任务实心蓝；旧「text 无底 / link 无框 / play-square」是缩放误读，已换。 */}
          <Button className="t24-btn-back" icon={<ArrowLeftOutlined />} onClick={() => navigate('/flow/tasks')}>
            返回列表
          </Button>
          <Button type="primary" ghost icon={<ImportOutlined />} onClick={() => navigate(workbenchPath)}>
            进入调试
          </Button>
          <Button type="primary" icon={<CaretRightFilled />} loading={runNotebook.isPending} onClick={runThisTask}>
            执行任务
          </Button>
        </div>
      </div>
      {runError ? (
        <Alert
          type="error"
          showIcon
          message={`执行任务失败：${runError}`}
          description="错误原样取自后端（不改写、不吞）：任务里有可运行的单元格后再试。"
        />
      ) : null}

      {/* B 四个统计小卡：图 y266..377 —— 一张白卡内 4 块 #f8fbff 小卡（h112 间距 16，40×40 浅底芯片）；数值只能取后端真实计数 */}
      <Card className="t24-tiles">
        <div className="t24-tile-row">
          <div className="t24-tile">
            <span className="t24-chip is-green"><ReloadOutlined /></span>
            <Statistic title="任务状态" value={state.label} />
          </div>
          <div className="t24-tile">
            <span className="t24-chip is-blue"><CodeOutlined /></span>
            <Statistic title="模型调试方法" value={taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, notebook.debug_method)} />
          </div>
          <div className="t24-tile">
            <span className="t24-chip is-purple"><DatabaseOutlined /></span>
            <Tooltip title="取关联合约下发的授权数据清单长度（contract.product_ids）；合约未下发清单时记「未记录」，不按 0 计。">
              <Statistic title="关联数据产品" value={grantedCount ?? UNKNOWN_TEXT} suffix={grantedCount === null ? undefined : '个'} />
            </Tooltip>
          </div>
          <div className="t24-tile">
            <span className="t24-chip is-orange"><ProfileOutlined /></span>
            <Tooltip title="取 GET /deliveries/dataset/bundles 的 total：成果包按合约归集（交付包表没有任务维度外键），这里是该合约下的成果包总数。">
              <Statistic title="产出结果集" value={bundleTotal ?? UNKNOWN_TEXT} suffix={bundleTotal === null ? undefined : '个'} />
            </Tooltip>
          </div>
        </div>
      </Card>

      {/* C 基本信息：label 12 灰在上 + #f7f8fa 值盒（三列，描述整宽 h82）；图上无 info 图标，口径挂到 label 悬浮 */}
      <Card className="t24-card" size="small" title={<span className="t24-ht"><span className="t24-bar" />基本信息</span>}>
        <div className="t24-fields">
          <div className="t24-field">
            <div className="t24-field-label">任务名称</div>
            <div className="t24-field-box">{notebook.title}</div>
          </div>
          <div className="t24-field">
            <div className="t24-field-label">任务编号</div>
            <div className="t24-field-box">{notebook.task_no ?? UNKNOWN_TEXT}</div>
          </div>
          <div className="t24-field">
            <Tooltip title={DEV_TASK_NODE_HINT}>
              <div className="t24-field-label">所属节点</div>
            </Tooltip>
            <div className="t24-field-box">{taskNodeText(organization)}</div>
          </div>
          <div className="t24-field t24-field-span">
            <Tooltip title="任务描述取后端 NotebookOut.description（创建任务时填写），后端未下发即「未记录」。">
              <div className="t24-field-label">任务描述</div>
            </Tooltip>
            <div className="t24-field-box is-multiline">{notebook.description ?? UNKNOWN_TEXT}</div>
          </div>
        </div>
      </Card>

      {/* D 我的合约：三枚灰底只读盒（基本信息同款），图上标题后有 ⓘ */}
      <Card
        className="t24-card"
        size="small"
        title={<span className="t24-ht"><span className="t24-bar" />我的合约<InfoCircleOutlined className="t24-hicon" /><span className="t24-hnote">合约用于约束模型输出格式与导出通道</span></span>}
      >
        {contract === null ? (
          <Alert type="info" showIcon message="本任务未关联合约：没有可展示的合约约束" />
        ) : (
          <div className="t24-fields">
            <div className="t24-field">
              <Tooltip title={`合约编号 ${contract.contract_no || NO_SOURCE_TEXT}`}>
                <div className="t24-field-label">合约名称</div>
              </Tooltip>
              <div className="t24-field-box">{contract.title}</div>
            </div>
            <div className="t24-field">
              <Tooltip title="后端把调试方法落在任务上（NotebookOut.debug_method），合约不下发这一项。">
                <div className="t24-field-label">模型调试方法</div>
              </Tooltip>
              <div className="t24-field-box">
                {notebook.debug_method ? <CodeOutlined className="t24-box-ico" /> : null}
                {taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, notebook.debug_method)}
              </div>
            </div>
            <div className="t24-field">
              <Tooltip title={EXPORT_MODE_SCOPE_HINT}>
                <div className="t24-field-label">结果导出方式</div>
              </Tooltip>
              <div className="t24-field-box">
                {exportModeText !== UNKNOWN_TEXT && exportModeText !== NO_SOURCE_TEXT ? <RiseOutlined className="t24-box-ico" /> : null}
                {exportModeText}
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* E 资源配置 */}
      <Card
        className="t24-card"
        size="small"
        title={<span className="t24-ht"><span className="t24-bar" />资源配置<InfoCircleOutlined className="t24-hicon" /><span className="t24-hnote">{MEMORY_QUOTA_NOTE}</span></span>}
      >
        {/* 图：三列（调试配额 / 执行配额 / 右端 328 宽 #f3f8ff 面板 y948..1101）；配额按 1:1 裁读是灰底只读盒 + 右端白底 G 片，不是大号数字 */}
        <div className="t24-res">
          <div>
            <div className="t24-quota-label">调试环境内存（G）</div>
            <div className="t24-quota-box">
              <span>{quotaParts(memoryQuotaText.debug).value}</span>
              {quotaParts(memoryQuotaText.debug).unit ? <span className="t24-quota-g">G</span> : null}
            </div>
            <span className="t24-quota-note">{DEBUG_MEMORY_CAPTION}</span>
          </div>
          <div>
            <div className="t24-quota-label">执行环境内存（G）</div>
            <div className="t24-quota-box">
              <span>{quotaParts(memoryQuotaText.exec).value}</span>
              {quotaParts(memoryQuotaText.exec).unit ? <span className="t24-quota-g">G</span> : null}
            </div>
            <span className="t24-quota-note">{EXEC_MEMORY_CAPTION}</span>
          </div>
          <div className="t24-panel">
            <div className="t24-panel-title"><CheckCircleFilled /> 资源使用情况</div>
            {memoryUsageRow('调试内存使用', memoryQuotaText.debug)}
            <div className="t24-mem-gap" />
            {memoryUsageRow('执行内存使用', memoryQuotaText.exec)}
            {/* 图面板尾行只有一行且就是会话态那句（2-4-measured §6 y1075..1092）；「未计量」声明从屏上退到
                行值本身（右端就是「未计量 / 配额」）与悬浮层，不再多占一行（§12.6「多一行」）。 */}
            <Tooltip
              title={`平台没有逐任务/逐会话的内存采样源，使用量记「${MEMORY_USAGE_UNMEASURED}」，分母才是任务上填写的配额上限；后端为什么没有分子：GET /flow/dashboard/home 的 not_collected: memory.measured。`}
            >
              <span className="t24-panel-foot">
                {activeSession ? SANDBOX_MEMORY_RUNNING_NOTE : NO_ACTIVE_DEBUG_SESSION_NOTE}
              </span>
            </Tooltip>
          </div>
        </div>
      </Card>

      {/* F 关联数据 */}
      <Card
        className="t24-card"
        size="small"
        title={<span className="t24-ht"><span className="t24-bar" />关联数据<LockOutlined className="t24-hicon" /><span className="t24-hnote">{GRANTED_DATA_NOTE}</span></span>}
        extra={grantedCount === null
          ? <Text type="secondary" style={footnoteStyle}>授权清单{UNKNOWN_TEXT}</Text>
          : <Text style={{ color: '#1b6ef3' }}><DatabaseOutlined style={{ marginRight: 6 }} />{`共 ${grantedCount} 个数据产品`}</Text>}
      >
        {contractQuery.isError ? (
          <QueryErrorAlert error={contractQuery.error} message="关联合约取不回：交付方式与使用场景两列只能记「—」" />
        ) : contract === null ? (
          <Alert type="info" showIcon message="本任务未关联合约：数据随合约整体授权进沙箱，没有合约就没有授权数据" />
        ) : contract.product_ids === null ? (
          <Alert
            type="warning"
            showIcon
            message={`合约 ${contract.contract_no} 未下发授权数据清单，不替它编一个数字出来`}
          />
        ) : (
          <Table<ContractDataItem>
            className="t24-table is-f"
            size="small"
            rowKey="id"
            columns={grantedColumns}
            dataSource={products.data?.items ?? []}
            loading={products.isPending}
            pagination={false}
            locale={tableEmpty(
              products.isError
                ? '授权数据取不回：详情列记「未记录」'
                : '该合约未绑定数据产品：任务在沙箱内没有可读的授权数据',
            )}
          />
        )}
        {contract?.product_ids && (products.data?.omitted ?? 0) > 0 ? (
          <Text className="t24-table-foot">
            授权清单 {contract.product_ids.length} 项，超出单次解析上限 {CONTRACT_DATA_RESOLVE_LIMIT} 项的部分未取回
            （{products.data?.omitted} 项），不假装已全部展示。
          </Text>
        ) : null}
      </Card>

      {/* G 运行记录 */}
      <TaskRunRecords notebook={notebook} debugRecords={debugRecords.map((record, index) => ({ ...record, key: `page:${index}` }))} />

      {/* H 结果集 */}
      <Card
        className="t24-card"
        size="small"
        title={
          <span className="t24-ht">
            <span className="t24-bar" />
            <Tooltip title={RESULT_SET_SCOPE_HINT}>
              <span>结果集</span>
            </Tooltip>
            <ExportOutlined className="t24-hicon" />
            <span className="t24-hnote">{RESULT_SET_NOTE}</span>
          </span>
        }
        extra={<Link to={`/deliveries?contract=${encodeURIComponent(contractId ?? '')}`}>前往我的结果集<span className="t24-arrow">&gt;</span></Link>}
      >
        <Table<DatasetBundleRow>
          className="t24-table"
          rowKey="bundle_id"
          size="small"
          columns={bundleColumns}
          dataSource={bundles.data?.items ?? []}
          loading={bundles.isPending}
          pagination={false}
          locale={tableEmpty(bundleTotal ? '已有成果包但本页未取到' : '该合约还没有产出成果包')}
        />
        {bundleTotal !== null && (bundles.data?.items.length ?? 0) < bundleTotal ? (
          <Text className="t24-table-foot">
            后端共 {bundleTotal} 个成果包，本页一次取回 {bundles.data?.items.length ?? 0} 个（单次上限 {FULL_PAGE_SIZE}）；
            图上无分页器，差额部分不去翻页假装已列全。
          </Text>
        ) : null}
      </Card>

      <Modal
        className="cds-t24-modal"
        open={detailProduct !== null}
        title="数据产品详情"
        footer={<Button onClick={() => setDetailProduct(null)}>关闭</Button>}
        onCancel={() => setDetailProduct(null)}
      >
        {detailProduct?.product ? (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="数据产品名称">{detailProduct.product.name}</Descriptions.Item>
            <Descriptions.Item label="产品简介">{detailProduct.product.description ?? UNKNOWN_TEXT}</Descriptions.Item>
            <Descriptions.Item label="产品类型">{productTypeLabel(detailProduct.product.product_type)}</Descriptions.Item>
            <Descriptions.Item label="行业">{detailProduct.product.industry ?? UNKNOWN_TEXT}</Descriptions.Item>
            <Descriptions.Item label="版本">
              {(detailProduct.product as unknown as { version?: string | null }).version ?? UNKNOWN_TEXT}
            </Descriptions.Item>
            <Descriptions.Item label="数据行数">{detailProduct.product.row_count ?? UNKNOWN_TEXT}</Descriptions.Item>
            <Descriptions.Item label="安全等级">{detailProduct.product.security_level ?? UNKNOWN_TEXT}</Descriptions.Item>
            <Descriptions.Item label="交付方式">
              {contract?.delivery_methods?.length ? contract.delivery_methods.map(describeDeliveryMethod).join('、') : NO_SOURCE_TEXT}
            </Descriptions.Item>
            <Descriptions.Item label="交付的使用场景">{contract?.purpose ?? NO_SOURCE_TEXT}</Descriptions.Item>
            <Descriptions.Item label="组织名称">{organizationOf(detailProduct)}</Descriptions.Item>
            <Descriptions.Item label="状态">{detailProduct.product.status}</Descriptions.Item>
            <Descriptions.Item label="数据资源 id">{detailProduct.product.resource_id ?? UNKNOWN_TEXT}</Descriptions.Item>
            <Descriptions.Item label="供应方 id">{detailProduct.product.provider_id}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatDateTime(detailProduct.product.created_at)}</Descriptions.Item>
          </Descriptions>
        ) : (
          <Alert
            type="warning"
            showIcon
            message={`这份授权数据没有取回（未发布或本账号无权查看），只有 id：${detailProduct?.id ?? UNKNOWN_TEXT}`}
          />
        )}
      </Modal>
    </Space>
  );
}
