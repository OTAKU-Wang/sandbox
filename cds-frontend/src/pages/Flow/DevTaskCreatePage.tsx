// 图 2-2「我的开发-新增开发任务」整页形态：基本信息 / 我的合约 / 资源配置 / 关联数据 四卡
// + 底部 取消·完成 条。取代原弹窗（勾选要真的进载荷，弹窗放不下已选面板）。
//
// 图上「配额使用建议」写的是「数据量超过 10 万行建议 16G」这类无后端依据的经验值，
// 这里换成合约真正下发的已批准内存上限（禁伪造铁律：不替平台编造建议阈值）。
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Tooltip, Typography } from 'antd';
import type { InputNumberProps } from 'antd';
import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { EnvironmentFilled, InfoCircleOutlined } from '@ant-design/icons';
import {
  TASK_DEBUG_METHOD_OPTIONS,
  TASK_EXPORT_MODE_OPTIONS,
  TASK_MEMORY_LIMITS,
  UNKNOWN_TEXT,
  buildDevTaskPayload,
  describeNotebookCreateIssues,
  formatMemoryGib,
  useCreateNotebook,
  validateDevTaskForm,
} from '../../services/flow';
import type { DevTaskFieldIssue, DevTaskFormValues } from '../../services/flow';
import DevTaskDataPicker from './DevTaskDataPicker';
import { useContractOptions } from './useContractOptions';
import './devTaskCreate.css';

const { Text, Title } = Typography;

/** 图 2-2 步进器盒（实测 y941 簇）：值居左、－ ＋ 在右端、G 块最右，整盒一圈描边 h40。
 *  Form.Item 注入的 value/onChange/id 直接透传：id 落在真 input 上，测试与 a11y 都认它。 */
type MemStepperProps = Omit<InputNumberProps, 'value' | 'onChange' | 'min' | 'max' | 'step'> & {
  value?: number | null;
  onChange?: (value: number | null) => void;
  min?: number;
  max: number;
  step?: number;
};

function MemStepper({ min = 0, max, step = 1, value, onChange, disabled, ...rest }: MemStepperProps) {
  const bump = (delta: number) => {
    if (typeof value !== 'number') return;
    onChange?.(Math.min(max, Math.max(min, value + delta)));
  };
  const stepValue = (next: string | number | null) => {
    if (next === null || next === '') return onChange?.(null);
    const num = typeof next === 'number' ? next : Number(next);
    if (Number.isFinite(num)) onChange?.(num);
  };
  const stepperDisabled = disabled || typeof value !== 'number';
  return (
    <div className="cds-mem-stepper">
      <InputNumber
        className="cds-mem-input"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={stepValue}
        disabled={disabled}
        controls={false}
        variant="borderless"
        style={{ width: '100%' }}
        {...rest}
      />
      <button type="button" className="cds-mem-step" aria-label="减少" disabled={stepperDisabled} onClick={() => bump(-step)}>－</button>
      <button type="button" className="cds-mem-step" aria-label="增加" disabled={stepperDisabled} onClick={() => bump(step)}>＋</button>
      <span className="cds-mem-g">G</span>
    </div>
  );
}

/** 任务编号只读位的口径文案（对外口径唯一源：flow.ts 任务编号注释）。 */
const TASK_NO_READ_ONLY_TEXT = '系统自动生成（TASK-YYYYMMDD-NNN）';

const ISSUE_FIELD_TO_FORM: Record<string, string | null> = {
  title: 'title',
  path: 'path',
  contract_id: 'contract_id',
  debug_method: 'debug_method',
  export_mode: 'export_mode',
  debug_memory_mb: 'debug_memory_gib',
  exec_memory_mb: 'exec_memory_gib',
  data_resource_ids: null,
  task_no: null,
};

