import { useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Descriptions,
  Tag,
  Button,
  Typography,
  Spin,
  Space,
  Card,
  Row,
  Col,
  Table,
  Empty,
  Input,
  Select,
  Steps,
  message,
  Tooltip,
  Switch,
} from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileTextOutlined,
  RocketOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { dataProductApi } from '../../services/dataProductApi';
import { fieldExposureApi, type ExposureRequest, type FieldRule, type FieldSensitivity } from '../../services/fieldExposureApi';
import { ProductStatus, UserRole } from '../../types/enums';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole } from '../../utils/roles';

const { Title, Paragraph, Text } = Typography;

const typeLabels: Record<string, string> = {
  structured: '结构化',
  unstructured: '非结构化',
  'semi-structured': '半结构化',
  api: 'API',
};

const statusLabels: Record<string, string> = {
  draft: '草稿',
  reviewing: '审核中',
  approved: '已批准',
  published: '已发布',
  suspended: '已暂停',
  archived: '已归档',
};

const statusColors: Record<string, string> = {
  draft: 'default',
  reviewing: 'processing',
  approved: 'blue',
  published: 'green',
  suspended: 'orange',
  archived: 'default',
};

const securityLevelLabels: Record<string, string> = {
  public: '公开',
  internal: '内部',
  confidential: '机密',
  secret: '绝密',
};

const securityLevelColors: Record<string, string> = {
  public: 'green',
  internal: 'blue',
  confidential: 'orange',
  secret: 'red',
};

const operationLabels: Record<string, string> = {
  read: '读取',
  query: '查询',
  export: '导出',
  train: '训练',
  aggregate: '聚合',
};

const sensitivityLabels: Record<FieldSensitivity, string> = {
  public: '公开',
  internal: '内部',
  sensitive: '敏感',
  pii: 'PII',
  restricted: '受限',
};

const sensitivityColors: Record<FieldSensitivity, string> = {
  public: 'green',
  internal: 'blue',
  sensitive: 'orange',
  pii: 'red',
  restricted: 'volcano',
};

const requestStatusColors: Record<string, string> = {
  pending: 'processing',
  approved: 'green',
  rejected: 'red',
  revoked: 'default',
};

type LifecycleAction = 'submit' | 'approve' | 'reject' | 'publish';

interface SchemaFieldRow {
  key: string;
  name: string;
  type: string;
  sensitivity: string;
  description: string;
}

function shortId(id?: string | null) {
  return id ? `${id.slice(0, 8)}...${id.slice(-6)}` : '-';
}

function formatNumber(value?: number | null) {
  return value == null ? '-' : value.toLocaleString();
}

