import { useState } from 'react';
import { Typography, Card, Row, Col, Select, Button, Input, Tag, Table, Space, message, Modal, Statistic, Divider, Alert, Descriptions } from 'antd';
import { PlayCircleOutlined, PlusOutlined, StopOutlined, CodeOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import Editor from '@monaco-editor/react';
import { sandboxApi, type DevExecuteResult, type DevSession } from '../../services/sandboxApi';
import type { SecurityFinding } from '../../types/security';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

const modeLabels: Record<string, string> = {
  structured: '结构化数据',
  unstructured: '非结构化数据',
  semi_structured: '半结构化数据',
};

const modeColors: Record<string, string> = {
  structured: 'blue',
  unstructured: 'purple',
  semi_structured: 'cyan',
};

const statusColors: Record<string, string> = {
  running: 'green', pending: 'blue', failed: 'red', terminated: 'default',
};

const severityColors: Record<string, string> = {
  critical: 'red',
  high: 'volcano',
  medium: 'orange',
  low: 'blue',
};

const stageLabels: Record<string, string> = {
  scene_routing: '场景路由',
  no_output: '无输出',
  format_validation: '格式校验',
  dlp_scan: 'DLP扫描',
  data_reconstruction: '重构风险',
  k_anonymity: 'K匿名',
  differential_privacy: '差分隐私',
  model_safety: '模型安全',
  watermark: '水印',
  signature: '签名',
  final_approval: '最终审批',
};

const templates = [
  { key: 'sql', label: 'SQL 查询模板', mode: 'structured' },
  { key: 'pandas', label: 'Pandas 分析模板', mode: 'structured' },
  { key: 'image-batch', label: '图像批处理模板', mode: 'unstructured' },
  { key: 'json-etl', label: 'JSON ETL 模板', mode: 'semi_structured' },
];

export default function DataProductDev() {
  const queryClient = useQueryClient();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [newMode, setNewMode] = useState<string>('structured');
  const [newEpsilon, setNewEpsilon] = useState<number | null>(null);

  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [language, setLanguage] = useState('python');
  const [execResult, setExecResult] = useState<DevExecuteResult | null>(null);

  const { data: sessions, isLoading } = useQuery({
    queryKey: ['dev-sessions'],
    queryFn: () => sandboxApi.devList(),
  });

  const createMutation = useMutation({
    mutationFn: () => sandboxApi.devCreate({
      mode: newMode as 'structured' | 'unstructured' | 'semi_structured',
      dp_epsilon_budget: newEpsilon,
    }),
    onSuccess: (s) => {
      message.success(`开发沙箱已创建: ${s.session_id.slice(0, 8)}...`);
      queryClient.invalidateQueries({ queryKey: ['dev-sessions'] });
      setCreateModalOpen(false);
      setSelectedSession(s.session_id);
    },
    onError: () => message.error('创建失败'),
  });

  const execMutation = useMutation({
    mutationFn: () => sandboxApi.devExecute(selectedSession!, code, language),
    onSuccess: (r) => {
      setExecResult(r);
      if (r.output_blocked) message.error('输出被安全策略阻断');
      else if (r.exit_code === 0) message.success('执行成功');
      else message.warning(`退出码: ${r.exit_code}`);
    },
    onError: () => message.error('执行请求失败'),
  });

  const terminateMutation = useMutation({
    mutationFn: (id: string) => sandboxApi.devTerminate(id),
    onSuccess: () => {
      message.success('已终止');
      queryClient.invalidateQueries({ queryKey: ['dev-sessions'] });
      if (selectedSession) setSelectedSession(null);
    },
    onError: () => message.error('终止失败'),
  });

  const loadTemplate = async (key: string) => {
    try {
      const r = await sandboxApi.devGetTemplate(key);
      setCode(r.template);
    } catch {
      message.error('加载模板失败');
    }
  };

  const columns: ColumnsType<DevSession> = [
    {
      title: '会话ID',
      dataIndex: 'session_id',
      render: (v) => <a onClick={() => setSelectedSession(v)}>{v.slice(0, 8)}...</a>,
    },
    {
      title: '模式',
      dataIndex: 'mode',
      render: (v) => <Tag color={modeColors[v]}>{modeLabels[v] || v}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (v) => <Tag color={statusColors[v]}>{v}</Tag>,
    },
    { title: '输入文件', dataIndex: 'input_files', render: (v) => v?.length || 0 },
    { title: '输出文件', dataIndex: 'output_files', render: (v) => v?.length || 0 },
    {
      title: '操作',
      render: (_, record) => (
        <Space>
          <a onClick={() => setSelectedSession(record.session_id)}>进入</a>
          {record.status === 'running' && (
            <a onClick={() => terminateMutation.mutate(record.session_id)} style={{ color: 'red' }}>终止</a>
          )}
        </Space>
      ),
    },
  ];

  const activeSession = sessions?.find(s => s.session_id === selectedSession);
  const editorLanguage = language === 'sql' ? 'sql' : language === 'shell' ? 'shell' : 'python';
  const securityReport = execResult?.security_report;
  const outputText = execResult ? (execResult.output ?? execResult.stdout ?? '') : '';
  const errorText = execResult ? (execResult.stderr ?? execResult.error ?? securityReport?.error ?? '') : '';
  const findings = securityReport?.findings ?? [];
  const stageRows = Object.entries(securityReport?.stage_results ?? {}).map(([stage, passed]) => ({
    stage,
    passed,
  }));

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>数据产品开发沙箱</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
          创建开发会话
        </Button>
      </div>

      <Row gutter={16}>
        {/* Session list */}
        <Col span={selectedSession ? 8 : 24}>
          <Card size="small">
            <Table
              columns={columns}
              dataSource={sessions || []}
              rowKey="session_id"
              loading={isLoading}
              size="small"
              pagination={false}
              locale={{ emptyText: '暂无开发会话' }}
            />
          </Card>
        </Col>

        {/* Code editor & execution */}
        {selectedSession && (
          <Col span={16}>
            <Card
              size="small"
              title={
                <Space>
                  <CodeOutlined />
                  <span>代码执行</span>
                  {activeSession && <Tag color={modeColors[activeSession.mode]}>{modeLabels[activeSession.mode]}</Tag>}
                  {activeSession && <Tag color={statusColors[activeSession.status]}>{activeSession.status}</Tag>}
                </Space>
              }
              extra={
                <Space>
                  <Select value={language} onChange={setLanguage} size="small" style={{ width: 120 }}
                    options={[
                      { label: 'Python', value: 'python' },
                      { label: 'SQL', value: 'sql' },
                      { label: 'Shell', value: 'shell' },
                    ]}
                  />
                  <Select placeholder="加载模板" size="small" style={{ width: 160 }} allowClear
                    onChange={(v) => v && loadTemplate(v)}
                    options={templates.filter(t => !activeSession || t.mode === activeSession.mode).map(t => ({ label: t.label, value: t.key }))}
                  />
                  <Button
                    type="primary"
                    size="small"
                    icon={<PlayCircleOutlined />}
                    loading={execMutation.isPending}
                    onClick={() => {
                      if (!code.trim()) { message.warning('请输入代码'); return; }
                      execMutation.mutate();
                    }}
                  >
                    执行
                  </Button>
                </Space>
              }
            >
              <div style={{ border: '1px solid #d9e2ea', borderRadius: 8, overflow: 'hidden' }}>
                <Editor
                  height="360px"
                  language={editorLanguage}
                  theme="vs"
                  value={code}
                  onChange={(value) => setCode(value ?? '')}
                  options={{
                    minimap: { enabled: false },
                    fontSize: 13,
                    lineNumbersMinChars: 3,
                    scrollBeyondLastLine: false,
                    automaticLayout: true,
                    tabSize: 2,
                    wordWrap: 'on',
                  }}
                />
              </div>
              {execResult && (
                <>
                  <Divider style={{ margin: '12px 0' }} />
                  <Space style={{ marginBottom: 8 }} wrap>
                    <Statistic title="退出码" value={execResult.exit_code} valueStyle={{ color: execResult.exit_code === 0 ? '#3f8600' : '#cf1322', fontSize: 16 }} />
                    <Tag color={execResult.output_blocked ? 'red' : 'green'}>
                      {execResult.output_blocked ? '输出已阻断' : '输出可释放'}
                    </Tag>
                    {securityReport && (
                      <Tag color={securityReport.passed === false || securityReport.blocked ? 'red' : 'green'}>
                        {securityReport.passed === false || securityReport.blocked ? '审查未通过' : '审查通过'}
                      </Tag>
                    )}
                  </Space>

                  {securityReport && (
                    <Alert
                      type={securityReport.passed === false || securityReport.blocked ? 'error' : 'success'}
                      showIcon
                      message={securityReport.passed === false || securityReport.blocked ? '安全审查发现风险' : '安全审查通过'}
                      description={`发现项 ${securityReport.findings_count ?? findings.length} 个${securityReport.max_severity ? `，最高风险 ${securityReport.max_severity}` : ''}`}
                      style={{ marginBottom: 12 }}
                    />
                  )}

                  {stageRows.length > 0 && (
                    <Table
                      dataSource={stageRows}
                      rowKey="stage"
                      size="small"
                      pagination={false}
                      style={{ marginBottom: 12 }}
                      columns={[
                        { title: '审查阶段', dataIndex: 'stage', render: (v) => stageLabels[v] || v },
                        { title: '结果', dataIndex: 'passed', render: (v) => <Tag color={v ? 'green' : 'red'}>{v ? '通过' : '未通过'}</Tag> },
                      ]}
                    />
                  )}

                  {findings.length > 0 && (
                    <Table<SecurityFinding>
                      dataSource={findings}
                      rowKey={(_, i) => String(i)}
                      size="small"
                      pagination={false}
                      style={{ marginBottom: 12 }}
                      columns={[
                        { title: '阶段', dataIndex: 'stage', render: (v) => stageLabels[v] || v },
                        { title: '严重度', dataIndex: 'severity', render: (v) => <Tag color={severityColors[v] || 'default'}>{v}</Tag> },
                        { title: '类型', dataIndex: 'type' },
                        { title: '说明', dataIndex: 'message' },
                      ]}
                    />
                  )}

                  {securityReport && (
                    <Descriptions size="small" column={1} bordered style={{ marginBottom: 12 }}>
                      <Descriptions.Item label="水印">{securityReport.watermark || '-'}</Descriptions.Item>
                      <Descriptions.Item label="签名">{securityReport.signature || '-'}</Descriptions.Item>
                    </Descriptions>
                  )}

                  {outputText && (
                    <div style={{ marginBottom: 8 }}>
                      <Text strong>审查后输出:</Text>
                      <pre style={{ background: '#f6f8fa', padding: 12, borderRadius: 6, maxHeight: 200, overflow: 'auto', fontSize: 12 }}>{outputText}</pre>
                    </div>
                  )}
                  {errorText && (
                    <div>
                      <Text strong type="danger">错误输出:</Text>
                      <pre style={{ background: '#fff2f0', padding: 12, borderRadius: 6, maxHeight: 200, overflow: 'auto', fontSize: 12 }}>{errorText}</pre>
                    </div>
                  )}
                </>
              )}
            </Card>
          </Col>
        )}
      </Row>

      {/* Create Modal */}
      <Modal
        title="创建开发沙箱会话"
        open={createModalOpen}
        onOk={() => createMutation.mutate()}
        onCancel={() => setCreateModalOpen(false)}
        confirmLoading={createMutation.isPending}
        okText="创建"
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text>开发模式</Text>
            <Select value={newMode} onChange={setNewMode} style={{ width: '100%', marginTop: 4 }}
              options={[
                { label: '结构化数据 — SQL查询/数据建模/特征工程', value: 'structured' },
                { label: '非结构化数据 — 图像/文档/音视频处理', value: 'unstructured' },
                { label: '半结构化数据 — JSON/XML/日志 ETL', value: 'semi_structured' },
              ]}
            />
          </div>
          <div>
            <Text>DP隐私预算 ε (可选)</Text>
            <Input
              type="number"
              value={newEpsilon ?? ''}
              onChange={(e) => setNewEpsilon(e.target.value ? Number(e.target.value) : null)}
              placeholder="留空表示不限制"
              style={{ marginTop: 4 }}
            />
          </div>
        </Space>
      </Modal>
    </div>
  );
}
