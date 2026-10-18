// 首页（原型 1、首页-平台总览.png = A-1 转录）—— 开发者工台的根路由首页。
//
// 版式按原型逐区块 1:1：页面标题带 → 欢迎带（两按钮）→ 4 张 KPI → 左「开发任务总览」/
// 右「沙箱内存使用」→ 「近 7 天调试与执行趋势」→ 「最新结果集」。文案逐字取原型，
// 数字一律取 GET /api/v1/flow/dashboard/home（口径与列表页同源，见 app/api/flow_runtime.py）。
//
// 与原型有意为之的两处偏差（都写在页面上，不藏在注释里）：
// · 内存卡副标题：原型写「实时占用」，后端只有「会话已分配的配额扣减」，没有任何实测内存来源
//   （响应 not_collected: memory.measured），所以副标题按真实口径写，并把后端 reason 挂在悬浮层；
// · 执行档分母：原型是 16 G，配置里不存在这一档（not_collected: memory.exec.quota），沿用租户配额。
// 缺数一律渲染「未记录」（homeModel 的不变式），示例值（张伟 / 12 / 1,204 次…）一个都不硬编码。
import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Button,
  Card,
  Col,
  Progress,
  Row,
  Select,
  Space,
  Table,
  Tooltip,
  Typography,
} from 'antd';
import {
  ApiOutlined,
  CheckCircleFilled,
  ExclamationCircleFilled,
  FileTextOutlined,
  InfoCircleOutlined,
  PlayCircleFilled,
  PlusOutlined,
  ProjectOutlined,
  RightOutlined,
  SwapOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  HOME_RECENT_ROWS,
  HOME_TREND_DAYS,
  UNKNOWN_TEXT,
  TASK_DEBUG_METHOD_OPTIONS,
  taskEnumLabel,
  useHomeOverview,
} from '../../services/flow';
import type { HomeMemoryPanel, HomeOverview, HomeOverviewTaskRow, HomeResultSetRow } from '../../services/flow';
import { CenteredLoading, QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import { useAuthStore } from '../../stores/authStore';
import { formatDevTaskTime } from './devTasksModel';
import {
  formatCallCount,
  formatGibFromMb,
  lastCallText,
  memoryPercent,
  memoryStatePill,
  memoryTicks,
  notCollectedReason,
  opsLogScopeText,
  opsLogSubtext,
  resultSetName,
  resultSetStatePill,
  taskFilterTabs,
  taskStatePill,
  trendAxisLabel,
  trendBarHeight,
  trendScale,
  type StatePill,
} from './homeModel';

const { Text, Title } = Typography;

/** 原型实测色（strict-1-home.md §4 色板）走 index.css 令牌，中性色只在这里补一份。 */
const ink = '#1d2129'; // 主文本
const muted = '#86909c'; // 三级文本（提示小字）
const footnoteInk = '#5d6877'; // 卡片脚注实测墨色
const quotaInk = '#8f98a4'; // 配额小字
const tickInk = '#abb2bc'; // 进度条刻度
const axisInk = '#a0aec3'; // X 轴标签
const barSoft = '#9cc4fb'; // 图表第二系列（浅蓝柱）
const dangerInk = '#e34d59'; // 异常圆标 + 异常文字
const tileTone: Record<string, { fg: string; bg: string }> = {
  // 图 1 KPI 芯片配色逐字取像素读数（strict-1-home.md §3/§4）：蓝 #e8f1ff/#1b6ef3、绿 #e8f7ee/#00a870、
  // 紫 #f1ecff/#7a5af8、橙 #fff3e8/#f77335；主题变量与后三档对不上（绿实测偏成 #e2f6ee/#00b070），所以写字面值。
  blue: { fg: 'var(--cds-primary)', bg: 'var(--cds-primary-soft)' },
  green: { fg: '#00a870', bg: '#e8f7ee' },
  purple: { fg: '#7a5af8', bg: '#f1ecff' },
  orange: { fg: '#f77335', bg: '#fff3e8' },
};
/** 卡头副标题/提示小字：12px #86909c（§4）。 */
const hint: CSSProperties = { fontSize: 12, color: muted };
/** 卡内正文脚注：12px 实测 #5d6877（§4）。 */
const footnote: CSSProperties = { fontSize: 12, color: footnoteInk };

/** 筛选标签：真值 strict-1-home.md §5/§6 h26 全圆角 r≈13；选中底 #e8f1ff 字 #1b6ef3；
    未选底 #f2f3f5 字 #4e5969（旧值 r6 / 底 #f2f4f7 / 字 #6b7280、高≈24）。 */
const tabStyle = (active: boolean): CSSProperties => ({
  fontSize: 12,
  lineHeight: '20px',
  padding: '3px 10px',
  borderRadius: 13,
  border: 'none',
  background: active ? 'var(--cds-primary-soft)' : '#f2f3f5',
  color: active ? 'var(--cds-primary)' : '#4e5969',
});

const TREND_RANGE_OPTIONS = [
  { value: 7, label: '近 7 天' },
  { value: 14, label: '近 14 天' },
  { value: 30, label: '近 30 天' },
];

function SectionLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} style={{ fontSize: 13 }}>
      {children} <RightOutlined style={{ fontSize: 10 }} />
    </Link>
  );
}

