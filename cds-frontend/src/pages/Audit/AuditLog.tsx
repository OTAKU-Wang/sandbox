import { useState, type CSSProperties } from 'react';
import {
  Button, Card, Collapse, DatePicker, Descriptions, Input, Modal, Select, Space,
  Table, Tag, Tooltip, Typography, message,
} from 'antd';
import { CheckCircleFilled, CloseCircleFilled, DownloadOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { Dayjs } from 'dayjs';
import type { ColumnsType } from 'antd/es/table';
import { auditApi, type AuditRecord, type AuditRecordDetail } from '../../services/auditApi';
import { QueryErrorAlert, getErrorMessage } from '../../components/Feedback/QueryFeedback';
import AuditVerifyModal from '../../components/Audit/AuditVerifyModal';
import { useAuthStore } from '../../stores/authStore';
import { ROLE_GROUPS, ROLE_LABELS, hasAnyRole } from '../../utils/roles';
import {
  AUDIT_TABS, UNKNOWN_TEXT, auditActionColor, auditActionLabel, auditActionCodeText,
  auditDurationText, auditEnvText, auditLinkedData, auditTimeText,
} from './auditDisplay';
import './auditLog.css';

const { Text } = Typography;
const { RangePicker } = DatePicker;

/**
 * 4-1 记录区类型页签（照抄首页 DevHome 的 tabStyle）：选中 = 浅蓝底 + 主色字，
 * 未选中 = 白底 + 1px 灰边。块高 32（原型墨迹 y338..369）由「font 12 + 行高 20 +
 * 上下 5 + 1px 边」凑出，两侧同给 1px 边框（选中侧透明），切换时不会差 2px 高。
 */
const tabStyle = (active: boolean): CSSProperties => ({
  fontSize: 12,
  lineHeight: '20px',
  padding: '5px 12px',
  borderRadius: 6,
  border: `1px solid ${active ? 'transparent' : '#dfe6ee'}`,
  background: active ? 'var(--cds-primary-soft)' : '#fff',
  color: active ? 'var(--cds-primary)' : '#6b7280',
});

/**
 * 操作类型标签底色（4-1 首列标签逐枚取实心像素，无描边）：
 * 结果集导出 #e8f7ee / 数据调用 #f1ecff / 创建调试沙箱 #e8f1ff / 进入生产环境 #fff3e8。
 * antd 预设色名（green/purple/blue/volcano）给的是「浅底 + 描边 + 深字」三件套，
 * 与图上的实色浅底不同档，所以色板在这里点名，样式在 auditLog.css。
 */
const AUDIT_TAG_CLASSES: Record<string, string> = {
  green: 't-green',
  purple: 't-purple',
  blue: 't-blue',
  volcano: 't-orange',
  red: 't-orange',
  orange: 't-orange',
};

function auditTagClass(record: AuditRecord | AuditRecordDetail): string {
  return AUDIT_TAG_CLASSES[auditActionColor(record)] ?? 't-plain';
}

/** 操作类型下拉：只能选后端真的写进 audit_logs.action 的代码（等值匹配）。 */
const ACTION_OPTIONS = [
  'notebook.dev_session_created', 'compute.run.requested', 'sandbox.execute',
  'delivery.dataset.downloaded', 'openapi.call', 'user.login', 'user.login_failed',
  'contract.sign', 'data_product.publish', 'retention.purge',
].map((code) => ({ value: code, label: `${auditActionLabel({ action: code })} (${code})` }));

const OUTCOME_OPTIONS = [
  { value: 'success', label: '成功' },
  { value: 'failure', label: '失败' },
];

/**
 * 导出窗口：图 4-1 页头右侧只有「导出日志 + 刷新」两颗钮，屏上没有窗口选择件，
 * 后端 GET /audit/export 也只认 days+format。所以这里只留一个真值常量：
 * 悬浮层承诺的天数与真正发出去的 days 取自同一处，不再有第二份「可选范围」摆着没人用。
 */
const EXPORT_WINDOW_DAYS = 30;

interface Filters {
  action?: string;
  keyword?: string;
  outcome?: 'success' | 'failure';
  range?: [Dayjs | null, Dayjs | null] | null;
  eventType?: string;
}

const EMPTY_FILTERS: Filters = {};

function MissingField({ reason }: { reason: string }) {
  return (
    <Tooltip title={reason}>
      <Text type="secondary" style={{ borderBottom: '1px dashed currentColor', cursor: 'help' }}>
        {UNKNOWN_TEXT}
      </Text>
    </Tooltip>
  );
}

/** 操作结果列：图上没有胶囊，只有 13×12 实心图标 + 同色文字（成功 #00a870 / 失败 #e5484d）。 */
function OutcomeCell({ outcome }: { outcome?: 'success' | 'failure' }) {
  if (outcome === 'success') {
    return <Text className="cds-audit-outcome is-success"><CheckCircleFilled />成功</Text>;
  }
  if (outcome === 'failure') {
    return <Text className="cds-audit-outcome is-failure"><CloseCircleFilled />失败</Text>;
  }
  return <MissingField reason="后端按动作名派生操作结果（audit.py::audit_outcome）；本次返回未带该字段" />;
}

/** 4-2 的操作人：原型形如「张伟（沈阳数据中心R / 模型工程师）」，只拼库里真有的段。 */
function OperatorText({ record }: { record: AuditRecordDetail }) {
  if (!record.username) {
    return <MissingField reason="该动作无登录用户上下文（系统自动锚定、节点上报等），库里 user_id 为空" />;
  }
  const roleLabel = record.role ? (ROLE_LABELS[record.role as keyof typeof ROLE_LABELS] ?? record.role) : null;
  const parts = [record.organization || null, roleLabel].filter((part): part is string => !!part);
  if (parts.length === 0) return <span>{record.username}</span>;
  return <span>{record.username}<Text type="secondary">（{parts.join(' / ')}）</Text></span>;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    // 中途抛错（浏览器不吃 a[download]、扩展拦截、DOM 被卸载）也必须 revoke：
    // Blob URL 挂在文档上不会自己回收，导出一次泄一份 CSV
    URL.revokeObjectURL(url);
  }
}

