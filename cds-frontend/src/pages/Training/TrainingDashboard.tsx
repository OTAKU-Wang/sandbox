import { useState } from 'react';
import { Card, Table, Tag, Typography, Space, Button, Modal, Form, InputNumber, Input, message, Descriptions, Drawer, Collapse, Popconfirm } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { trainingApi } from '../../services/trainingApi';
import type { TrainingJob, ValidationResult, TrainingConfig } from '../../services/trainingApi';
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
  const [detailDrawer, setDetailDrawer] = useState<{ open: boolean; job: TrainingJob | null }>({ open: false, job: null });
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [form] = Form.useForm();

  const { data: jobs, isLoading } = useQuery({
    queryKey: ['training-jobs'],
    queryFn: () => trainingApi.listJobs(),
  });

  const { data: selectedJobDetail, isLoading: detailLoading } = useQuery({
    queryKey: ['training-job', detailDrawer.job?.job_id],
    queryFn: () => trainingApi.getJob(detailDrawer.job!.job_id),
    enabled: detailDrawer.open && !!detailDrawer.job?.job_id,
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

  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => trainingApi.cancelJob(jobId),
    onSuccess: () => {
      message.success('训练任务已取消');
      queryClient.invalidateQueries({ queryKey: ['training-jobs'] });
      queryClient.invalidateQueries({ queryKey: ['training-job'] });
    },
    onError: () => message.error('取消训练任务失败'),
  });

  const detailJob = selectedJobDetail || detailDrawer.job;
  const canCancel = (status?: string) => status === 'pending' || status === 'running';
  const formatDate = (value?: string | null) => value ? new Date(value).toLocaleString() : '-';

  const jobColumns: ColumnsType<TrainingJob> = [
    { title: '任务ID', dataIndex: 'job_id', key: 'job_id', render: (v) => <Text code>{v.slice(0, 8)}...</Text> },
    { title: '模型', dataIndex: 'model_name', key: 'model_name', render: (_, r) => r.config?.model_name || r.base_model || r.model_name || '-' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <Tag color={statusColors[v]}>{v}</Tag> },
    { title: '学习率', key: 'lr', render: (_, r) => r.config?.learning_rate ?? '-' },
    { title: 'Epochs', key: 'epochs', render: (_, r) => r.config?.epochs ?? '-' },
    { title: 'Batch Size', key: 'bs', render: (_, r) => r.config?.batch_size ?? '-' },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', render: (v) => formatDate(v) },
    {
      title: '操作', key: 'action', width: 200,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => setDetailDrawer({ open: true, job: record })}>详情</Button>
          {canCancel(record.status) && (
            <Popconfirm
              title="确认取消该训练任务？"
              okText="确认取消"
              cancelText="返回"
              onConfirm={() => cancelMutation.mutate(record.job_id)}
            >
              <Button size="small" danger loading={cancelMutation.isPending && cancelMutation.variables === record.job_id}>取消</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
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

      <Drawer
        title="训练任务详情"
        open={detailDrawer.open}
        onClose={() => setDetailDrawer({ open: false, job: null })}
        width={760}
      >
        {detailJob ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label="任务ID"><Text code>{detailJob.job_id}</Text></Descriptions.Item>
              <Descriptions.Item label="任务类型">{detailJob.job_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="模型">{detailJob.config?.model_name || detailJob.base_model || detailJob.model_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="状态"><Tag color={statusColors[detailJob.status]}>{detailJob.status}</Tag></Descriptions.Item>
              <Descriptions.Item label="输出路径">{detailJob.output_path ? <Text code>{detailJob.output_path}</Text> : '-'}</Descriptions.Item>
              <Descriptions.Item label="错误信息">{detailJob.error_message ? <Text type="danger">{detailJob.error_message}</Text> : '-'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatDate(detailJob.created_at)}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{formatDate(detailJob.started_at)}</Descriptions.Item>
              <Descriptions.Item label="完成时间">{formatDate(detailJob.completed_at)}</Descriptions.Item>
            </Descriptions>

            <Card size="small" title="训练配置" loading={detailLoading}>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {JSON.stringify(detailJob.config || {}, null, 2)}
              </pre>
            </Card>

            <Card size="small" title="训练指标" loading={detailLoading}>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {JSON.stringify(detailJob.metrics || {}, null, 2)}
              </pre>
            </Card>
          </Space>
        ) : (
          <Text type="secondary">请选择训练任务</Text>
        )}
      </Drawer>
    </div>
  );
}
