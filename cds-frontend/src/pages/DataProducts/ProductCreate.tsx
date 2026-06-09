import { useState } from 'react';
import { Form, Input, Select, Button, Card, Typography, Steps, message, InputNumber, Alert, Descriptions } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { dataProductApi } from '../../services/dataProductApi';
import { dataResourceApi } from '../../services/dataResourceApi';

const { Title } = Typography;
const { TextArea } = Input;

interface ProductFormValues {
  name: string;
  description?: string;
  product_type: string;
  resource_id?: string;
  industry?: string;
  security_level?: string;
  allowed_operations?: string[];
  row_count?: number;
  data_schema?: string;
  max_output_rows?: number;
  require_dp?: boolean;
  dp_epsilon?: number;
  max_runtime_seconds?: number;
  allowed_output_formats?: string[];
}

export default function ProductCreate() {
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [current, setCurrent] = useState(0);
  const selectedResourceId = Form.useWatch('resource_id', form);

  const { data: resources } = useQuery({
    queryKey: ['data-resources-product-create'],
    queryFn: () => dataResourceApi.list({ page: 1, page_size: 100 }),
  });

  const selectedResource = resources?.items.find((resource) => resource.id === selectedResourceId);

  const mutation = useMutation({
    mutationFn: dataProductApi.create,
    onSuccess: (product) => {
      message.success('产品创建成功');
      navigate(`/data-products/${product.id}`);
    },
    onError: () => message.error('创建失败'),
  });

  const steps = [
    { title: '基本信息', content: (
      <>
        <Form.Item name="resource_id" label="绑定数据资源">
          <Select
            allowClear
            showSearch
            placeholder="选择已上传且处理完成的数据资源"
            optionFilterProp="label"
            options={(resources?.items || []).map((resource) => ({
              label: `${resource.name} / ${resource.format} / ${resource.status}`,
              value: resource.id,
              disabled: resource.status !== 'ready',
            }))}
          />
        </Form.Item>
        {selectedResource && (
          <Descriptions size="small" bordered column={3} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="格式">{selectedResource.format}</Descriptions.Item>
            <Descriptions.Item label="行数">{selectedResource.row_count ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="大小">
              {selectedResource.file_size_bytes ? `${(selectedResource.file_size_bytes / 1024).toFixed(1)} KB` : '-'}
            </Descriptions.Item>
          </Descriptions>
        )}
        <Form.Item name="name" label="产品名称" rules={[{ required: true, max: 200, message: '请输入名称（最多200字）' }]}>
          <Input placeholder="数据产品名称" />
        </Form.Item>
        <Form.Item name="description" label="产品描述">
          <TextArea rows={3} placeholder="产品描述" />
        </Form.Item>
        <Form.Item name="product_type" label="数据类型" rules={[{ required: true, message: '请选择数据类型' }]}>
          <Select options={[
            { label: '结构化数据', value: 'structured' },
            { label: '非结构化数据', value: 'unstructured' },
            { label: '半结构化数据', value: 'semi-structured' },
            { label: 'API服务', value: 'api' },
          ]} />
        </Form.Item>
      </>
    )},
    { title: '安全配置', content: (
      <>
        <Form.Item name="industry" label="所属行业">
          <Input placeholder="所属行业（可选）" />
        </Form.Item>
        <Form.Item name="security_level" label="安全等级">
          <Select allowClear placeholder="选择安全等级" options={[
            { label: '公开', value: 'public' },
            { label: '内部', value: 'internal' },
            { label: '机密', value: 'confidential' },
            { label: '绝密', value: 'secret' },
          ]} />
        </Form.Item>
        <Form.Item name="allowed_operations" label="允许操作">
          <Select mode="multiple" allowClear placeholder="选择允许的操作" options={[
            { label: '读取', value: 'read' },
            { label: '查询', value: 'query' },
            { label: '导出', value: 'export' },
            { label: '训练', value: 'train' },
            { label: '聚合', value: 'aggregate' },
          ]} />
        </Form.Item>
        <Form.Item name="row_count" label="数据行数">
          <InputNumber min={0} style={{ width: '100%' }} placeholder="未绑定资源时可手工填写" />
        </Form.Item>
        <Form.Item name="data_schema" label="数据 Schema">
          <TextArea rows={4} placeholder='{"fields":[{"name":"age","type":"integer","sensitivity":"low"}]}' />
        </Form.Item>
      </>
    )},
    { title: '输出约束', content: (
      <>
        <Form.Item name="max_output_rows" label="最大输出行数">
          <InputNumber min={1} max={1000000} style={{ width: '100%' }} placeholder="默认由合约或网关控制" />
        </Form.Item>
        <Form.Item name="require_dp" label="差分隐私要求">
          <Select allowClear options={[
            { label: '要求差分隐私', value: true },
            { label: '不强制', value: false },
          ]} />
        </Form.Item>
        <Form.Item name="dp_epsilon" label="默认 DP ε">
          <InputNumber min={0.01} step={0.1} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="max_runtime_seconds" label="最大运行秒数">
          <InputNumber min={60} max={86400} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="allowed_output_formats" label="允许输出格式">
          <Select mode="multiple" allowClear options={[
            { label: 'CSV', value: 'csv' },
            { label: 'JSON', value: 'json' },
            { label: 'Parquet', value: 'parquet' },
            { label: 'XLSX', value: 'xlsx' },
          ]} />
        </Form.Item>
      </>
    )},
    { title: '确认', content: (
      <Alert type="info" showIcon message="确认创建数据产品" description="创建后产品进入草稿状态，可继续补充资源、策略和发布流程。" />
    )},
  ];

  const next = async () => {
    if (current === 0) {
      await form.validateFields(['name', 'product_type']);
    }
    setCurrent(current + 1);
  };

  const onFinish = () => {
    const values = form.getFieldsValue(true) as ProductFormValues;
    let dataSchema: Record<string, unknown> | undefined;
    if (values.data_schema?.trim()) {
      try {
        dataSchema = JSON.parse(values.data_schema);
      } catch {
        message.error('数据 Schema 必须是合法 JSON');
        return;
      }
    }

    const outputConstraints = {
      maxOutputRows: values.max_output_rows,
      requireDP: values.require_dp,
      epsilon: values.dp_epsilon,
      maxRuntimeSeconds: values.max_runtime_seconds,
      allowedOutputFormats: values.allowed_output_formats,
    };
    const cleanOutputConstraints = Object.fromEntries(
      Object.entries(outputConstraints).filter(([, value]) => value !== undefined && value !== null),
    );

    mutation.mutate({
      name: values.name,
      description: values.description,
      product_type: values.product_type,
      resource_id: values.resource_id,
      industry: values.industry,
      row_count: values.row_count,
      data_schema: dataSchema,
      security_level: values.security_level,
      allowed_operations: values.allowed_operations,
      output_constraints: Object.keys(cleanOutputConstraints).length ? cleanOutputConstraints : undefined,
    });
  };

  return (
    <div>
      <Title level={4}>创建数据产品</Title>
      <Card>
        <Steps current={current} items={steps.map(s => ({ title: s.title }))} style={{ marginBottom: 24 }} />
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            product_type: 'structured',
            security_level: 'confidential',
            allowed_operations: ['read', 'query', 'aggregate'],
            max_output_rows: 100,
            require_dp: true,
            dp_epsilon: 1.0,
            max_runtime_seconds: 3600,
            allowed_output_formats: ['csv', 'json'],
          }}
        >
          <div style={{ minHeight: 200 }}>{steps[current].content}</div>
        </Form>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 24 }}>
          <Button onClick={() => current > 0 ? setCurrent(current - 1) : navigate('/data-products')}>上一步</Button>
          {current < steps.length - 1 && <Button type="primary" onClick={next}>下一步</Button>}
          {current === steps.length - 1 && <Button type="primary" loading={mutation.isPending} onClick={onFinish}>创建</Button>}
        </div>
      </Card>
    </div>
  );
}
