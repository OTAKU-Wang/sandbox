import { useEffect, useRef } from 'react';
import { Collapse, Form, Input, Select, Button, Card, Typography, InputNumber, message } from 'antd';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { contractApi } from '../../services/contractApi';
import { dataProductApi } from '../../services/dataProductApi';
import { usersApi } from '../../services/usersApi';
import { contractLevelFootnote, contractLevelOptions, metaApi, SANDBOX_LEVELS_UNDISCLOSED } from '../../services/metaApi';
// 档位标签的唯一来源是部署能力披露端点（metaApi.getSandboxLevels）。
// 这里过去从 types/enums 里取 SandboxLevel.L2C 当固定默认值——那正是 dev compose
// 把 DEV_SANDBOX_LEVEL 设成 L3 之后本机必 400 的根因（拍板②，2026-10-30）。
import FlowConfigFields from '../Flow/FlowConfigFields';
import {
  DEFAULT_COMPUTE_TZ,
  ComputeFrequency,
  buildFlowConfigPayload,
  describeFlowError,
  flowApi,
  validateFlowConfig,
} from '../../services/flow';
import type { FlowConfigFormValues, FlowConfigIssue, FlowConfigPayload } from '../../services/flow';

const { Title } = Typography;
const { TextArea } = Input;

interface ContractFormValues extends FlowConfigFormValues {
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
  purpose?: string;
  purpose_scope?: string[];
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

  // 拍板②（2026-10-30）：档位选项与披露文案来自「当前部署真实能力」只读披露端点，
  // 不再在表单里硬编码一个 disabled 的 l2c —— 那是 dev compose（DEV_SANDBOX_LEVEL=L3）
  // 本机必 400 的直接原因（specs/ss-19-full-platform-docker-e2e.md §10.1-4）。
  const { data: levelDisclosure } = useQuery({
    queryKey: ['sandbox-levels'],
    queryFn: () => metaApi.getSandboxLevels(),
    staleTime: 60_000,
  });
  const levelOptions = contractLevelOptions(levelDisclosure);
  const watchedLevels = Form.useWatch('allowed_sandbox_levels', form);
  const levelFootnote = contractLevelFootnote(levelDisclosure, watchedLevels);

  // 披露到齐后把默认值对齐到「这台部署真能跑」的档位；用户动过就绝不覆盖。
  // 读不到披露时一次都不猜：字段保持不上行，由后端按其声明的默认档落库。
  const autoAppliedLevel = useRef<string | null>(null);
  useEffect(() => {
    const recommended = levelDisclosure?.recommended_level;
    if (!recommended || autoAppliedLevel.current === recommended) return;
    autoAppliedLevel.current = recommended;
    if (!form.isFieldTouched('allowed_sandbox_levels')) {
      form.setFieldsValue({ allowed_sandbox_levels: [recommended] });
    }
  }, [form, levelDisclosure?.recommended_level]);

  // 记录已建成的合约 id：流程配置是第二次请求，失败时不能让用户以为整单未创建
  const createdContractRef = useRef<string | null>(null);

