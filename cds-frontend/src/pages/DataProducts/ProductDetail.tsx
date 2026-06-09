import { useMemo } from 'react';
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
  Steps,
  message,
  Tooltip,
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

  const { data: product, isLoading } = useQuery({
    queryKey: ['data-product', id],
    queryFn: () => dataProductApi.get(id!),
    enabled: !!id,
  });

  const isOwner = !!product && user?.id === product.provider_id;
  const canReview = hasAnyRole(user?.role, [UserRole.OPERATOR, UserRole.ADMIN]);

  const { data: versions } = useQuery({
    queryKey: ['data-product-versions', id],
    queryFn: () => dataProductApi.listVersions(id!),
    enabled: !!id && (isOwner || canReview),
  });

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
    </div>
  );
}
