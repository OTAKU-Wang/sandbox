import { useState } from 'react';
import { Card, Table, Tag, Typography, Space, Button, Drawer, Descriptions, message, Modal, Form, Input, InputNumber, Select, Result, Spin } from 'antd';
import { PlusOutlined, StopOutlined, EyeOutlined, CheckCircleOutlined } from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { certificateApi, type Certificate, type CertificateDetail, type ChainVerifyResult } from '../../services/identityApi';
import { confirmHighRiskOperation } from '../../utils/highRiskOperation';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  active: 'green',
  revoked: 'red',
  expired: 'orange',
};

const statusLabels: Record<string, string> = {
  active: '有效',
  revoked: '已撤销',
  expired: '已过期',
};

export default function Certificates() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [detailDrawerOpen, setDetailDrawerOpen] = useState(false);
  const [selectedCertId, setSelectedCertId] = useState<string | null>(null);
  const [verifyResult, setVerifyResult] = useState<ChainVerifyResult | null>(null);
  const [verifyModalOpen, setVerifyModalOpen] = useState(false);
  const [form] = Form.useForm();

  const { data: certificates, isLoading } = useQuery({
    queryKey: ['certificates'],
    queryFn: () => certificateApi.listCertificates(),
  });

  const { data: certDetail, isLoading: detailLoading } = useQuery({
    queryKey: ['certificate-detail', selectedCertId],
    queryFn: () => certificateApi.getCertificate(selectedCertId!),
    enabled: !!selectedCertId && detailDrawerOpen,
  });

  const generateMutation = useMutation({
    mutationFn: certificateApi.generateCertificate,
    onSuccess: () => {
      message.success('证书生成成功');
      setCreateOpen(false);
      form.resetFields();
      queryClient.invalidateQueries({ queryKey: ['certificates'] });
    },
    onError: () => message.error('证书生成失败'),
  });

  const revokeMutation = useMutation({
    mutationFn: (payload: { id: string; reason: string; ticket_id?: string | null }) =>
      certificateApi.revokeCertificate(payload.id, { reason: payload.reason, ticket_id: payload.ticket_id }),
    onSuccess: () => {
      message.success('证书已撤销');
      queryClient.invalidateQueries({ queryKey: ['certificates'] });
    },
    onError: () => message.error('证书撤销失败'),
  });

  const verifyMutation = useMutation({
    mutationFn: (id: string) => certificateApi.verifyChain(id),
    onSuccess: (data) => {
      setVerifyResult(data);
      setVerifyModalOpen(true);
    },
    onError: () => message.error('证书链验证失败'),
  });

  const handleRevoke = (id: string) => {
    confirmHighRiskOperation({
      title: '确认撤销证书',
      content: '撤销后该证书将无法使用，此操作不可逆。是否继续？',
      okText: '确认撤销',
      onConfirm: (payload) => revokeMutation.mutate({ id, ...payload }),
    });
  };

  const openDetail = (id: string) => {
    setSelectedCertId(id);
    setDetailDrawerOpen(true);
  };

  const columns: ColumnsType<Certificate> = [
    { title: '主题', dataIndex: 'subject', ellipsis: true },
    { title: '颁发者', dataIndex: 'issuer', ellipsis: true },
    {
      title: '序列号',
      dataIndex: 'serial_number',
      render: (v) => <Text code>{v.length > 20 ? `${v.slice(0, 20)}...` : v}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      render: (v) => <Tag color={statusColors[v]}>{statusLabels[v] || v}</Tag>,
    },
    {
      title: '生效时间',
      dataIndex: 'valid_from',
      render: (v) => (v ? new Date(v).toLocaleString() : '-'),
    },
    {
      title: '过期时间',
      dataIndex: 'valid_to',
      render: (v) => (v ? new Date(v).toLocaleString() : '-'),
    },
    { title: '算法', dataIndex: 'algorithm' },
    {
      title: '操作',
      key: 'action',
      width: 280,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(record.id)}>
            详情
          </Button>
          <Button
            size="small"
            icon={<CheckCircleOutlined />}
            loading={verifyMutation.isPending}
            onClick={() => verifyMutation.mutate(record.id)}
          >
            验证链
          </Button>
          {record.status === 'active' && (
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              loading={revokeMutation.isPending}
              onClick={() => handleRevoke(record.id)}
            >
              撤销
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={4}>证书管理</Title>

      <Card
        title="SM2 证书列表"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            生成证书
          </Button>
        }
      >
        <Table
          columns={columns}
          dataSource={certificates || []}
          rowKey="id"
          loading={isLoading}
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: '暂无证书' }}
        />
      </Card>

      {/* Generate Certificate Modal */}
      <Modal
        title="生成SM2证书"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={generateMutation.isPending}
      >
        <Form form={form} layout="vertical" onFinish={(v) => generateMutation.mutate(v)}>
          <Form.Item
            name="subject"
            label="证书主题 (Subject)"
            rules={[{ required: true, message: '请输入证书主题' }]}
          >
            <Input placeholder="CN=example,O=CDS,C=CN" />
          </Form.Item>
          <Form.Item name="cert_type" label="证书用途" initialValue="signing">
            <Select
              options={[
                { label: '签名证书', value: 'signing' },
                { label: '加密证书', value: 'encryption' },
              ]}
            />
          </Form.Item>
          <Form.Item name="organization" label="组织名称">
            <Input placeholder="CDS Provider" />
          </Form.Item>
          <Form.Item name="validity_days" label="有效期 (天)" initialValue={365}>
            <InputNumber min={1} max={3650} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      {/* Certificate Detail Drawer */}
      <Drawer
        title="证书详情"
        open={detailDrawerOpen}
        onClose={() => {
          setDetailDrawerOpen(false);
          setSelectedCertId(null);
        }}
        width={560}
      >
        {detailLoading ? (
          <Spin style={{ display: 'block', margin: '40px auto' }} />
        ) : certDetail ? (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="主题">{certDetail.subject}</Descriptions.Item>
            <Descriptions.Item label="颁发者">{certDetail.issuer}</Descriptions.Item>
            <Descriptions.Item label="序列号">
              <Text code>{certDetail.serial_number}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="指纹">
              <Text code>{certDetail.fingerprint}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={statusColors[certDetail.status]}>{statusLabels[certDetail.status]}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="算法">{certDetail.algorithm}</Descriptions.Item>
            <Descriptions.Item label="生效时间">
              {new Date(certDetail.valid_from).toLocaleString()}
            </Descriptions.Item>
            <Descriptions.Item label="过期时间">
              {new Date(certDetail.valid_to).toLocaleString()}
            </Descriptions.Item>
            <Descriptions.Item label="证书PEM">
              <Text code copyable style={{ fontSize: 11, wordBreak: 'break-all' }}>
                {certDetail.cert_pem || certDetail.public_key}
              </Text>
            </Descriptions.Item>
            {certDetail.key_pem && (
              <Descriptions.Item label="私钥PEM">
                <Text code copyable type="danger" style={{ fontSize: 11, wordBreak: 'break-all' }}>
                  {certDetail.key_pem}
                </Text>
              </Descriptions.Item>
            )}
            <Descriptions.Item label="创建时间">
              {new Date(certDetail.created_at).toLocaleString()}
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Text type="secondary">无法加载证书详情</Text>
        )}
      </Drawer>

      {/* Chain Verify Result Modal */}
      <Modal
        title="证书链验证结果"
        open={verifyModalOpen}
        onCancel={() => {
          setVerifyModalOpen(false);
          setVerifyResult(null);
        }}
        footer={
          <Button onClick={() => { setVerifyModalOpen(false); setVerifyResult(null); }}>关闭</Button>
        }
        width={520}
      >
        {verifyResult && (
          <div>
            <Result
              status={verifyResult.valid ? 'success' : 'error'}
              title={verifyResult.valid ? '证书链验证通过' : '证书链验证失败'}
              subTitle={verifyResult.valid ? '所有证书链节点均有效' : `发现 ${verifyResult.errors.length} 个错误`}
            />
            {verifyResult.chain && verifyResult.chain.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Text strong>证书链详情:</Text>
                <Table
                  dataSource={verifyResult.chain}
                  rowKey={(_, i) => String(i)}
                  pagination={false}
                  size="small"
                  style={{ marginTop: 8 }}
                  columns={[
                    { title: '主题', dataIndex: 'subject' },
                    { title: '颁发者', dataIndex: 'issuer' },
                    {
                      title: '状态',
                      dataIndex: 'valid',
                      render: (v: boolean) => (
                        <Tag color={v ? 'green' : 'red'}>{v ? '有效' : '无效'}</Tag>
                      ),
                    },
                  ]}
                />
              </div>
            )}
            {verifyResult.errors && verifyResult.errors.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Text strong type="danger">错误信息:</Text>
                <ul style={{ marginTop: 8 }}>
                  {verifyResult.errors.map((err, i) => (
                    <li key={i}><Text type="danger">{err}</Text></li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}
