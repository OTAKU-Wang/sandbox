import { useEffect, useState, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { useCrumbStackStore } from '../../stores/crumbStackStore';
import BundlePreviewModal from './BundlePreviewModal';
import { useCrumbSegments } from '../../components/Layout/useCrumbSegments';
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  DatePicker,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  BugOutlined,
  CheckCircleOutlined,
  CopyOutlined,
  DashboardOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  FieldTimeOutlined,
  InfoCircleOutlined,
  DownloadOutlined,
  KeyOutlined,
  LeftOutlined,
  LinkOutlined,
  LockOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  SwapOutlined,
} from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { sandboxApi } from '../../services/sandboxApi';
import {
  OPEN_API_DEBUG_BODY_LIMIT,
  OPEN_API_DEBUG_KEY_SCOPES,
  OPENAPI_CALLS_WINDOW,
  UNKNOWN_TEXT,
  WEBHOOK_EVENT_OPTIONS,
  buildCurlExample,
  buildRequestExample,
  callOpenApiWithApiKey,
  computeEndpointCallMetrics,
  describeFlowError,
  flattenOpenApiOperations,
  flowApi,
  flowKeys,
  formatBytes,
  formatDateTime,
  useApiEndpoints,
  useBuildDatasetBundle,
  useContractOpenApiDoc,
  useCreateEndpoint,
  useCreateWebhook,
  useDatasetBundles,
  useDeleteEndpoint,
  useDeleteWebhook,
  useEndpointCalls,
  useRedeliver,
  useRotateWebhookSecret,
  useTestWebhook,
  useUpdateEndpoint,
  useUpdateWebhook,
  useWebhookDeliveries,
  useWebhooks,
  useWorkload,
  useWorkloads,
} from '../../services/flow';
import type {
  ApiEndpoint,
  ApiEndpointCall,
  ApiEndpointCreatePayload,
  ApiEndpointUpdatePayload,
  DatasetBundle,
  DatasetBundleRow,
  OpenApiDebugAttempt,
  OpenApiDoc,
  WebhookDelivery,
  WebhookSubscription,
} from '../../services/flow';
import { apiKeysApi } from '../../services/apiKeysApi';
import type { ApiKeyCreated } from '../../services/apiKeysApi';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import {
  DELIVERY_STATUS_META,
  WEBHOOK_STATUS_META,
  attemptLabel,
  bundleReviewMeta,
  bundleWatermarkMeta,
  openApiPathForSlug,
  responseStatusColor,
  statusMeta,
formatMinute,
} from './display';
import type { StatusMeta } from './display';
import { useContractOptions } from './useContractOptions';
import { copyText } from './copyText';
import './deliveryOpenapi.css';

const { Text, Title, Paragraph, Link } = Typography;

function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    const ok = await copyText(text);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } else {
      message.error('复制失败，请手动选择文本复制');
    }
  };
  return (
    <Button size="small" icon={<CopyOutlined />} onClick={() => { void handleCopy(); }}>
      {copied ? '已复制' : label}
    </Button>
  );
}

function SecretReveal({ secret, onClose }: { secret: string; onClose: () => void }) {
  return (
    <Alert
      type="warning"
      showIcon
      icon={<LockOutlined />}
      message="签名密钥仅此一次可见"
      description={
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            后端只在创建/轮转时返回一次 secret，关闭后无法再次查看；如遗失请轮转密钥。请先复制并写入接收端的密钥管理，再关闭本提示。
          </Text>
          <Paragraph code copyable={{ text: secret }} style={{ marginBottom: 0, wordBreak: 'break-all' }}>
            {secret}
          </Paragraph>
          <Space>
            <CopyButton text={secret} label="复制密钥" />
            <Button size="small" onClick={onClose}>
              我已保存，关闭
            </Button>
          </Space>
        </Space>
      }
    />
  );
}

async function describeDownloadError(error: unknown): Promise<string> {
  const data = (error as { response?: { data?: unknown } } | null)?.response?.data;
  if (data instanceof Blob) {
    try {
      return describeFlowError(JSON.parse(await data.text()), '成果下载失败');
    } catch {
      return '成果下载失败：后端返回了无法解析的错误内容';
    }
  }
  return describeFlowError(error, '成果下载失败');
}