export default function AuditLog() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [verifyTargetId, setVerifyTargetId] = useState<string | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const user = useAuthStore((s) => s.user);
  /**
   * 审计这一屏的唯一权限判定：后端 app/api/audit.py 对 /records、/records/{id}、
   * /anchor、/verify/{id}、/merkle-proof/{id}、/export 写的是同一组
   * require_roles(OPERATOR, REGULATOR, ADMIN)，与 ROLE_GROUPS.auditReaders 一字不差。
   * 所以「能看列表」并不比「能锚定/能验真/能导出」更宽：这一屏要嘛整屏可用，要嘛整屏
   * 置灰（路由级 RoleRoute 之外再摆一道，是为了角色被改掉而旧标签页还开着的那种时刻 —
   * 点下去只会拿 403 的按钮不该亮着）。
   */
  const canAuditOps = hasAnyRole(user?.role, ROLE_GROUPS.auditReaders);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['audit-records', page, pageSize, applied],
    queryFn: () => auditApi.list({
      page,
      page_size: pageSize,
      action: applied.action,
      keyword: applied.keyword?.trim() || undefined,
      outcome: applied.outcome,
      event_type: applied.eventType,
      date_from: applied.range?.[0]?.startOf('day').toISOString(),
      date_to: applied.range?.[1]?.endOf('day').toISOString(),
    }),
  });

  const detailQuery = useQuery({
    queryKey: ['audit-record', detailId],
    queryFn: () => auditApi.get(detailId as string),
    enabled: !!detailId,
  });

  const anchorMutation = useMutation({
    mutationFn: (ids: string[]) => auditApi.anchor(ids),
    onSuccess: (result, ids) => {
      // merkle_root 缺失或空串都当「没有根」：后端 merkle_service.get_root() 空树回 ""，
      // 网关吃掉字段时更是直接缺键 —— 那种时候宁可写清「没给根」，不拼 undefined
      const root = result.merkle_root?.trim();
      const rootText = root ? `, Merkle Root: ${root.slice(0, 16)}...` : '（本次响应未带 Merkle root，请在详情里复核）';
      const height = result.block_number != null ? `, 区块高度: ${result.block_number}` : '';
      message.success(`已锚定 ${result.anchored} 条记录${rootText}${height}`);
      queryClient.invalidateQueries({ queryKey: ['audit-records'] });
      // 失效的是「刚被锚定那几条」的详情缓存：id 取 mutation 入参，不读 detailId —
      // 请求回来的时候弹窗可能已经被关掉，那时 detailId 是 null，等于谁都没失效
      ids.forEach((id) => queryClient.invalidateQueries({ queryKey: ['audit-record', id] }));
    },
    onError: (err) => message.error(getErrorMessage(err, '锚定失败')),
  });

  const exportMutation = useMutation({
    // 后端 GET /audit/export 只接受 days+format；把列表筛选传过去只会让人以为导出跟随了筛选。
    mutationFn: () => auditApi.export({ days: EXPORT_WINDOW_DAYS, format: 'csv' }),
    onSuccess: ({ blob, filename }) => {
      saveBlob(blob, filename);
      message.success(`已导出 ${filename}`);
    },
    onError: (err) => message.error(getErrorMessage(err, '导出失败')),
  });

  const patchDraft = (patch: Filters) => setDraft((prev) => ({ ...prev, ...patch }));

  const applyDraft = () => {
    setApplied(draft);
    setPage(1);
  };

  const resetFilters = () => {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setPage(1);
  };

  const pickTab = (eventType?: string) => {
    setApplied((prev) => ({ ...prev, eventType }));
    setDraft((prev) => ({ ...prev, eventType }));
    setPage(1);
  };

  const columns: ColumnsType<AuditRecord> = [
    {
      title: '操作时间',
      dataIndex: 'created_at',
      key: 'created_at',
      // 列宽逐列照原型列界（表 x267..1391，内距 16 ⇒ 文字左缘 284/494/684/854/1034/1194）
      width: 210,
      render: (value: string) => auditTimeText(value) ?? UNKNOWN_TEXT,
    },
    {
      title: '操作类型',
      dataIndex: 'action',
      key: 'action',
      width: 190,
      render: (_, record) => (
        <Tooltip title={record.action}>
          <Tag className={`cds-audit-tag ${auditTagClass(record)}`}>{auditActionLabel(record)}</Tag>
        </Tooltip>
      ),
    },
    {
      title: '操作人',
      dataIndex: 'username',
      key: 'username',
      width: 170,
      render: (value: string | null, record) =>
        value || <MissingField reason={record.user_id ? '用户已被删除，库里只剩 user_id' : '该动作无登录用户上下文'} />,
    },
    {
      title: '来源 IP',
      dataIndex: 'ip_address',
      key: 'ip_address',
      width: 180,
      render: (value: string | null) => value || <MissingField reason="写入侧未带 ip_address，后端不反推来源" />,
    },
    { title: '操作结果', dataIndex: 'outcome', key: 'outcome', width: 160, render: (value) => <OutcomeCell outcome={value} /> },
    {
      title: '操作',
      key: 'operations',
      width: 213,
      render: (_, record) => <a onClick={() => setDetailId(record.id)}>详情</a>,
    },
  ];

  const detail = detailQuery.data;
  const envText = detail ? auditEnvText(detail.detail) : null;
  const linkedData = detail ? auditLinkedData(detail.detail) : null;
  const durationText = detail ? auditDurationText(detail.detail) : null;
  const optionalMissing = detail
    ? [
        envText ? null : '环境标识',
        detail.related_contract ? null : '关联合约',
        linkedData ? null : '关联数据',
        durationText ? null : '耗时',
      ].filter((item): item is string => !!item)
    : [];

  return (
    <div className="cds-audit-screen">
      {/* 卡1 页头卡（原型 y122..215，h94）：标题 20px + 灰说明，右侧只有导出日志 + 刷新两颗钮（图真值）。
          标题带在原型里就是一张卡（左上角圆角与卡3 同形：y122 起 x254 → y124 x248 → y126 x245），
          之前它是裸排在页底色上的，整屏纵向因此差 20px 以上。 */}
      <Card className="cds-audit-pagehead" style={{ marginBottom: 16 }}>
        <div className="cds-audit-pagehead-row">
          <div>
            <div className="cds-audit-title">操作日志</div>
            <div className="cds-audit-desc">
              留存创建调试沙箱、进入生产环境、结果集导出、数据调用等关键环节的操作记录，日志仅可查看与导出，不可修改或删除
            </div>
          </div>
          <Space size={12}>
            {/* 图 4-1 页头右侧只有「导出日志 + 刷新」两颗钮，屏上无窗口选择件：窗口是固定值。
                悬浮层只说人话（范围 + 不跟随列表筛选），不把内部路由/内部符号印给用户看。 */}
            <Tooltip
              title={canAuditOps
                ? `导出近 ${EXPORT_WINDOW_DAYS} 天的全部操作日志（CSV）：只按天数取，不跟随列表筛选`
                : '导出与锚定需运营方 / 监管方 / 系统管理员角色，当前角色无权调用'}
            >
              <span>
                <Button
                  size="large"
                  className="cds-audit-btn-export"
                  icon={<DownloadOutlined />}
                  disabled={!canAuditOps}
                  loading={exportMutation.isPending}
                  onClick={() => exportMutation.mutate()}
                >
                  导出日志
                </Button>
              </span>
            </Tooltip>
            <Button size="large" type="primary" className="cds-audit-btn-refresh" icon={<ReloadOutlined />} loading={isFetching} onClick={() => { void refetch(); }}>
              <span>刷新</span>
            </Button>
          </Space>
        </div>
      </Card>

      {/* 卡2 筛选卡（原型 y232..298）：一行六件套，控件 40 高（large 档），宽度逐件照图。 */}
      <Card className="cds-audit-filter" style={{ marginBottom: 16 }}>
        <div className="cds-audit-filter-row">
          <div className="cds-filter-field f-action">
            <span className="cds-filter-label">操作类型</span>
            <Select
              size="large"
              placeholder="全部类型"
              allowClear
              value={draft.action}
              onChange={(value) => patchDraft({ action: value })}
              options={ACTION_OPTIONS}
            />
          </div>
          <div className="cds-filter-field f-keyword">
            <span className="cds-filter-label">关键字</span>
            <Input
              size="large"
              placeholder="操作对象 / 操作人"
              allowClear
              value={draft.keyword}
              onChange={(e) => patchDraft({ keyword: e.target.value })}
              onPressEnter={applyDraft}
            />
          </div>
          <div className="cds-filter-field f-outcome">
            <span className="cds-filter-label">操作结果</span>
            <Select
              size="large"
              placeholder="全部"
              allowClear
              value={draft.outcome}
              onChange={(value) => patchDraft({ outcome: value })}
              options={OUTCOME_OPTIONS}
            />
          </div>
          <div className="cds-filter-field f-range">
            <span className="cds-filter-label">操作时间</span>
            <RangePicker
              size="large"
              placeholder={['开始日期', '结束日期']}
              separator="-"
              value={draft.range}
              onChange={(range) => patchDraft({ range })}
            />
          </div>
          <Space className="cds-audit-actions" size={12}>
            <Button size="large" className="cds-audit-btn-reset" onClick={resetFilters}><span>重置</span></Button>
            <Button size="large" type="primary" className="cds-audit-btn-search" icon={<SearchOutlined />} onClick={applyDraft}><span>查询</span></Button>
          </Space>
        </div>
      </Card>

      {/* 卡3 记录卡（原型 y316..1044）：卡头 72 高且无下边框，卡体左右 24 内距。 */}
      <Card
        className="cds-audit-records"
        title="操作记录"
        extra={
          <Space size={12} wrap>
            <button
              type="button"
              style={tabStyle(!applied.eventType)}
              data-testid="task-tab-all"
              onClick={() => pickTab(undefined)}
            >
              全部
            </button>
            {AUDIT_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                style={tabStyle(applied.eventType === tab.key)}
                data-testid={`task-tab-${tab.key}`}
                onClick={() => pickTab(applied.eventType === tab.key ? undefined : tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </Space>
        }
      >
        {isError && <QueryErrorAlert error={error} message="审计日志加载失败" onRetry={() => { void refetch(); }} />}
        <Table<AuditRecord>
          columns={columns}
          dataSource={data?.items || []}
          rowKey="id"
          loading={isLoading}
          pagination={{
            current: page,
            pageSize,
            total: data?.total || 0,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 条记录`,
            onChange: (nextPage, nextSize) => {
              setPage(nextPage);
              setPageSize(nextSize);
            },
          }}
        />
      </Card>

      <Modal
        className="cds-detail-modal cds-audit-modal"
        open={!!detailId}
        width={720}
        onCancel={() => setDetailId(null)}
        footer={
          <Space size={12}>
            {detail && (
              <>
                {/* 验真/锚定与导出共用 canAuditOps：后端这几个端点要的是同一组角色 */}
                <Tooltip title={canAuditOps ? undefined : '存证验真需运营方 / 监管方 / 系统管理员角色，当前角色无权调用'}>
                  <span>
                    <Button
                      size="large"
                      disabled={!canAuditOps}
                      onClick={() => { setVerifyTargetId(detail.id); setVerifyOpen(true); }}
                    >
                      存证验真
                    </Button>
                  </span>
                </Tooltip>
                <Tooltip
                  title={!canAuditOps
                    ? '锚定需运营方 / 监管方 / 系统管理员角色，当前角色无权调用'
                    : detail.blockchain_tx_hash
                      ? '本条已锚定，重复锚定会被后端拒绝'
                      : '把本条写入 Merkle 树并锚定到链上'}
                >
                  <span>
                    <Button
                      size="large"
                      disabled={!canAuditOps || !!detail.blockchain_tx_hash}
                      loading={anchorMutation.isPending}
                      onClick={() => anchorMutation.mutate([detail.id])}
                    >
                      锚定本条
                    </Button>
                  </span>
                </Tooltip>
              </>
            )}
            <Button size="large" onClick={() => setDetailId(null)}><span>关闭</span></Button>
          </Space>
        }
        title={
          detail ? (
            <div>
              <span>操作日志详情</span>
              <Tag className={`cds-audit-modal-tag ${auditTagClass(detail)}`}>{auditActionLabel(detail)}</Tag>
              <div style={{ marginTop: 4 }}>
                <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                  日志编号{' '}
                  <Tooltip title="由 created_at（UTC）+ 记录 ID 前 4 位确定性生成，仅作展示；库里没有独立的业务编号列">
                    <Text code style={{ fontWeight: 400 }}>{detail.display_no ?? detail.id}</Text>
                  </Tooltip>
                  {' '}· 生成时间 {auditTimeText(detail.created_at) ?? UNKNOWN_TEXT}
                </Text>
              </div>
            </div>
          ) : '操作日志详情'
        }
      >
        {detailQuery.isError && (
          <QueryErrorAlert error={detailQuery.error} message="详情加载失败" onRetry={() => { void detailQuery.refetch(); }} />
        )}
        {detail && (
          <div style={{ maxHeight: 520, overflowY: 'auto' }}>
            {/* 分区标题：4×16 蓝条 + 16px 墨字（原型 x24..28 + x37..96），图上没有分隔线 */}
            <div className="cds-audit-section">基础信息</div>
            <Descriptions className="cds-audit-basics" layout="vertical" column={2} colon={false}>
              <Descriptions.Item label="操作时间">
                {auditTimeText(detail.created_at) ?? <MissingField reason="库里没有 created_at" />}
              </Descriptions.Item>
              <Descriptions.Item label="操作人"><OperatorText record={detail} /></Descriptions.Item>
              <Descriptions.Item label="来源 IP">
                {detail.ip_address || <MissingField reason="写入侧未带 ip_address，后端不反推来源" />}
              </Descriptions.Item>
              <Descriptions.Item label="操作结果"><OutcomeCell outcome={detail.outcome} /></Descriptions.Item>
              <Descriptions.Item label="操作对象" span={2}>
                {auditActionLabel(detail)}
                {detail.resource_id ? <> · <Text code>{detail.resource_id}</Text></> : <Text type="secondary"> · {UNKNOWN_TEXT}</Text>}
              </Descriptions.Item>
            </Descriptions>

            <div className="cds-audit-section">操作内容</div>
            {/* 图上这五项坐在一条 #f7f9fc 灰面板里（x23..696 y354..511，行距 28） */}
            <div className="cds-audit-panel">
              <Descriptions className="cds-audit-content" column={1}>
                <Descriptions.Item label="操作类型"><Text code>{auditActionCodeText(detail)}</Text></Descriptions.Item>
                {envText && <Descriptions.Item label="环境标识"><Text code>{envText}</Text></Descriptions.Item>}
                {detail.related_contract && (
                  <Descriptions.Item label="关联合约">
                    {detail.related_contract.title}（编号 <Text code>{detail.related_contract.contract_no}</Text>）
                  </Descriptions.Item>
                )}
                {linkedData && (
                  <Descriptions.Item label="关联数据">{linkedData.join('、')}</Descriptions.Item>
                )}
                {durationText && <Descriptions.Item label="耗时">{durationText}</Descriptions.Item>}
                <Descriptions.Item label="detail 原文">
                  {/* 图外补充（转录 y25–613 无此块）：保留取证能力，但默认收起不抢版面 */}
                  <Collapse
                    size="small"
                    items={[{
                      key: 'raw',
                      label: '展开查看 detail 原文（后端下发的 JSON，未做改写）',
                      children: (
                        <pre style={{ fontSize: 12, maxHeight: 180, overflow: 'auto', margin: 0 }}>
                          {JSON.stringify(detail.detail, null, 2)}
                        </pre>
                      ),
                    }]}
                  />
                </Descriptions.Item>
              </Descriptions>
            </div>
            {optionalMissing.length > 0 && (
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
                本条审计未随附：{optionalMissing.join(' / ')}（audit_logs 无对应列，后端不下发即不显示）
              </Text>
            )}

            <div className="cds-audit-section">不可篡改存证</div>
            <Descriptions className="cds-audit-proof" column={1}>
              <Descriptions.Item label="存证状态">
                {detail.blockchain_tx_hash ? <Tag color="green">已锚定</Tag> : <Tag>未锚定</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="存证 TX">
                {detail.blockchain_tx_hash
                  ? <Text code>{detail.blockchain_tx_hash}</Text>
                  : <MissingField reason="该记录还没有 blockchain_tx_hash；锚定后可用「存证验真」做 SM3 + Merkle 重算比对" />}
              </Descriptions.Item>
            </Descriptions>
          </div>
        )}
      </Modal>

      <AuditVerifyModal recordId={verifyTargetId} open={verifyOpen} onClose={() => setVerifyOpen(false)} />
    </div>
  );
}
