// SS-18 M2「我的开发 > 任务列表」：任务 = 开发笔记本（notebook），本页是它的卡片读视图。
//
// 诚实口径（项目铁律 honest degradation / 禁伪造）：
// · 后端未下发的字段一律渲染「未记录」，不补示例值、不编统计数；
// · 存量行（无 task_no / 无任务内存）是列表里正常的一张卡，默认不隐藏；
// · 列表接口只支持 contract_id / status 服务端过滤，关键词与任务级字段只在已载入的这一页里筛，
//   页面必须把这层范围说清楚，不自称全量检索。
// 「新增开发任务」是整页 `/flow/tasks/new`（图 2-2 四卡），建完由服务端回显任务编号。
import { Button, Card, Col, Input, Pagination, Row, Select, Space, Tag, Tooltip, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { type ReactNode, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ApiOutlined,
  BugOutlined,
  CalendarOutlined,
  CodeOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import {
  TASK_DEBUG_METHOD_OPTIONS,
  flowApi,
} from '../../services/flow';
import { CenteredLoading, EmptyState, QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import { RuntimeStatusBanner } from './RuntimeStatusBanner';
import {
  DEV_TASK_STATE_OPTIONS,
  EMPTY_DEV_TASK_FILTERS,
  devTaskCreateHref,
  activeDevTaskFilterLabels,
  buildDevTaskCards,
  devTaskEmptyDescription,
  filterDevTaskCards,
} from './devTasksModel';
import type { DevTaskCard, DevTaskFilters, DevTaskStateFilter } from './devTasksModel';

const { Text, Title } = Typography;

// 后端 page_size 上限 100（notebooks.py::list_notebooks）；超出部分如实告知，不假装已全部载入。
const DEV_TASK_PAGE_SIZE = 100;

/** 图 2-1 卡左上角标：按任务真实调试方法推导（禁伪造：没登记方法就给中性灰档，不编彩色图标）。 */
const DEBUG_ICON: Record<string, { cls: string; icon: ReactNode }> = {
  web_ide: { cls: 'is-blue', icon: <CodeOutlined /> },
  local_plugin: { cls: 'is-green', icon: <ApiOutlined /> },
  jupyter: { cls: 'is-purple', icon: <ExperimentOutlined /> },
};

/** 图 2-1 卡三带：图标方块 + 名称/裸任务编号 + 状态胶囊 / 带小图标的规格两行 / 三钮。 */
function DevTaskCardView({ card }: { card: DevTaskCard }) {
  const navigate = useNavigate();
  const ico = DEBUG_ICON[card.debug_method ?? ''] ?? { cls: 'is-gray', icon: <FileTextOutlined /> };
  // 胶囊配色照图：进行中=绿底绿字+环形进度图标，已结束=灰底灰字+减号圆（state_bucket 同口径）。
  const ended = card.state_bucket === 'ended';
  // 可执行时就是个普通站内跳转；不可执行时才包 Tooltip 说真原因（无已发布生产版本 / 未关联合约）。
  const execButton = (
    <Button className="cds-btn-outline-blue" icon={<PlayCircleOutlined />} disabled={!!card.exec_disabled_reason} onClick={() => navigate(card.exec_path)}>
      执行
    </Button>
  );
  return (
    <div className="cds-task-card">
      <div className="cds-task-card-head">
        <span className={`cds-task-ico ${ico.cls}`}>{ico.icon}</span>
        <div className="cds-task-card-titles">
          <Tooltip title={card.title}>
            <div className="cds-task-card-title">{card.title}</div>
          </Tooltip>
        </div>
        <span className={`cds-state-chip ${ended ? 'is-gray' : 'is-green'}`}>
          <span className="cds-state-dot" />
          {card.state.label}
        </span>
      </div>

      <dl className="cds-task-card-specs">
        <div className="cds-spec-row">
          <dt><CodeOutlined /> 模型调试方法</dt>
          <dd><Tag className="cds-tag-plain">{card.debug_method_text}</Tag></dd>
        </div>
        <div className="cds-spec-row">
          <dt><CalendarOutlined /> 创建时间</dt>
          <dd>{card.created_text}</dd>
        </div>
      </dl>

      <div className="cds-task-card-foot">
        <Button type="primary" icon={<BugOutlined />} onClick={() => navigate(card.workbench_path)}>
          调试
        </Button>
        {card.exec_disabled_reason ? (
          // 禁用态的 button 不吃鼠标事件，按 antd 文档包一层 span，Tooltip 才说得出真原因。
          // 包层必须 flex-basis:0px + min-width:0：0% basis 会被 Chromium 拿内容最小宽钉住（实测 82px）。
          <Tooltip title={card.exec_disabled_reason}>
            <span className="cds-foot-tip-wrap">{execButton}</span>
          </Tooltip>
        ) : (
          execButton
        )}
        <Button icon={<FileTextOutlined />} onClick={() => navigate(card.detail_path)}>
          详情
        </Button>
      </div>
    </div>
  );
}

/** 图 2-1 筛选带：每个条件左侧一个灰字标签（原型逐项给出）。 */
function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="cds-filter-field">
      <span className="cds-filter-label">{label}</span>
      {children}
    </div>
  );
}

export default function FlowDevTasks() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<DevTaskFilters>(EMPTY_DEV_TASK_FILTERS);
  const goCreate = () => navigate(devTaskCreateHref(filters.contract_id));
  const [pager, setPager] = useState({ page: 1, pageSize: 10 });

  const notebooksQuery = useQuery({
    queryKey: ['flow', 'dev-tasks', 'notebooks'],
    queryFn: () => flowApi.listNotebooks({ page: 1, page_size: DEV_TASK_PAGE_SIZE }),
  });
  const workloadsQuery = useQuery({
    queryKey: ['flow', 'dev-tasks', 'workloads'],
    queryFn: () => flowApi.listWorkloads({ page: 1, page_size: DEV_TASK_PAGE_SIZE }),
  });

  const notebooks = useMemo(() => notebooksQuery.data?.items ?? [], [notebooksQuery.data]);
  const workloads = useMemo(() => workloadsQuery.data?.items ?? [], [workloadsQuery.data]);

  const cards = useMemo(() => buildDevTaskCards(notebooks, workloads), [notebooks, workloads]);
  const visibleCards = useMemo(() => filterDevTaskCards(cards, filters), [cards, filters]);
  // 图 2-1 底部「共 N 条 / 10条/页 / 前往 X 页」分页带：在已载入范围内切页（口径写在页头）。
  const pageCount = Math.max(1, Math.ceil(visibleCards.length / pager.pageSize));
  const page = Math.min(pager.page, pageCount);
  const pageCards = useMemo(
    () => visibleCards.slice((page - 1) * pager.pageSize, page * pager.pageSize),
    [visibleCards, page, pager.pageSize],
  );

  const activeFilters = activeDevTaskFilterLabels(filters);
  const total = notebooksQuery.data?.total ?? 0;
  const patchFilters = (patch: Partial<DevTaskFilters>) => setFilters((previous) => ({ ...previous, ...patch }));
  const refetchAll = () => {
    void notebooksQuery.refetch();
    void workloadsQuery.refetch();
  };

  if (notebooksQuery.isPending) return <CenteredLoading tip="加载我的开发" />;
  if (notebooksQuery.isError || !notebooksQuery.data) {
    return <QueryErrorAlert error={notebooksQuery.error} message="我的开发加载失败" onRetry={refetchAll} />;
  }

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Title level={4} style={{ margin: 0 }}>我的开发</Title>
          <Text type="secondary">
            集中管理数据沙箱开发任务，支持模型调试、任务执行与详情查看
          </Text>
          {/* PNG 2-1 的页头只有「标题 + 一句灰字」（标题带 y163..207、卡 h94）；把计数从这一行拿掉才不拆行。
             诚实披露不丢：只有「确实被分页上限截断」时才多出一行点名（原型没这个场景），
             正常路径与图逐字一致。 */}
          {total > cards.length && (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                共 {total} 个开发任务，本页已载入 {cards.length} 个（后端分页上限 {DEV_TASK_PAGE_SIZE} 条，其余未载入）；筛选只作用于已载入的这些。
              </Text>
            </div>
          )}
        </div>
        <Space>
          {/* 图 2-1 页头只有一个「创建任务」钮（像素簇 135×40 → large 档，4-1 同判据）；刷新不占页头。 */}
          <Button size="large" type="primary" icon={<PlusOutlined />} onClick={goCreate}>
            创建任务
          </Button>
        </Space>
      </div>

      <RuntimeStatusBanner />

      <Card size="small" style={{ marginBottom: 16 }}>
        {/* 图 2-1 筛选带就四项：任务名称 / 任务状态 / 调试方法 / 重置+查询。 */}
        <Row gutter={[12, 12]} align="middle">
          <Col xs={24} sm={12} md={8}>
            <FilterField label="任务名称">
              {/* 真值输入框 238 宽、无清空按钮；maxWidth 兜住窄视口，不让固定宽撑出横向溢出。 */}
              <Input
                placeholder="请输入任务名称"
                style={{ width: 238, maxWidth: '100%', flex: 'none' }}
                value={filters.keyword}
                onChange={(event) => patchFilters({ keyword: event.target.value })}
              />
            </FilterField>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <FilterField label="任务状态">
              <Select
                allowClear
                className="cds-filter-state"
                style={{ width: 148, maxWidth: '100%', flex: 'none' }}
                placeholder="全部"
                value={filters.state ?? undefined}
                onChange={(value?: DevTaskStateFilter) => patchFilters({ state: value ?? null })}
                options={DEV_TASK_STATE_OPTIONS.map(({ label, value }) => ({ label, value }))}
              />
            </FilterField>
          </Col>
          <Col xs={12} sm={8} md={5}>
            <FilterField label="调试方法">
              <Select
                allowClear
                style={{ width: 148, maxWidth: '100%', flex: 'none' }}
                placeholder="全部"
                value={filters.debug_method ?? undefined}
                onChange={(value?: string) => patchFilters({ debug_method: value ?? null })}
                options={TASK_DEBUG_METHOD_OPTIONS.map(({ label, value }) => ({ label, value }))}
              />
            </FilterField>
          </Col>
          <Col>
            <Space size={8}>
              {/* PRD §3.2.1 筛选栏的「重置」：清空全部筛选条件回默认口径，不重发请求。 */}
              <Button onClick={() => setFilters(EMPTY_DEV_TASK_FILTERS)}>重置</Button>
              <Tooltip title="筛选即时生效；这里再向后端拉取一次列表。">
                <Button type="primary" icon={<SearchOutlined />} onClick={refetchAll}>查询</Button>
              </Tooltip>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* strict-2-1.md §2/§5：卡栅格直接坐在页面底色上（3×2，卡 380×218），分页是独立一条
         244,800,1172,64；因此这里既不再套「开发任务」包装卡，也不在卡上挂「当前显示 x / y 个」。 */}
      {visibleCards.length === 0 ? (
        <Card size="small">
          <EmptyState
            description={devTaskEmptyDescription(filters, cards.length)}
            action={activeFilters.length > 0 ? (
              <Button onClick={() => setFilters(EMPTY_DEV_TASK_FILTERS)}>清空筛选</Button>
            ) : (
              <Button type="primary" onClick={goCreate}>新增开发任务</Button>
            )}
          />
        </Card>
      ) : (
        <>
          <div className="cds-task-grid">
            {pageCards.map((card) => (
              <DevTaskCardView key={card.key} card={card} />
            ))}
          </div>
          <div className="cds-task-pager-band">
            <Pagination
              className="cds-task-pager"
              size="small"
              current={page}
              pageSize={pager.pageSize}
              total={visibleCards.length}
              showSizeChanger
              showQuickJumper
              showTotal={(value) => `共 ${value} 条`}
              onChange={(next, size) => setPager({ page: next, pageSize: size })}
            />
          </div>
        </>
      )}
    </div>
  );
}