async function saveBundleFile(bundleId: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `cds-bundle-${bundleId}.zip`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

function DatasetBundleTab({ contractId }: { contractId: string | null }) {
  const [form] = Form.useForm();
  const [bundle, setBundle] = useState<DatasetBundle | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const { data: contracts } = useContractOptions();
  const sessionsQuery = useQuery({
    queryKey: ['sandbox-sessions', 'delivery-bundle', 1, 50],
    queryFn: () => sandboxApi.list({ page: 1, page_size: 50 }),
  });
  const bundleMutation = useBuildDatasetBundle();
  const bundlesQuery = useDatasetBundles(contractId, historyPage, 10);

  const build = () => {
    void form
      .validateFields()
      .then((values: { session_id: string; contract_id: string }) => {
        bundleMutation.mutate(
          { session_id: values.session_id, contract_id: values.contract_id },
          {
            onSuccess: (result) => {
              setBundle(result);
              setHistoryPage(1);
              void bundlesQuery.refetch();
              message.success(`打包完成：${result.files?.length ?? 0} 个文件，${formatBytes(result.size_bytes)}`);
            },
            onError: (err) => message.error(describeFlowError(err, '打包失败')),
          },
        );
      })
      .catch(() => undefined);
  };

  /** 下载走同一条已有通道（flowApi.downloadBundle → 浏览器落盘），历史行与新包共用。 */
  const downloadById = (bundleId: string) => {
    setDownloadingId(bundleId);
    void flowApi
      .downloadBundle(bundleId)
      .then(
        (blob) => {
          void saveBundleFile(bundleId, blob);
          message.success('已开始下载');
        },
        (err: unknown) => {
          void describeDownloadError(err).then((text) => message.error(text));
        },
      )
      .finally(() => setDownloadingId(null));
  };

  const download = () => {
    if (!bundle) return;
    downloadById(bundle.bundle_id);
  };

  const [previewBundleId, setPreviewBundleId] = useState<string | null>(null);

  const historyColumns: ColumnsType<DatasetBundleRow> = [
    {
      title: '成果包',
      dataIndex: 'bundle_id',
      render: (bundleId: string, record) => (
        <Space direction="vertical" size={0}>
          <Text code copyable={{ text: bundleId }}>
            {bundleId.slice(0, 8)}…
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {record.contract_no}
          </Text>
        </Space>
      ),
    },
    { title: '文件数', dataIndex: 'file_count', width: 80 },
    {
      title: '数据行数',
      dataIndex: 'row_count',
      width: 100,
      render: (value: number | null | undefined) =>
        typeof value === 'number' ? value : (
          <Tooltip title="行数只统计包内数据表文件（.csv/.tsv/.jsonl）；无数据表或该列上线前打的包不记行数。">
            <Tag>未记录</Tag>
          </Tooltip>
        ),
    },
    { title: '大小', dataIndex: 'size_bytes', width: 100, render: (size: number) => formatBytes(size) },
    { title: '生成时间', dataIndex: 'created_at', width: 170, render: (value: string | null) => formatDateTime(value) },
    {
      title: 'T5 审查',
      key: 't5_review',
      width: 150,
      render: (_: unknown, record) => {
        const meta = bundleReviewMeta(record.t5_review_status);
        return record.t5_review_status ? (
          <Tag color={meta.color}>{meta.label}</Tag>
        ) : (
          <Tooltip title="T5 审查状态是打包时落在交付包行上的结论；该列上线前打的包不回溯反推。">
            <Tag>{meta.label}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '水印',
      key: 'watermark',
      width: 130,
      render: (_: unknown, record) => {
        const meta = bundleWatermarkMeta(record.watermark_status);
        return record.watermark_status ? (
          <Tag color={meta.color}>{meta.label}</Tag>
        ) : (
          <Tooltip title="水印状态与打包时写进包内 MANIFEST.json 的逐文件结论同源；该列上线前打的包不作推定。">
            <Tag>{meta.label}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      render: (_: unknown, record) => (
        <Space>
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => setPreviewBundleId(record.bundle_id)}
          >
            预览
          </Button>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={downloadingId === record.bundle_id}
            onClick={() => downloadById(record.bundle_id)}
          >
            下载
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Row gutter={16}>
      <Col xs={24} lg={10}>
        <Card size="small" title="选择来源会话与合约">
          <Form form={form} layout="vertical">
            <Form.Item name="session_id" label="沙箱会话" rules={[{ required: true, message: '请选择会话' }]} extra="仅列出当前账号可见的会话">
              <Select
                showSearch
                optionFilterProp="label"
                placeholder="选择产出成果的会话"
                loading={sessionsQuery.isLoading}
                notFoundContent={sessionsQuery.isError ? '会话列表加载失败' : '暂无会话'}
                options={(sessionsQuery.data?.items ?? []).map((session) => ({
                  label: `${session.id.slice(0, 8)}… · ${session.status}`,
                  value: session.id,
                }))}
              />
            </Form.Item>
            <Form.Item name="contract_id" label="关联合约" rules={[{ required: true, message: '请选择合约' }]} extra="水印与输出管控按合约策略执行">
              <Select
                showSearch
                optionFilterProp="label"
                placeholder="选择合约"
                options={(contracts ?? []).map((contract) => ({ label: `${contract.title}（${contract.contract_no}）`, value: contract.id }))}
              />
            </Form.Item>
            <Button type="primary" block loading={bundleMutation.isPending} onClick={build}>
              生成成果包
            </Button>
          </Form>
        </Card>
      </Col>
      <Col xs={24} lg={14}>
        {!bundle && (
          <Card size="small">
            <Text type="secondary">生成后此处显示文件清单与下载入口；未经打包不会有可下载内容。</Text>
          </Card>
        )}
        {bundle && (
          <Card
            size="small"
            title="成果包"
            extra={
              <Button size="small" type="primary" icon={<DownloadOutlined />} onClick={download}>
                下载
              </Button>
            }
          >
            <Descriptions size="small" column={2} style={{ marginBottom: 12 }}>
              <Descriptions.Item label="成果包 ID">{bundle.bundle_id}</Descriptions.Item>
              <Descriptions.Item label="总大小">{formatBytes(bundle.size_bytes)}</Descriptions.Item>
              <Descriptions.Item label="水印">
                {bundle.watermark_applied ? <Tag color="green">已嵌入水印</Tag> : <Tag color="orange">后端报告未加水印</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="文件数">{bundle.files?.length ?? 0}</Descriptions.Item>
            </Descriptions>
            <Table<{ name: string; size_bytes: number }>
              rowKey="name"
              size="small"
              pagination={false}
              dataSource={bundle.files ?? []}
              locale={tableEmpty('成果包内没有文件')}
              columns={[
                { title: '文件', dataIndex: 'name' },
                { title: '大小', dataIndex: 'size_bytes', width: 120, render: (size: number) => formatBytes(size) },
              ]}
            />
            {!bundle.watermark_applied && (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 12 }}
                message="该成果包未嵌入水印：请按合规要求确认是否允许外发，必要时重新打包。"
              />
            )}
          </Card>
        )}
      </Col>
      <Col xs={24}>
        <Card
          size="small"
          title="成果包历史"
          extra={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => {
                void bundlesQuery.refetch();
              }}
            >
              刷新
            </Button>
          }
        >
          {bundlesQuery.isError && (
            <QueryErrorAlert
              error={bundlesQuery.error}
              message="成果包历史加载失败"
              onRetry={() => {
                void bundlesQuery.refetch();
              }}
            />
          )}
          <Table<DatasetBundleRow>
            rowKey="bundle_id"
            size="small"
            columns={historyColumns}
            dataSource={bundlesQuery.data?.items ?? []}
            loading={bundlesQuery.isLoading}
            locale={tableEmpty(contractId ? '该合约还没有生成过成果包' : '还没有生成过成果包（选择合约可只看它的历史）')}
            pagination={{
              current: historyPage,
              pageSize: 10,
              total: bundlesQuery.data?.total ?? 0,
              size: 'small',
              showSizeChanger: false,
              onChange: (next) => setHistoryPage(next),
            }}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            列表来自交付包归属表{contractId ? '，已按所选合约过滤' : ''}：只看得见自己作为买方/提供方的合约下的包。下载仍会逐次按合约当事方鉴权，
            加固前生成的旧包会被以“未经出口审查”拦下。
          </Text>
          <BundlePreviewModal bundleId={previewBundleId} onClose={() => setPreviewBundleId(null)} />
        </Card>
      </Col>
    </Row>
  );
}

function OpenApiPreview({ doc, endpoints }: { doc: OpenApiDoc | null; endpoints: ApiEndpoint[] }) {
  const rows = flattenOpenApiOperations(doc);
  const firstSlug = endpoints[0]?.slug;
  const firstRow = rows[0];
  const curl = firstSlug ? buildCurlExample(firstSlug, buildRequestExample(firstRow, endpoints[0])) : '';

  if (!doc) {
    return (
      <Alert
        type="info"
        showIcon
        message="该合约还没有可展示的 OpenAPI 文档"
        description="端点创建后，后端会按合约与负载生成 OpenAPI 3.1 文档；未生成时此处保持空白，不做示例伪造。"
      />
    );
  }

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Descriptions size="small" column={3}>
        <Descriptions.Item label="文档标题">{doc.info?.title ?? '后端未提供 info.title'}</Descriptions.Item>
        <Descriptions.Item label="版本">{doc.info?.version ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="OpenAPI">{doc.openapi ?? '-'}</Descriptions.Item>
      </Descriptions>

      {rows.length ? (
        <Table
          rowKey="key"
          size="small"
          pagination={false}
          dataSource={rows}
          columns={[
            { title: '方法', dataIndex: 'method', width: 80, render: (method: string) => <Tag color="blue">{method}</Tag> },
            { title: '路径', dataIndex: 'path' },
            { title: '说明', dataIndex: 'summary', render: (summary: string) => summary || <Text type="secondary">文档未提供 summary</Text> },
            {
              title: '参数',
              dataIndex: 'parameters',
              render: (parameters: string[]) =>
                parameters.length ? parameters.join('，') : <Text type="secondary">无</Text>,
            },
            {
              title: '请求体字段',
              dataIndex: 'bodyFields',
              render: (fields: { name: string; type: string; required: boolean }[], record) =>
                fields.length ? (
                  <Space size={4} wrap>
                    {fields.map((field) => (
                      <Tag key={field.name} color={field.required ? 'red' : 'default'}>
                        {field.name}: {field.type}
                      </Tag>
                    ))}
                  </Space>
                ) : record.requestBodyRequired ? (
                  <Text type="secondary">需请求体，但文档未声明字段</Text>
                ) : (
                  <Text type="secondary">无</Text>
                ),
            },
            {
              title: '响应码',
              dataIndex: 'responseCodes',
              width: 140,
              render: (codes: string[]) => codes.join(' / ') || '-',
            },
          ]}
        />
      ) : (
        <Text type="secondary">文档内没有任何接口定义</Text>
      )}

      {curl && (
        <div>
          <Space style={{ marginBottom: 6 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              调用示例（外部路径固定 POST {openApiPathForSlug(firstSlug ?? '')}，使用平台 API Key 鉴权）
            </Text>
            <CopyButton text={curl} label="复制 curl" />
          </Space>
          <pre className="cds-json-block">{curl}</pre>
        </div>
      )}

      <Collapse
        size="small"
        items={[
          {
            key: 'raw',
            label: '查看后端返回的原始 OpenAPI JSON',
            children: <pre className="cds-json-block">{JSON.stringify(doc, null, 2)}</pre>,
          },
        ]}
      />
    </Space>
  );
}

const CDS_PRIMARY = '#1b6ef3';
const CDS_TEXT_GRAY = 'rgba(0, 0, 0, 0.45)';
const CDS_OK = '#52c41a';
const CDS_FAIL = '#ff4d4f';

const NO_EXPIRY_HINT =
  '后端端点记录没有有效期字段：到期自动下线未实现，接口长期有效，需要停止对外服务时在列表里下线。';
const ONLINE_DERIVED_HINT =
  '后端没有独立的上下线字段：上线/离线由 status 与 published_at 推导——status=enabled 且已发布即对外开放调用（网关对已停用端点直接返回 ENDPOINT_DISABLED）。';
const CLIENT_FILTER_HINT =
  '接口列表来自 GET /deliveries/openapi/endpoints；查询与重置在已取回的列表内筛选（后端列表接口只按合约过滤，没有名称/状态/上下线参数）。';
/** 图上「接口描述」是必填红星，但后端创建载荷与 api_endpoints 都没有这个字段：填了不会被存，必须写明。 */
const NO_DESCRIPTION_HINT =
  '后端端点记录没有描述字段：此处填写不会落库，列表的「接口描述」因此记未记录（需先给 api_endpoints 加列）。';


/**
 * 图 3-2 底部固定句的后半段宣称「可在列表中修改……安全白名单与查询范围」，但 `api_endpoints`
 * 没有这两个字段（见 R-2）：上屏只写实际能改的，差异说明留在 Tooltip（禁伪造铁律）。
 */
const CREATED_FOOT_TEXT = '接口创建后默认按「上线」状态发布，可在列表中修改状态（安全白名单与查询范围暂不可配置）';
const CREATED_FOOT_NOTE =
  '原型此处原句为「接口创建后默认按「上线」状态发布，可在列表中修改状态、安全白名单与查询范围」：后端端点记录没有安全白名单与查询范围字段，网关按 API Key 与合约授权放行，这两项在列表与编辑里都改不了，故此处只写实际可改的状态。';

const RESULT_SET_EMPTY = '后端未下发结果集字段结构（端点 response_schema 为空），不用原型示例数据填充';

/** 图 3-1 的状态与「是否在线」是两列彩色纯文字（无底色徽标）；带底色 chip 只出现在 3-3 监控页。 */
const ENDPOINT_TEXT_STATUS: Record<string, { color: string; label: string }> = {
  enabled: { color: CDS_OK, label: '正常' },
  disabled: { color: CDS_TEXT_GRAY, label: '已停用' },
};

function endpointTextStatus(status: string | null | undefined) {
  if (!status) return { color: CDS_TEXT_GRAY, label: UNKNOWN_TEXT };
  return ENDPOINT_TEXT_STATUS[status] ?? { color: CDS_TEXT_GRAY, label: status };
}

function endpointOnline(endpoint: ApiEndpoint) {
  if (endpoint.status !== 'enabled') return false;
  return typeof endpoint.published_at === 'string' ? endpoint.published_at.length > 0 : true;
}

/** apiUrl 两段都来自后端：合约文档 servers[0].url + 端点 path；文档没下发就只给路径，不编主机名。 */
function endpointApiUrl(endpoint: ApiEndpoint, doc: OpenApiDoc | null) {
  const path = endpoint.path ?? openApiPathForSlug(endpoint.slug);
  const server = doc?.servers?.[0]?.url;
  const base = typeof server === 'string' ? server.replace(/\/+$/, '') : '';
  return { path, url: base ? `${base}${path}` : null };
}

function truncateUrl(value: string, keep = 38) {
  return value.length > keep ? `${value.slice(0, keep)}…` : value;
}

const CRUMB_LIST = ['OpenAPI导出', '接口列表'];
const CRUMB_CREATE = ['OpenAPI导出', '新建API'];
const CRUMB_MONITOR = ['OpenAPI导出', '接口监控'];

/** 页内钻取段交给壳层 PageCrumb 统一渲染（2026-10-17 面包屑全面化定稿），本页不再自绘面包屑。 */
function CrumbAnchor({ segs }: { segs: string[] }) {
  useCrumbSegments(segs);
  return null;
}

function SectionHead({ text, extra }: { text: string; extra?: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <Space size={8}>
        <span style={{ width: 8, height: 8, borderRadius: 8, background: CDS_PRIMARY, display: 'inline-block' }} />
        <Text strong>{text}</Text>
      </Space>
      {extra ?? null}
    </div>
  );
}

function PlainStatusText({ meta, hint }: { meta: { color: string; label: string }; hint?: string }) {
  // audit-C 1-02/1-03（G-05）：图 3-1 状态/是否在线两列 = 纯色文字，文字就是列首（绿墨簇 x1095 = 列左沿）。
  // 曾经多一个前导实心圆图标，整列彩色墨右移 ≈22px，已删。
  const text = <span style={{ color: meta.color, whiteSpace: 'nowrap' }}>{meta.label}</span>;
  return hint ? <Tooltip title={hint}>{text}</Tooltip> : text;
}

function OpenApiEndpointListView({
  contractId,
  doc,
  docFailed,
  onCreate,
  onMonitor,
  onEdit,
}: {
  contractId: string | null;
  doc: OpenApiDoc | null;
  docFailed: boolean;
  onCreate: () => void;
  onMonitor: (endpoint: ApiEndpoint) => void;
  onEdit: (endpoint: ApiEndpoint) => void;
}) {
  const [nameInput, setNameInput] = useState('');
  const [statusInput, setStatusInput] = useState<string | undefined>(undefined);
  const [onlineInput, setOnlineInput] = useState<string | undefined>(undefined);
  const [applied, setApplied] = useState<{ name: string; status?: string; online?: string }>({ name: '' });
  const [tablePage, setTablePage] = useState(1);
  const endpointsQuery = useApiEndpoints(contractId);
  const deleteMutation = useDeleteEndpoint();
  const all = endpointsQuery.data ?? [];

  const matched = all.filter((endpoint) => {
    if (applied.name.trim() && !endpoint.name.includes(applied.name.trim())) return false;
    if (applied.status && endpoint.status !== applied.status) return false;
    if (applied.online && endpointOnline(endpoint) !== (applied.online === 'online')) return false;
    return true;
  });

  // 列宽按图 3-1 列簇照图：墨迹左界 284/454/644/804/904/1094/1164/1244 在「盒左 + 单元格左内边距 20」下
  // 唯一对应盒宽 170/190/160/100/190/70/80；第八列（操作）取到**列宽合计 = 容器宽 = 1130**，
  // 因为 `tableLayout="fixed"` 下只要合计不等于容器宽，浏览器就会把富余按比例摊给每一列（实测 22px 富余
  // 逐列摊 +2…4px，到第 8 列累计偏 15px）——所以合计必须钉死，短量只能落在最右列。
  const columns: ColumnsType<ApiEndpoint> = [
    { title: '接口名称', dataIndex: 'name', width: 170, render: (name: string) => <Text>{name}</Text> },
    {
      title: 'API ID',
      dataIndex: 'id',
      width: 190,
      render: (id: string) => (
        <Text style={{ fontSize: 12, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={id}>
          {id}
        </Text>
      ),
    },
    {
      title: '接口描述',
      dataIndex: 'description',
      width: 160,
      render: (description: string | null | undefined) =>
        description ? (
          <Text>{description}</Text>
        ) : (
          <Tooltip title={NO_DESCRIPTION_HINT}>
            <span style={{ color: CDS_TEXT_GRAY }}>{UNKNOWN_TEXT}</span>
          </Tooltip>
        ),
    },
    {
      title: '有效期',
      key: 'expiry',
      width: 100,
      render: () => <PlainStatusText meta={{ color: CDS_TEXT_GRAY, label: '永不过期' }} hint={NO_EXPIRY_HINT} />,
    },
    {
      title: 'apiUrl',
      key: 'apiUrl',
      width: 190,
      render: (_: unknown, record) => {
        const { path, url } = endpointApiUrl(record, doc);
        if (!url) {
          return (
            <Tooltip
              title={
                docFailed
                  ? '合约 OpenAPI 文档加载失败，取不到后端下发的 servers，只展示调用路径。'
                  : '未选择合约或文档未下发 servers：完整地址要后端 servers 字段，这里只展示调用路径。'
              }
            >
              <Link style={{ fontSize: 12 }}>{path}</Link>
            </Tooltip>
          );
        }
        return (
          <Tooltip title={url}>
            <Link href={url} style={{ fontSize: 12, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {truncateUrl(url)}
            </Link>
          </Tooltip>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 70,
      render: (status: string) => <PlainStatusText meta={endpointTextStatus(status)} />,
    },
    {
      title: '是否在线',
      key: 'online',
      width: 80,
      render: (_: unknown, record) => (
        <PlainStatusText
          meta={endpointOnline(record) ? { color: CDS_OK, label: '上线' } : { color: CDS_TEXT_GRAY, label: '离线' }}
          hint={ONLINE_DERIVED_HINT}
        />
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 170,
      render: (_: unknown, record) => (
        <Space size={12} style={{ whiteSpace: 'nowrap' }}>
          <Link onClick={() => onEdit(record)}>编辑</Link>
          <Popconfirm
            title="删除该接口？"
            description="删除后外部调用立即失效，历史调用日志保留。"
            okText="确认删除"
            cancelText="取消"
            onConfirm={() =>
              deleteMutation.mutate(record.id, {
                onSuccess: () => message.success('接口已删除'),
                onError: (err) => message.error(describeFlowError(err, '删除失败')),
              })
            }
          >
            <Link style={{ color: CDS_FAIL }}>删除</Link>
          </Popconfirm>
          <Link onClick={() => onMonitor(record)}>接口监控</Link>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <CrumbAnchor segs={CRUMB_LIST} />
      <Card size="small" className="cds-openapi-filter" style={{ marginBottom: 16 }}>
        <div className="cds-filter-row">
          <div className="cds-filter-field">
            <span className="cds-filter-label">接口名称：</span>
            <Input
              style={{ width: 200 }}
              placeholder="请输入接口名称"
              value={nameInput}
              onChange={(event) => setNameInput(event.target.value)}
            />
          </div>
          <div className="cds-filter-field">
            <span className="cds-filter-label">状态：</span>
            <Select
              style={{ width: 140 }}
              placeholder="全部"
              allowClear
              value={statusInput}
              onChange={(value?: string) => setStatusInput(value)}
              options={[
                { label: '正常', value: 'enabled' },
                { label: '已停用', value: 'disabled' },
              ]}
            />
          </div>
          <div className="cds-filter-field">
            <span className="cds-filter-label">是否在线：</span>
            <Select
              style={{ width: 140 }}
              placeholder="全部"
              allowClear
              value={onlineInput}
              onChange={(value?: string) => setOnlineInput(value)}
              options={[
                { label: '上线', value: 'online' },
                { label: '离线', value: 'offline' },
              ]}
            />
          </div>
          {/* 图上本带只有三个筛选条件与两个按钮，没有第四行说明：取数口径收进「查询」的 Tooltip。 */}
          <div className="cds-filter-actions">
            <Tooltip title={CLIENT_FILTER_HINT}>
              <Button
                type="primary"
                onClick={() => {
                  setApplied({ name: nameInput, status: statusInput, online: onlineInput });
                  setTablePage(1);
                }}
              >
                查询
              </Button>
            </Tooltip>
            <Button
              onClick={() => {
                setNameInput('');
                setStatusInput(undefined);
                setOnlineInput(undefined);
                setApplied({ name: '' });
                setTablePage(1);
              }}
            >
              重置
            </Button>
          </div>
        </div>
      </Card>

      {/* 图 3-1：新建入口、表体、分页同属一张白卡（2026-10-17 复量：卡内无横向滚动条） */}
      <Card size="small">
      <div style={{ marginBottom: 16 }}>
        <Button type="primary" size="large" style={{ minWidth: 120 }} icon={<PlusOutlined />} onClick={onCreate}>
          新建接口
        </Button>
      </div>

      <Table<ApiEndpoint>
        rowKey="id"
        columns={columns}
        dataSource={matched}
        loading={endpointsQuery.isPending}
        scroll={{ x: 1130 }}
        tableLayout="fixed"
        className="cds-openapi-table"
        pagination={{
          current: tablePage,
          pageSize: 10,
          total: matched.length,
          onChange: setTablePage,
          className: 'cds-openapi-pag',
          showTotal: (total) => `共 ${total} 个接口`,
          locale: { jump_to: '前往' },
          showSizeChanger: true,
          showQuickJumper: true,
          pageSizeOptions: [10, 20, 50],
          hideOnSinglePage: false,
        }}
        locale={tableEmpty(contractId ? '该合约还没有 OpenAPI 接口' : '当前账号还没有 OpenAPI 接口')}
      />
      </Card>
      {endpointsQuery.isError && (
        <QueryErrorAlert
          error={endpointsQuery.error}
          message="接口列表加载失败"
          onRetry={() => {
            void endpointsQuery.refetch();
          }}
        />
      )}
    </div>
  );
}

function ResultSetFieldTable() {
  return (
    <Table<{ name: string; type: string }>
      rowKey="name"
      size="small"
      pagination={false}
      dataSource={[]}
      columns={[
        { title: '字段名', dataIndex: 'name', width: 200 },
        { title: '字段类型', dataIndex: 'type', width: 200 },
        {
          title: '算子',
          key: 'operator',
          render: () => (
            <Tooltip title="后端结果集结构里没有字段级算子配置，此处不编取值。">
              <span style={{ color: CDS_TEXT_GRAY }}>{UNKNOWN_TEXT}</span>
            </Tooltip>
          ),
        },
      ]}
      locale={tableEmpty(RESULT_SET_EMPTY)}
    />
  );
}

async function createEndpointWithStatus(
  create: (payload: ApiEndpointCreatePayload) => Promise<ApiEndpoint>,
  update: (vars: { id: string; payload: ApiEndpointUpdatePayload }) => Promise<ApiEndpoint>,
  payload: ApiEndpointCreatePayload,
  online: boolean,
) {
  const endpoint = await create(payload);
  if (online) return { endpoint, offlineWritten: true };
  try {
    return { endpoint, offlineWritten: (await update({ id: endpoint.id, payload: { status: 'disabled' } })).status !== 'enabled' };
  } catch {
    return { endpoint, offlineWritten: false };
  }
}

function OpenApiCreateView({
  contractId,
  onCancel,
  onCreated,
}: {
  contractId: string | null;
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [form] = Form.useForm();
  const [online, setOnline] = useState(true);
  const [expiry, setExpiry] = useState('never');
  const workloadsQuery = useWorkloads({ contractId, env: 'production', status: 'published' });
  const createMutation = useCreateEndpoint();
  const updateMutation = useUpdateEndpoint();
  const queryClient = useQueryClient();
  const workloads = (workloadsQuery.data?.items ?? []).filter((workload) => workload.contract_id);

  const submit = async () => {
    let values: { name: string; workload_id: string; description: string };
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const workload = workloads.find((candidate) => candidate.id === values.workload_id);
    const targetContract = workload?.contract_id ?? contractId;
    if (!targetContract) {
      message.error('关联任务未绑定合约，后端无法创建接口');
      return;
    }
    try {
      const { endpoint, offlineWritten } = await createEndpointWithStatus(
        (payload) => createMutation.mutateAsync(payload),
        (vars) => updateMutation.mutateAsync(vars),
        {
          contract_id: targetContract,
          workload_id: workload?.id ?? null,
          name: values.name.trim(),
          description: values.description.trim(),
        },
        online,
      );
      await queryClient.invalidateQueries({ queryKey: flowKeys.openapi(targetContract) });
      message.success(`接口已创建：${openApiPathForSlug(endpoint.slug)}`);
      if (!offlineWritten) {
        message.warning('接口已创建，但下线状态未写入：列表里仍是上线，可在「编辑」里重试');
      }
      onCreated();
    } catch (err) {
      message.error(describeFlowError(err, '创建接口失败'));
    }
  };

  return (
    <div>
      <CrumbAnchor segs={CRUMB_CREATE} />
      <div className="cds-api-create-form">
        <Form form={form} layout="vertical">
          {/* 图 3-2 第 1 字段带是三列栅格：接口名称 / 关联任务 / 接口状态 同一行（接口状态不再是整行）。 */}
          <Row gutter={0} className="cds-row3">
            <Col xs={24} md={8}>
              <Form.Item name="name" label="接口名称" rules={[{ required: true, message: '请输入接口名称' }]}>
                <Input placeholder="请输入接口名称" maxLength={128} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="workload_id" label="关联任务" rules={[{ required: true, message: '请选择关联任务' }]}>
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="请选择已发布的生产任务"
                  loading={workloadsQuery.isPending}
                  notFoundContent={workloadsQuery.isPending ? '加载中…' : '后端没有可绑定的已发布生产任务'}
                  options={workloads.map((workload) => ({ label: workload.name, value: workload.id }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="接口状态">
                <Space align="center">
                  <Switch checkedChildren="上线" unCheckedChildren="离线" checked={online} onChange={setOnline} />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    关闭后接口不可被外部调用
                  </Text>
                </Space>
              </Form.Item>
            </Col>
          </Row>
          {/* 图 3-2 第 2 字段带 y238–370 同是三列：描述 / 有效期 / 保障（单选圆点像素簇 650,270 与 1030,270）。 */}
          <Row gutter={0} className="cds-row3">
            <Col xs={24} md={8}>
              <Form.Item
                name="description"
                label="接口描述"
                rules={[{ required: true, message: '请输入接口描述' }]}
                extra={NO_DESCRIPTION_HINT}
              >
                <Input.TextArea rows={3} placeholder="请输入接口描述" maxLength={500} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="接口有效期">
            <Space direction="vertical" size={4}>
              <Radio.Group
                name="endpoint-expiry"
                value={expiry}
                onChange={(event) => setExpiry(event.target.value)}
                options={[
                  { label: '永不过期', value: 'never' },
                  { label: '选择日期', value: 'date' },
                ]}
              />
              {expiry === 'date' ? <DatePicker disabled placeholder="选择日期" /> : null}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {NO_EXPIRY_HINT}
              </Text>
              </Space>
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="安全保障">
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Radio.Group
                name="endpoint-security"
                disabled
                value="unrestricted"
                options={[
                  { label: '白名单', value: 'whitelist' },
                  { label: '不限制', value: 'unrestricted' },
                ]}
              />
              <Input.TextArea
                disabled
                rows={2}
                placeholder="支持输入IP地址，多个IP地址用逗号隔开，例如192.168.22.20,192.168.22.21；支持输入网段，例如 36.110.223.0/24"
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                后端端点记录没有 IP 白名单字段，网关按 API Key 与合约授权放行：此处不可配置，平台实际行为是不限制来源。
              </Text>
            </Space>
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Form.Item label="查询模式">
                <Radio.Group
                  name="endpoint-query-mode"
                  disabled
                  value="open"
                  options={[
                    { label: '开放查询', value: 'open' },
                    { label: '限定查询', value: 'limited' },
                  ]}
                />
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    网关把请求体原样交给已发布负载执行，不做请求 Schema 校验：此处不可配置，平台实际行为是开放查询。
                  </Text>
                </div>
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="查询范围">
                <Radio.Group
                  name="endpoint-query-scope"
                  disabled
                  value="latest"
                  options={[
                    { label: '最新结果集', value: 'latest' },
                    { label: '历史所有结果集', value: 'history' },
                  ]}
                />
                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    每次调用即时执行已发布负载并返回本次输出，后端没有历史结果集查询接口：此处不可配置。
                  </Text>
                </div>
              </Form.Item>
            </Col>
          </Row>
          <div style={{ marginTop: 8 }}>
            <Space size={6}>
              <Text strong>结果集字段</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                （来自关联合约的结果集结构）
              </Text>
            </Space>
            <div style={{ marginTop: 8 }}>
              <ResultSetFieldTable />
            </div>
          </div>
        </Form>
      </div>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          marginTop: 16,
          paddingTop: 16,
          borderTop: '1px solid #f1f2f4',
        }}
      >
        {/* 原型原句后半段「、安全白名单与查询范围」宣称了产品没有的能力（无白名单/查询范围字段），
            按禁伪造改成诚实版；与原型原句的差异写进 Tooltip。 */}
        <Tooltip title={CREATED_FOOT_NOTE}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            <InfoCircleOutlined style={{ marginRight: 6 }} />
            {CREATED_FOOT_TEXT}
          </Text>
        </Tooltip>
        <Space>
          <Button onClick={onCancel}>取消</Button>
          <Button
            type="primary"
            size="large"
            style={{ paddingInline: 22 }}
            loading={createMutation.isPending || updateMutation.isPending}
            onClick={() => void submit()}
          >
            新增接口
          </Button>
        </Space>
      </div>
    </div>
  );
}

function EditEndpointModal({ endpoint, onClose }: { endpoint: ApiEndpoint; onClose: () => void }) {
  const [online, setOnline] = useState(endpointOnline(endpoint));
  const [rate, setRate] = useState<number | null>(endpoint.rate_limit_per_minute ?? null);
  const [quota, setQuota] = useState<number | null>(endpoint.quota_total ?? null);
  const queryClient = useQueryClient();
  const updateMutation = useUpdateEndpoint();

  const save = () => {
    updateMutation.mutate(
      {
        id: endpoint.id,
        payload: { status: online ? 'enabled' : 'disabled', rate_limit_per_minute: rate, quota_total: quota },
      },
      {
        onSuccess: () => {
          void queryClient.invalidateQueries({ queryKey: flowKeys.openapi(endpoint.contract_id) });
          message.success('接口已更新');
          onClose();
        },
        onError: (err) => message.error(describeFlowError(err, '更新失败')),
      },
    );
  };

  return (
    <Modal
      title={`编辑接口 · ${endpoint.name}`}
      open
      onCancel={onClose}
      onOk={save}
      okText="保存"
      confirmLoading={updateMutation.isPending}
      width={520}
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <div>
          <Text>接口状态</Text>
          <div style={{ marginTop: 8 }}>
            <Space align="center">
              <Switch checkedChildren="上线" unCheckedChildren="离线" checked={online} onChange={setOnline} />
              <Text type="secondary" style={{ fontSize: 12 }}>
                关闭后接口不可被外部调用
              </Text>
            </Space>
          </div>
        </div>
        <div>
          <Text>每分钟限流</Text>
          <div style={{ marginTop: 8 }}>
            <InputNumber
              style={{ width: '100%' }}
              min={1}
              max={10000}
              precision={0}
              value={rate}
              onChange={(value) => setRate(value)}
            />
          </div>
        </div>
        <div>
          <Text>总配额（调用次数）</Text>
          <div style={{ marginTop: 8 }}>
            <InputNumber
              style={{ width: '100%' }}
              min={1}
              precision={0}
              placeholder="留空为不限"
              value={quota}
              onChange={(value) => setQuota(value)}
            />
          </div>
        </div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          后端 PATCH 只接受状态、每分钟限流与总配额：接口名称、调用路径与有效期创建后不可修改。
        </Text>
      </Space>
    </Modal>
  );
}

const MONITOR_TABLE_PAGE_SIZE = 10;

const CALL_STATUS_META: Record<string, StatusMeta> = {
  succeeded: { color: 'green', label: '调用成功' },
  failed: { color: 'red', label: '调用失败' },
};

const CALL_FAILURE_CODE_META: Record<string, StatusMeta> = {
  RATE_LIMITED: { color: 'orange', label: '限流阻断' },
  QUOTA_EXCEEDED: { color: 'orange', label: '配额用尽' },
  ENDPOINT_DISABLED: { color: 'default', label: '端点已停用' },
  WORKLOAD_UNAVAILABLE: { color: 'red', label: '负载不可用' },
  CALLER_UNKNOWN: { color: 'red', label: '调用方不存在' },
  WORKLOAD_FAILED: { color: 'red', label: '负载执行失败' },
  OUTPUT_BLOCKED: { color: 'red', label: '输出被拦截' },
};

function CallStatusTag({ call }: { call: ApiEndpointCall }) {
  if (!call.status) return <Tag>{UNKNOWN_TEXT}</Tag>;
  const base = statusMeta(CALL_STATUS_META, call.status);
  if (call.status === 'succeeded') return <Tag color={base.color}>{base.label}</Tag>;
  const known = call.code ? statusMeta(CALL_FAILURE_CODE_META, call.code) : null;
  const useCode = Boolean(call.code && known && known.label !== call.code);
  const tag = <Tag color={useCode ? known!.color : base.color}>{useCode ? known!.label : base.label}</Tag>;
  return call.message ? <Tooltip title={call.message}>{tag}</Tooltip> : tag;
}

function MetricTile({
  testId,
  title,
  hint,
  value,
  note,
  icon,
  divider,
}: {
  testId: string;
  title: string;
  hint: string;
  value: string;
  note: string;
  icon: ReactNode;
  /** 图 3-3 三张 KPI 卡无独立底色，卡间靠一条 1px 细竖线分隔。 */
  divider?: boolean;
}) {
  return (
    <Col xs={24} sm={8}>
      <div
        className={divider ? 'cds-kpi-divider' : undefined}
        style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}
      >
        <span
          style={{
            width: 40,
            height: 40,
            borderRadius: 8,
            background: '#f0f5ff',
            color: CDS_PRIMARY,
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 18,
          }}
        >
          {icon}
        </span>
        <div data-testid={testId} style={{ minWidth: 0 }}>
          <Statistic
            title={
              <Tooltip title={hint}>
                <span>{title}</span>
              </Tooltip>
            }
            value={value}
            valueStyle={{ fontSize: 20, color: CDS_PRIMARY }}
          />
          <Text type="secondary" style={{ fontSize: 12 }}>
            {note}
          </Text>
        </div>
      </div>
    </Col>
  );
}

export function OpenApiMonitorView({
  endpoint,
  onClose,
  onEdit,
}: {
  endpoint: ApiEndpoint | null;
  onClose: () => void;
  onEdit?: (endpoint: ApiEndpoint) => void;
}) {
  const [tablePage, setTablePage] = useState(1);
  const callsQuery = useEndpointCalls(endpoint?.id ?? null);
  const workloadQuery = useWorkload(endpoint?.workload_id ?? null);
  const payload = callsQuery.data ?? null;
  const metrics = computeEndpointCallMetrics(payload);
  const rows = payload?.items ?? [];
  const online = endpoint ? endpointOnline(endpoint) : false;
  const enabled = endpoint?.status === 'enabled';

  const columns: ColumnsType<ApiEndpointCall> = [
    {
      title: '序号',
      key: 'index',
      width: 80,
      render: (_: unknown, __: ApiEndpointCall, index: number) => (tablePage - 1) * MONITOR_TABLE_PAGE_SIZE + index + 1,
    },
    {
      title: '调用ID',
      dataIndex: 'id',
      render: (id: string | null | undefined) =>
        id ? <Text style={{ fontSize: 12 }}>{id}</Text> : <span style={{ color: CDS_TEXT_GRAY }}>{UNKNOWN_TEXT}</span>,
    },
    {
      title: '调用时间',
      dataIndex: 'created_at',
      width: 230,
      render: (value: string | null | undefined) =>
        value ? formatMinute(value) : <span style={{ color: CDS_TEXT_GRAY }}>{UNKNOWN_TEXT}</span>,
    },
    { title: '调用状态', key: 'status', width: 140, render: (_: unknown, record) => <CallStatusTag call={record} /> },
  ];

  return (
    <div>
      <CrumbAnchor segs={CRUMB_MONITOR} />
      {/* 图 3-3（2026-10-17 复量）：标题区是一整块白卡，chip 与三按钮都在卡内，不落灰底。 */}
      <Card size="small" style={{ marginBottom: 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <Space size={8} align="center" wrap>
            <Title level={4} style={{ margin: 0 }}>
              {endpoint?.name ?? UNKNOWN_TEXT}
            </Title>
            <Tag color={enabled ? 'green' : 'default'}>
              {enabled ? <CheckCircleOutlined /> : null}
              {enabled ? '接口正常' : endpoint ? `接口${endpointTextStatus(endpoint.status).label}` : UNKNOWN_TEXT}
            </Tag>
            <Tag color={online ? 'blue' : 'default'}>{online ? <LinkOutlined /> : null}{online ? '已上线' : '已离线'}</Tag>
          </Space>
          <div style={{ marginTop: 4 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {`API ID：${endpoint?.id ?? UNKNOWN_TEXT} · 关联任务：${workloadQuery.data?.name ?? UNKNOWN_TEXT} · 有效期：永不过期`}
            </Text>
          </div>
        </div>
        <Space>
          <Button size="large" style={{ minWidth: 125 }} icon={<LeftOutlined />} onClick={onClose}>
            返回列表
          </Button>
          <Button
            size="large"
            style={{ minWidth: 125 }}
            icon={<ReloadOutlined />}
            onClick={() => {
              void callsQuery.refetch();
            }}
          >
            刷新数据
          </Button>
          <Button type="primary" size="large" style={{ minWidth: 125 }} icon={<EditOutlined />} disabled={!onEdit} onClick={() => endpoint && onEdit?.(endpoint)}>
            编辑接口
          </Button>
        </Space>
      </div>
      </Card>

      <Card size="small" className="cds-kpi-band" style={{ marginBottom: 16 }}>
        <SectionHead text="使用调用详情" />
        <Row gutter={[24, 16]} style={{ marginTop: 16 }}>
          <MetricTile
            testId="monitor-total"
            icon={<DashboardOutlined />}
            title="调用总次数（次）"
            hint="取后端下发的调用总数（数据源为带 Merkle 锚定的审计日志计数），不是本页行数。"
            value={metrics.total != null ? String(metrics.total) : UNKNOWN_TEXT}
            note={
              metrics.total == null
                ? '后端未下发调用总数'
                : metrics.totalIsFallback
                  ? `后端未下发总数，按本次返回的 ${metrics.windowCount} 条计`
                  : metrics.windowCount < metrics.total
                    ? `明细取最近 ${metrics.windowCount} 条`
                    : '后端下发的调用总数'
            }
          />
          <MetricTile
            testId="monitor-latest"
            divider
            icon={<FieldTimeOutlined />}
            title="最新调用时间"
            hint="取本次返回明细里最晚一条的调用时间（后端按时间倒序返回），不用当前时间推算。"
            value={metrics.latestAt ? formatDateTime(metrics.latestAt) : UNKNOWN_TEXT}
            note={metrics.latestAt ? '明细里最近一次调用时间' : '明细为空，无从取到最近时间'}
          />
          <MetricTile
            testId="monitor-rate"
            divider
            icon={<CheckCircleOutlined />}
            title="调用成功率"
            hint="只按后端下发的成功/失败判定计算；一条判定都没有时记「未记录」，不按全成功推算。"
            value={metrics.successRate != null ? `${metrics.successRate}%` : UNKNOWN_TEXT}
            note={metrics.rateNote}
          />
        </Row>
      </Card>

      <Card
        size="small"
        title={
          <SectionHead
            text="使用调用详情"
            extra={
              <Space size={12}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  数据更新至 {metrics.latestAt ? formatMinute(metrics.latestAt) : UNKNOWN_TEXT}
                </Text>
                <Button
                  type="link"
                  size="small"
                  icon={<ReloadOutlined />}
                  style={{ padding: 0 }}
                  onClick={() => {
                    void callsQuery.refetch();
                  }}
                >
                  刷新
                </Button>
              </Space>
            }
          />
        }
      >
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            指标与明细读同一个窗口：最多取最近 {OPENAPI_CALLS_WINDOW} 条调用（后端单页上界）
            {metrics.total != null && metrics.total > metrics.windowCount ? `，后端共 ${metrics.total} 次调用` : ''}
          </Text>
        </div>
        {callsQuery.isError && (
          <QueryErrorAlert
            error={callsQuery.error}
            message="调用明细加载失败"
            onRetry={() => {
              void callsQuery.refetch();
            }}
          />
        )}
        <Table<ApiEndpointCall>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={rows}
          loading={callsQuery.isLoading}
          pagination={{
            current: tablePage,
            pageSize: MONITOR_TABLE_PAGE_SIZE,
            total: rows.length,
            onChange: setTablePage,
            className: 'cds-openapi-pag',
            showTotal: (total) => `共 ${total} 条`,
          locale: { jump_to: '前往' },
            showSizeChanger: true,
            showQuickJumper: true,
            pageSizeOptions: [10, 20, 50],
            hideOnSinglePage: false,
          }}
          locale={tableEmpty('该端点还没有调用明细')}
        />
      </Card>
    </div>
  );
}

export function OpenApiExportTab({ contractId }: { contractId: string | null }) {
  const [view, setView] = useState<'list' | 'create'>('list');
  const [monitorEndpointId, setMonitorEndpointId] = useState<string | null>(null);
  const [editEndpoint, setEditEndpoint] = useState<ApiEndpoint | null>(null);
  const [debugPick, setDebugPick] = useState<string | null>(null);
  const [debugEndpointId, setDebugEndpointId] = useState<string | null>(null);
  const endpointsQuery = useApiEndpoints(contractId);
  const docQuery = useContractOpenApiDoc(contractId);
  const endpoints = endpointsQuery.data ?? [];
  const docRows = flattenOpenApiOperations(docQuery.data ?? null);
  const monitorEndpoint = endpoints.find((candidate) => candidate.id === monitorEndpointId) ?? null;

  const bodyFor = (endpoint: ApiEndpoint) => {
    const row = docRows.find((candidate) => candidate.path.includes(endpoint.slug));
    return JSON.stringify(buildRequestExample(row, endpoint), null, 2);
  };

  if (view === 'create') {
    return <OpenApiCreateView contractId={contractId} onCancel={() => setView('list')} onCreated={() => setView('list')} />;
  }

  if (monitorEndpointId) {
    return (
      <>
        <OpenApiMonitorView
          endpoint={monitorEndpoint}
          onClose={() => setMonitorEndpointId(null)}
          onEdit={(endpoint) => setEditEndpoint(endpoint)}
        />
        {editEndpoint && <EditEndpointModal endpoint={editEndpoint} onClose={() => setEditEndpoint(null)} />}
      </>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <OpenApiEndpointListView
        contractId={contractId}
        doc={docQuery.data ?? null}
        docFailed={docQuery.isError}
        onCreate={() => setView('create')}
        onMonitor={(endpoint) => setMonitorEndpointId(endpoint.id)}
        onEdit={(endpoint) => setEditEndpoint(endpoint)}
      />
      <Card size="small">
        <Collapse
          size="small"
          items={[
            {
              key: 'doc',
              label: 'OpenAPI 文档预览与在线调试（原型三屏之外的既有能力）',
              children: (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {!contractId && <Alert type="info" showIcon message="OpenAPI 文档按合约返回：列表页未选合约时仅展示调用路径；合约范围文档请在「数据集打包 / 结果推送」视图选定合约后查看。" />}
                  {contractId && docQuery.isLoading && <Text type="secondary">文档加载中…</Text>}
                  {contractId && docQuery.isError && (
                    <QueryErrorAlert
                      error={docQuery.error}
                      message="OpenAPI 文档加载失败"
                      onRetry={() => {
                        void docQuery.refetch();
                      }}
                    />
                  )}
                  {contractId && !docQuery.isLoading && !docQuery.isError && (
                    <OpenApiPreview doc={docQuery.data ?? null} endpoints={endpoints} />
                  )}
                  <Space wrap>
                    <Select
                      style={{ width: 260 }}
                      placeholder="选择接口做在线试调用"
                      value={debugPick ?? undefined}
                      onChange={(value?: string) => setDebugPick(value ?? null)}
                      options={endpoints.map((endpoint) => ({ label: endpoint.name, value: endpoint.id }))}
                    />
                    <Button
                      size="small"
                      icon={<BugOutlined />}
                      disabled={!debugPick}
                      onClick={() => setDebugEndpointId(debugPick)}
                    >
                      调试
                    </Button>
                  </Space>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      {editEndpoint && <EditEndpointModal endpoint={editEndpoint} onClose={() => setEditEndpoint(null)} />}
      {debugEndpointId && (
        <ApiDebugModal
          open
          onClose={() => setDebugEndpointId(null)}
          endpoints={endpoints}
          initialEndpointId={debugEndpointId}
          bodyForEndpoint={bodyFor}
        />
      )}
    </Space>
  );
}

/**
 * SS-17 在线试调用：选一个已发布的端点 → 拿一把真实的网关作用域 API Key →
 * 直接 `POST /open/v1/{slug}` 看真实响应。
 *
 * 凭据不是模拟的：密钥由 `POST /api/v1/api-keys`（preset=custom + gateway:write）签发，
 * 裸密钥后端只回一次。创建需要 ADMIN（该路由 human-only + ADMIN 门），非管理员可以
 * 粘贴管理员已发的 `cds_ak_…` 走同一条调用——两种路径都不会伪装成“密钥可用”。
 */
function ApiDebugModal({
  open,
  onClose,
  endpoints,
  initialEndpointId,
  bodyForEndpoint,
}: {
  open: boolean;
  onClose: () => void;
  endpoints: ApiEndpoint[];
  initialEndpointId: string | null;
  bodyForEndpoint: (endpoint: ApiEndpoint) => string;
}) {
  const [endpointId, setEndpointId] = useState<string | null>(initialEndpointId ?? endpoints[0]?.id ?? null);
  const [bodyByEndpoint, setBodyByEndpoint] = useState<Record<string, string>>({});
  const [keyCreated, setKeyCreated] = useState<ApiKeyCreated | null>(null);
  const [pastedKey, setPastedKey] = useState('');
  const [keyError, setKeyError] = useState<string | null>(null);
  const [creatingKey, setCreatingKey] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [attempt, setAttempt] = useState<OpenApiDebugAttempt | null>(null);
  const [calling, setCalling] = useState(false);

  const endpoint = endpoints.find((item) => item.id === endpointId) ?? null;
  const apiKey = pastedKey.trim() || keyCreated?.key || '';
  const bodyText = endpoint ? bodyByEndpoint[endpoint.id] ?? bodyForEndpoint(endpoint) : '';

  const createKey = () => {
    if (!endpoint) {
      message.error('请先选择要调试的端点');
      return;
    }
    setKeyError(null);
    setCreatingKey(true);
    void apiKeysApi
      .create({
        name: `openapi-debug-${endpoint.slug}`.slice(0, 64),
        description: '成果交付页在线试调用创建（仅 gateway:write，30 天有效）',
        preset: 'custom',
        scopes: [...OPEN_API_DEBUG_KEY_SCOPES],
        expires_in_days: 30,
        rate_limit: 60,
      })
      .then((created) => {
        setKeyCreated(created);
        setPastedKey('');
        setAttempt(null);
        message.warning('调试密钥已创建：裸密钥只显示这一次，请立即复制保存');
      })
      .catch((err: unknown) => {
        setKeyError(describeFlowError(err, '创建调试密钥失败'));
      })
      .finally(() => setCreatingKey(false));
  };

  const revokeKey = () => {
    if (!keyCreated) return;
    setRevoking(true);
    void apiKeysApi
      .revoke(keyCreated.id)
      .then(() => {
        setKeyCreated(null);
        setAttempt(null);
        message.success('调试密钥已吊销');
      })
      .catch((err: unknown) => message.error(describeFlowError(err, '吊销密钥失败')))
      .finally(() => setRevoking(false));
  };

  const call = () => {
    if (!endpoint) {
      message.error('请先选择要调试的端点');
      return;
    }
    let parsed: unknown;
    try {
      parsed = bodyText.trim() ? (JSON.parse(bodyText) as unknown) : {};
    } catch {
      message.error('请求体不是合法 JSON，未发起调用');
      return;
    }
    setCalling(true);
    setAttempt(null);
    void callOpenApiWithApiKey(endpoint.slug, apiKey, parsed)
      .then((result) => setAttempt(result))
      .finally(() => setCalling(false));
  };

  const failureCopy = attempt
    ? [
        attempt.errorInfo?.detail,
        attempt.errorInfo?.message,
        attempt.errorInfo?.code ? `错误码 ${attempt.errorInfo.code}` : null,
        attempt.errorInfo?.requestId ? `request_id ${attempt.errorInfo.requestId}` : null,
      ]
        .filter((part): part is string => !!part)
        .join(' · ')
    : '';

  return (
    <Modal
      title="在线试调用 OpenAPI 端点"
      open={open}
      onCancel={onClose}
      width={780}
      footer={[
        attempt?.bodyText ? <CopyButton key="copy" text={attempt.bodyText} label="复制响应体" /> : null,
        <Button key="close" onClick={onClose}>
          关闭
        </Button>,
      ]}
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {!endpoints.length && <Alert type="warning" showIcon message="当前合约下没有可调试的端点：请先在上方新建端点。" />}
        {endpoints.length > 0 && (
          <Select
            style={{ width: '100%' }}
            value={endpointId ?? undefined}
            placeholder="选择要调试的端点"
            onChange={(value: string) => {
              setEndpointId(value);
              setAttempt(null);
            }}
            options={endpoints.map((item) => ({
              label: `${item.name}（POST ${openApiPathForSlug(item.slug)}）`,
              value: item.id,
            }))}
          />
        )}
        {endpoint && endpoint.status !== 'enabled' && (
          <Alert
            type="warning"
            showIcon
            message={`端点当前状态为 ${endpoint.status}，网关会按后端口径拒绝调用；本页面只如实展示后端返回，不伪装成功。`}
          />
        )}

        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            请求体（JSON，已按 OpenAPI 文档预填，可改）
          </Text>
          <Input.TextArea
            rows={5}
            style={{ marginTop: 4, fontFamily: 'monospace' }}
            value={bodyText}
            onChange={(event) => {
              if (!endpoint) return;
              setBodyByEndpoint((prev) => ({ ...prev, [endpoint.id]: event.target.value }));
            }}
          />
        </div>

        <Card size="small" title="调用凭据（平台 API Key）">
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              密钥走后端真实签发：POST /api/v1/api-keys，作用域仅为 {OPEN_API_DEBUG_KEY_SCOPES.join('、')}（网关组写动词 ={' '}
              {'POST /open/v1/{slug}'}），30 天过期。创建需要 ADMIN 账号；已有密钥可直接粘贴使用。
            </Text>
            {keyCreated && (
              <Alert
                type="warning"
                showIcon
                icon={<KeyOutlined />}
                message="裸密钥仅此一次可见"
                description={
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Paragraph code copyable={{ text: keyCreated.key }} style={{ marginBottom: 0, wordBreak: 'break-all' }}>
                      {keyCreated.key}
                    </Paragraph>
                    <Space wrap>
                      <CopyButton text={keyCreated.key} label="复制密钥" />
                      <Button size="small" danger loading={revoking} onClick={revokeKey}>
                        吊销此密钥
                      </Button>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        前缀 {keyCreated.key_prefix} · 作域 {(keyCreated.scopes ?? []).join('，')}
                      </Text>
                    </Space>
                  </Space>
                }
              />
            )}
            <Space wrap>
              {!keyCreated && (
                <Button size="small" icon={<KeyOutlined />} loading={creatingKey} onClick={createKey}>
                  创建网关调试密钥
                </Button>
              )}
              <Input.Password
                style={{ width: 320 }}
                placeholder="或粘贴已有的 cds_ak_… 密钥"
                value={pastedKey}
                onChange={(event) => setPastedKey(event.target.value)}
              />
            </Space>
            {keyError && (
              <Alert
                type="error"
                showIcon
                message="拿不到可用的 API Key"
                description={`${keyError}——创建平台 API Key 需要 ADMIN（该路由 human-only），非管理员请粘贴管理员签发的密钥。未拿到密钥前不会发起调用。`}
              />
            )}
          </Space>
        </Card>

        <Space wrap>
          <Tooltip title={apiKey ? '' : '先创建或粘贴一把 API Key'}>
            <Button type="primary" icon={<BugOutlined />} disabled={!apiKey || !endpoint} loading={calling} onClick={call}>
              发起调用
            </Button>
          </Tooltip>
          <Text type="secondary" style={{ fontSize: 12 }}>
            每次试调用都会被审计并计入端点配额/限流。
          </Text>
        </Space>

        {attempt && (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Descriptions size="small" column={3}>
              <Descriptions.Item label="HTTP 状态">
                {attempt.httpStatus === 0 ? (
                  <Tag>未送达</Tag>
                ) : (
                  <Tag color={attempt.ok ? 'green' : responseStatusColor(attempt.httpStatus)}>
                    {attempt.httpStatus}
                    {attempt.statusText ? ` ${attempt.statusText}` : ''}
                  </Tag>
                )}
              </Descriptions.Item>
              <Descriptions.Item label="耗时">{attempt.latencyMs} ms</Descriptions.Item>
              <Descriptions.Item label="Content-Type">{attempt.contentType ?? '-'}</Descriptions.Item>
            </Descriptions>
            {attempt.transportError && (
              <Alert type="error" showIcon message="请求没有拿到 HTTP 响应" description={attempt.transportError} />
            )}
            {!attempt.ok && !attempt.transportError && (
              <Alert
                type="error"
                showIcon
                message={`网关返回 ${attempt.httpStatus}`}
                description={failureCopy || '后端未返回可解析的 W2 错误体，原文见下方响应体。'}
              />
            )}
            <Text type="secondary" style={{ fontSize: 12 }}>
              响应体
              {attempt.bodyTruncated
                ? `（共 ${attempt.bodyChars} 字符，仅展示前 ${OPEN_API_DEBUG_BODY_LIMIT} 字符）`
                : attempt.bodyChars
                  ? `（${attempt.bodyChars} 字符）`
                  : '为空'}
            </Text>
            <pre className="cds-json-block">{attempt.bodyText || '（空响应体）'}</pre>
          </Space>
        )}
      </Space>
    </Modal>
  );
}

function DeliveriesDrawer({ webhook, onClose }: { webhook: WebhookSubscription; onClose: () => void }) {
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const deliveriesQuery = useWebhookDeliveries(webhook.id, { status: statusFilter ?? undefined });
  const redeliverMutation = useRedeliver();

  const columns: ColumnsType<WebhookDelivery> = [
    { title: '事件', dataIndex: 'event_type', width: 190 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 150,
      render: (status: string) => (
        <Tag color={statusMeta(DELIVERY_STATUS_META, status).color}>{statusMeta(DELIVERY_STATUS_META, status).label}</Tag>
      ),
    },
    {
      title: '尝试',
      key: 'attempt',
      width: 80,
      render: (_: unknown, record) => attemptLabel(record.attempt, record.max_attempts),
    },
    {
      title: '响应码',
      dataIndex: 'response_status',
      width: 90,
      render: (status: number | null) => (typeof status === 'number' ? <Tag color={responseStatusColor(status)}>{status}</Tag> : <Text type="secondary">无响应</Text>),
    },
    {
      title: '错误',
      dataIndex: 'last_error',
      render: (error: string | null) => error || <Text type="secondary">-</Text>,
    },
    { title: '创建时间', dataIndex: 'created_at', width: 170, render: (value: string) => formatDateTime(value) },
    { title: '送达时间', dataIndex: 'delivered_at', width: 170, render: (value: string | null) => formatDateTime(value) },
    {
      title: '重投',
      key: 'redeliver',
      width: 80,
      render: (_: unknown, record) => (
        <Button
          size="small"
          icon={<SwapOutlined />}
          loading={redeliverMutation.isPending && redeliverMutation.variables?.deliveryId === record.id}
          onClick={() =>
            redeliverMutation.mutate(
              { id: webhook.id, deliveryId: record.id },
              {
                onSuccess: (result) => message.success(`已重新投递（状态 ${result.status}）`),
                onError: (err) => message.error(describeFlowError(err, '重投失败')),
              },
            )
          }
        >
          重投
        </Button>
      ),
    },
  ];

  return (
    <Drawer title={`投递日志 · ${webhook.name}`} width={960} open onClose={onClose}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          allowClear
          style={{ width: 160 }}
          placeholder="全部状态"
          value={statusFilter ?? undefined}
          onChange={(value?: string) => setStatusFilter(value ?? null)}
          options={Object.entries(DELIVERY_STATUS_META).map(([value, meta]) => ({ label: meta.label, value }))}
        />
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => {
            void deliveriesQuery.refetch();
          }}
        >
          刷新
        </Button>
      </Space>
      {deliveriesQuery.isError && (
        <QueryErrorAlert
          error={deliveriesQuery.error}
          message="投递日志加载失败"
          onRetry={() => {
            void deliveriesQuery.refetch();
          }}
        />
      )}
      <Table<WebhookDelivery>
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={deliveriesQuery.data?.items ?? []}
        loading={deliveriesQuery.isLoading}
        pagination={{ pageSize: 20 }}
        locale={tableEmpty('还没有投递记录')}
        scroll={{ x: 1100 }}
      />
    </Drawer>
  );
}

function CreateWebhookModal({
  open,
  onClose,
  contractId,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  contractId: string | null;
  onCreated: (webhook: WebhookSubscription) => void;
}) {
  const [form] = Form.useForm();
  const createMutation = useCreateWebhook();

  const submit = () => {
    void form
      .validateFields()
      .then((values: { name: string; url: string; events: string[] }) => {
        createMutation.mutate(
          { name: values.name.trim(), url: values.url.trim(), events: values.events, contract_id: contractId },
          {
            onSuccess: (webhook) => {
              onCreated(webhook);
              form.resetFields();
              onClose();
            },
            onError: (err) => message.error(describeFlowError(err, '创建订阅失败')),
          },
        );
      })
      .catch(() => undefined);
  };

  return (
    <Modal title="新建结果推送订阅" open={open} onCancel={onClose} onOk={submit} confirmLoading={createMutation.isPending} okText="创建" width={560}>
      <Form form={form} layout="vertical">
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="风控结果回调" maxLength={80} />
        </Form.Item>
        <Form.Item
          name="url"
          label="接收地址"
          rules={[
            { required: true, message: '请输入接收地址' },
            { pattern: /^https?:\/\/.+/, message: '必须是 http(s) 绝对地址' },
          ]}
        >
          <Input placeholder="https://partner.example.com/cds/callback" />
        </Form.Item>
        <Form.Item name="events" label="订阅事件" rules={[{ required: true, message: '至少选择一个事件' }]}>
          <Select mode="multiple" options={WEBHOOK_EVENT_OPTIONS} placeholder="选择需要接收的事件" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function ResultPushTab({ contractId }: { contractId: string | null }) {
  const [createOpen, setCreateOpen] = useState(false);
  const [secretState, setSecretState] = useState<{ value: string | null } | null>(null);
  const [logWebhook, setLogWebhook] = useState<WebhookSubscription | null>(null);
  const webhooksQuery = useWebhooks(contractId);
  const updateMutation = useUpdateWebhook();
  const deleteMutation = useDeleteWebhook();
  const rotateMutation = useRotateWebhookSecret();
  const testMutation = useTestWebhook();

  const columns: ColumnsType<WebhookSubscription> = [
    { title: '名称', dataIndex: 'name', width: 160 },
    {
      title: '接收地址',
      dataIndex: 'url',
      render: (url: string) => (
        <Text code copyable={{ text: url }}>
          {url}
        </Text>
      ),
    },
    {
      title: '事件',
      dataIndex: 'events',
      width: 260,
      render: (events: string[]) => (
        <Space size={4} wrap>
          {(events ?? []).map((event) => (
            <Tag key={event}>{event}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (status: string) => (
        <Tag color={statusMeta(WEBHOOK_STATUS_META, status).color}>{statusMeta(WEBHOOK_STATUS_META, status).label}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 320,
      render: (_: unknown, record) => (
        <Space size={4} wrap>
          <Button
            size="small"
            icon={<SendOutlined />}
            loading={testMutation.isPending && testMutation.variables === record.id}
            onClick={() =>
              testMutation.mutate(record.id, {
                onSuccess: (result) => {
                  if (result.delivered) message.success(`测试事件已送达（${result.response_status ?? '无响应码'}）`);
                  else message.error(`测试事件未送达：${result.detail ?? `响应码 ${result.response_status ?? '无'}`}`);
                },
                onError: (err) => message.error(describeFlowError(err, '发送测试失败')),
              })
            }
          >
            发送测试
          </Button>
          <Button size="small" onClick={() => setLogWebhook(record)}>
            投递日志
          </Button>
          <Switch
            size="small"
            checked={record.status === 'active'}
            loading={updateMutation.isPending && updateMutation.variables?.id === record.id}
            onChange={(checked) =>
              updateMutation.mutate(
                { id: record.id, payload: { status: checked ? 'active' : 'paused' } },
                {
                  onSuccess: () => message.success(checked ? '已启用' : '已暂停'),
                  onError: (err) => message.error(describeFlowError(err, '状态更新失败')),
                },
              )
            }
            checkedChildren="启"
            unCheckedChildren="停"
          />
          <Tooltip title="轮转后旧密钥立刻失效，接收端必须同步更新">
            <Button
              size="small"
              icon={<LockOutlined />}
              onClick={() =>
                rotateMutation.mutate(record.id, {
                  onSuccess: (result) => {
                    setSecretState({ value: result.secret ?? null });
                    message.warning('密钥已轮转，请立刻更新接收端配置');
                  },
                  onError: (err) => message.error(describeFlowError(err, '轮转密钥失败')),
                })
              }
            >
              轮转密钥
            </Button>
          </Tooltip>
          <Popconfirm
            title="删除该订阅？"
            description="删除后不再推送新事件，历史投递日志保留。"
            okText="确认删除"
            cancelText="取消"
            onConfirm={() =>
              deleteMutation.mutate(record.id, {
                onSuccess: () => message.success('订阅已删除'),
                onError: (err) => message.error(describeFlowError(err, '删除失败')),
              })
            }
          >
            <Button size="small" danger type="text" icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {secretState?.value && <SecretReveal secret={secretState.value} onClose={() => setSecretState(null)} />}
      {secretState && !secretState.value && (
        <Alert
          type="info"
          showIcon
          closable
          message="后端本次未返回 secret"
          description="为不伪装可用密钥，此处不展示任何示例值；如需密钥请使“轮转密钥”重新下发。"
          onClose={() => setSecretState(null)}
        />
      )}
      <Card
        size="small"
        title="推送订阅"
        extra={
          <Space>
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
              新建订阅
            </Button>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => {
                void webhooksQuery.refetch();
              }}
            >
              刷新
            </Button>
          </Space>
        }
      >
        {webhooksQuery.isError && (
          <QueryErrorAlert
            error={webhooksQuery.error}
            message="订阅列表加载失败"
            onRetry={() => {
              void webhooksQuery.refetch();
            }}
          />
        )}
        <Table<WebhookSubscription>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={webhooksQuery.data ?? []}
          loading={webhooksQuery.isLoading}
          pagination={false}
          locale={tableEmpty('还没有推送订阅')}
          scroll={{ x: 1100 }}
        />
      </Card>

      {createOpen && <CreateWebhookModal open onClose={() => setCreateOpen(false)} contractId={contractId} onCreated={(webhook) => setSecretState({ value: webhook.secret ?? null })} />}
      {logWebhook && <DeliveriesDrawer webhook={logWebhook} onClose={() => setLogWebhook(null)} />}
    </Space>
  );
}

export default function DeliveryCenter() {
  const { pathname } = useLocation();
  // PNG 真值（3-1/3-2/3-3）：「我的结果集」落地页就是 OpenAPI 接口列表，没有「成果交付」头卡也没有页签条；
  // 数据集打包/结果推送保留为兄弟路由，入口在侧栏「我的结果集」子项（导航原则 2026-10-17 定稿）。
  const view: 'api' | 'dataset' | 'push' = pathname.startsWith('/deliveries/dataset')
    ? 'dataset'
    : pathname.startsWith('/deliveries/push')
      ? 'push'
      : 'api';
  const [contractId, setContractId] = useState<string | null>(null);
  const setStack = useCrumbStackStore((s) => s.setStack);
  const { data: contracts } = useContractOptions();
  const selectedContract = (contracts ?? []).find((contract) => contract.id === contractId);
  const configuredMethods = selectedContract?.delivery_methods ?? null;

  useEffect(() => {
    // dataset/push 的段名已在注册表静态给出，不能再往钻取栈里 push（否则段重复）；
    // 本容器只负责卸载时清栈，把面包屑还给下一个页面。
    return () => setStack([]);
  }, [setStack]);

  if (view === 'api') {
    // 列表/新建/监控的页内段由各视图的 CrumbAnchor 写入；新建提交时从关联负载反推合约。
    return <OpenApiExportTab contractId={contractId} />;
  }

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Title level={4} style={{ margin: 0 }}>
            {view === 'dataset' ? '数据集打包' : '结果推送'}
          </Title>
          <Text type="secondary">
            {view === 'dataset' ? '结果集打包与下载：水印、审查状态以后端返回为准。' : '结果推送订阅：投递状态与密钥可用性以后端返回为准。'}
          </Text>
        </div>
        <Space wrap>
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 280 }}
            placeholder="全部合约"
            value={contractId ?? undefined}
            onChange={(value?: string) => setContractId(value ?? null)}
            options={(contracts ?? []).map((contract) => ({ label: `${contract.title}（${contract.contract_no}）`, value: contract.id }))}
          />
        </Space>
      </div>

      {contractId && configuredMethods !== null && configuredMethods.length === 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="该合约尚未在合约详情里配置交付方式"
          description="可以先在此页操作，但交付方式与自动计算配置需要在合约详情的「交付与计算配置」中确认后才会纳入排程。"
        />
      )}
      {contractId && configuredMethods === null && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="合约列表未下发 delivery_methods，无法判定交付方式是否已配置"
          description="请前往合约详情查看「交付与计算配置」。"
        />
      )}

      <Card size="small" className="cds-delivery-tabs-card">
        {view === 'dataset' ? <DatasetBundleTab contractId={contractId} /> : <ResultPushTab contractId={contractId} />}
      </Card>
    </div>
  );
}