  const mutation = useMutation({
    mutationFn: async (input: { contract: Parameters<typeof contractApi.create>[0]; flow: FlowConfigPayload | null }) => {
      const created = await contractApi.create(input.contract);
      createdContractRef.current = created.id;
      if (input.flow) await flowApi.saveFlowConfig(created.id, input.flow);
      return created;
    },
    onSuccess: (contract) => {
      message.success('合约创建成功');
      navigate(`/contracts/${contract.id}`);
    },
    onError: (error) => {
      const createdId = createdContractRef.current;
      if (createdId) {
        message.warning('合约已创建，但交付与计算配置未保存：请到合约详情页重新配置');
        navigate(`/contracts/${createdId}`);
        return;
      }
      message.error(describeFlowError(error, '创建失败'));
    },
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
      // A314：目的限定字段——空串与空数组一律**不交这个键**（而不是交 '' / []）：
      // 交空串会被后端当成“目的为空”，不交才是“没做目的限定”的原进口径。
      const purpose = values.purpose?.trim() ?? '';
      const purposeScope = (values.purpose_scope ?? []).map((tag) => tag.trim()).filter(Boolean);
      const methods = values.delivery_methods ?? [];
      const flowValues: FlowConfigFormValues = {
        delivery_methods: methods,
        auto_compute: values.auto_compute === true,
        compute_frequency: values.compute_frequency,
        compute_minute: values.compute_minute,
        compute_hour: values.compute_hour,
        compute_tz: values.compute_tz,
        cpu_cores: values.cpu_cores,
        memory_mb: values.memory_mb,
        disk_gb: values.disk_gb,
        gpu_count: values.gpu_count,
        timeout_seconds: values.timeout_seconds,
        image_template: values.image_template,
      };
      // 未展开该面板即不提交流程配置，后端按默认（仅数据集交付 + 手动计算）处理
      if (methods.length) {
        const issues: FlowConfigIssue[] = validateFlowConfig(flowValues);
        if (issues.length) {
          message.error(issues[0].message);
          return;
        }
      }
      mutation.mutate({
        flow: methods.length ? buildFlowConfigPayload(flowValues) : null,
        contract: {
          title: values.title,
          contract_type: values.contract_type,
          product_ids: values.product_ids,
          buyer_id: values.buyer_id,
          ...(purpose ? { purpose } : {}),
          ...(purposeScope.length ? { purpose_scope: purposeScope } : {}),
          allowed_sandbox_levels: joinOrUndefined(values.allowed_sandbox_levels),
          allowed_sandbox_modes: values.allowed_sandbox_modes,
          allowed_operations: joinOrUndefined(values.allowed_operations),
          max_duration_hours: values.max_duration_hours,
          dp_epsilon_budget: values.dp_epsilon_budget,
          max_output_rows: values.max_output_rows,
          allowed_output_formats: joinOrUndefined(values.allowed_output_formats),
            terms: parseJsonField(values.terms, '合约条款'),
            inspection_rule_set: parseJsonField(values.inspection_rule_set, '审查规则'),
        },
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
            // 档位不预填：默认值来自部署能力披露的 recommended_level（见上面的 effect），
            // 读不到披露时保持空 = 该字段不上行，由后端按它声明的默认档落库。
            allowed_sandbox_modes: ['query'],
            allowed_operations: ['read', 'analyze'],
            max_duration_hours: 24,
            max_output_rows: 10000,
            allowed_output_formats: ['csv', 'json'],
            auto_compute: false,
            compute_frequency: ComputeFrequency.Daily,
            compute_hour: 2,
            compute_minute: 30,
            compute_tz: DEFAULT_COMPUTE_TZ,
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
          <Form.Item name="purpose" label="数据用途"
            tooltip="Gap A4 目的限定：填了之后，后端才会对本合约下的会话启用目的门（代码/查询与约定用途不符时可拒执行）；目的与白名单一同进 SM2 签署原文。留空 = 不做目的限定。"
            rules={[{ max: 500, message: '数据用途不超过 500 字' }]}>
            <Input placeholder="如：零售客单价建模" />
          </Form.Item>
          <Form.Item name="purpose_scope" label="用途白名单"
            tooltip="可选：限定只允许这些用途词（回车或逗号成词）。后端把它写进目的门的白名单，不在表内的用途不得取数。">
            <Select
              mode="tags"
              allowClear
              tokenSeparators={[',', '，']}
              placeholder="输入标签后回车或逗号分隔"
            />
          </Form.Item>
          <Form.Item
            name="allowed_sandbox_levels"
            label="允许沙箱级别"
            tooltip="选项与披露都取自本部署的能力披露（只读端点 /meta/sandbox-levels）。合约只是授权：执行时后端逐项硬校验，当前跑不起来的档位照旧按 SS-13 返回 400，披露能力不等于放宽校验。"
            extra={
              <div style={{ marginTop: 6 }}>
                {levelFootnote.map((line) => (
                  <div key={line} style={{ fontSize: 12, lineHeight: '18px', color: 'rgba(0,0,0,0.45)' }}>
                    {line}
                  </div>
                ))}
              </div>
            }
          >
            <Select
              mode="multiple"
              // 读不到披露就如实显示「未记录」并保持不可选：宁可让用户等一下重试，
              // 也不替用户编一个档位（旧实现硬编码 l2c 就是本机 400 的根因）。
              disabled={!levelOptions.length}
              placeholder={levelOptions.length ? '选择本部署可授权的沙箱档位' : SANDBOX_LEVELS_UNDISCLOSED}
              optionFilterProp="label"
              options={levelOptions.map((option) => ({
                value: option.value,
                label: `${option.label}${option.runtime_available ? '' : '（当前部署不可执行）'}`,
                disclosure: option.disclosure,
              }))}
              // 选项里直接把后端的披露文案写在第二行：勾之前就得看得见「为什么跑不起来」，
              // 而不是等人去悬停原生 title。合约只是授权，勾选不代表执行会放行。
              optionRender={(option) => (
                <div style={{ padding: '2px 0' }}>
                  <div className="cds-level-option-label">{option.data.label}</div>
                  <div
                    className="cds-level-option-note"
                    style={{ fontSize: 12, lineHeight: '18px', color: 'rgba(0,0,0,0.45)', whiteSpace: 'normal' }}
                  >
                    {option.data.disclosure}
                  </div>
                </div>
              )}
            />
          </Form.Item>
          <Form.Item name="allowed_sandbox_modes" label="允许沙箱模式">
            <Select mode="multiple" allowClear options={[
              { label: 'SQL 查询', value: 'query' },
              { label: '建模训练', value: 'train' },
              { label: '产品开发', value: 'develop' },
              { label: '应用调用', value: 'application' },
            ]} />
          </Form.Item>
          <Form.Item
            name="allowed_operations"
            label="允许操作"
            tooltip="这里是正向授权：没选的操作就是不允许。不让清空：后端把空 allowed_operations 当「不限制」（sandbox_sessions.py _operation_allowed_by_contract），清空等于把 read/analyze/train/infer 全放开了。"
            rules={[{ required: true, type: 'array', message: '请至少选择一项允许操作：留空会被后端视为不限制，等于扩大授权' }]}
          >
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
          <Collapse
            defaultActiveKey={['flow']}
            style={{ marginBottom: 24 }}
            items={[
              {
                key: 'flow',
                label: '交付与计算配置（不展开则默认仅数据集交付、手动计算，可稍后在合约详情页配置）',
                children: <FlowConfigFields />,
              },
            ]}
          />
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={mutation.isPending} block>创建合约</Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
