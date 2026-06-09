import { useState } from 'react';
import { Table, Button, Space, Tag, Typography, message, Popconfirm, Upload, Modal, Form, Input, Drawer, Descriptions } from 'antd';
import { PlusOutlined, UploadOutlined, DeleteOutlined, EyeOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { dataResourceApi } from '../../services/dataResourceApi';
import type { DataResource } from '../../services/dataResourceApi';
import type { ColumnsType } from 'antd/es/table';

const { Title } = Typography;

const statusColors: Record<string, string> = {
  ready: 'green',
  processing: 'blue',
  error: 'red',
};

export default function ResourceList() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedResourceId, setSelectedResourceId] = useState<string | null>(null);
  const [form] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ['data-resources', page],
    queryFn: () => dataResourceApi.list({ page, page_size: 20 }),
  });

  const { data: selectedResource } = useQuery({
    queryKey: ['data-resource', selectedResourceId],
    queryFn: () => dataResourceApi.get(selectedResourceId!),
    enabled: !!selectedResourceId,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => dataResourceApi.delete(id),
    onSuccess: () => {
      message.success('删除成功');
      queryClient.invalidateQueries({ queryKey: ['data-resources'] });
    },
    onError: () => message.error('删除失败'),
  });

  const uploadMutation = useMutation({
    mutationFn: (values: { name: string; description?: string; file: File }) =>
      dataResourceApi.upload(values),
    onSuccess: () => {
      message.success('上传成功');
      setUploadOpen(false);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: ['data-resources'] });
    },
    onError: () => message.error('上传失败'),
  });

  const columns: ColumnsType<DataResource> = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 200 },
    { title: '类型', dataIndex: 'format', key: 'format', width: 100,
      render: (t: string) => <Tag>{t?.toUpperCase()}</Tag> },
    { title: '行数', dataIndex: 'row_count', key: 'row_count', width: 100,
      render: (n: number) => n?.toLocaleString() ?? '-' },
    { title: '大小', dataIndex: 'file_size_bytes', key: 'file_size_bytes', width: 100,
      render: (s: number) => s ? `${(s / 1024).toFixed(1)} KB` : '-' },
    { title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={statusColors[s] ?? 'default'}>{s}</Tag> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180,
      render: (t: string) => new Date(t).toLocaleString('zh-CN') },
    {
      title: '操作', key: 'action', width: 150,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => setSelectedResourceId(record.id)}>
            查看
          </Button>
          <Popconfirm title="确认删除？" onConfirm={() => deleteMutation.mutate(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <Title level={4}>数据资源管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setUploadOpen(true)}>
          上传资源
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        rowKey="id"
        pagination={{
          current: page,
          pageSize: 20,
          total: data?.total ?? 0,
          onChange: setPage,
        }}
      />

      <Modal
        title="上传数据资源"
        open={uploadOpen}
        onCancel={() => { setUploadOpen(false); form.resetFields(); }}
        onOk={() => form.submit()}
        confirmLoading={uploadMutation.isPending}
      >
        <Form form={form} layout="vertical" onFinish={uploadMutation.mutate}>
          <Form.Item name="name" label="资源名称" rules={[{ required: true }]}>
            <Input placeholder="输入资源名称" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} placeholder="资源描述（可选）" />
          </Form.Item>
          <Form.Item name="file" label="文件" rules={[{ required: true, message: '请选择文件' }]}>
            <Upload.Dragger
              beforeUpload={(file) => { form.setFieldsValue({ file }); return false; }}
              maxCount={1}
              accept=".csv,.json,.parquet,.xlsx"
            >
              <p><UploadOutlined style={{ fontSize: 32, color: '#1890ff' }} /></p>
              <p>点击或拖拽文件上传</p>
              <p style={{ color: '#999' }}>支持 CSV、JSON、Parquet、Excel</p>
            </Upload.Dragger>
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title="数据资源详情"
        open={!!selectedResourceId}
        onClose={() => setSelectedResourceId(null)}
        width={640}
      >
        {selectedResource && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="名称">{selectedResource.name}</Descriptions.Item>
              <Descriptions.Item label="描述">{selectedResource.description || '-'}</Descriptions.Item>
              <Descriptions.Item label="资源类型">{selectedResource.resource_type}</Descriptions.Item>
              <Descriptions.Item label="格式">{selectedResource.format}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColors[selectedResource.status] ?? 'default'}>{selectedResource.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="行数">{selectedResource.row_count?.toLocaleString() || '-'}</Descriptions.Item>
              <Descriptions.Item label="大小">
                {selectedResource.file_size_bytes ? `${(selectedResource.file_size_bytes / 1024).toFixed(1)} KB` : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">{new Date(selectedResource.created_at).toLocaleString('zh-CN')}</Descriptions.Item>
              <Descriptions.Item label="错误信息">{selectedResource.error_message || '-'}</Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Text strong>Schema</Typography.Text>
              <pre style={{ background: '#f6f8fa', padding: 12, borderRadius: 6, maxHeight: 320, overflow: 'auto', fontSize: 12 }}>
                {selectedResource.schema_fields ? JSON.stringify(selectedResource.schema_fields, null, 2) : '-'}
              </pre>
            </div>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
