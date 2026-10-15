import { useState } from 'react';
import { Alert, Button, Card, Descriptions, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, Typography, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { mpcApi, type MpcKey, type MpcShare } from '../services/mpcApi';
import SimulationBadge from '../components/Feedback/SimulationBadge';
import { useAuthStore } from '../stores/authStore';
import { normalizeUserRole } from '../utils/roles';
import { UserRole } from '../types/enums';
import { confirmHighRiskOperation } from '../utils/highRiskOperation';
import { QueryErrorAlert, tableEmpty } from '../components/Feedback/QueryFeedback';

const { Title, Text } = Typography;

const KEY_STATUS_COLORS: Record<string, string> = {
  active: 'green',
  rotated: 'orange',
  destroyed: 'red',
};

function ShareValue({ value }: { value: string }) {
  return <Text copyable={{ text: value }} style={{ fontFamily: 'monospace', fontSize: 12 }}>{value.length > 16 ? `${value.slice(0, 16)}…` : value}</Text>;
}

const shareColumns: ColumnsType<MpcShare> = [
  { title: '份额', dataIndex: 'index', key: 'index', width: 80, render: (v: number) => `#${v}` },
  { title: '持有方', dataIndex: 'holder', key: 'holder', width: 140, render: (v) => v || '—' },
  { title: '份额值', dataIndex: 'share_value', key: 'share_value', render: (v: string) => <ShareValue value={v} /> },
];

export default function MpcPage() {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const role = normalizeUserRole(user?.role);
  const canSplit = role === UserRole.OPERATOR || role === UserRole.ADMIN;
  const canManage = role === UserRole.ADMIN;

  const [splitOpen, setSplitOpen] = useState(false);
  const [splitResult, setSplitResult] = useState<MpcShare[] | null>(null);
  const [shareKey, setShareKey] = useState<string | null>(null);
  const [splitForm] = Form.useForm();

  const { data: capabilities, isError: capIsError, error: capError, refetch: refetchCap } = useQuery({
    queryKey: ['mpc-capabilities'],
    queryFn: () => mpcApi.getCapabilities(),
    staleTime: 5 * 60 * 1000,
  });

  const { data: keys, isLoading: keysLoading, isError: keysIsError, error: keysError, refetch: refetchKeys } = useQuery({
    queryKey: ['mpc-keys'],
    queryFn: () => mpcApi.listKeys(),
  });

  const {
    data: shares,
    isLoading: sharesLoading,
    isError: sharesIsError,
    error: sharesError,
    refetch: refetchShares,
  } = useQuery({
    queryKey: ['mpc-shares', shareKey],
    queryFn: () => mpcApi.getShares(shareKey as string),
    enabled: !!shareKey,
  });

  const splitMutation = useMutation({
    mutationFn: (payload: {
      secret_hex: string;
      threshold: number;
      total_shares: number;
      algorithm: string;
    }) => mpcApi.splitKey(payload),
    onSuccess: (data) => {
      setSplitResult(data);
      setSplitOpen(false);
      splitForm.resetFields();
      message.success('密钥已拆分');
      void queryClient.invalidateQueries({ queryKey: ['mpc-keys'] });
    },
    onError: () => message.error('密钥拆分失败'),
  });

  const rotateMutation = useMutation({
    mutationFn: (payload: { keyId: string; reason: string; ticket_id?: string | null }) =>
      mpcApi.rotateKey(payload.keyId, { reason: payload.reason, ticket_id: payload.ticket_id }),
    onSuccess: () => {
      message.success('密钥已轮换');
      void queryClient.invalidateQueries({ queryKey: ['mpc-keys'] });
    },
    onError: () => message.error('密钥轮换失败'),
  });

  const destroyMutation = useMutation({
    mutationFn: (payload: { keyId: string; reason: string; ticket_id?: string | null }) =>
      mpcApi.destroyKey(payload.keyId, { reason: payload.reason, ticket_id: payload.ticket_id }),
    onSuccess: () => {
      message.success('密钥已销毁');
      void queryClient.invalidateQueries({ queryKey: ['mpc-keys'] });
    },
    onError: () => message.error('密钥销毁失败'),
  });

  const handleRotate = (record: MpcKey) => {
    confirmHighRiskOperation({
      title: '确认轮换密钥',
      content: `将为密钥 ${record.key_id} 生成新份额组，旧份额立即失效。持有方必须同步更新。`,
      okText: '确认轮换',
      onConfirm: (payload) => rotateMutation.mutate({ keyId: record.key_id, ...payload }),
    });
  };

  const handleDestroy = (record: MpcKey) => {
    confirmHighRiskOperation({
      title: '确认销毁密钥',
      content: `密钥 ${record.key_id} 及其全部分额将被密码学擦除，操作不可恢复。`,
      okText: '确认销毁',
      onConfirm: (payload) => destroyMutation.mutate({ keyId: record.key_id, ...payload }),
    });
  };

  const keyColumns: ColumnsType<MpcKey> = [
    {
      title: '密钥 ID',
      dataIndex: 'key_id',
      key: 'key_id',
      render: (v: string) => <Text copyable={{ text: v }} style={{ fontFamily: 'monospace' }}>{v.length > 16 ? `${v.slice(0, 16)}…` : v}</Text>,
    },
    { title: '算法', dataIndex: 'algorithm', key: 'algorithm', width: 100 },
    {
      title: '阈值/总数',
      key: 'threshold',
      width: 110,
      render: (_, record) => `${record.threshold} / ${record.total_shares}`,
    },
    { title: '份额数', dataIndex: 'share_count', key: 'share_count', width: 90 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (v: string) => <Tag color={KEY_STATUS_COLORS[v] ?? 'default'}>{v}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 260,
      render: (_, record) => (
        <Space size={4} wrap>
          <Button size="small" onClick={() => setShareKey(record.key_id)}>份额</Button>
          {canManage && record.status !== 'destroyed' && (
            <Button size="small" onClick={() => handleRotate(record)}>轮换</Button>
          )}
          {canManage && record.status !== 'destroyed' && (
            <Button size="small" danger onClick={() => handleDestroy(record)}>销毁</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>MPC 密钥共享</Title>
            <Text type="secondary">Shamir 秘密托管（密钥分割/份额管理）；HE/MPC 计算能力为评估状态，未部署。</Text>
          </div>
          {canSplit && (
            <Button type="primary" onClick={() => setSplitOpen(true)}>拆分新密钥</Button>
          )}
        </Space>
      </Space>

      {capIsError ? (
        <QueryErrorAlert error={capError} message="MPC 能力状态加载失败" onRetry={() => { void refetchCap(); }} />
      ) : capabilities ? (
        <Card size="small" title="能力披露（N11 诚实边界）" style={{ marginBottom: 16 }}>
          <Descriptions size="small" column={2}>
            <Descriptions.Item label="运行时模式">
              <Space size={8}><SimulationBadge state="real" label="真实（已部署）" /><Text>{capabilities.label}</Text></Space>
            </Descriptions.Item>
            <Descriptions.Item label="HE/MPC 计算">
              <Space size={8}><Tag color="blue">评估/规划中</Tag><Text type="secondary">{capabilities.backend_label}</Text></Space>
            </Descriptions.Item>
            <Descriptions.Item label="说明" span={2}>
              <Text type="secondary">{capabilities.note}</Text>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      ) : null}

      {keysIsError ? (
        <QueryErrorAlert error={keysError} message="MPC 密钥列表加载失败" onRetry={() => { void refetchKeys(); }} />
      ) : (
        <Card size="small" title="托管密钥">
          <Table<MpcKey>
            rowKey="key_id"
            columns={keyColumns}
            dataSource={keys ?? []}
            loading={keysLoading}
            pagination={false}
            size="small"
            locale={tableEmpty('暂无托管密钥' + (canSplit ? '，可点击右上角「拆分新密钥」创建' : ''))}
          />
        </Card>
      )}

      <Modal
        title="拆分新密钥（Shamir 秘密共享）"
        open={splitOpen}
        onCancel={() => setSplitOpen(false)}
        onOk={() => splitForm.submit()}
        confirmLoading={splitMutation.isPending}
        okText="拆分"
        width={560}
      >
        <Form
          form={splitForm}
          layout="vertical"
          initialValues={{ threshold: 3, total_shares: 5, algorithm: 'sm4' }}
          onFinish={(values) => splitMutation.mutate(values)}
        >
          <Form.Item
            name="secret_hex"
            label="密钥（hex）"
            rules={[
              { required: true, message: '请输入密钥' },
              { pattern: /^[0-9a-fA-F]+$/, message: '必须为 hex 字符串' },
            ]}
          >
            <Input.TextArea rows={2} placeholder="如 00112233445566778899aabbccddeeff" />
          </Form.Item>
          <Space style={{ width: '100%' }} size={12} wrap>
            <Form.Item
              name="threshold"
              label="阈值 t"
              rules={[
                { required: true },
                ({ getFieldValue }) => ({
                  validator: (_, value) =>
                    value && getFieldValue('total_shares') && value > getFieldValue('total_shares')
                      ? Promise.reject(new Error('阈值 t 不能大于总份额 n'))
                      : Promise.resolve(),
                }),
              ]}
            >
              <InputNumber min={2} max={20} />
            </Form.Item>
            <Form.Item name="total_shares" label="总份额 n" rules={[{ required: true }]}>
              <InputNumber min={2} max={50} />
            </Form.Item>
            <Form.Item name="algorithm" label="算法" rules={[{ required: true }]}>
              <Select
                style={{ width: 120 }}
                options={[
                  { label: 'SM4', value: 'sm4' },
                  { label: 'AES-256', value: 'aes256' },
                ]}
              />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      <Modal
        title="拆分成功 — 请线下分发份额给各持有方"
        open={!!splitResult}
        onCancel={() => setSplitResult(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => setSplitResult(null)}>关闭</Button>,
        ]}
        width={680}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="任一份额价值相等：泄露份额数达到阈值 t 即可重建秘密。份额请勿与密钥原文同介质存放。"
        />
        <Table<MpcShare>
          rowKey="share_id"
          size="small"
          pagination={false}
          dataSource={splitResult ?? []}
          columns={shareColumns}
        />
      </Modal>

      <Modal
        title={`密钥份额（${shareKey ? `${shareKey.slice(0, 12)}…` : ''}）`}
        open={!!shareKey}
        onCancel={() => setShareKey(null)}
        footer={null}
        width={680}
      >
        {sharesIsError && (
          <QueryErrorAlert error={sharesError} message="份额加载失败" onRetry={() => { void refetchShares(); }} />
        )}
        <Table<MpcShare>
          rowKey="share_id"
          size="small"
          pagination={false}
          loading={sharesLoading}
          dataSource={shares ?? []}
          columns={shareColumns}
        />
      </Modal>
    </div>
  );
}