export default function DevTaskCreatePage() {
  const [form] = Form.useForm<DevTaskFormValues>();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [issues, setIssues] = useState<DevTaskFieldIssue[]>([]);
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[] | null>(null);
  const { data: contractChoices, isPending: contractsPending } = useContractOptions();
  const createTask = useCreateNotebook();

  const debugMemory = Form.useWatch('debug_memory_gib', form);
  const contractId = Form.useWatch('contract_id', form);

  const usableContracts = useMemo(
    () => (contractChoices ?? []).filter((choice) => choice.status === 'active'),
    [contractChoices],
  );
  const contractOptions = useMemo(
    () => usableContracts.map((choice) => ({ label: `${choice.title}（${choice.contract_no}）`, value: choice.id })),
    [usableContracts],
  );
  const selectedContract = useMemo(
    () => usableContracts.find((choice) => choice.id === contractId) ?? null,
    [contractId, usableContracts],
  );
  const approvedMemoryText = useMemo(() => {
    if (!contractId) return '尚未选择合约';
    const spec = selectedContract?.sandbox_resource_spec;
    const approved = spec && typeof spec.memory_mb === 'number' ? spec.memory_mb : null;
    return approved === null
      ? `合约未下发已批准内存规格，上限按产品档位 ${TASK_MEMORY_LIMITS.debug.max_gib} GiB`
      : `合约已批准内存 ${formatMemoryGib(approved)}，任务内存不得超过它`;
  }, [contractId, selectedContract]);

  const debugLimit = TASK_MEMORY_LIMITS.debug;
  const execLimit = TASK_MEMORY_LIMITS.exec;
  const debugKnown = typeof debugMemory === 'number';

  const showIssues = (next: DevTaskFieldIssue[]) => {
    const fieldNames = [...new Set(Object.values(ISSUE_FIELD_TO_FORM).filter((name): name is string => !!name))];
    const cleared = fieldNames.map((name) => ({ name: name as keyof DevTaskFormValues, errors: [] as string[] }));
    const byField = new Map<keyof DevTaskFormValues, string[]>();
    for (const issue of next) {
      const name = issue.field ? ISSUE_FIELD_TO_FORM[issue.field] ?? null : null;
      if (!name) continue;
      const key = name as keyof DevTaskFormValues;
      byField.set(key, [...(byField.get(key) ?? []), issue.text]);
    }
    form.setFields([...cleared, ...[...byField.entries()].map(([name, errors]) => ({ name, errors }))]);
    setIssues(next);
  };

  const submit = async () => {
    let values: DevTaskFormValues;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const withSelection: DevTaskFormValues = { ...values, data_resource_ids: selectedResourceIds };
    const local = validateDevTaskForm(withSelection);
    if (local.length) {
      showIssues(local);
      return;
    }
    try {
      const notebook = await createTask.mutateAsync(buildDevTaskPayload(withSelection));
      navigate(`/flow/tasks/${notebook.id}`);
    } catch (error) {
      showIssues(describeNotebookCreateIssues(error));
    }
  };

  return (
    <div className="cds-dev-task-create">
      {/* 标题带与 2-1 同口径：共用 .cds-page-toolbar（零新 CSS），Title 同取 level={3}。 */}
      <div className="cds-page-toolbar">
        <div>
          <Title level={3} style={{ margin: 0 }}>新增开发任务</Title>
          <Text type="secondary">交付场景 / 数据沙箱开发 · 配置合约、资源与关联数据后即可启动调试</Text>
        </div>
        <Button onClick={() => navigate('/flow/tasks')}>← 返回列表</Button>
      </div>

      {issues.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="创建未成功"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {issues.map((issue, index) => <li key={`${issue.field ?? 'global'}-${index}`}>{issue.text}</li>)}
            </ul>
          }
        />
      )}

      <Form<DevTaskFormValues>
        form={form}
        layout="vertical"
        initialValues={{
          separate_exec_memory: true,
          debug_memory_gib: null,
          exec_memory_gib: null,
          contract_id: params.get('contract_id') ?? null,
        }}
      >
        <Card
          size="small"
          title="基本信息"
          style={{ marginBottom: 16 }}
        >
          <div className="cds-trio">
            <div>
              <Form.Item name="title" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}>
                <Input placeholder="例如：用户行为分析模型" maxLength={255} />
              </Form.Item>
            </div>
            <div>
              <Form.Item label="任务编号">
                {/* 口径（服务端按当日序号生成、客户端不提交）写在 placeholder 与本注释；图上标签无 ? 图标，不另加。 */}
                <Input readOnly disabled defaultValue={TASK_NO_READ_ONLY_TEXT} aria-label="任务编号" />
              </Form.Item>
            </div>
            <div>
              <Form.Item label="所属节点">
                {/* 后端未按任务记录所属节点，也不下发节点名；如实显示未记录，不拿部署名冒充（图上这里是节点名示例值）。 */}
                <Input readOnly disabled value={UNKNOWN_TEXT} aria-label="所属节点" />
              </Form.Item>
            </div>
          </div>
          <Form.Item name="description" label="任务描述">
            <Input.TextArea rows={3} maxLength={4000} placeholder="说明本任务要解决的问题、输入口径与期望产出。" />
          </Form.Item>
        </Card>

        <Card
          size="small"
          title={
            <span className="cds-card-head-inner">
              我的合约<InfoCircleOutlined className="cds-card-head-info" />
              <Text type="secondary" style={{ fontWeight: 400 }}>合约用于约束模型输出格式与导出通道</Text>
            </span>
          }
          style={{ marginBottom: 16 }}
        >
          <div className="cds-trio">
            <div>
              <Form.Item name="contract_id" label="合约名称" rules={[{ required: true, message: '请选择合约名称' }]}>
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择我可使用的合约"
                  loading={contractsPending}
                  options={contractOptions}
                />
              </Form.Item>
            </div>
            <div>
              <Form.Item name="debug_method" label="模型调试方法" rules={[{ required: true, message: '请选择模型调试方法' }]}>
                <Select allowClear placeholder="选择调试方法" options={TASK_DEBUG_METHOD_OPTIONS.map(({ label, value, hint }) => ({ label, value, title: hint }))} />
              </Form.Item>
            </div>
            <div>
              <Form.Item name="export_mode" label="结果导出方式" rules={[{ required: true, message: '请选择结果导出方式' }]}>
                <Select allowClear placeholder="选择导出方式" options={TASK_EXPORT_MODE_OPTIONS.map(({ label, value, hint }) => ({ label, value, title: hint }))} />
              </Form.Item>
            </div>
          </div>
        </Card>

        <Card
          size="small"
          title={
            <span className="cds-card-head-inner">
              资源配置<InfoCircleOutlined className="cds-card-head-info" />
              <Text type="secondary" style={{ fontWeight: 400 }}>内存配额将应用于调试与执行阶段</Text>
            </span>
          }
          style={{ marginBottom: 16 }}
        >
          <div className="cds-trio">
            <div>
              <Form.Item
                name="debug_memory_gib"
                className="cds-unit-item"
                label="调试环境内存(G)"
                rules={[{ required: true, message: '请输入调试环境内存' }]}
                extra={`范围 ${debugLimit.min_gib}G ~ ${debugLimit.max_gib}G，用于在线调试环境`}
              >
                {/* 单位 `G` 由 .cds-unit-item 的 CSS 画在框内右侧（图 A-3：「后缀 G」）；
                    不写 InputNumber 的 suffix：antd 5.29 只有类型、运行时不渲染；也不能包一层 div：会打断 Form 绑定。 */}
                <MemStepper min={debugLimit.min_gib} max={debugLimit.max_gib} step={debugLimit.step} precision={debugLimit.precision} placeholder="如 8" />
              </Form.Item>
            </div>
            <div>
              <Form.Item
                name="exec_memory_gib"
                className="cds-unit-item"
                label="执行环境内存(G)"
                dependencies={['debug_memory_gib']}
                rules={[{ required: true, message: '请输入执行环境内存' }]}
                extra={`范围 ${execLimit.min_gib}G ~ ${execLimit.max_gib}G，用于任务正式执行环境`}
              >
                <MemStepper
                  min={Math.max(execLimit.min_gib, debugKnown ? debugMemory : 0)}
                  max={execLimit.max_gib}
                  step={execLimit.step}
                  precision={execLimit.precision}
                  placeholder={debugKnown ? `不低于 ${debugMemory}` : '如 16'}
                />
              </Form.Item>
            </div>
            <div>
              {/* 图上是浅蓝 alert 卡（💡 标题蓝）；内容不照抄图里无依据的经验值，写合约真实下发的口径。 */}
              <div className="cds-quota-advice">
                <div className="cds-quota-advice-title"><EnvironmentFilled /> 配额使用建议</div>
                <div className="cds-quota-advice-body">{approvedMemoryText}。沙箱按任务内存硬上限拒绝超限请求，不会静默降档。</div>
              </div>
            </div>
          </div>
        </Card>

        <Card
          size="small"
          title={
            <span className="cds-card-head-inner">
              关联数据<InfoCircleOutlined className="cds-card-head-info" />
              <Text type="secondary" style={{ fontWeight: 400 }}>选择需要授权到沙箱的数据产品</Text>
            </span>
          }
          style={{ marginBottom: 16 }}
        >
          <DevTaskDataPicker contract={selectedContract} value={selectedResourceIds} onChange={setSelectedResourceIds} />
        </Card>

        <Card size="small" className="cds-dev-task-foot">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
            <Text type="secondary"><InfoCircleOutlined style={{ marginRight: 6 }} />提交后可在「我的开发」列表中进行调试与执行</Text>
            <Space>
              <Button onClick={() => navigate('/flow/tasks')}>取消</Button>
              <Tooltip title={selectedContract ? null : '先选择合约'}>
                <Button type="primary" loading={createTask.isPending} onClick={() => { void submit(); }}>完成</Button>
              </Tooltip>
            </Space>
          </div>
        </Card>
      </Form>
    </div>
  );
}
