import { useState } from 'react';
import { Typography, Card, Steps, Table, Tag, Descriptions, Space, Button, Input, InputNumber, Select, message, Row, Col, Statistic, Divider } from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import { outputControlApi, type InspectionResult, type DPNoiseResult } from '../../services/outputControlApi';
import { sandboxApi } from '../../services/sandboxApi';
import { EmptyState, QueryErrorAlert } from '../../components/Feedback/QueryFeedback';

const { Title, Text } = Typography;
const { TextArea } = Input;

const stages = [
  { key: 'scene_routing', title: '场景路由' },
  { key: 'format_validation', title: '格式校验' },
  { key: 'dlp_scan', title: 'DLP扫描' },
  { key: 'data_reconstruction', title: '重构风险' },
  { key: 'k_anonymity', title: 'K匿名' },
  { key: 'differential_privacy', title: '差分隐私' },
  { key: 'model_safety', title: '模型安全' },
  { key: 'watermark', title: '水印' },
  { key: 'signature', title: '签名' },
  { key: 'final_approval', title: '最终审批' },
];

const stageLabels = Object.fromEntries(stages.map((stage) => [stage.key, stage.title]));

const severityColors: Record<string, string> = {
  critical: 'red',
  high: 'volcano',
  medium: 'orange',
  low: 'blue',
};

