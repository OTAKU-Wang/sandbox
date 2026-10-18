import { useState } from 'react';
import {
  Alert,
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
  Tooltip,
} from 'antd';
import {
  PlusOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  KeyOutlined,
  HeartOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { connectorApi } from '../../services/connectorApi';
import type { Connector, HeartbeatResponse } from '../../services/connectorApi';
import { confirmHighRiskOperation } from '../../utils/highRiskOperation';
import type { ColumnsType } from 'antd/es/table';
import { QueryErrorAlert } from '../../components/Feedback/QueryFeedback';

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

  // Fetch connectors（A313：keyset 游标可达 —— 后端每页只给 100 条，续拉游标在 X-Next-Cursor 头里，
  // 服务层已把它并进结果；这里用 useInfiniteQuery 逐页拉，第 101 条起要能真的被看到）
  const {
    data: connectorPages,
    isLoading,
    isError,
    error,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['connectors'],
    queryFn: ({ pageParam }) => connectorApi.listConnectors(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
  });
  const connectors = connectorPages ? connectorPages.pages.flatMap((page) => page.items) : undefined;
  const lastPage = connectorPages?.pages[connectorPages.pages.length - 1];
  // 后端声明了「还有更多」而游标没透传过来（代理剥头）：无法续拉，只能显式披露截断。
  const cursorLost = Boolean(lastPage && lastPage.possiblyTruncated && !hasNextPage);

  // Register connector mutation
  const registerMutation = useMutation({
    mutationFn: connectorApi.registerConnector,
    onSuccess: (connector) => {
      message.success('连接器注册成功');
      setRegisterOpen(false);
      form.resetFields();
      if (connector.api_key) {
        setNewApiKey(connector.api_key);
        setNewKeyModalOpen(true);
      }
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError: () => message.error('注册失败'),
  });

  // Suspend connector mutation
  const suspendMutation = useMutation({
    mutationFn: (payload: { id: string; reason: string; ticket_id?: string | null }) =>
      connectorApi.suspendConnector(payload.id, { reason: payload.reason, ticket_id: payload.ticket_id }),
    onSuccess: () => {
      message.success('连接器已暂停');
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError: () => message.error('暂停失败'),
  });

  // Reactivate connector mutation
  const reactivateMutation = useMutation({
    mutationFn: (payload: { id: string; reason: string; ticket_id?: string | null }) =>
      connectorApi.reactivateConnector(payload.id, { reason: payload.reason, ticket_id: payload.ticket_id }),
    onSuccess: () => {
      message.success('连接器已重新激活');
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
    onError: () => message.error('激活失败'),
  });

  // Rotate key mutation
  const rotateKeyMutation = useMutation({
    mutationFn: (payload: { id: string; reason: string; ticket_id?: string | null }) =>
      connectorApi.rotateKey(payload.id, { reason: payload.reason, ticket_id: payload.ticket_id }),
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

  const handleSuspend = (id: string) => {
    confirmHighRiskOperation({
      title: '确认暂停连接器',
      content: '暂停后该空间将无法继续通过此连接器访问数据产品。',
      okText: '确认暂停',
      onConfirm: (payload) => suspendMutation.mutate({ id, ...payload }),
    });
  };

  const handleReactivate = (id: string) => {
    confirmHighRiskOperation({
      title: '确认恢复连接器',
      content: '恢复后该空间可继续通过此连接器访问授权数据产品。',
      okText: '确认恢复',
      onConfirm: (payload) => reactivateMutation.mutate({ id, ...payload }),
    });
  };

  const handleRotateKey = (id: string) => {
    confirmHighRiskOperation({
      title: '确认轮换 API Key',
      content: '旧 Key 将立即失效，远端空间需要同步更新凭证。',
      okText: '确认轮换',
      onConfirm: (payload) => rotateKeyMutation.mutate({ id, ...payload }),
    });
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
      render: (_, record) => <Tag color={record.is_healthy ? 'green' : 'default'}>{record.is_healthy ? 'HEALTHY' : 'UNKNOWN'}</Tag>,
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
            <Tooltip title="暂停">
              <Button
                size="small"
                icon={<PauseCircleOutlined />}
                loading={suspendMutation.isPending && suspendMutation.variables?.id === record.id}
                onClick={() => handleSuspend(record.id)}
              >
                暂停
              </Button>
            </Tooltip>
          )}
          {record.status === 'suspended' && (
            <Tooltip title="激活">
              <Button
                size="small"
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={reactivateMutation.isPending && reactivateMutation.variables?.id === record.id}
                onClick={() => handleReactivate(record.id)}
              >
                激活
              </Button>
            </Tooltip>
          )}
          <Tooltip title="轮换 Key">
            <Button
              size="small"
              icon={<KeyOutlined />}
              loading={rotateKeyMutation.isPending && rotateKeyMutation.variables?.id === record.id}
              onClick={() => handleRotateKey(record.id)}
            >
              轮换 Key
            </Button>
          </Tooltip>
          <Tooltip title="心跳检测">
            <Button
              size="small"
              icon={<HeartOutlined />}
              loading={heartbeatMutation.isPending && heartbeatMutation.variables === record.id}
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
        {isError && <QueryErrorAlert error={error} message="连接器列表加载失败" onRetry={() => { void refetch(); }} />}
        <Table
          columns={columns}
          dataSource={connectors || []}
          rowKey="id"
          loading={isLoading}
          pagination={false}
        />
        {hasNextPage && (
          <div style={{ marginTop: 12, textAlign: 'center' }}>
            <Button
              data-testid="connectors-load-more"
              onClick={() => { void fetchNextPage(); }}
              loading={isFetchingNextPage}
            >
              已加载 {connectors?.length ?? 0} 条，还有更多 —— 加载下一页
            </Button>
          </div>
        )}
        {cursorLost && (
          <div data-testid="connectors-truncated-warning">
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message="后端声明连接器列表尚未取完，但续拉游标（X-Next-Cursor 响应头）没有透传到浏览器，当前列表可能不完整。"
            />
          </div>
        )}
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
          <Form.Item name="space_id" label="空间ID" rules={[{ required: true, message: '请输入空间ID' }]}>
            <Input placeholder="remote-space-001" />
          </Form.Item>
          <Form.Item name="space_name" label="空间名称" rules={[{ required: true, message: '请输入空间名称' }]}>
            <Input placeholder="远端可信数据空间" />
          </Form.Item>
          <Form.Item
            name="space_url"
            label="端点地址"
            rules={[
              { required: true, message: '请输入端点地址' },
              { type: 'url', message: '请输入有效的 URL' },
            ]}
          >
            <Input placeholder="https://example.com/api" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} placeholder="连接器用途、对接系统或审批备注" />
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
              <Tag color={selectedConnector.is_healthy ? 'green' : 'default'}>{selectedConnector.is_healthy ? 'HEALTHY' : 'UNKNOWN'}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="API Key 前缀">
              <Text code>{selectedConnector.api_key_prefix || '-'}</Text>
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
              {heartbeatData.last_seen ? new Date(heartbeatData.last_seen).toLocaleString() : '无记录'}
            </Descriptions.Item>
            {/* A318：后端 heartbeat 的 latency_ms 恒为 0（connectors.py get_connector_heartbeat_status
                写死），不是实测延迟 —— 按禁伪造铁律不再把常量当测量值展示 */}
          </Descriptions>
        )}
      </Modal>

      {/* New API Key Modal */}
      <Modal
        title="API Key 已生成"
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
