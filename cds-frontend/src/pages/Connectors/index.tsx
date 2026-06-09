import { useState } from 'react';
import {
  Card,
  Table,
  Tag,
  Typography,
  Space,
  Button,
  Modal,
  Form,
  Input,
  message,
  Descriptions,
  Drawer,
  Spin,
  Tooltip,
  Popconfirm,
} from 'antd';
import {
  PlusOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  KeyOutlined,
  HeartOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { connectorApi } from '../../services/connectorApi';
import type { Connector, HeartbeatResponse } from '../../services/connectorApi';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  active: 'green',
  suspended: 'orange',
  revoked: 'red',
};

const statusLabels: Record<string, string> = {
  active: '活跃',
  suspended: '已暂停',
  revoked: '已撤销',
};

const heartbeatStatusColors: Record<string, string> = {
  healthy: 'green',
  unhealthy: 'red',
  unknown: 'default',
};

const heartbeatStatusLabels: Record<string, string> = {
  healthy: '健康',
  unhealthy: '异常',
  unknown: '未知',
};

export default function Connectors() {
  const queryClient = useQueryClient();
  const [registerOpen, setRegisterOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedConnector, setSelectedConnector] = useState<Connector | null>(null);
  const [heartbeatModalOpen, setHeartbeatModalOpen] = useState(false);
  const [heartbeatData, setHeartbeatData] = useState<HeartbeatResponse | null>(null);
  const [newKeyModalOpen, setNewKeyModalOpen] = useState(false);
  const [newApiKey, setNewApiKey] = useState<string>('');
  const [form] = Form.useForm();

  // Fetch connectors
  const { data: connectors, isLoading } = useQuery({
    queryKey: ['connectors'],
    queryFn: () => connectorApi.listConnectors(),
  });

  // Register connector mutation
  const registerMutation = useMutation({
    mutationFn: connectorApi.registerConnector,
    onSuccess: () => {
      message.success('连接器注册成功');
      setRegisterOpen(false);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError: () => message.error('注册失败'),
  });

  // Suspend connector mutation
  const suspendMutation = useMutation({
    mutationFn: (id: string) => connectorApi.suspendConnector(id),
    onSuccess: () => {
      message.success('连接器已暂停');
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError: () => message.error('暂停失败'),
  });

  // Reactivate connector mutation
  const reactivateMutation = useMutation({
    mutationFn: (id: string) => connectorApi.reactivateConnector(id),
    onSuccess: () => {
      message.success('连接器已重新激活');
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError: () => message.error('激活失败'),
  });

  // Rotate key mutation
  const rotateKeyMutation = useMutation({
    mutationFn: (id: string) => connectorApi.rotateKey(id),
    onSuccess: (data) => {
      setNewApiKey(data.api_key);
      setNewKeyModalOpen(true);
      message.success('API Key 已轮换');
    },
    onError: () => message.error('Key 轮换失败'),
  });

  // Heartbeat query (lazy - only fetched when requested)
  const heartbeatMutation = useMutation({
    mutationFn: (id: string) => connectorApi.getHeartbeat(id),
    onSuccess: (data) => {
      setHeartbeatData(data);
      setHeartbeatModalOpen(true);
    },
    onError: () => message.error('获取心跳状态失败'),
  });

  const showDetails = (record: Connector) => {
    setSelectedConnector(record);
    setDrawerOpen(true);
  };

  const columns: ColumnsType<Connector> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      render: (text, record) => (
        <a onClick={() => showDetails(record)}>{text}</a>
      ),
    },
    {
      title: '端点地址',
      dataIndex: 'endpoint_url',
      key: 'endpoint_url',
      ellipsis: true,
      render: (v) => <Text copyable={{ text: v }}>{v}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (v) => <Tag color={statusColors[v]}>{statusLabels[v] || v}</Tag>,
    },
    {
      title: '信任等级',
      dataIndex: 'trust_level',
      key: 'trust_level',
      render: (v) => <Tag>{v?.toUpperCase()}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v) => (v ? new Date(v).toLocaleString() : '-'),
    },
    {
      title: '最后心跳',
      dataIndex: 'last_heartbeat',
      key: 'last_heartbeat',
      render: (v) => (v ? new Date(v).toLocaleString() : '无记录'),
    },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_, record) => (
        <Space size="small">
          {record.status === 'active' && (
            <Popconfirm
              title="确定暂停此连接器？"
              onConfirm={() => suspendMutation.mutate(record.id)}
              okText="确定"
              cancelText="取消"
            >
              <Tooltip title="暂停">
                <Button size="small" icon={<PauseCircleOutlined />} loading={suspendMutation.isPending}>
                  暂停
                </Button>
              </Tooltip>
            </Popconfirm>
          )}
          {record.status === 'suspended' && (
            <Popconfirm
              title="确定重新激活此连接器？"
              onConfirm={() => reactivateMutation.mutate(record.id)}
              okText="确定"
              cancelText="取消"
            >
              <Tooltip title="激活">
                <Button size="small" type="primary" icon={<PlayCircleOutlined />} loading={reactivateMutation.isPending}>
                  激活
                </Button>
              </Tooltip>
            </Popconfirm>
          )}
          <Popconfirm
            title="确定轮换此连接器的 API Key？"
            description="旧 Key 将立即失效"
            onConfirm={() => rotateKeyMutation.mutate(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Tooltip title="轮换 Key">
              <Button size="small" icon={<KeyOutlined />} loading={rotateKeyMutation.isPending}>
                轮换 Key
              </Button>
            </Tooltip>
          </Popconfirm>
          <Tooltip title="心跳检测">
            <Button
              size="small"
              icon={<HeartOutlined />}
              loading={heartbeatMutation.isPending}
              onClick={() => heartbeatMutation.mutate(record.id)}
            >
              心跳
            </Button>
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>连接器管理</Title>

      <Card
        title="连接器列表"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setRegisterOpen(true)}>
            注册连接器
          </Button>
        }
      >
        <Table
          columns={columns}
          dataSource={connectors || []}
          rowKey="id"
          loading={isLoading}
          pagination={false}
        />
      </Card>

      {/* Register Connector Modal */}
      <Modal
        title="注册新连接器"
        open={registerOpen}
        onCancel={() => setRegisterOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={registerMutation.isPending}
        destroyOnClose
      >
        <Form form={form} layout="vertical" onFinish={(v) => registerMutation.mutate(v)}>
          <Form.Item name="name" label="连接器名称" rules={[{ required: true, message: '请输入连接器名称' }]}>
            <Input placeholder="输入连接器名称" />
          </Form.Item>
          <Form.Item
            name="endpoint_url"
            label="端点地址"
            rules={[
              { required: true, message: '请输入端点地址' },
              { type: 'url', message: '请输入有效的 URL' },
            ]}
          >
            <Input placeholder="https://example.com/api" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true, message: '请输入 API Key' }]}>
            <Input.Password placeholder="输入 API Key" />
          </Form.Item>
        </Form>
      </Modal>

      {/* Connector Details Drawer */}
      <Drawer
        title="连接器详情"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={520}
      >
        {selectedConnector && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="ID">
              <Text code>{selectedConnector.id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="名称">{selectedConnector.name}</Descriptions.Item>
            <Descriptions.Item label="端点地址">
              <Text copyable={{ text: selectedConnector.endpoint_url }}>{selectedConnector.endpoint_url}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusColors[selectedConnector.status]}>
                {statusLabels[selectedConnector.status] || selectedConnector.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="信任等级">
              <Tag>{selectedConnector.trust_level?.toUpperCase()}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">
              {selectedConnector.created_at ? new Date(selectedConnector.created_at).toLocaleString() : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="最后心跳">
              {selectedConnector.last_heartbeat
                ? new Date(selectedConnector.last_heartbeat).toLocaleString()
                : '无记录'}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>

      {/* Heartbeat Status Modal */}
      <Modal
        title="心跳状态"
        open={heartbeatModalOpen}
        onCancel={() => setHeartbeatModalOpen(false)}
        footer={<Button onClick={() => setHeartbeatModalOpen(false)}>关闭</Button>}
      >
        {heartbeatData && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="连接器 ID">
              <Text code>{heartbeatData.connector_id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={heartbeatStatusColors[heartbeatData.status]}>
                {heartbeatStatusLabels[heartbeatData.status] || heartbeatData.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="最后在线">
              {new Date(heartbeatData.last_seen).toLocaleString()}
            </Descriptions.Item>
            <Descriptions.Item label="延迟">
              {heartbeatData.latency_ms} ms
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>

      {/* New API Key Modal */}
      <Modal
        title="API Key 轮换成功"
        open={newKeyModalOpen}
        onCancel={() => { setNewKeyModalOpen(false); setNewApiKey(''); }}
        footer={<Button onClick={() => { setNewKeyModalOpen(false); setNewApiKey(''); }}>关闭</Button>}
      >
        <div style={{ marginBottom: 8 }}>
          <Text type="warning">
            <InfoCircleOutlined /> 请妥善保存新 Key，此 Key 不会再次显示。
          </Text>
        </div>
        <Input.TextArea
          value={newApiKey}
          readOnly
          autoSize={{ minRows: 2, maxRows: 4 }}
          style={{ fontFamily: 'monospace' }}
        />
      </Modal>
    </div>
  );
}