export default function InspectionPipeline() {
  // Inspection state
  const [inspectOutput, setInspectOutput] = useState('');
  const [inspectSessionId, setInspectSessionId] = useState('');
  const [inspectEpsilon, setInspectEpsilon] = useState<number | null>(null);
  const [inspectResult, setInspectResult] = useState<InspectionResult | null>(null);

  // DP Noise state
  const [noiseValue, setNoiseValue] = useState(100);
  const [noiseSensitivity, setNoiseSensitivity] = useState(1.0);
  const [noiseEpsilon, setNoiseEpsilon] = useState(1.0);
  const [noiseMechanism, setNoiseMechanism] = useState<'laplace' | 'gaussian'>('laplace');
  const [noiseResult, setNoiseResult] = useState<DPNoiseResult | null>(null);

  // DP Budget state
  const [budgetSessionId, setBudgetSessionId] = useState('');
  const [budgetEpsilon, setBudgetEpsilon] = useState(10.0);

  const {
    data: sessions,
    isLoading: sessionsLoading,
    isError: sessionsIsError,
    error: sessionsError,
    refetch: refetchSessions,
  } = useQuery({
    queryKey: ['output-control-sessions'],
    queryFn: () => sandboxApi.list({ page: 1, page_size: 100 }),
  });

  // Inspection mutation
  const inspectMutation = useMutation({
    mutationFn: outputControlApi.inspect,
    onSuccess: (data) => {
      setInspectResult(data);
      message.success(data.passed ? '审查通过' : '审查未通过');
    },
    onError: () => message.error('审查请求失败'),
  });

  // DP Noise mutation
  const noiseMutation = useMutation({
    mutationFn: outputControlApi.applyDPNoise,
    onSuccess: (data) => {
      setNoiseResult(data);
      message.success('DP噪声已生成');
    },
    onError: () => message.error('DP噪声请求失败'),
  });

  // DP Budget init mutation
  const budgetInitMutation = useMutation({
    mutationFn: () => outputControlApi.initDPBudget(budgetSessionId, budgetEpsilon),
    onSuccess: (data) => {
      message.success(`DP预算已初始化: ε=${data.epsilon_allocated}`);
    },
    onError: () => message.error('DP预算初始化失败'),
  });

  // DP Budget query
  const {
    data: budgetData,
    isFetching: budgetLoading,
    isError: budgetIsError,
    error: budgetError,
    refetch: refetchBudget,
  } = useQuery({
    queryKey: ['dp-budget', budgetSessionId],
    queryFn: () => outputControlApi.getDPBudget(budgetSessionId),
    enabled: false,
  });

  // Build steps from inspection result
  const getSteps = () => {
    if (!inspectResult) return stages.map((s) => ({ title: s.title, status: 'wait' as const }));
    return stages.map((s) => {
      const passed = inspectResult.stage_results[s.key];
      if (passed === undefined) return { title: s.title, status: 'wait' as const };
      return { title: s.title, status: passed ? ('finish' as const) : ('error' as const) };
    });
  };

  const stageRows = Object.entries(inspectResult?.stage_results ?? {}).map(([stage, passed]) => ({
    stage,
    passed,
  }));
  const sessionOptions = (sessions?.items ?? []).map((session) => ({
    label: `${session.id.slice(0, 8)}... / ${session.status} / ${session.sandbox_level}`,
    value: session.id,
  }));

  return (
    <div>
      <Title level={4}>输出审查管线</Title>

      {sessionsIsError && (
        <QueryErrorAlert error={sessionsError} message="沙箱会话加载失败" onRetry={() => { void refetchSessions(); }} />
      )}

      {/* Pipeline Steps */}
      <Card style={{ marginBottom: 16 }}>
        <Title level={5}>审查流程</Title>
        <Steps items={getSteps()} />
      </Card>

      <Row gutter={16}>
        {/* Inspection Card */}
        <Col span={24}>
          <Card style={{ marginBottom: 16 }}>
            <Title level={5}>DLP 审查测试</Title>
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <Space style={{ width: '100%' }}>
                <Select
                  showSearch
                  placeholder="选择会话"
                  value={inspectSessionId}
                  onChange={setInspectSessionId}
                  options={sessionOptions}
                  optionFilterProp="label"
                  loading={sessionsLoading}
                  notFoundContent={sessionsLoading ? '加载中' : '暂无可用会话'}
                  style={{ width: 200 }}
                />
                <InputNumber
                  placeholder="DP ε"
                  min={0.01}
                  step={0.1}
                  value={inspectEpsilon ?? undefined}
                  onChange={(value) => setInspectEpsilon(value ?? null)}
                  style={{ width: 150 }}
                />
              </Space>
              <TextArea
                rows={4}
                placeholder="输入待审查的沙箱输出内容..."
                value={inspectOutput}
                onChange={(e) => setInspectOutput(e.target.value)}
              />
              <Button
                type="primary"
                loading={inspectMutation.isPending}
                onClick={() => {
                  if (!inspectOutput.trim() || !inspectSessionId.trim()) {
                    message.warning('请输入输出内容和会话ID');
                    return;
                  }
                  inspectMutation.mutate({
                    output: inspectOutput,
                    session_id: inspectSessionId,
                    dp_epsilon: inspectEpsilon ?? undefined,
                  });
                }}
              >
                执行审查
              </Button>
            </Space>

            {inspectResult ? (
              <>
                <Divider />
                <Descriptions bordered column={2} size="small">
                  <Descriptions.Item label="审查结果">
                    <Tag color={inspectResult.passed ? 'green' : 'red'}>
                      {inspectResult.passed ? '通过' : '未通过'}
                    </Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="DP已应用">
                    <Tag color={inspectResult.dp_applied ? 'blue' : 'default'}>
                      {inspectResult.dp_applied ? '是' : '否'}
                    </Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="脱敏输出" span={2}>
                    <Text code>{inspectResult.redacted_output || '-'}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="水印" span={2}>
                    <Text code>{inspectResult.watermark || '-'}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="SM2签名" span={2}>
                    <Text code copyable={!!inspectResult.signature}>{inspectResult.signature || '-'}</Text>
                  </Descriptions.Item>
                </Descriptions>

                {stageRows.length > 0 && (
                  <>
                    <Title level={5} style={{ marginTop: 16 }}>阶段结果</Title>
                    <Table
                      dataSource={stageRows}
                      rowKey="stage"
                      size="small"
                      pagination={false}
                      columns={[
                        { title: '阶段', dataIndex: 'stage', render: (v) => stageLabels[v] || v },
                        { title: '结果', dataIndex: 'passed', render: (v) => <Tag color={v ? 'green' : 'red'}>{v ? '通过' : '未通过'}</Tag> },
                      ]}
                    />
                  </>
                )}

                {inspectResult.findings.length > 0 && (
                  <>
                    <Title level={5} style={{ marginTop: 16 }}>发现项 ({inspectResult.findings.length})</Title>
                    <Table
                      dataSource={inspectResult.findings}
                      rowKey={(_, i) => String(i)}
                      size="small"
                      pagination={false}
                      columns={[
                        { title: '阶段', dataIndex: 'stage', render: (v) => stageLabels[v] || v },
                        { title: '严重度', dataIndex: 'severity', render: (v) => <Tag color={severityColors[v] || 'default'}>{v}</Tag> },
                        { title: '类型', dataIndex: 'type' },
                        { title: '说明', dataIndex: 'message' },
                      ]}
                    />
                  </>
                )}
              </>
            ) : (
              <>
                <Divider />
                <EmptyState description="暂无审查结果" />
              </>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        {/* DP Noise Test Card */}
        <Col span={12}>
          <Card style={{ marginBottom: 16 }}>
            <Title level={5}>DP噪声测试</Title>
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <Space style={{ width: '100%' }}>
                <div>
                  <Text type="secondary">原始值</Text>
                  <InputNumber value={noiseValue} onChange={(v) => setNoiseValue(v ?? 100)} style={{ width: '100%' }} />
                </div>
                <div>
                  <Text type="secondary">敏感度</Text>
                  <InputNumber value={noiseSensitivity} onChange={(v) => setNoiseSensitivity(v ?? 1)} min={0.01} step={0.1} style={{ width: '100%' }} />
                </div>
              </Space>
              <Space style={{ width: '100%' }}>
                <div>
                  <Text type="secondary">隐私预算 ε</Text>
                  <InputNumber value={noiseEpsilon} onChange={(v) => setNoiseEpsilon(v ?? 1)} min={0.01} step={0.1} style={{ width: '100%' }} />
                </div>
                <div>
                  <Text type="secondary">机制</Text>
                  <Select value={noiseMechanism} onChange={setNoiseMechanism} style={{ width: 120 }} options={[
                    { label: 'Laplace', value: 'laplace' },
                    { label: 'Gaussian', value: 'gaussian' },
                  ]} />
                </div>
              </Space>
              <Button
                type="primary"
                loading={noiseMutation.isPending}
                onClick={() => noiseMutation.mutate({
                  value: noiseValue,
                  sensitivity: noiseSensitivity,
                  epsilon: noiseEpsilon,
                  mechanism: noiseMechanism,
                })}
              >
                生成噪声
              </Button>
            </Space>

            {noiseResult ? (
              <>
                <Divider />
                <Row gutter={16}>
                  <Col span={8}>
                    <Statistic title="原始值" value={noiseResult.original} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="噪声值" value={noiseResult.noisy_value} precision={6} />
                  </Col>
                  <Col span={8}>
                    <Statistic title="噪声量" value={Math.abs(noiseResult.noisy_value - noiseResult.original)} precision={6} />
                  </Col>
                </Row>
                <Descriptions size="small" column={2} style={{ marginTop: 8 }}>
                  <Descriptions.Item label="机制">{noiseResult.mechanism}</Descriptions.Item>
                  <Descriptions.Item label="ε">{noiseResult.epsilon}</Descriptions.Item>
                  <Descriptions.Item label="敏感度">{noiseResult.sensitivity}</Descriptions.Item>
                </Descriptions>
              </>
            ) : (
              <>
                <Divider />
                <EmptyState description="暂无噪声结果" />
              </>
            )}
          </Card>
        </Col>

        {/* DP Budget Management Card */}
        <Col span={12}>
          <Card style={{ marginBottom: 16 }}>
            <Title level={5}>DP预算管理</Title>
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <Space style={{ width: '100%' }}>
                <Select
                  showSearch
                  placeholder="选择会话"
                  value={budgetSessionId}
                  onChange={setBudgetSessionId}
                  options={sessionOptions}
                  optionFilterProp="label"
                  loading={sessionsLoading}
                  notFoundContent={sessionsLoading ? '加载中' : '暂无可用会话'}
                  style={{ width: 200 }}
                />
                <div>
                  <Text type="secondary">分配 ε</Text>
                  <InputNumber value={budgetEpsilon} onChange={(v) => setBudgetEpsilon(v ?? 10)} min={0.01} step={1} style={{ width: 120 }} />
                </div>
              </Space>
              <Space>
                <Button
                  type="primary"
                  loading={budgetInitMutation.isPending}
                  onClick={() => {
                    if (!budgetSessionId.trim()) { message.warning('请输入会话ID'); return; }
                    budgetInitMutation.mutate();
                  }}
                >
                  初始化预算
                </Button>
                <Button
                  loading={budgetLoading}
                  onClick={() => {
                    if (!budgetSessionId.trim()) { message.warning('请输入会话ID'); return; }
                    refetchBudget();
                  }}
                >
                  查询余额
                </Button>
              </Space>
            </Space>

            {budgetIsError && (
              <>
                <Divider />
                <QueryErrorAlert error={budgetError} message="DP预算查询失败" onRetry={() => { void refetchBudget(); }} />
              </>
            )}

            {budgetData ? (
              <>
                <Divider />
                <Descriptions bordered column={1} size="small">
                  <Descriptions.Item label="会话ID">{budgetData.session_id}</Descriptions.Item>
                  <Descriptions.Item label="剩余预算 ε">
                    <Statistic value={budgetData.epsilon_remaining} precision={2} valueStyle={{ fontSize: 20 }} />
                  </Descriptions.Item>
                </Descriptions>
              </>
            ) : !budgetIsError && (
              <>
                <Divider />
                <EmptyState description="暂无预算余额结果" />
              </>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