function getConstraintValue(constraints: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = constraints[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
}

function renderConstraintValue(value: unknown) {
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object' && value !== null) return JSON.stringify(value);
  return String(value);
}

export default function ProductDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [fieldRules, setFieldRules] = useState<Record<string, FieldRule>>({});
  const [defaultSensitivity, setDefaultSensitivity] = useState<FieldSensitivity>('internal');

  const { data: product, isLoading } = useQuery({
    queryKey: ['data-product', id],
    queryFn: () => dataProductApi.get(id!),
    enabled: !!id,
  });

  const isOwner = !!product && user?.id === product.provider_id;
  const canReview = hasAnyRole(user?.role, [UserRole.OPERATOR, UserRole.ADMIN]);
  const canEditFieldRules = isOwner;

  const { data: versions } = useQuery({
    queryKey: ['data-product-versions', id],
    queryFn: () => dataProductApi.listVersions(id!),
    enabled: !!id && (isOwner || canReview),
  });

  const { data: visibilityConfig } = useQuery({
    queryKey: ['field-visibility', id],
    queryFn: () => fieldExposureApi.getVisibility(id!),
    enabled: !!id && (isOwner || canReview),
    retry: false,
  });

  const { data: exposureRequests } = useQuery({
    queryKey: ['field-exposure-requests', id],
    queryFn: () => fieldExposureApi.listRequests({ product_id: id, as_provider: true }),
    enabled: !!id && (isOwner || canReview),
  });

  useEffect(() => {
    if (!visibilityConfig) return;
    setFieldRules(visibilityConfig.field_rules || {});
    setDefaultSensitivity(visibilityConfig.default_sensitivity);
  }, [visibilityConfig]);

  const lifecycleMutation = useMutation({
    mutationFn: (action: LifecycleAction) => {
      if (!id) throw new Error('缺少产品 ID');
      if (action === 'submit') return dataProductApi.submit(id);
      if (action === 'approve') return dataProductApi.approve(id);
      if (action === 'reject') return dataProductApi.reject(id);
      return dataProductApi.publish(id);
    },
    onSuccess: (updated, action) => {
      const successText: Record<LifecycleAction, string> = {
        submit: '已提交审核',
        approve: '已通过审核',
        reject: '已退回草稿',
        publish: '已发布到目录',
      };
      message.success(successText[action]);
      queryClient.setQueryData(['data-product', id], updated);
      queryClient.invalidateQueries({ queryKey: ['data-products'] });
      queryClient.invalidateQueries({ queryKey: ['catalog'] });
      queryClient.invalidateQueries({ queryKey: ['data-product-versions', id] });
    },
    onError: () => message.error('操作失败'),
  });

  const saveFieldRulesMutation = useMutation({
    mutationFn: () => {
      const rules: Record<string, FieldRule> = {};
      schemaFields.forEach((field) => {
        const rule = fieldRules[field.name] || { sensitivity: defaultSensitivity };
        rules[field.name] = {
          sensitivity: rule.sensitivity || defaultSensitivity,
          mask_pattern: rule.mask_pattern || null,
          auto_approve: !!rule.auto_approve,
          description: rule.description || null,
        };
      });
      return fieldExposureApi.setVisibility(product!.id, {
        default_sensitivity: defaultSensitivity,
        field_rules: rules,
      });
    },
    onSuccess: (config) => {
      message.success('字段可见性规则已保存');
      setFieldRules(config.field_rules || {});
      queryClient.invalidateQueries({ queryKey: ['field-visibility', id] });
    },
    onError: () => message.error('字段可见性规则保存失败'),
  });

  const reviewExposureMutation = useMutation({
    mutationFn: ({ request, reject }: { request: ExposureRequest; reject?: boolean }) => (
      reject
        ? fieldExposureApi.reviewRequest(request.id, { rejection_reason: '不符合当前数据最小化授权策略' })
        : fieldExposureApi.reviewRequest(request.id, { approved_fields: request.requested_fields })
    ),
    onSuccess: () => {
      message.success('字段申请已处理');
      queryClient.invalidateQueries({ queryKey: ['field-exposure-requests', id] });
    },
    onError: (error: any) => {
      const detail = error?.detail;
      message.error(typeof detail === 'string' ? detail : '字段申请处理失败');
    },
  });

  const schemaFields = useMemo<SchemaFieldRow[]>(() => {
    const fields = product?.data_schema?.fields;
    if (!Array.isArray(fields)) return [];
    return fields.map((field, index) => {
      const item = field as Record<string, unknown>;
      return {
        key: String(item.name ?? index),
        name: String(item.name ?? `字段 ${index + 1}`),
        type: String(item.type ?? '-'),
        sensitivity: String(item.sensitivity ?? item.level ?? '-'),
        description: String(item.description ?? item.comment ?? '-'),
      };
    });
  }, [product?.data_schema]);

  if (isLoading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  if (!product) return <Empty description="产品不存在或无权访问" />;

  const outputConstraints = product.output_constraints ?? {};
  const constraintRows = [
    { label: '最大输出行数', value: getConstraintValue(outputConstraints, ['maxOutputRows', 'max_output_rows', 'maxRows']) },
    { label: '要求差分隐私', value: getConstraintValue(outputConstraints, ['requireDP', 'require_dp']) },
    { label: '默认 DP ε', value: getConstraintValue(outputConstraints, ['epsilon', 'dp_epsilon']) },
    { label: '最大运行秒数', value: getConstraintValue(outputConstraints, ['maxRuntimeSeconds', 'max_runtime_seconds']) },
    { label: '允许输出格式', value: getConstraintValue(outputConstraints, ['allowedOutputFormats', 'allowed_output_formats', 'formats']) },
  ].filter((item) => item.value !== undefined);

  const lifecycleSteps = [
    { key: ProductStatus.DRAFT, title: '草稿' },
    { key: ProductStatus.REVIEWING, title: '审核中' },
    { key: ProductStatus.APPROVED, title: '已批准' },
    { key: ProductStatus.PUBLISHED, title: '已发布' },
  ];
  const lifecycleIndex = lifecycleSteps.findIndex((step) => step.key === product.status);

  return (
    <div>
      <div className="cds-page-toolbar">
        <div>
          <Space wrap align="center">
            <Title level={4} style={{ margin: 0 }}>{product.name}</Title>
            <Tag color={statusColors[product.status] ?? 'default'}>{statusLabels[product.status] ?? product.status}</Tag>
            {product.security_level && (
              <Tag color={securityLevelColors[product.security_level] ?? 'default'}>
                {securityLevelLabels[product.security_level] ?? product.security_level}
              </Tag>
            )}
          </Space>
          <Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
            {product.description || '暂无描述'}
          </Paragraph>
        </div>
        <Space wrap>
          <Button onClick={() => navigate('/data-products')}>返回列表</Button>
          {isOwner && product.status === ProductStatus.PUBLISHED && (
            <Button icon={<FileTextOutlined />} onClick={() => navigate(`/contracts/create?product_id=${product.id}`)}>
              创建合约
            </Button>
          )}
          {isOwner && product.status === ProductStatus.DRAFT && (
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={lifecycleMutation.isPending}
              onClick={() => lifecycleMutation.mutate('submit')}
            >
              提交审核
            </Button>
          )}
          {canReview && product.status === ProductStatus.REVIEWING && (
            <>
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                loading={lifecycleMutation.isPending}
                onClick={() => lifecycleMutation.mutate('approve')}
              >
                通过审核
              </Button>
              <Button
                danger
                icon={<CloseCircleOutlined />}
                loading={lifecycleMutation.isPending}
                onClick={() => lifecycleMutation.mutate('reject')}
              >
                退回
              </Button>
            </>
          )}
          {isOwner && product.status === ProductStatus.APPROVED && (
            <Tooltip title={product.resource_id ? '' : '发布前需要绑定数据资源'}>
              <span>
                <Button
                  type="primary"
                  icon={<RocketOutlined />}
                  disabled={!product.resource_id}
                  loading={lifecycleMutation.isPending}
                  onClick={() => lifecycleMutation.mutate('publish')}
                >
                  发布到目录
                </Button>
              </span>
            </Tooltip>
          )}
        </Space>
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card title="产品信息">
            <Descriptions bordered column={2} size="small">
              <Descriptions.Item label="数据类型">
                <Tag>{typeLabels[product.product_type] || product.product_type}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="所属行业">{product.industry || '-'}</Descriptions.Item>
              <Descriptions.Item label="数据行数">{formatNumber(product.row_count)}</Descriptions.Item>
              <Descriptions.Item label="资源绑定">
                {product.resource_id ? <Text code>{shortId(product.resource_id)}</Text> : <Tag color="red">未绑定</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="Provider">
                <Text code>{shortId(product.provider_id)}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">{new Date(product.created_at).toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="更新时间">{new Date(product.updated_at).toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="允许操作" span={2}>
                <Space wrap>
                  {(product.allowed_operations || []).map((op) => (
                    <Tag key={op}>{operationLabels[op] || op}</Tag>
                  ))}
                  {!product.allowed_operations?.length && '-'}
                </Space>
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="生命周期">
            <Steps
              size="small"
              direction="vertical"
              current={lifecycleIndex >= 0 ? lifecycleIndex : 0}
              items={lifecycleSteps.map((step) => ({ title: step.title }))}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={14}>
          <Card title="数据 Schema">
            {schemaFields.length > 0 ? (
              <Table
                size="small"
                pagination={false}
                dataSource={schemaFields}
                columns={[
                  { title: '字段', dataIndex: 'name', key: 'name' },
                  { title: '类型', dataIndex: 'type', key: 'type', width: 140 },
                  { title: '敏感级别', dataIndex: 'sensitivity', key: 'sensitivity', width: 120, render: (value) => <Tag>{value}</Tag> },
                  { title: '说明', dataIndex: 'description', key: 'description' },
                ]}
              />
            ) : product.data_schema ? (
              <pre className="cds-json-block">{JSON.stringify(product.data_schema, null, 2)}</pre>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Schema" />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="输出约束">
            {constraintRows.length > 0 ? (
              <Descriptions bordered column={1} size="small">
                {constraintRows.map((item) => (
                  <Descriptions.Item key={item.label} label={item.label}>
                    {renderConstraintValue(item.value)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无输出约束" />
            )}
          </Card>
        </Col>
      </Row>

      {(versions?.versions?.length || 0) > 0 && (
        <Card title="版本记录" style={{ marginTop: 16 }}>
          <Table
            size="small"
            pagination={false}
            rowKey="id"
            dataSource={versions?.versions || []}
            columns={[
              { title: '版本', dataIndex: 'version', key: 'version', width: 90, render: (value) => `v${value}` },
              { title: '状态', dataIndex: 'status', key: 'status', width: 120, render: (value) => <Tag color={statusColors[value] ?? 'default'}>{statusLabels[value] ?? value}</Tag> },
              { title: '最新', dataIndex: 'is_latest', key: 'is_latest', width: 90, render: (value) => value ? <Tag color="green">是</Tag> : '-' },
              { title: '变更说明', dataIndex: 'change_summary', key: 'change_summary', render: (value) => value || '-' },
              { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180, render: (value) => new Date(value).toLocaleString() },
            ]}
          />
        </Card>
      )}

      {(isOwner || canReview) && (
        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col xs={24} xl={14}>
            <Card
              title="字段最小化规则"
              extra={canEditFieldRules && (
                <Space>
                  <Select
                    value={defaultSensitivity}
                    onChange={setDefaultSensitivity}
                    style={{ width: 120 }}
                    options={(Object.keys(sensitivityLabels) as FieldSensitivity[]).map((value) => ({
                      label: sensitivityLabels[value],
                      value,
                    }))}
                  />
                  <Button loading={saveFieldRulesMutation.isPending} onClick={() => saveFieldRulesMutation.mutate()}>
                    保存规则
                  </Button>
                </Space>
              )}
            >
              {schemaFields.length > 0 ? (
                <Table
                  size="small"
                  pagination={false}
                  rowKey="name"
                  dataSource={schemaFields}
                  columns={[
                    { title: '字段', dataIndex: 'name', key: 'name', width: 160 },
                    {
                      title: '敏感级别',
                      key: 'sensitivity',
                      width: 150,
                      render: (_, field) => {
                        const value = fieldRules[field.name]?.sensitivity || defaultSensitivity;
                        return canEditFieldRules ? (
                          <Select
                            value={value}
                            style={{ width: 120 }}
                            onChange={(next) => setFieldRules((current) => ({
                              ...current,
                              [field.name]: { ...(current[field.name] || { sensitivity: defaultSensitivity }), sensitivity: next },
                            }))}
                            options={(Object.keys(sensitivityLabels) as FieldSensitivity[]).map((item) => ({
                              label: sensitivityLabels[item],
                              value: item,
                            }))}
                          />
                        ) : <Tag color={sensitivityColors[value]}>{sensitivityLabels[value]}</Tag>;
                      },
                    },
                    {
                      title: '自动审批',
                      key: 'auto_approve',
                      width: 100,
                      render: (_, field) => (
                        <Switch
                          checked={!!fieldRules[field.name]?.auto_approve}
                          disabled={!canEditFieldRules || (fieldRules[field.name]?.sensitivity || defaultSensitivity) === 'restricted'}
                          onChange={(checked) => setFieldRules((current) => ({
                            ...current,
                            [field.name]: { ...(current[field.name] || { sensitivity: defaultSensitivity }), auto_approve: checked },
                          }))}
                        />
                      ),
                    },
                    {
                      title: '脱敏方式',
                      key: 'mask_pattern',
                      render: (_, field) => (
                        canEditFieldRules ? (
                          <Input
                            value={fieldRules[field.name]?.mask_pattern || ''}
                            placeholder="redact/hash/keep_first_3_last_4"
                            onChange={(event) => setFieldRules((current) => ({
                              ...current,
                              [field.name]: { ...(current[field.name] || { sensitivity: defaultSensitivity }), mask_pattern: event.target.value },
                            }))}
                          />
                        ) : fieldRules[field.name]?.mask_pattern || '-'
                      ),
                    },
                  ]}
                />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="产品 Schema 中没有字段定义" />
              )}
            </Card>
          </Col>
          <Col xs={24} xl={10}>
            <Card title="字段访问申请">
              {(exposureRequests || []).length > 0 ? (
                <Table
                  size="small"
                  pagination={false}
                  rowKey="id"
                  dataSource={exposureRequests || []}
                  columns={[
                    { title: '买方', dataIndex: 'buyer_id', key: 'buyer_id', width: 110, render: (value) => <Text code>{shortId(value)}</Text> },
                    { title: '字段', dataIndex: 'requested_fields', key: 'requested_fields', render: (value: string[]) => value.join(', ') },
                    { title: '状态', dataIndex: 'status', key: 'status', width: 95, render: (value) => <Tag color={requestStatusColors[value] || 'default'}>{value}</Tag> },
                    {
                      title: '操作',
                      key: 'action',
                      width: 130,
                      render: (_, request) => request.status === 'pending' ? (
                        <Space>
                          <Button size="small" type="link" onClick={() => reviewExposureMutation.mutate({ request })}>批准</Button>
                          <Button size="small" type="link" danger onClick={() => reviewExposureMutation.mutate({ request, reject: true })}>拒绝</Button>
                        </Space>
                      ) : '-',
                    },
                  ]}
                />
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无字段申请" />
              )}
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
}
