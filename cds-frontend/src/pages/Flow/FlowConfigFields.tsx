import {
  Alert,
  Checkbox,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd';
import {
  COMPUTE_TZ_OPTIONS,
  DELIVERY_METHOD_OPTIONS,
  FLOW_RESOURCE_LIMITS,
  ComputeFrequency,
} from '../../services/flow';
import type { SandboxResourceSpec } from '../../services/flow';
import { RESOURCE_FIELDS, formatResourceSpec, numberRangeRule } from './flowResource';

const { Text } = Typography;

export default function FlowConfigFields({ currentSpec }: { currentSpec?: SandboxResourceSpec | null }) {
  const form = Form.useFormInstance();
  const autoCompute = Form.useWatch('auto_compute', form) ?? false;
  const frequency = Form.useWatch('compute_frequency', form) ?? ComputeFrequency.Manual;
  const currentSpecText = formatResourceSpec(currentSpec);

  return (
    <>
      <Form.Item
        name="delivery_methods"
        label="交付方式（可多选）"
        rules={[{ required: true, message: '至少选择一种交付方式' }]}
      >
        <Checkbox.Group>
          <Space direction="vertical" size={6}>
            {DELIVERY_METHOD_OPTIONS.map((option) => (
              <Checkbox key={option.value} value={option.value}>
                <Space direction="vertical" size={0}>
                  <Text>{option.label}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {option.hint}
                  </Text>
                </Space>
              </Checkbox>
            ))}
          </Space>
        </Checkbox.Group>
      </Form.Item>

      <Divider style={{ margin: '8px 0 16px' }} />

      <Row gutter={16}>
        <Col span={8}>
          <Form.Item name="auto_compute" label="自动计算" valuePropName="checked" tooltip="关闭后仅可手动触发计算">
            <Switch checkedChildren="开" unCheckedChildren="关" />
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item name="compute_frequency" label="执行频率">
            <Radio.Group disabled={!autoCompute}>
              <Radio value={ComputeFrequency.Hourly}>每小时</Radio>
              <Radio value={ComputeFrequency.Daily}>每日</Radio>
            </Radio.Group>
          </Form.Item>
        </Col>
        <Col span={8}>
          <Form.Item name="compute_tz" label="计算时区" rules={[{ required: true, message: '请选择计算时区' }]}>
            <Select options={COMPUTE_TZ_OPTIONS} showSearch optionFilterProp="label" />
          </Form.Item>
        </Col>
      </Row>

      {!autoCompute && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="自动计算已关闭：频率按 manual 提交，需要时在计算调度页手动触发。"
        />
      )}

      <Row gutter={16}>
        <Col span={12}>
          <Form.Item
            name="compute_minute"
            label="执行分钟（0~59）"
            rules={[
              numberRangeRule('执行分钟', 0, 59),
              {
                validator: (_rule, value: number | null | undefined) => {
                  if (value == null) return Promise.resolve();
                  return Number.isInteger(value) ? Promise.resolve() : Promise.reject(new Error('执行分钟需为整数'));
                },
              },
            ]}
          >
            <InputNumber style={{ width: '100%' }} min={0} max={59} precision={0} disabled={!autoCompute} />
          </Form.Item>
        </Col>
        <Col span={12}>
          <Form.Item
            name="compute_hour"
            label={frequency === ComputeFrequency.Daily ? '执行小时（0~23，每日）' : '执行小时（0~23，每日时使用）'}
            rules={[
              numberRangeRule('执行小时', 0, 23),
              {
                validator: (_rule, value: number | null | undefined) => {
                  if (value == null) return Promise.resolve();
                  return Number.isInteger(value) ? Promise.resolve() : Promise.reject(new Error('执行小时需为整数'));
                },
              },
            ]}
          >
            <InputNumber style={{ width: '100%' }} min={0} max={23} precision={0} disabled={!autoCompute} />
          </Form.Item>
        </Col>
      </Row>

      <Divider orientation="left" plain style={{ margin: '8px 0 16px' }}>
        沙箱资源
      </Divider>

      {currentSpec && (
        <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
          合约当前生效资源：{currentSpecText || '未设置'}；留空表示沿用合约执行策略派生值。
        </Text>
      )}

      <Row gutter={16}>
        {RESOURCE_FIELDS.map((field) => {
          const limit = FLOW_RESOURCE_LIMITS[field];
          return (
            <Col span={8} key={field}>
              <Form.Item name={field} label={limit.label} rules={[numberRangeRule(limit.label, limit.min, limit.max)]}>
                <InputNumber
                  style={{ width: '100%' }}
                  min={limit.min}
                  max={limit.max}
                  step={limit.step}
                  precision={limit.precision}
                  placeholder={`默认 ${limit.min} ~ ${limit.max}`}
                />
              </Form.Item>
            </Col>
          );
        })}
        <Col span={8}>
          <Form.Item name="image_template" label="执行镜像模板">
            <Input placeholder="留空使用默认模板" maxLength={120} />
          </Form.Item>
        </Col>
      </Row>
    </>
  );
}