/**
 * 表内状态（strict-1-home.md §3#19-23 / #28-31、§8-10）：12×12 实心圆图标 + **无底色**文字，
 * 首页没有 #f2f3f5 药丸底 ⇒ 不再是 antd Tag。进行中=播放圆 #00a870，已结束/正常=对勾圆
 * （灰 #a6adb8 / 绿 #00a870），异常=感叹号圆 #e34d59；文字取 §4 正文 13px #1d2129。
 */
function StateText({ pill, icon }: { pill: StatePill; icon?: 'play' | 'check' | 'alert' }) {
  const toneColor =
    pill.tone === 'success'
      ? 'var(--cds-success)'
      : pill.tone === 'danger'
        ? dangerInk
        : pill.tone === 'warning'
          ? 'var(--cds-orange)'
          : '#a6adb8';
  const glyph = icon ?? (pill.tone === 'danger' || pill.tone === 'warning' ? 'alert' : 'check');
  const Icon = glyph === 'play' ? PlayCircleFilled : glyph === 'alert' ? ExclamationCircleFilled : CheckCircleFilled;
  return (
    <Text style={{ fontSize: 13, color: ink }}>
      <Icon style={{ fontSize: 12, color: toneColor, marginRight: 8 }} />
      {pill.label}
    </Text>
  );
}

/**
 * 内存卡的档位徽标（§8-14）：纯文字小药丸，无图标无圆点，h16 全圆角；
 * 占用中 底 #e8f7ee 字 #00a870，待执行/空闲 底 #f2f3f5 字 #86909c。
 */
function StateChip({ pill }: { pill: StatePill }) {
  const on = pill.tone === 'success';
  return (
    <span
      style={{
        flex: 'none',
        display: 'inline-block',
        padding: '0 8px',
        borderRadius: 999,
        fontSize: 12,
        lineHeight: '16px',
        background: on ? 'var(--cds-success-soft)' : '#f2f3f5',
        color: on ? 'var(--cds-success)' : muted,
      }}
    >
      {pill.label}
    </span>
  );
}

