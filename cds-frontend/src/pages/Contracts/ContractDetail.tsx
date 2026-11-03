import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Descriptions, Tag, Button, Typography, Space, message, Alert, Progress, Table, Card } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { contractApi, DP_LEDGER_ENTRY_LIMIT } from '../../services/contractApi';
import { authApi } from '../../services/authApi';
import { useAuthStore } from '../../stores/authStore';
import { CenteredLoading, EmptyState, QueryErrorAlert } from '../../components/Feedback/QueryFeedback';
import {
  sm2Sign,
  contractSignPayload,
  loadSM2PrivateKey,
  saveSM2PrivateKey,
  SM2_KEY_STORAGE_DISCLOSURE,
} from '../../utils/crypto';
import { confirmHighRiskOperation } from '../../utils/highRiskOperation';
import FlowConfigCard from '../Flow/FlowConfigCard';
import { describeFlowError } from '../../services/flow';
import type { ContractWithFlow } from '../../services/flow';

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

/**
 * A317：DP 消费流水的截断披露口径。后端只回最近 20 条（app/api/contracts.py:302
 * `for e in ledger[:20]`），响应体里既没 total 也没 has_more，所以满页就是被截断的
 * 信号；上限本身钉在 contractApi 的 DP_LEDGER_ENTRY_LIMIT，页面不许自己拿魔数。禁把 20 行当全量台账。
 */
const DP_LEDGER_TRUNCATION_NOTE =
  `当前流水列表展示最近 ${DP_LEDGER_ENTRY_LIMIT} 条消费记录；如需核验完整全量流水明细，请前往「操作日志」中按时间范围检索。`;

export default function ContractDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [signing, setSigning] = useState(false);

  const { data: contract, isLoading, isError, error, refetch } = useQuery({
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
        // 验签读服务端登记的公钥（user.sm2_certificate），它只由该端点写入，
        // 因此私钥必须用同一次调用的返回值，本地自生密钥对验不过。
        const keypair = await authApi.generateSM2Keys();
        saveSM2PrivateKey(user.id, keypair.private_key);
        privateKey = keypair.private_key;
        message.info('SM2 密钥对已由服务端安全签发，并已在当前浏览器工作区就绪，可直接执行国密签署。');
      }

      const partyRole = user.id === contract.provider_id ? 'provider' : 'buyer';
      const timestamp = new Date().toISOString();
      const payload = contractSignPayload(
        contract.id,
        contract.contract_no,
        partyRole,
        timestamp,
        contract.purpose,
        contract.purpose_scope,
      );
      const signature = sm2Sign(payload, privateKey);
      return contractApi.sign(id!, signature, timestamp);
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

  // A316：失败提示必须带出后端 W2 信封里的原因（detail 优先、次 message，末尾附错误码），
  // 固定「激活失败」四个字让运维只能来逐个问人；口径与签署路径一致，走 flow.ts 的 describeFlowError。
  const activateMutation = useMutation({
    mutationFn: () => contractApi.activate(id!),
    onSuccess: () => { message.success('激活成功'); queryClient.invalidateQueries({ queryKey: ['contract', id] }); },
    onError: (error: unknown) => message.error(`激活失败: ${describeFlowError(error, '后端未返回具体原因')}`),
  });

  const terminateMutation = useMutation({
    mutationFn: (payload: { reason: string; ticket_id?: string | null }) => contractApi.terminate(id!, payload),
    onSuccess: () => { message.success('已终止'); queryClient.invalidateQueries({ queryKey: ['contract', id] }); },
    // A316：同上——终止被拒（状态机拒绝/无权）时把后端原因透出来，不只说「终止失败」。
    onError: (error: unknown) => message.error(`终止失败: ${describeFlowError(error, '后端未返回具体原因')}`),
  });

  const handleTerminate = () => {
    confirmHighRiskOperation({
      title: '确认终止合约',
      content: '终止后将撤销所有关联的沙箱会话，此操作不可逆。',
      okText: '确认终止',
      onConfirm: (payload) => terminateMutation.mutate(payload),
    });
  };

  if (isLoading) return <CenteredLoading tip="加载合约详情" />;
  if (isError) {
    return <QueryErrorAlert error={error} message="合约详情加载失败" onRetry={() => { void refetch(); }} />;
  }
  if (!contract) {
    return (
      <EmptyState
        description="合约不存在或当前账号无权访问"
        action={<Button onClick={() => navigate('/contracts')}>返回合约列表</Button>}
      />
    );
  }

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
          description={`点击"SM2签署"按钮将使用SM2国密算法对合约进行数字签名。首次签署若本地无密钥，会向服务端申请一对 SM2 密钥并保存在本浏览器。${SM2_KEY_STORAGE_DISCLOSURE}双方签署后合约自动生效。`}
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

      <FlowConfigCard contract={contract as unknown as ContractWithFlow} />

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
            <>
              {/* A317：满页 = 被后端截断，必须显式说出来，不让 20 行当全量台账。 */}
              {dpBudget.entries.length >= DP_LEDGER_ENTRY_LIMIT && (
                <div data-testid="dp-budget-truncated" style={{ marginTop: 12 }}>
                  <Text type="secondary">{DP_LEDGER_TRUNCATION_NOTE}</Text>
                </div>
              )}
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
            </>
          )}
        </Card>
      )}
    </div>
  );
}
