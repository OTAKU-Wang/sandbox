import { useState } from 'react';
import { Card, Table, Tag, Typography, Space, Button, Modal, Form, InputNumber, Input, message, Descriptions, Drawer, Timeline, Collapse } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { trainingApi } from '../../services/trainingApi';
import type { TrainingJob, ValidationResult, AuditEntry, CheckpointInfo, TrainingConfig } from '../../services/trainingApi';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  pending: 'blue', running: 'green', completed: 'green', failed: 'red', cancelled: 'default',
};

const severityColors: Record<string, string> = {
  error: 'red', warning: 'orange', info: 'blue',
};

export default function TrainingDashboard() {
  const queryClient = useQueryClient();
  const [validateOpen, setValidateOpen] = useState(false);
  const [auditDrawer, setAuditDrawer] = useState<{ open: boolean; jobId: string | null }>({ open: false, jobId: null });
  const [checkpointDrawer, setCheckpointDrawer] = useState<{ open: boolean; jobId: string | null }>({ open: false, jobId: null });
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [form] = Form.useForm();

  const { data: jobs, isLoading } = useQuery({
    queryKey: ['training-jobs'],
    queryFn: () => trainingApi.listJobs(),
  });

  const { data: auditLog } = useQuery({
    queryKey: ['training-audit', auditDrawer.jobId],
    queryFn: () => trainingApi.getAuditLog(auditDrawer.jobId!),
    enabled: !!auditDrawer.jobId && auditDrawer.open,
  });

  const { data: checkpoints } = useQuery({
    queryKey: ['training-checkpoints', checkpointDrawer.jobId],
    queryFn: () => trainingApi.listCheckpoints(checkpointDrawer.jobId!),
    enabled: !!checkpointDrawer.jobId && checkpointDrawer.open,
  });

  const validateMutation = useMutation({
    mutationFn: (config: TrainingConfig) => trainingApi.validateConfig(config),
    onSuccess: (data) => {
      setValidationResult(data);
      if (data.valid) message.success('配置校验通过');
      else message.error(`配置有 ${data.errors} 个错误`);
    },
    onError: () => message.error('校验请求失败'),
  });

  const jobColumns: ColumnsType<TrainingJob> = [
    { title: '任务ID', dataIndex: 'job_id', key: 'job_id', render: (v) => <Text code>{v.slice(0, 8)}...</Text> },
    { title: '模型', dataIndex: 'model_name', key: 'model_name', render: (_, r) => r.config?.model_name || '-' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <Tag color={statusColors[v]}>{v}</Tag> },
    { title: '学习率', key: 'lr', render: (_, r) => r.config?.learning_rate },
    { title: 'Epochs', key: 'epochs', render: (_, r) => r.config?.epochs },
    { title: 'Batch Size', key: 'bs', render: (_, r) => r.config?.batch_size },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v) => new Date(v).toLocaleString() },
    {
      title: '操作', key: 'action', width: 200,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => setAuditDrawer({ open: true, jobId: record.job_id })}>审计日志</Button>
          <Button size="small" onClick={() => setCheckpointDrawer({ open: true, jobId: record.job_id })}>检查点</Button>
        </Space>
      ),
    },
  ];

  const auditColumns: ColumnsType<AuditEntry> = [
    { title: '事件', dataIndex: 'event_type', key: 'event_type', render: (v) => <Tag>{v}</Tag> },
    { title: '时间', dataIndex: 'timestamp', key: 'timestamp', render: (v) => new Date(v).toLocaleString() },
    { title: '操作者', dataIndex: 'actor', key: 'actor' },
    { title: '哈希', dataIndex: 'entry_hash', key: 'hash', render: (v) => <Text code>{v.slice(0, 12)}...</Text> },
  ];

  const checkpointColumns: ColumnsType<CheckpointInfo> = [
    { title: '检查点ID', dataIndex: 'checkpoint_id', key: 'checkpoint_id', render: (v) => <Text code>{v.slice(0, 8)}...</Text> },
    { title: 'Epoch', dataIndex: 'epoch', key: 'epoch' },
    { title: 'Step', dataIndex: 'step', key: 'step' },
    { title: '大小', dataIndex: 'size_bytes', key: 'size', render: (v) => `${(v / 1024).toFixed(1)} KB` },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v) => new Date(v).toLocaleString() },
  ];

  const handleValidate = () => {
    const values = form.getFieldsValue();
    const config: TrainingConfig = {
      learning_rate: values.learning_rate,
      epochs: values.epochs,
      batch_size: values.batch_size,
      max_seq_length: values.max_seq_length,
      model_name: values.model_name,
    };
    if (values.noise_multiplier || values.dp_max_grad_norm || values.delta) {
      config.dp_config = {
        noise_multiplier: values.noise_multiplier || 1.0,
        max_grad_norm: values.dp_max_grad_norm || 1.0,
        delta: values.delta || 1e-5,
      };
    }
    validateMutation.mutate(config);
  };

  return (
    <div>
      <Title level={4}>训练任务管理</Title>

      {/* Job List */}
      <Card title="训练任务" style={{ marginBottom: 16 }} extra={<Button type="primary" onClick={() => { setValidateOpen(true); setValidationResult(null); form.resetFields(); }}>配置校验</Button>}>
        <Table columns={jobColumns} dataSource={jobs || []} rowKey="job_id" loading={isLoading} pagination={{ pageSize: 10 }} />
      </Card>

      {/* Config Validation Modal */}
      <Modal title="训练配置校验" open={validateOpen} onCancel={() => setValidateOpen(false)} width={700}
        footer={[
          <Button key="validate" type="primary" loading={validateMutation.isPending} onClick={handleValidate}>校验配置</Button>,
          <Button key="close" onClick={() => setValidateOpen(false)}>关闭</Button>,
        ]}>
        <Form form={form} layout="vertical">
          <Collapse defaultActiveKey={['basic', 'dp']} ghost
            items={[
              {
                key: 'basic',
                label: '基础参数',
                children: (
                  <Space wrap>
                    <Form.Item name="learning_rate" label="学习率" rules={[{ required: true }]}><InputNumber min={0} step={0.0001} style={{ width: 160 }} /></Form.Item>
                    <Form.Item name="epochs" label="Epochs" rules={[{ required: true }]}><InputNumber min={1} style={{ width: 120 }} /></Form.Item>
                    <Form.Item name="batch_size" label="Batch Size" rules={[{ required: true }]}><InputNumber min={1} style={{ width: 120 }} /></Form.Item>
                    <Form.Item name="max_seq_length" label="Max Seq Length"><InputNumber min={1} style={{ width: 140 }} /></Form.Item>
                    <Form.Item name="model_name" label="模型名称"><Input placeholder="e.g. bert-base-uncased" style={{ width: 220 }} /></Form.Item>
                  </Space>
                ),
              },
              {
                key: 'dp',
                label: 'DP-SGD 参数（可选）',
                children: (
                  <Space wrap>
                    <Form.Item name="noise_multiplier" label="噪声乘数"><InputNumber min={0} step={0.1} style={{ width: 160 }} /></Form.Item>
                    <Form.Item name="dp_max_grad_norm" label="梯度裁剪"><InputNumber min={0} step={0.1} style={{ width: 160 }} /></Form.Item>
                    <Form.Item name="delta" label="Delta"><InputNumber min={0} step={0.00001} style={{ width: 160 }} /></Form.Item>
                  </Space>
                ),
              },
            ]}
          />
        </Form>

        {validationResult && (
          <Card size="small" style={{ marginTop: 16, borderColor: validationResult.valid ? '#b7eb8f' : '#ffa39e' }}
            title={validationResult.valid ? '校验通过' : `校验失败 (${validationResult.errors} 错误, ${validationResult.warnings} 警告)`}>
            {validationResult.issues.map((issue, idx) => (
              <div key={idx} style={{ marginBottom: 4 }}>
                <Tag color={severityColors[issue.severity]}>{issue.severity}</Tag>
                <Text strong>{issue.field}</Text>: {issue.message}
                {issue.current_value && <Text type="secondary"> (当前: {issue.current_value})</Text>}
                {issue.expected_range && <Text type="secondary"> (期望: {issue.expected_range})</Text>}
              </div>
            ))}
          </Card>
        )}
      </Modal>

      {/* Audit Log Drawer */}
      <Drawer title="训练审计日志" open={auditDrawer.open} onClose={() => setAuditDrawer({ open: false, jobId: null })} width={700}>
        {auditLog && auditLog.length > 0 ? (
          <Timeline items={auditLog.map((entry) => ({
            children: (
              <div>
                <Tag>{entry.event_type}</Tag>
                <Text type="secondary">{new Date(entry.timestamp).toLocaleString()}</Text>
                {entry.actor && <Text> — {entry.actor}</Text>}
                <br />
                <Text code style={{ fontSize: 11 }}>hash: {entry.entry_hash.slice(0, 16)}...</Text>
                {Object.keys(entry.details).length > 0 && (
                  <pre style={{ fontSize: 11, marginTop: 4, background: '#f5f5f5', padding: 4, borderRadius: 4 }}>
                    {JSON.stringify(entry.details, null, 2)}
                  </pre>
                )}
              </div>
            ),
          }))} />
        ) : (
          <Text type="secondary">暂无审计记录</Text>
        )}
      </Drawer>

      {/* Checkpoints Drawer */}
      <Drawer title="加密检查点" open={checkpointDrawer.open} onClose={() => setCheckpointDrawer({ open: false, jobId: null })} width={700}>
        <Table columns={checkpointColumns} dataSource={checkpoints || []} rowKey="checkpoint_id" pagination={false} />
      </Drawer>
    </div>
  );
}
