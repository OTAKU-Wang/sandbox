import { useEffect } from 'react';
import { Form, Input, Select, Button, Card, Typography, InputNumber, message } from 'antd';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { contractApi } from '../../services/contractApi';
import { dataProductApi } from '../../services/dataProductApi';
import { usersApi } from '../../services/usersApi';

const { Title } = Typography;
const { TextArea } = Input;

interface ContractFormValues {
  title: string;
  contract_type: string;
  product_ids: string[];
  buyer_id: string;
  allowed_sandbox_levels?: string[];
  allowed_sandbox_modes?: string[];
  allowed_operations?: string[];
  max_duration_hours?: number;
  dp_epsilon_budget?: number;
  max_output_rows?: number;
  allowed_output_formats?: string[];
  terms?: string;
  inspection_rule_set?: string;
}

export default function ContractCreate() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [form] = Form.useForm();
  const preselectedProductId = searchParams.get('product_id');

  const { data: products } = useQuery({
    queryKey: ['data-products-select'],
    queryFn: () => dataProductApi.list({ page: 1, page_size: 100 }),
  });

  const { data: buyers, isLoading: buyersLoading } = useQuery({
    queryKey: ['buyer-options'],
    queryFn: () => usersApi.options({ role: 'buyer', limit: 100 }),
  });

  const mutation = useMutation({
    mutationFn: contractApi.create,
    onSuccess: (contract) => {
      message.success('合约创建成功');
      navigate(`/contracts/${contract.id}`);
    },
    onError: () => message.error('创建失败'),
  });

  useEffect(() => {
    if (preselectedProductId) {
      form.setFieldsValue({ product_ids: [preselectedProductId] });
    }
  }, [form, preselectedProductId]);

  useEffect(() => {
    if (!preselectedProductId || !products?.items?.length || form.getFieldValue('title')) return;
    const product = products.items.find((item) => item.id === preselectedProductId);
    if (product) {
      form.setFieldsValue({ title: `${product.name} 数据使用合约` });
    }
  }, [form, preselectedProductId, products?.items]);

  const parseJsonField = (value: string | undefined, fieldName: string) => {
    if (!value?.trim()) return undefined;
    try {
      return JSON.parse(value);
    } catch {
      throw new Error(`${fieldName} 必须是合法 JSON`);
    }
  };

  const onFinish = (values: ContractFormValues) => {
    try {
      const joinOrUndefined = (items?: string[]) => items?.length ? items.join(',') : undefined;
      mutation.mutate({
        title: values.title,
        contract_type: values.contract_type,
        product_ids: values.product_ids,
        buyer_id: values.buyer_id,
        allowed_sandbox_levels: joinOrUndefined(values.allowed_sandbox_levels),
        allowed_sandbox_modes: values.allowed_sandbox_modes,
        allowed_operations: joinOrUndefined(values.allowed_operations),
        max_duration_hours: values.max_duration_hours,
        dp_epsilon_budget: values.dp_epsilon_budget,
        max_output_rows: values.max_output_rows,
        allowed_output_formats: joinOrUndefined(values.allowed_output_formats),
        terms: parseJsonField(values.terms, '合约条款'),
        inspection_rule_set: parseJsonField(values.inspection_rule_set, '审查规则'),
      });
    } catch (error) {
      message.error(error instanceof Error ? error.message : '表单格式错误');
    }
  };

  return (
    <div>
      <Title level={4}>创建合约</Title>
      <Card>
        <Form
          form={form}
          layout="vertical"
          onFinish={onFinish}
          initialValues={{
            contract_type: 'data_query',
            allowed_sandbox_levels: ['L3'],
            allowed_sandbox_modes: ['query'],
            allowed_operations: ['read', 'analyze'],
            max_duration_hours: 24,
            max_output_rows: 10000,
            allowed_output_formats: ['csv', 'json'],
          }}
        >
          <Form.Item name="title" label="合约标题" rules={[{ required: true, max: 200, message: '请输入标题' }]}>
            <Input placeholder="合约标题" />
          </Form.Item>
          <Form.Item name="contract_type" label="合约类型" rules={[{ required: true, message: '请选择类型' }]}>
            <Select options={[
              { label: '数据查询', value: 'data_query' },
              { label: '模型训练', value: 'model_training' },
              { label: '数据应用', value: 'data_application' },
              { label: 'API服务', value: 'api_service' },
              { label: '联合计算', value: 'joint_compute' },
              { label: '产品开发', value: 'product_dev' },
              { label: '数据建模', value: 'data_modeling' },
            ]} />
          </Form.Item>
          <Form.Item name="product_ids" label="数据产品" rules={[{ required: true, message: '请选择数据产品' }]}>
            <Select
              mode="multiple"
              showSearch
              placeholder="选择数据产品"
              optionFilterProp="label"
              options={(products?.items || []).map(p => ({
                label: `${p.name}${p.industry ? ` / ${p.industry}` : ''}`,
                value: p.id,
              }))}
            />
          </Form.Item>
          <Form.Item name="buyer_id" label="买方" rules={[{ required: true, message: '请选择买方' }]}>
            <Select
              showSearch
              loading={buyersLoading}
              placeholder="选择买方"
              optionFilterProp="label"
              options={(buyers || []).map((buyer) => ({
                label: `${buyer.username}${buyer.organization ? ` / ${buyer.organization}` : ''}`,
                value: buyer.id,
              }))}
            />
          </Form.Item>
          <Form.Item name="allowed_sandbox_levels" label="允许沙箱级别">
            <Select mode="multiple" allowClear options={[
              { label: 'L1 - TEE', value: 'L1' },
              { label: 'L2 - 轻量级虚拟化', value: 'L2' },
              { label: 'L3 - 进程隔离', value: 'L3' },
              { label: 'K8s - 分布式 Pod', value: 'k8s' },
            ]} />
          </Form.Item>
          <Form.Item name="allowed_sandbox_modes" label="允许沙箱模式">
            <Select mode="multiple" allowClear options={[
              { label: 'SQL 查询', value: 'query' },
              { label: '建模训练', value: 'train' },
              { label: '产品开发', value: 'develop' },
              { label: '应用调用', value: 'application' },
            ]} />
          </Form.Item>
          <Form.Item name="allowed_operations" label="允许操作">
            <Select mode="multiple" allowClear options={[
              { label: '读取', value: 'read' },
              { label: '分析', value: 'analyze' },
              { label: '训练', value: 'train' },
              { label: '推理', value: 'infer' },
            ]} />
          </Form.Item>
          <Form.Item name="max_duration_hours" label="最大运行时长（小时）">
            <InputNumber min={1} max={720} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="dp_epsilon_budget" label="DP隐私预算(ε)">
            <InputNumber min={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="max_output_rows" label="最大输出行数">
            <InputNumber min={1} max={1000000} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="allowed_output_formats" label="允许输出格式">
            <Select mode="multiple" allowClear options={[
              { label: 'CSV', value: 'csv' },
              { label: 'JSON', value: 'json' },
              { label: 'Parquet', value: 'parquet' },
              { label: 'XLSX', value: 'xlsx' },
              { label: 'Arrow', value: 'arrow' },
            ]} />
          </Form.Item>
          <Form.Item name="terms" label="合约条款">
            <TextArea rows={3} placeholder='{"purpose":"统计分析","retention_days":30}' />
          </Form.Item>
          <Form.Item name="inspection_rule_set" label="输出审查规则">
            <TextArea rows={3} placeholder='{"k_anonymity":true,"block_raw_rows":true}' />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={mutation.isPending} block>创建合约</Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
