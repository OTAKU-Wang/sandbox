import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Space,
  Tag,
  Typography,
  message,
} from 'antd';
import { SaveOutlined, SettingOutlined } from '@ant-design/icons';
import {
  DEFAULT_COMPUTE_TZ,
  ComputeFrequency,
  buildFlowConfigPayload,
  describeDeliveryMethod,
  describeFlowError,
  describeFrequency,
  flowConfigToFormValues,
  formatDateTime,
  useSaveFlowConfig,
  validateFlowConfig,
} from '../../services/flow';
import type { ContractWithFlow, DeliveryMethod, FlowConfigFormValues, FlowConfigIssue } from '../../services/flow';
import FlowConfigFields from './FlowConfigFields';
import { formatResourceSpec } from './flowResource';

const { Text } = Typography;

function issueAlert(issues: FlowConfigIssue[]) {
  if (!issues.length) return null;
  return (
    <Alert
      type="error"
      showIcon
      style={{ marginBottom: 16 }}
      message="配置未提交：请先修正以下问题"
      description={
        <Space direction="vertical" size={2}>
          {issues.map((issue) => (
            <Text key={`${issue.field}-${issue.message}`} style={{ fontSize: 12 }}>
              {issue.message}
            </Text>
          ))}
        </Space>
      }
    />
  );
}

function FlowConfigDrawer({
  contract,
  onClose,
}: {
  contract: ContractWithFlow;
  onClose: (saved: boolean) => void;
}) {
  const [form] = Form.useForm<FlowConfigFormValues>();
  const [issues, setIssues] = useState<FlowConfigIssue[]>([]);
  const [savedNext, setSavedNext] = useState<string | null | undefined>(undefined);
  const saveMutation = useSaveFlowConfig();

  const initial = flowConfigToFormValues(contract);
  const currentSpec = contract.sandbox_resource_spec;

  const submit = () => {
    void form
      .validateFields()
      .then((values: FlowConfigFormValues) => {
        const merged = { ...initial, ...values };
        const nextIssues = validateFlowConfig(merged);
        setIssues(nextIssues);
        if (nextIssues.length) return;
        saveMutation.mutate(
          { contractId: contract.id, payload: buildFlowConfigPayload(merged) },
          {
            onSuccess: (updated) => {
              setSavedNext(updated.next_compute_at ?? null);
              message.success('交付与计算配置已保存');
              onClose(true);
            },
            onError: (err) => message.error(describeFlowError(err, '配置保存失败')),
          },
        );
      })
      .catch(() => undefined);
  };

  return (
    <Drawer
      title={`交付与计算配置 · ${contract.contract_no}`}
      width={720}
      open
      onClose={() => onClose(false)}
      extra={
        <Space>
          <Button onClick={() => onClose(false)}>取消</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saveMutation.isPending} onClick={submit}>
            保存配置
          </Button>
        </Space>
      }
    >
      {issueAlert(issues)}

      {savedNext !== undefined && (
        <Alert
          type="success"
          showIcon
          style={{ marginBottom: 16 }}
          message={savedNext ? `配置已生效，下次计算时间：${formatDateTime(savedNext)}` : '配置已生效：自动计算已关闭，仅支持手动触发'}
        />
      )}

      <Form
        form={form}
        layout="vertical"
        initialValues={initial}
        disabled={saveMutation.isPending}
      >
        <FlowConfigFields currentSpec={currentSpec} />
      </Form>
    </Drawer>
  );
}

export default function FlowConfigCard({ contract }: { contract: ContractWithFlow }) {
  const [open, setOpen] = useState(false);
  const methods = contract.delivery_methods ?? [];
  const configured = methods.length > 0 || typeof contract.auto_compute === 'boolean';
  const spec = contract.sandbox_resource_spec;

  return (
    <Card
      size="small"
      style={{ marginTop: 16 }}
      title="交付与计算配置"
      extra={
        <Button size="small" type={configured ? 'default' : 'primary'} icon={<SettingOutlined />} onClick={() => setOpen(true)}>
          {configured ? '修改配置' : '配置交付方式'}
        </Button>
      }
    >
      {!configured && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="该合约尚未配置交付方式与自动计算：成果交付、计算调度页面将没有可调度的目标。"
        />
      )}
      <Descriptions size="small" column={3}>
        <Descriptions.Item label="交付方式" span={2}>
          {methods.length
            ? methods.map((method: DeliveryMethod) => (
                <Tag key={method} color="blue">
                  {describeDeliveryMethod(method)}
                </Tag>
              ))
            : <Text type="secondary">未配置</Text>}
        </Descriptions.Item>
        <Descriptions.Item label="自动计算">
          {contract.auto_compute === true ? <Tag color="green">已开启</Tag> : <Tag>未开启</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="频率">
          {describeFrequency(contract.compute_frequency)}
          {contract.auto_compute === true && contract.compute_frequency === ComputeFrequency.Hourly
            ? ` · 第 ${contract.compute_minute ?? '-'} 分钟`
            : ''}
          {contract.auto_compute === true && contract.compute_frequency === ComputeFrequency.Daily
            ? ` · ${contract.compute_hour ?? '-'}:${String(contract.compute_minute ?? '-').padStart(2, '0')}`
            : ''}
        </Descriptions.Item>
        <Descriptions.Item label="时区">{contract.compute_tz || DEFAULT_COMPUTE_TZ}</Descriptions.Item>
        <Descriptions.Item label="沙箱资源">
          {spec ? formatResourceSpec(spec, true) || '沿用合约默认' : '沿用合约默认'}
        </Descriptions.Item>
        <Descriptions.Item label="下次计算时间">
          {contract.next_compute_at ? (
            <Tag color="processing">{formatDateTime(contract.next_compute_at)}</Tag>
          ) : (
            <Text type="secondary">{contract.auto_compute ? '后端未下发 next_compute_at' : '自动计算关闭，无排程'}</Text>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="上次计算完成">{formatDateTime(contract.last_computed_at)}</Descriptions.Item>
      </Descriptions>

      {open && <FlowConfigDrawer contract={contract} onClose={() => setOpen(false)} />}
    </Card>
  );
}
