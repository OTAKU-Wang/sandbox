import { useState } from 'react';
import { Input, Select, Row, Col, Card, Tag, Typography, Empty, Spin, Pagination, Descriptions, Modal, Button, Space } from 'antd';
import { CloudServerOutlined, FileSearchOutlined, SearchOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { catalogApi, type CatalogProduct } from '../../services/catalogApi';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, ROLE_GROUPS } from '../../utils/roles';

const { Title, Paragraph, Text } = Typography;

const securityLevelColors: Record<string, string> = {
  public: 'green',
  internal: 'blue',
  confidential: 'orange',
  secret: 'red',
};

const productTypeLabels: Record<string, string> = {
  structured: '结构化',
  unstructured: '非结构化',
  'semi-structured': '半结构化',
  api: 'API',
};

export default function CatalogPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const canCreateSession = hasAnyRole(user?.role, ROLE_GROUPS.sandboxUsers);
  const [q, setQ] = useState('');
  const [productType, setProductType] = useState<string | undefined>();
  const [industry, setIndustry] = useState<string | undefined>();
  const [securityLevel, setSecurityLevel] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<CatalogProduct | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['catalog', q, productType, industry, securityLevel, page],
    queryFn: () => catalogApi.search({ q: q || undefined, product_type: productType, industry, security_level: securityLevel, page, page_size: 12 }),
  });

  const { data: detailData, isFetching: detailLoading } = useQuery({
    queryKey: ['catalog-detail', detail?.id],
    queryFn: () => catalogApi.get(detail!.id),
    enabled: !!detail?.id,
  });

  const openSandboxCreate = (productId: string) => {
    navigate(`/sandbox-sessions?create=1&product_id=${productId}`);
  };

  const selectedDetail = detailData || detail;

  return (
    <div>
      <Title level={4}>数据目录</Title>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Input
            placeholder="搜索数据产品..."
            prefix={<SearchOutlined />}
            value={q}
            onChange={(e) => { setQ(e.target.value); setPage(1); }}
            allowClear
          />
        </Col>
        <Col span={5}>
          <Select
            placeholder="数据类型"
            value={productType}
            onChange={(v) => { setProductType(v); setPage(1); }}
            allowClear
            style={{ width: '100%' }}
            options={[
              { label: '结构化', value: 'structured' },
              { label: '非结构化', value: 'unstructured' },
              { label: '半结构化', value: 'semi-structured' },
            ]}
          />
        </Col>
        <Col span={5}>
          <Select
            placeholder="安全等级"
            value={securityLevel}
            onChange={(v) => { setSecurityLevel(v); setPage(1); }}
            allowClear
            style={{ width: '100%' }}
            options={[
              { label: '公开', value: 'public' },
              { label: '内部', value: 'internal' },
              { label: '机密', value: 'confidential' },
              { label: '绝密', value: 'secret' },
            ]}
          />
        </Col>
        <Col span={6}>
          <Input
            placeholder="行业筛选"
            value={industry}
            onChange={(e) => { setIndustry(e.target.value || undefined); setPage(1); }}
            allowClear
          />
        </Col>
      </Row>

      {isLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
      ) : !data?.items.length ? (
        <Empty description="暂无数据产品" />
      ) : (
        <>
          <Row gutter={[16, 16]}>
            {data.items.map((product) => (
              <Col key={product.id} xs={24} sm={12} md={8} lg={6}>
                <Card
                  hoverable
                  onClick={() => setDetail(product)}
                  style={{ height: '100%' }}
                >
                  <div style={{ marginBottom: 8 }}>
                    <Tag color="blue">{productTypeLabels[product.product_type] || product.product_type}</Tag>
                    {product.security_level && (
                      <Tag color={securityLevelColors[product.security_level]}>{product.security_level}</Tag>
                    )}
                  </div>
                  <Title level={5} style={{ marginBottom: 8 }} ellipsis>{product.name}</Title>
                  <Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                    {product.description || '暂无描述'}
                  </Paragraph>
                  {product.industry && <Text type="secondary">行业: {product.industry}</Text>}
                  {product.row_count != null && <Text type="secondary" style={{ display: 'block' }}>数据行数: {product.row_count.toLocaleString()}</Text>}
                  <Space wrap style={{ marginTop: 16 }} onClick={(event) => event.stopPropagation()}>
                    <Button size="small" icon={<FileSearchOutlined />} onClick={() => setDetail(product)}>
                      详情
                    </Button>
                    {canCreateSession && (
                      <Button size="small" type="primary" icon={<CloudServerOutlined />} onClick={() => openSandboxCreate(product.id)}>
                        创建沙箱
                      </Button>
                    )}
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
          <div style={{ textAlign: 'center', marginTop: 24 }}>
            <Pagination
              current={page}
              total={data.total}
              pageSize={12}
              onChange={setPage}
              showTotal={(total) => `共 ${total} 个产品`}
            />
          </div>
        </>
      )}

      <Modal
        title="数据产品详情"
        open={!!selectedDetail}
        onCancel={() => setDetail(null)}
        footer={[
          <Button key="close" onClick={() => setDetail(null)}>关闭</Button>,
          canCreateSession && selectedDetail ? (
            <Button key="sandbox" type="primary" icon={<CloudServerOutlined />} onClick={() => openSandboxCreate(selectedDetail.id)}>
              创建沙箱
            </Button>
          ) : null,
        ]}
        width={640}
      >
        {detailLoading && !detailData ? (
          <Spin style={{ display: 'block', margin: '40px auto' }} />
        ) : selectedDetail && (
          <Descriptions bordered column={1} size="small">
            <Descriptions.Item label="名称">{selectedDetail.name}</Descriptions.Item>
            <Descriptions.Item label="描述">{selectedDetail.description || '-'}</Descriptions.Item>
            <Descriptions.Item label="数据类型">{productTypeLabels[selectedDetail.product_type] || selectedDetail.product_type}</Descriptions.Item>
            <Descriptions.Item label="行业">{selectedDetail.industry || '-'}</Descriptions.Item>
            <Descriptions.Item label="安全等级">
              {selectedDetail.security_level ? <Tag color={securityLevelColors[selectedDetail.security_level]}>{selectedDetail.security_level}</Tag> : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="允许操作">{selectedDetail.allowed_operations?.join(', ') || '-'}</Descriptions.Item>
            <Descriptions.Item label="数据行数">{selectedDetail.row_count?.toLocaleString() || '-'}</Descriptions.Item>
            <Descriptions.Item label="输出约束">
              {selectedDetail.output_constraints ? <pre className="cds-json-block">{JSON.stringify(selectedDetail.output_constraints, null, 2)}</pre> : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="数据Schema">
              {selectedDetail.data_schema ? <pre className="cds-json-block">{JSON.stringify(selectedDetail.data_schema, null, 2)}</pre> : '-'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
