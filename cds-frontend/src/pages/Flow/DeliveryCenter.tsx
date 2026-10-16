import { useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  LockOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  SwapOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { sandboxApi } from '../../services/sandboxApi';
import {
  WEBHOOK_EVENT_OPTIONS,
  buildCurlExample,
  buildRequestExample,
  describeFlowError,
  flattenOpenApiOperations,
  flowApi,
  formatBytes,
  formatDateTime,
  pickCallCount,
  useApiEndpoints,
  useBuildDatasetBundle,
  useContractOpenApiDoc,
  useCreateEndpoint,
  useCreateWebhook,
  useDeleteEndpoint,
  useDeleteWebhook,
  useRedeliver,
  useRotateWebhookSecret,
  useTestWebhook,
  useUpdateEndpoint,
  useUpdateWebhook,
  useWebhookDeliveries,
  useWebhooks,
} from '../../services/flow';
import type {
  ApiEndpoint,
  DatasetBundle,
  OpenApiDoc,
  WebhookDelivery,
  WebhookSubscription,
} from '../../services/flow';
import { QueryErrorAlert, tableEmpty } from '../../components/Feedback/QueryFeedback';
import {
  DELIVERY_STATUS_META,
  ENDPOINT_STATUS_META,
  WEBHOOK_STATUS_META,
  attemptLabel,
  openApiPathForSlug,
  responseStatusColor,
  statusMeta,
} from './display';
import { useContractOptions } from './useContractOptions';
import { copyText } from './copyText';

const { Text, Title, Paragraph } = Typography;

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

function DatasetBundleTab() {
  const [form] = Form.useForm();
  const [bundle, setBundle] = useState<DatasetBundle | null>(null);
  const { data: contracts } = useContractOptions();
  const sessionsQuery = useQuery({
    queryKey: ['sandbox-sessions', 'delivery-bundle', 1, 50],
    queryFn: () => sandboxApi.list({ page: 1, page_size: 50 }),
  });
  const bundleMutation = useBuildDatasetBundle();

  const build = () => {
    void form
      .validateFields()
      .then((values: { session_id: string; contract_id: string }) => {
        bundleMutation.mutate(
          { session_id: values.session_id, contract_id: values.contract_id },
          {
            onSuccess: (result) => {
              setBundle(result);
              message.success(`打包完成：${result.files?.length ?? 0} 个文件，${formatBytes(result.size_bytes)}`);
            },
            onError: (err) => message.error(describeFlowError(err, '打包失败')),
          },
        );
      })
      .catch(() => undefined);
  };

  const download = () => {
    if (!bundle) return;
    void flowApi.downloadBundle(bundle.bundle_id).then(
      (blob) => {
        void saveBundleFile(bundle.bundle_id, blob);
        message.success('已开始下载');
      },
      (err: unknown) => {
        void describeDownloadError(err).then((text) => message.error(text));
      },
    );
  };

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

function CreateEndpointModal({
  open,
  onClose,
  contractId,
}: {
  open: boolean;
  onClose: () => void;
  contractId: string | null;
}) {
  const [form] = Form.useForm();
  const createMutation = useCreateEndpoint();
  const { data: contractOptions } = useContractOptions();

  const submit = () => {
    void form
      .validateFields()
      .then((values: { contract_id: string; name: string; slug: string; workload_id?: string; rate_limit_per_minute?: number; quota_total?: number; request_schema?: string }) => {
        let schema: Record<string, unknown> | null = null;
        const rawSchema = values.request_schema?.trim();
        if (rawSchema) {
          try {
            schema = JSON.parse(rawSchema) as Record<string, unknown>;
          } catch {
            message.error('请求 Schema 不是合法 JSON');
            return;
          }
        }
        createMutation.mutate(
          {
            contract_id: values.contract_id,
            name: values.name.trim(),
            slug: values.slug.trim(),
            workload_id: values.workload_id || null,
            rate_limit_per_minute: values.rate_limit_per_minute ?? null,
            quota_total: values.quota_total ?? null,
            request_schema: schema,
          },
          {
            onSuccess: (endpoint) => {
              message.success(`端点已创建：${openApiPathForSlug(endpoint.slug)}`);
              form.resetFields();
              onClose();
            },
            onError: (err) => message.error(describeFlowError(err, '创建端点失败')),
          },
        );
      })
      .catch(() => undefined);
  };

  return (
    <Modal title="新建 API 端点" open={open} onCancel={onClose} onOk={submit} confirmLoading={createMutation.isPending} okText="创建" width={560}>
      <Form form={form} layout="vertical">
        <Form.Item name="contract_id" label="合约" rules={[{ required: true, message: '请选择合约' }]} initialValue={contractId ?? undefined}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="选择合约"
            options={(contractOptions ?? []).map((contract) => ({ label: `${contract.title}（${contract.contract_no}）`, value: contract.id }))}
          />
        </Form.Item>
        <Form.Item name="name" label="端点名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input placeholder="流失评分查询" maxLength={80} />
        </Form.Item>
        <Form.Item
          name="slug"
          label="slug"
          extra="外部调用路径为 POST /open/v1/{slug}"
          rules={[
            { required: true, message: '请输入 slug' },
            { pattern: /^[a-z0-9][a-z0-9_-]{1,63}$/, message: '仅允许小写字母、数字、下划线与短横线，长度 2~64' },
          ]}
        >
          <Input placeholder="churn-score" />
        </Form.Item>
        <Form.Item name="workload_id" label="绑定生产负载 ID" tooltip="必须是已发布的生产版本负载，后端会校验">
          <Input placeholder="负载 ID" />
        </Form.Item>
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="rate_limit_per_minute" label="每分钟限流">
              <InputNumber style={{ width: '100%' }} min={1} max={100000} precision={0} placeholder="如 60" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="quota_total" label="总配额（调用次数）">
              <InputNumber style={{ width: '100%' }} min={1} precision={0} placeholder="留空为不限" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="request_schema" label="请求 Schema（JSON，可选）">
          <Input.TextArea rows={4} placeholder='{"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]}' />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function ApiDeliveryTab({ contractId }: { contractId: string | null }) {
  const [createOpen, setCreateOpen] = useState(false);
  const endpointsQuery = useApiEndpoints(contractId);
  const docQuery = useContractOpenApiDoc(contractId);
  const updateMutation = useUpdateEndpoint();
  const deleteMutation = useDeleteEndpoint();
  const docRows = flattenOpenApiOperations(docQuery.data ?? null);

  const curlFor = (endpoint: ApiEndpoint) => {
    const row = docRows.find((candidate) => candidate.path.includes(endpoint.slug));
    return buildCurlExample(endpoint.slug, buildRequestExample(row, endpoint));
  };

  const columns: ColumnsType<ApiEndpoint> = [
    { title: '名称', dataIndex: 'name' },
    {
      title: '调用路径',
      dataIndex: 'slug',
      render: (slug: string) => <Text code copyable={{ text: openApiPathForSlug(slug) }}>{openApiPathForSlug(slug)}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (status: string) => (
        <Tag color={statusMeta(ENDPOINT_STATUS_META, status).color}>{statusMeta(ENDPOINT_STATUS_META, status).label}</Tag>
      ),
    },
    {
      title: '限流',
      dataIndex: 'rate_limit_per_minute',
      width: 110,
      render: (value: number | null) => (typeof value === 'number' ? `${value} 次/分` : <Text type="secondary">未设置</Text>),
    },
    {
      title: '调用量 / 配额',
      key: 'usage',
      width: 160,
      render: (_: unknown, record) => {
        const used = pickCallCount(record as unknown as Record<string, unknown>);
        const quota = record.quota_total;
        return (
          <Space direction="vertical" size={0}>
            <Text>{typeof used === 'number' ? `${used} 次` : '后端未下发调用量'}</Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              配额 {typeof quota === 'number' ? quota : '不限'}
            </Text>
          </Space>
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 260,
      render: (_: unknown, record) => (
        <Space size={4}>
          <CopyButton text={curlFor(record)} label="调用示例" />
          <Button
            size="small"
            onClick={() =>
              updateMutation.mutate(
                { id: record.id, payload: { status: record.status === 'enabled' ? 'disabled' : 'enabled' } },
                {
                  onSuccess: () => message.success(record.status === 'enabled' ? '端点已停用' : '端点已启用'),
                  onError: (err) => message.error(describeFlowError(err, '状态更新失败')),
                },
              )
            }
          >
            {record.status === 'enabled' ? '停用' : '启用'}
          </Button>
          <Popconfirm
            title="删除该端点？"
            description="删除后外部调用立即失效，历史调用日志保留。"
            okText="确认删除"
            cancelText="取消"
            onConfirm={() =>
              deleteMutation.mutate(record.id, {
                onSuccess: () => message.success('端点已删除'),
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
      <Card
        size="small"
        title="端点"
        extra={
          <Space>
            <Button size="small" type="primary" icon={<PlusOutlined />} disabled={!contractId} onClick={() => setCreateOpen(true)}>
              新建端点
            </Button>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => {
                void endpointsQuery.refetch();
                void docQuery.refetch();
              }}
            >
              刷新
            </Button>
          </Space>
        }
      >
        {!contractId && <Alert type="info" showIcon message="请先在上方选择合约：端点与 OpenAPI 文档都按合约维度返回。" />}
        {contractId && (
          <Table<ApiEndpoint>
            rowKey="id"
            size="small"
            columns={columns}
            dataSource={endpointsQuery.data ?? []}
            loading={endpointsQuery.isLoading}
            pagination={false}
            locale={tableEmpty('该合约还没有 API 端点')}
          />
        )}
        {endpointsQuery.isError && (
          <QueryErrorAlert
            error={endpointsQuery.error}
            message="端点列表加载失败"
            onRetry={() => {
              void endpointsQuery.refetch();
            }}
          />
        )}
      </Card>

      <Card size="small" title="OpenAPI 文档预览">
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
          <OpenApiPreview doc={docQuery.data ?? null} endpoints={endpointsQuery.data ?? []} />
        )}
      </Card>

      {createOpen && <CreateEndpointModal open onClose={() => setCreateOpen(false)} contractId={contractId} />}
    </Space>
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
  const [contractId, setContractId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('dataset');
  const { data: contracts } = useContractOptions();
  const selectedContract = (contracts ?? []).find((contract) => contract.id === contractId);
  const configuredMethods = selectedContract?.delivery_methods ?? null;

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Title level={4} style={{ margin: 0 }}>
            成果交付
          </Title>
          <Text type="secondary">数据集打包、API 交付与结果推送：水印、配额、投递状态与密钥可用性均以后端返回为准。</Text>
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

      <Card size="small">
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={[
            {
              key: 'dataset',
              label: '数据集打包',
              children: <DatasetBundleTab />,
            },
            {
              key: 'api',
              label: (
                <Space size={6}>
                  API 交付
                  {contractId && configuredMethods?.includes('open_api') ? <Badge status="processing" /> : null}
                </Space>
              ),
              children: <ApiDeliveryTab contractId={contractId} />,
            },
            {
              key: 'push',
              label: '结果推送',
              children: <ResultPushTab contractId={contractId} />,
            },
          ]}
        />
      </Card>
    </div>
  );
}