function KpiCard({
  testId,
  title,
  caliber,
  value,
  sub,
  tone,
  icon,
}: {
  testId: string;
  title: string;
  caliber: string;
  value: number;
  sub: ReactNode;
  tone: keyof typeof tileTone;
  icon: ReactNode;
}) {
  const palette = tileTone[tone];
  return (
    <div className="cds-kpi-cell" data-testid={testId}>
      {/* 图 1：芯片在左（40×40 圆角 8，浅底 + 同色系图标），标题/值/副行在右 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 13 }}>
        <span
          style={{
            width: 40,
            height: 40,
            flex: '0 0 auto',
            borderRadius: 8,
            display: 'grid',
            placeItems: 'center',
            fontSize: 16,
            color: palette.fg,
            background: palette.bg,
          }}
        >
          {icon}
        </span>
        <div style={{ minWidth: 0 }}>
          <Tooltip title={caliber}>
            <Text type="secondary" style={{ fontSize: 12, color: '#86909c' }}>
              {title}
            </Text>
          </Tooltip>
          <div style={{ fontSize: 24, lineHeight: '32px', fontWeight: 700, color: '#1d2129' }}>{value}</div>
          <Text type="secondary" style={{ fontSize: 12, color: '#86909c' }}>
            {sub}
          </Text>
        </div>
      </div>
    </div>
  );
}

/** 内存子卡：分子=已分配（会话按档位扣减），分母=租户配额；实测占用后端给不出，卡上不出现「实测」字样。 */
/**
 * 内存子卡：分子=已分配（会话按档位扣减），分母=租户配额；实测占用后端给不出，卡上不出现「实测」字样。
 * 真值 strict-1-home.md §5 这一段只有 标题/徽标/大数字/配额/进度条/刻度，且直接坐在卡体白底上
 * （§8：无内嵌盒描边与底色）⇒ 去掉 .cds-home-mem 外壳；执行档多出的「运行中·排队」与
 * 「16 G 独立配额未记录」不再占版面，收进配额数字的 Tooltip（quotaNote），信息一字不丢。
 */
function MemoryPanelCard({
  testId,
  title,
  panel,
  quotaMb,
  measuredReason,
  quotaNote,
}: {
  testId: string;
  title: string;
  panel: HomeMemoryPanel;
  quotaMb: number | null;
  measuredReason: string | null;
  quotaNote?: string;
}) {
  const pill = memoryStatePill(panel);
  const ticks = memoryTicks(quotaMb);
  const quotaTip = [measuredReason, quotaNote].filter(Boolean).join('；') || null;
  return (
    <div data-testid={testId}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text style={{ fontSize: 13 }}>{title}</Text>
        <StateChip pill={pill} />
      </div>
      <div style={{ margin: '6px 0 2px' }}>
        {/* 图上大号占用值是品牌蓝（非近黑），字号 24 */}
        <span style={{ fontSize: 24, fontWeight: 600, color: 'var(--cds-primary)' }}>{formatGibFromMb(panel.reserved_mb)}</span>
        {/* 配额小字：真值 12px #8f98a4（§5） */}
        <Text style={{ fontSize: 12, color: quotaInk }}>
          <Tooltip title={quotaTip ?? undefined}>
            <span>{` / ${quotaMb === null ? `${UNKNOWN_TEXT} 配额` : `${formatGibFromMb(quotaMb)} 配额`}`}</span>
          </Tooltip>
        </Text>
      </div>
      <Progress
        percent={memoryPercent(panel.reserved_mb, quotaMb)}
        showInfo={false}
        strokeColor="var(--cds-primary)"
        trailColor="#e3edfb"
        size="small"
      />
      {ticks.length ? (
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: tickInk }}>
          {ticks.map((tick) => (
            <span key={tick}>{tick}</span>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: tickInk }}>配额刻度{UNKNOWN_TEXT}</div>
      )}
    </div>
  );
}

/** 分组柱状图：两组（调试/执行）共用一个 scaling，与原型一致——无 y 轴刻度、柱上无数字标注。
 *  柱几何取 strict-1-home.md §5：柱宽 28、组内间隔 12（旧值 38% 宽 + 3px 间隔）；
 *  卡底那段后端 note 真值图上没有 ⇒ 收进卡头标题的 Tooltip，不再占版面。 */
function TrendChart({ overview, maxHeight = 92 }: { overview: HomeOverview; maxHeight?: number }) {
  const points = overview.trend.days;
  const scale = trendScale(points);
  return (
    <div data-testid="trend-chart">
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, paddingTop: 6 }}>
        {points.map((point) => (
          <div key={point.date} style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'center', gap: 12, height: maxHeight }}>
              <span
                data-bar="debug"
                title={`${trendAxisLabel(point.date)} 调试 ${point.debug} 次`}
                style={{
                  width: 28,
                  height: trendBarHeight(point.debug, scale, maxHeight),
                  borderRadius: '3px 3px 0 0',
                  background: point.debug > 0 ? 'var(--cds-primary)' : '#eef2f7',
                }}
              />
              <span
                data-bar="exec"
                title={`${trendAxisLabel(point.date)} 执行 ${point.exec} 次`}
                style={{
                  width: 28,
                  height: trendBarHeight(point.exec, scale, maxHeight),
                  borderRadius: '3px 3px 0 0',
                  background: point.exec > 0 ? barSoft : '#eef2f7',
                }}
              />
            </div>
            <div data-testid="trend-x-label" style={{ fontSize: 12, color: axisInk, marginTop: 4, textAlign: 'center' }}>
              {trendAxisLabel(point.date)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function FlowDevHome() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const [days, setDays] = useState<number>(HOME_TREND_DAYS);
  const [taskTab, setTaskTab] = useState<'all' | 'active' | 'ended'>('all');
  const overviewQuery = useHomeOverview(days, HOME_RECENT_ROWS);

  if (overviewQuery.isPending) return <CenteredLoading tip="加载首页" />;
  if (overviewQuery.isError || !overviewQuery.data) {
    return (
      <QueryErrorAlert
        error={overviewQuery.error}
        message="首页数据加载失败"
        onRetry={() => {
          void overviewQuery.refetch();
        }}
      />
    );
  }

  const overview = overviewQuery.data;
  const measuredReason = notCollectedReason(overview, 'memory.measured');
  const execQuotaReason = notCollectedReason(overview, 'memory.exec.quota');
  const recentTasks = overview.recent_tasks.filter((task) => {
    if (taskTab === 'active') return task.status !== 'archived';
    if (taskTab === 'ended') return task.status === 'archived';
    return true;
  });

  const taskColumns: ColumnsType<HomeOverviewTaskRow> = [
    {
      title: '任务名称',
      dataIndex: 'title',
      ellipsis: true,
      render: (title: string, row) => (
        <Tooltip title={`任务编号 ${row.task_no ?? UNKNOWN_TEXT}`}>
          <Text strong>{title}</Text>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      key: 'state',
      width: 96,
      render: (_, row) => <StateText pill={taskStatePill(row.status)} icon={row.status === 'archived' ? 'check' : 'play'} />,
    },
    {
      title: '调试方法',
      key: 'debug_method',
      width: 110,
      render: (_, row) => taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, row.debug_method),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: (value: string | null) => formatDevTaskTime(value) ?? <Text type="secondary">{UNKNOWN_TEXT}</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_, row) => (
        <Space size={10}>
          <Link to={`/notebooks?task=${encodeURIComponent(row.id)}`}>调试</Link>
          <Link to="/compute">执行</Link>
          <Link to={`/flow/tasks/${encodeURIComponent(row.id)}`}>详情</Link>
        </Space>
      ),
    },
  ];

  const resultSetColumns: ColumnsType<HomeResultSetRow> = [
    {
      title: '结果集名称',
      key: 'file_name',
      ellipsis: true,
      render: (_, row) => <Text>{resultSetName(row)}</Text>,
    },
    {
      title: '生成时间',
      dataIndex: 'created_at',
      width: 160,
      render: (value: string | null) => formatDevTaskTime(value) ?? <Text type="secondary">{UNKNOWN_TEXT}</Text>,
    },
    {
      title: '调用次数',
      key: 'call_count',
      width: 110,
      render: (_, row) => (
        <Tooltip title={overview.call_count_caliber}>
          <span>{formatCallCount(row.call_count)}</span>
        </Tooltip>
      ),
    },
    {
      title: '状态',
      key: 'state',
      width: 96,
      render: (_, row) => {
        const pill = resultSetStatePill(row.state);
        return row.state_reason ? (
          <Tooltip title={row.state_reason}>
            <span>
              <StateText pill={pill} />
            </span>
          </Tooltip>
        ) : (
          <StateText pill={pill} />
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      // 操作列文案真值「API详情」（strict-1-home.md §5 x1174–1238；group-A 的「查看结果集」是另一读法）
      render: (_, row) => <Link to={`/deliveries?contract=${encodeURIComponent(row.contract_id)}`}>API详情</Link>,
    },
  ];

  return (
    <div className="cds-home">
      {/* 首页顶部不再自带 24px 大标题：strict-1-home.md §2 行 48 / §5 行 152 量到的是
          面包屑带（浮于底色，文字墨高 14），已由 PageCrumb 统一负麦。 */}

      {/* C1 欢迎/操作卡：真值 strict-1-home.md §2 是一张卡（bbox 244,123,1172,78），不是坐在底色上的裸行；
          卡内一行标题 + 两按钮（标题 ink 268,153、次按钮 1077,142、主按钮 1241,143）。
          次按钮文字色取 §4 实测 #4e5969；描边真值只到 `[?]`（#e5e6eb）⇒ 不写 inline 描边色。 */}
      <Card size="small" data-testid="home-welcome" style={{ marginBottom: 16 }}>
        <Row justify="space-between" align="middle" gutter={[20, 16]}>
          <Col xs={24} md={12}>
            <Title level={4} style={{ margin: 0 }}>
              欢迎回来，{user?.username ?? UNKNOWN_TEXT}
            </Title>
          </Col>
          <Col>
            <Space size={12}>
              <Button size="large" style={{ color: '#4e5969' }} icon={<UnorderedListOutlined />} onClick={() => { void navigate('/flow/tasks'); }}>
                查看开发任务
              </Button>
              <Button size="large" type="primary" icon={<PlusOutlined />} onClick={() => { void navigate('/flow/tasks/new'); }}>
                创建开发任务
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* §2：卡片纵向间距 16、同一行两卡横向间距 20 ⇒ gutter [20,16]（行负边距的溢出由 index.css 接手）。 */}
      {/* 图 1 §5 C2 = 一个 1172×136 面板内 4 列（芯片 x285/570/855/1140，面板内无分隔线；像素探针 c2probe3.py）。 */}
      <div className="cds-kpi-strip" data-testid="home-kpi">
      <KpiCard
        testId="kpi-tasks"
        title="开发任务总数"
        caliber={`口径：${overview.dev_tasks.status_caliber.active}；${overview.dev_tasks.status_caliber.ended}`}
        value={overview.dev_tasks.total}
        sub={`进行中 ${overview.dev_tasks.active} · 已结束 ${overview.dev_tasks.ended}`}
        tone="blue"
        icon={<ProjectOutlined />}
      />
      <KpiCard
        testId="kpi-interfaces"
        title="结果集导出接口"
        caliber={`口径：在线=${overview.export_interfaces.status_caliber.online}、离线=${overview.export_interfaces.status_caliber.offline}（按合约当事方统计）`}
        value={overview.export_interfaces.total}
        sub={`在线 ${overview.export_interfaces.online} · 离线 ${overview.export_interfaces.offline}`}
        tone="green"
        icon={<ApiOutlined />}
      />
      <KpiCard
        testId="kpi-calls"
        title="今日数据调用次数"
        caliber={`口径：今日（UTC 自然日）**全平台**审计行 ${overview.calls_today.actions.join(' / ')}——对外 HMAC 调用没有人的主体，归不到单账号，本卡不是仅当前主体的计数；来源 ${overview.calls_today.source}`}
        value={overview.calls_today.count}
        sub={lastCallText(overview.calls_today.last_called_at)}
        tone="purple"
        icon={<SwapOutlined />}
      />
      <KpiCard
        testId="kpi-ops"
        title="今日操作日志"
        caliber={`${overview.ops_log_today.note} ${opsLogScopeText(overview.ops_log_today)}`}
        value={overview.ops_log_today.count}
        // 真值副行只有「失败 3」（§5）；可见性口径一字不丢，收进卡名标题的 Tooltip。
        sub={opsLogSubtext(overview.ops_log_today)}
        tone="orange"
        icon={<FileTextOutlined />}
      />
      </div>

      <Row gutter={[20, 16]} align="stretch" style={{ marginBottom: 16 }}>
        {/* 两栏宽度比取 A-1 bbox：左栏 x245–1030、右栏 x1060–1412 ⇒ 17 : 7 */}
        <Col xs={24} lg={17}>
          <Card
            size="small"
            data-testid="task-overview"
            style={{ height: '100%' }}
            title={
              <Space size={10} align="center" wrap>
                <span>开发任务总览</span>
                {/* ⓘ 真值 11×12 #86909c（§3#13），旧值 13px / #b6bec9 偏大偏浅 */}
                <InfoCircleOutlined style={{ color: muted, fontSize: 12 }} />
                <Text style={hint}>仅展示最近创建的 {HOME_RECENT_ROWS} 个任务</Text>
              </Space>
            }
            extra={<SectionLink to="/flow/tasks">查看全部任务</SectionLink>}
          >
            <Space size={8} wrap style={{ marginBottom: 12 }}>
              {taskFilterTabs(overview).map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  style={tabStyle(taskTab === tab.key)}
                  data-testid={`task-tab-${tab.key}`}
                  onClick={() => setTaskTab(tab.key as 'all' | 'active' | 'ended')}
                >
                  {tab.label} {tab.count}
                </button>
              ))}
            </Space>
            <Table<HomeOverviewTaskRow>
              rowKey="id"
              size="small"
              columns={taskColumns}
              dataSource={recentTasks}
              pagination={false}
              /* 自适应（specs/ss-18 §9 规则 2）：列最小可读宽合计 651（真机 max-content 实测）。
                 1440 档盒宽 762 不触发；≤1280 盒子不够时让卡内横滚，而不是把列压到 scrollWidth>clientWidth。 */
              scroll={{ x: 660 }}
              locale={tableEmpty(
                taskTab === 'all'
                  ? '还没有开发任务，点上方「创建开发任务」建第一个'
                  : `「${taskFilterTabs(overview).find((tab) => tab.key === taskTab)?.label ?? taskTab}」里暂时没有任务。`,
              )}
            />
          </Card>
        </Col>

        <Col xs={24} lg={7}>
          <Card
            size="small"
            data-testid="memory-panel"
            style={{ height: '100%' }}
            title={
              <Space size={10} align="center" wrap>
                <span>沙箱内存使用</span>
                {/* 可见副标题按真值长度收成一句（§5「调试与执行环境内存实时占用」）；
                    「实时占用」后端给不出（not_collected: memory.measured）⇒ 真实口径全句与
                    后端 reason 一起进 Tooltip，不假装有实测。 */}
                <Tooltip title={`口径：会话已分配的配额，非进程实测占用${measuredReason ? `。后端原文：${measuredReason}` : ''}`}>
                  <Text style={hint}>调试与执行环境内存占用</Text>
                </Tooltip>
              </Space>
            }
          >
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <MemoryPanelCard
                testId="memory-debug"
                title="调试内存使用"
                panel={overview.memory.debug}
                quotaMb={overview.memory.quota_mb}
                measuredReason={measuredReason}
              />
              <div>
                <MemoryPanelCard
                  testId="memory-exec"
                  title="执行内存使用"
                  panel={overview.memory.exec}
                  quotaMb={overview.memory.quota_mb}
                  measuredReason={execQuotaReason ?? measuredReason}
                  quotaNote={`正式执行：运行中 ${overview.memory.exec.running_runs ?? 0} · 排队 ${overview.memory.exec.pending_runs ?? 0}；执行档沿用${overview.memory.quota_mb === null ? UNKNOWN_TEXT : formatGibFromMb(overview.memory.quota_mb)}租户配额（16 G 独立配额${UNKNOWN_TEXT}）`}
                />
              </div>
              <div style={{ ...footnote, lineHeight: 1.9 }}>
                <div>调试内存随调试会话占用，退出调试环境后自动释放</div>
                <div>执行内存在任务正式执行时占用，执行完成后自动释放</div>
              </div>
            </Space>
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        data-testid="trend-panel"
        style={{ marginBottom: 16 }}
        title={
          <Space direction="vertical" size={2}>
            {/* 卡题真值「沙箱运行与执行次数趋势」（strict-1-home.md §5 ink 282,936,159,15；
                group-A 的「近 N 天…」是另一读法，strict 优先）；窗口天数由右侧下拉自己说。 */}
            <Tooltip title={overview.trend.note}>
              <span>沙箱运行与执行次数趋势</span>
            </Tooltip>
            <Text style={hint}>统计沙箱内在线调试运行与正式执行的次数，含失败重试</Text>
          </Space>
        }
        extra={
          <Space size={14} align="center">
            <Space size={6} align="center">
              <span style={{ width: 10, height: 10, borderRadius: 2, background: 'var(--cds-primary)', display: 'inline-block' }} />
              <Text style={{ fontSize: 12 }}>调试次数</Text>
            </Space>
            <Space size={6} align="center">
              <span style={{ width: 10, height: 10, borderRadius: 2, background: barSoft, display: 'inline-block' }} />
              <Text style={{ fontSize: 12 }}>执行次数</Text>
            </Space>
            {/* §8-7 明确否决日历图标：真值只有一个带边框的下拉（ink 1296,940,104,31）+ 右侧 8×5 箭头 */}
            <Select
              size="small"
              value={days}
              options={TREND_RANGE_OPTIONS}
              style={{ width: 104 }}
              onChange={(value) => setDays(value)}
              data-testid="trend-range"
            />
          </Space>
        }
      >
        <TrendChart overview={overview} />
      </Card>

      <Card
        size="small"
        data-testid="result-sets"
        title={
          <Space size={10} align="center" wrap>
            {/* 真值卡头只有蓝竖条 + 标题 + 灰小字，无导出图标（§3 在 y1200 一带只量到竖条 ink）；
                卡底那句「调用次数口径…」真值图上没有（§6：卡底即表格）⇒ 整句收进标题 Tooltip。 */}
            <Tooltip
              title={
                <span>
                  调用次数口径：{overview.call_count_caliber}
                  {overview.ops_log_today.visibility === 'self' ? '；今日操作日志与本页所有数字均为当前账号口径。' : ''}
                </span>
              }
            >
              <span>最新结果集</span>
            </Tooltip>
            <Text style={hint}>按合约约定通过 OpenApi 导出的结果集</Text>
          </Space>
        }
        extra={<SectionLink to="/deliveries">前往我的结果集</SectionLink>}
      >
        <Table<HomeResultSetRow>
          rowKey="bundle_id"
          size="small"
          columns={resultSetColumns}
          dataSource={overview.latest_result_sets}
          pagination={false}
          /* 同上：列最小宽合计 838；1440 档盒宽 1109 不触发（真值 1:1 不变）。 */
          scroll={{ x: 840 }}
          locale={tableEmpty('还没有结果集：任务产物需在成果交付中心打包')}
        />
      </Card>
    </div>
  );
}
