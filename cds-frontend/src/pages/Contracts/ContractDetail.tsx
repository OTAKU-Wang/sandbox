import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Descriptions, Tag, Button, Typography, Spin, Space, message, Modal, Alert, Progress, Table, Card } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { contractApi } from '../../services/contractApi';
import { authApi } from '../../services/authApi';
import { useAuthStore } from '../../stores/authStore';
import {
  generateSM2KeyPair,
  sm2Sign,
  contractSignPayload,
  loadSM2PrivateKey,
  saveSM2PrivateKey,
} from '../../utils/crypto';

const { Title, Text } = Typography;

const statusColors: Record<string, string> = {
  draft: 'default', negotiating: 'blue', signed: 'green', active: 'lime',
  suspended: 'orange', completed: 'purple', violated: 'red', breached: 'red',
  terminated: 'gray', archived: 'gray',
};

const typeLabels: Record<string, string> = {
  data_query: '数据查询', model_training: '模型训练', data_application: '数据应用',
  api_service: 'API服务', joint_compute: '联合计算', product_dev: '产品开发', data_modeling: '数据建模',
};

export default function ContractDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [signing, setSigning] = useState(false);

  const { data: contract, isLoading } = useQuery({
    queryKey: ['contract', id],
    queryFn: () => contractApi.get(id!),
    enabled: !!id,
  });

  const { data: dpBudget } = useQuery({
    queryKey: ['contract-dp-budget', id],
    queryFn: () => contractApi.getDPBudget(id!),
    enabled: !!id && contract?.status === 'active',
  });

  const signMutation = useMutation({
    mutationFn: async () => {
      if (!user || !contract) throw new Error('Missing user or contract');

      let privateKey = loadSM2PrivateKey(user.id);
      if (!privateKey) {
        const keypair = generateSM2KeyPair();
        saveSM2PrivateKey(user.id, keypair.privateKey);
        try { await authApi.generateSM2Keys(); } catch { /* continue with local signing */ }
        privateKey = keypair.privateKey;
        message.info('SM2密钥对已生成并保存在本地，请妥善保管');
      }

      const partyRole = user.id === contract.provider_id ? 'provider' : 'buyer';
      const timestamp = new Date().toISOString();
      const payload = contractSignPayload(contract.id, contract.contract_no, partyRole, timestamp);
      const signature = sm2Sign(payload, privateKey);
      return contractApi.sign(id!, signature);
    },
    onSuccess: () => {
      message.success('签署成功');
      queryClient.invalidateQueries({ queryKey: ['contract', id] });
      setSigning(false);
    },
    onError: (err: Error) => {
      message.error(`签署失败: ${err.message}`);
      setSigning(false);
    },
  });

  const activateMutation = useMutation({
    mutationFn: () => contractApi.activate(id!),
    onSuccess: () => { message.success('激活成功'); queryClient.invalidateQueries({ queryKey: ['contract', id] }); },
    onError: () => message.error('激活失败'),
  });

  const terminateMutation = useMutation({
    mutationFn: () => contractApi.terminate(id!),
    onSuccess: () => { message.success('已终止'); queryClient.invalidateQueries({ queryKey: ['contract', id] }); },
    onError: () => message.error('终止失败'),
  });

  const handleTerminate = () => {
    Modal.confirm({
      title: '确认终止合约',
      content: '终止后将撤销所有关联的沙箱会话，此操作不可逆。',
      okText: '确认终止',
      okType: 'danger',
      cancelText: '取消',
      onOk: () => terminateMutation.mutate(),
    });
  };

  if (isLoading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  if (!contract) return <div>合约不存在</div>;

  const isParty = user && (user.id === contract.provider_id || user.id === contract.buyer_id);
  const userRole = user?.id === contract.provider_id ? 'provider' : 'buyer';
  const hasProviderSigned = !!contract.provider_signed_at;
  const hasBuyerSigned = !!contract.buyer_signed_at;
  const userHasSigned = userRole === 'provider' ? hasProviderSigned : hasBuyerSigned;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <Title level={4} style={{ margin: 0 }}>{contract.title}</Title>
          <Tag color={statusColors[contract.status]}>{contract.status}</Tag>
        </Space>
        <Space>
          {/* Draft: first party signs */}
          {contract.status === 'draft' && isParty && !userHasSigned && (
            <Button type="primary" loading={signing} onClick={() => { setSigning(true); signMutation.mutate(); }}>
              SM2签署
            </Button>
          )}
          {/* Negotiating: second party signs */}
          {contract.status === 'negotiating' && isParty && !userHasSigned && (
            <Button type="primary" loading={signing} onClick={() => { setSigning(true); signMutation.mutate(); }}>
              SM2签署
            </Button>
          )}
          {/* Signed: activate */}
          {contract.status === 'signed' && isParty && (
            <Button type="primary" onClick={() => activateMutation.mutate()}>激活</Button>
          )}
          {/* Active: terminate */}
          {contract.status === 'active' && isParty && (
            <Button danger onClick={handleTerminate}>终止合约</Button>
          )}
          <Button onClick={() => navigate('/contracts')}>返回列表</Button>
        </Space>
      </div>

      {/* Signing guidance */}
      {contract.status === 'draft' && isParty && !userHasSigned && (
        <Alert
          message="合约签署说明"
          description={'点击"SM2签署"按钮将使用SM2国密算法对合约进行数字签名。首次签署会自动生成SM2密钥对，私钥安全存储在本地。双方签署后合约自动生效。'}
          type="info" showIcon style={{ marginBottom: 16 }}
        />
      )}
      {contract.status === 'negotiating' && isParty && !userHasSigned && (
        <Alert
          message="等待您签署"
          description={`对方已完成签署，请您确认合约条款后点击"SM2签署"完成双方签署。`}
          type="warning" showIcon style={{ marginBottom: 16 }}
        />
      )}

      <Descriptions bordered column={2}>
        <Descriptions.Item label="合约编号">{contract.contract_no}</Descriptions.Item>
        <Descriptions.Item label="合约类型">{typeLabels[contract.contract_type] || contract.contract_type}</Descriptions.Item>
        <Descriptions.Item label="状态"><Tag color={statusColors[contract.status]}>{contract.status}</Tag></Descriptions.Item>
        <Descriptions.Item label="数据产品数">{contract.product_ids?.length ?? 0}</Descriptions.Item>
        <Descriptions.Item label="允许沙箱级别">{contract.allowed_sandbox_levels || '-'}</Descriptions.Item>
        <Descriptions.Item label="允许沙箱模式">{contract.allowed_sandbox_modes?.join(', ') || '-'}</Descriptions.Item>
        <Descriptions.Item label="允许操作">{contract.allowed_operations || '-'}</Descriptions.Item>
        <Descriptions.Item label="DP预算(ε)">{contract.dp_epsilon_budget ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="最大输出行数">{contract.max_output_rows ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="允许输出格式">{contract.allowed_output_formats || '-'}</Descriptions.Item>
        <Descriptions.Item label="最大时长">{contract.max_duration_hours ? `${contract.max_duration_hours}小时` : '-'}</Descriptions.Item>
        <Descriptions.Item label="提供方签署">
          {hasProviderSigned ? (
            <Space><Tag color="green">已签署</Tag><Text type="secondary">{new Date(contract.provider_signed_at!).toLocaleString()}</Text></Space>
          ) : <Tag color="default">待签署</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="使用方签署">
          {hasBuyerSigned ? (
            <Space><Tag color="green">已签署</Tag><Text type="secondary">{new Date(contract.buyer_signed_at!).toLocaleString()}</Text></Space>
          ) : <Tag color="default">待签署</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">{new Date(contract.created_at).toLocaleString()}</Descriptions.Item>
        <Descriptions.Item label="合约条款" span={2}>{contract.terms ? JSON.stringify(contract.terms) : '-'}</Descriptions.Item>
      </Descriptions>

      {/* DP Budget Status */}
      {contract.status === 'active' && dpBudget && (
        <Card title="DP 隐私预算" style={{ marginTop: 16 }}>
          <Descriptions column={3}>
            <Descriptions.Item label="总预算(ε)">{dpBudget.total_epsilon}</Descriptions.Item>
            <Descriptions.Item label="已消耗(ε)">{dpBudget.consumed_epsilon.toFixed(4)}</Descriptions.Item>
            <Descriptions.Item label="剩余(ε)">{dpBudget.remaining_epsilon.toFixed(4)}</Descriptions.Item>
          </Descriptions>
          {dpBudget.total_epsilon > 0 && (
            <Progress
              percent={Math.round((dpBudget.consumed_epsilon / dpBudget.total_epsilon) * 100)}
              status={dpBudget.remaining_epsilon <= 0 ? 'exception' : 'active'}
              style={{ marginTop: 8 }}
            />
          )}
          {dpBudget.entries.length > 0 && (
            <Table
              dataSource={dpBudget.entries}
              rowKey={(r) => `${r.session_id}-${r.timestamp}`}
              size="small"
              style={{ marginTop: 16 }}
              pagination={false}
              columns={[
                { title: '操作', dataIndex: 'operation', key: 'operation' },
                { title: '消耗(ε)', dataIndex: 'epsilon_consumed', key: 'epsilon_consumed', render: (v: number) => v.toFixed(4) },
                { title: '时间', dataIndex: 'timestamp', key: 'timestamp', render: (v: string) => new Date(v).toLocaleString() },
              ]}
            />
          )}
        </Card>
      )}
    </div>
  );
}
