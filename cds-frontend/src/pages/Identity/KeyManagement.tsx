import { useState } from 'react';
import { Card, Table, Tag, Typography, Space, Button, Drawer, message, Modal, Form, Select, Timeline, Spin } from 'antd';
import { PlusOutlined, ReloadOutlined, StopOutlined, AuditOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { kmsApi, type KmsKey, type KeyAuditEntry } from '../../services/identityApi';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

const keyTypeLabels: Record<string, string> = {
  dek: 'DEK',
  session: 'Session',
  kek: 'KEK',
};

const keyTypeColors: Record<string, string> = {
  dek: 'blue',
  session: 'cyan',
  kek: 'purple',
};

const statusColors: Record<string, string> = {
  active: 'green',
  rotated: 'blue',
  revoked: 'red',
  destroyed: 'red',
};

const statusLabels: Record<string, string> = {
  active: '活跃',
  rotated: '已轮换',
  revoked: '已撤销',
  destroyed: '已销毁',
};

export default function KeyManagement() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [auditDrawerOpen, setAuditDrawerOpen] = useState(false);
  const [auditKeyId, setAuditKeyId] = useState<string | null>(null);
  const [form] = Form.useForm();

  const { data: keys, isLoading } = useQuery({
    queryKey: ['kms-keys'],
    queryFn: () => kmsApi.listKeys(),
  });

  const { data: auditLog, isLoading: auditLoading } = useQuery({
    queryKey: ['kms-key-audit', auditKeyId],
    queryFn: () => kmsApi.getKeyAudit(auditKeyId!),
    enabled: !!auditKeyId,
  });

  const createMutation = useMutation({
    mutationFn: kmsApi.createKey,
    onSuccess: () => {
      message.success('密钥创建成功');
      setCreateOpen(false);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: ['kms-keys'] });
    },
    onError: () => message.error('密钥创建失败'),
  });

  const rotateMutation = useMutation({
    mutationFn: (keyId: string) => kmsApi.rotateKey(keyId),
    onSuccess: () => {
      message.success('密钥轮换成功');
      queryClient.invalidateQueries({ queryKey: ['kms-keys'] });
    },
    onError: () => message.error('密钥轮换失败'),
  });

  const revokeMutation = useMutation({
    mutationFn: (keyId: string) => kmsApi.revokeKey(keyId),
    onSuccess: () => {
      message.success('密钥已撤销');
      queryClient.invalidateQueries({ queryKey: ['kms-keys'] });
    },
    onError: () => message.error('密钥撤销失败'),
  });

  const handleRevoke = (keyId: string) => {
    Modal.confirm({
      title: '确认撤销密钥',
      content: '撤销后该密钥将无法使用，此操作不可逆。是否继续？',
      okText: '确认撤销',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => revokeMutation.mutate(keyId),
    });
  };

  const openAuditDrawer = (keyId: string) => {
    setAuditKeyId(keyId);
    setAuditDrawerOpen(true);
  };

  const columns: ColumnsType<KmsKey> = [
    {
      title: '密钥ID',
      dataIndex: 'key_id',
      render: (v) => <Text code>{v.length > 16 ? `${v.slice(0, 16)}...` : v}</Text>,
    },
    {
      title: '密钥类型',
      dataIndex: 'key_type',
      render: (v) => <Tag color={keyTypeColors[v]}>{keyTypeLabels[v] || v}</Tag>,
    },
    { title: '算法', dataIndex: 'algorithm' },
    {
      title: '状态',
      dataIndex: 'status',
      render: (v) => <Tag color={statusColors[v]}>{statusLabels[v] || v}</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      render: (v) => (v ? new Date(v).toLocaleString() : '-'),
    },
    {
      title: '过期时间',
      dataIndex: 'expires_at',
      render: (v) => (v ? new Date(v).toLocaleString() : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_, record) => (
        <Space>
          {record.status === 'active' && (
            <>
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={rotateMutation.isPending}
                onClick={() => rotateMutation.mutate(record.key_id)}
              >
                轮换
              </Button>
              <Button
                size="small"
                danger
                icon={<StopOutlined />}
                loading={revokeMutation.isPending}
                onClick={() => handleRevoke(record.key_id)}
              >
                撤销
              </Button>
            </>
          )}
          <Button
            size="small"
            icon={<AuditOutlined />}
            onClick={() => openAuditDrawer(record.key_id)}
          >
            审计
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>密钥管理</Title>

      <Card
        title="加密密钥列表"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            创建密钥
          </Button>
        }
      >
        <Table
          columns={columns}
          dataSource={keys || []}
          rowKey="key_id"
          loading={isLoading}
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: '暂无密钥' }}
        />
      </Card>

      {/* Create Key Modal */}
      <Modal
        title="创建加密密钥"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending}
      >
        <Form form={form} layout="vertical" onFinish={(v) => createMutation.mutate(v)}>
          <Form.Item name="key_type" label="密钥类型" rules={[{ required: true, message: '请选择密钥类型' }]}>
            <Select
              options={[
                { label: 'DEK — 数据加密密钥', value: 'dek' },
                { label: 'Session — 会话密钥', value: 'session' },
                { label: 'KEK — 密钥加密密钥', value: 'kek' },
              ]}
            />
          </Form.Item>
          <Form.Item name="algorithm" label="算法" initialValue="SM4">
            <Select
              options={[
                { label: 'SM4', value: 'SM4' },
                { label: 'AES-256-GCM', value: 'AES-256-GCM' },
                { label: 'ChaCha20-Poly1305', value: 'ChaCha20-Poly1305' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Audit Drawer */}
      <Drawer
        title={`密钥审计日志 — ${auditKeyId ? auditKeyId.slice(0, 16) + '...' : ''}`}
        open={auditDrawerOpen}
        onClose={() => {
          setAuditDrawerOpen(false);
          setAuditKeyId(null);
        }}
        width={480}
      >
        {auditLoading ? (
          <Spin style={{ display: 'block', margin: '40px auto' }} />
        ) : auditLog && auditLog.length > 0 ? (
          <Timeline
            items={auditLog.map((entry: KeyAuditEntry) => ({
              children: (
                <div>
                  <Text strong>{entry.action}</Text>
                  <br />
                  <Text type="secondary">操作人: {entry.operator}</Text>
                  <br />
                  <Text type="secondary">{new Date(entry.created_at).toLocaleString()}</Text>
                  {entry.detail && (
                    <pre style={{ fontSize: 12, marginTop: 4, background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                      {JSON.stringify(entry.detail, null, 2)}
                    </pre>
                  )}
                </div>
              ),
            }))}
          />
        ) : (
          <Text type="secondary">暂无审计记录</Text>
        )}
      </Drawer>
    </div>
  );
}
