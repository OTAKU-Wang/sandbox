import { Alert, Card, Space, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { monitoringApi, type SecurityCapability } from '../services/monitoringApi';
import { CenteredLoading, QueryErrorAlert } from '../components/Feedback/QueryFeedback';
import SimulationBadge, { simulationStateForValue } from '../components/Feedback/SimulationBadge';

const { Title, Text } = Typography;

const RECOMMENDATION_META: Record<string, { color: string; label: string }> = {
  go: { color: 'green', label: '可发布' },
  conditional_go: { color: 'gold', label: '有条件发布' },
  no_go: { color: 'red', label: '发布阻塞' },
};

const CAPABILITY_STATUS_META: Record<string, { color: string; label: string }> = {
  verified: { color: 'green', label: '已验证' },
  configured: { color: 'blue', label: '已配置' },
  software: { color: 'gold', label: '软件/模拟' },
  not_configured: { color: 'default', label: '未配置' },
  risk: { color: 'red', label: '风险' },
};

const DISCLOSURE_FIELDS: Array<{
  key: 'signing_backend' | 'aead_algorithm' | 'trust_root' | 'chain_anchoring';
  label: string;
  description: string;
}> = [
  {
    key: 'signing_backend',
    label: '签名后端（CDS 审计签名）',
    description: 'hsm = 真实 HSM 密钥；software = 内存软件密钥对（降级路径，validate_security_config 启动时披露）。',
  },
  {
    key: 'aead_algorithm',
    label: 'AEAD 算法（静态加密）',
    description: 'Tongsuo 接线前 SM4-GCM 标签实际为 AES-256-GCM（aes_fallback，诚实降级）。',
  },
  {
    key: 'trust_root',
    label: '信任根（远程认证）',
    description: 'external = 外部信任根；local-simulated = 本地静态根（模拟，待外部根配置）。',
  },
  {
    key: 'chain_anchoring',
    label: '链锚定（审计存证）',
    description: 'CDS_BLOCKCHAIN_BACKEND 开关对应的实际锚定后端状态。',
  },
];

type DisclosureDef = (typeof DISCLOSURE_FIELDS)[number];

export default function SystemStatus() {
  const { data: posture, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['security-posture'],
    queryFn: () => monitoringApi.getSecurityPosture(),
    refetchInterval: 30_000,
  });

  if (isError) {
    return (
      <QueryErrorAlert error={error} message="系统状态加载失败" onRetry={() => { void refetch(); }} />
    );
  }
  if (isLoading || !posture) {
    return <CenteredLoading tip="加载系统状态" />;
  }

  const recommendation = RECOMMENDATION_META[posture.release_recommendation]
    ?? { color: 'default' as string, label: posture.release_recommendation };

  const disclosureColumns: ColumnsType<DisclosureDef> = [
    { title: '开关/字段', key: 'field', width: 220, render: (_, def) => def.label },
    {
      title: '当前状态',
      key: 'value',
      width: 180,
      render: (_, def) => <Text code>{posture[def.key] ?? '—'}</Text>,
    },
    {
      title: '性质',
      key: 'nature',
      width: 120,
      render: (_, def) => {
        const state = simulationStateForValue(posture[def.key]);
        return state ? <SimulationBadge state={state} /> : <Tag>未返回</Tag>;
      },
    },
    {
      title: '说明',
      key: 'description',
      render: (_, def) => <Text type="secondary" style={{ fontSize: 12 }}>{def.description}</Text>,
    },
  ];

  const capabilityColumns: ColumnsType<SecurityCapability> = [
    { title: '能力', dataIndex: 'name', key: 'name', width: 160 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (v: string) => {
        const meta = CAPABILITY_STATUS_META[v] ?? { color: 'default', label: v };
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: '风险',
      dataIndex: 'risk_level',
      key: 'risk_level',
      width: 90,
      render: (v: string) => {
        const colors: Record<string, string> = { ok: 'green', info: 'blue', warn: 'gold', critical: 'red' };
        return <Tag color={colors[v] ?? 'default'}>{v}</Tag>;
      },
    },
    {
      title: '发布门禁',
      dataIndex: 'release_gate',
      key: 'release_gate',
      width: 110,
      render: (v: string) => {
        const gates: Record<string, { color: string; label: string }> = {
          pass: { color: 'green', label: '通过' },
          conditional: { color: 'gold', label: '有条件' },
          block: { color: 'red', label: '阻塞' },
        };
        const meta = gates[v] ?? { color: 'default', label: v };
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    { title: '摘要', dataIndex: 'summary', key: 'summary', ellipsis: true },
    {
      title: '关键开关证据',
      dataIndex: 'evidence',
      key: 'evidence',
      width: 320,
      render: (items: string[]) => (
        <Space size={4} wrap>
          {(items ?? []).map((item) => (
            <Tag key={item} style={{ fontSize: 12 }}>{item}</Tag>
          ))}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>系统状态</Title>
        <Text type="secondary">
          只读视图：安全配置实时披露（validate_security_config 同源）+ 关键 CDS_* 开关状态 + 模拟路径枚举。
        </Text>
      </Space>

      <Alert
        type={recommendation.color === 'green' ? 'success' : recommendation.color === 'red' ? 'error' : 'warning'}
        showIcon
        style={{ marginBottom: 16 }}
        message={
          <Space>
            <Tag color={recommendation.color}>{recommendation.label}</Tag>
            <Text type="secondary">
              安全态势评估（{new Date(posture.generated_at).toLocaleString()}）
            </Text>
          </Space>
        }
      />

      <Card size="small" title="安全配置披露（实时状态）" style={{ marginBottom: 16 }}>
        <Table<DisclosureDef>
          rowKey="key"
          columns={disclosureColumns}
          dataSource={DISCLOSURE_FIELDS}
          pagination={false}
          size="small"
        />
      </Card>

      <Card size="small" title="能力与模拟路径枚举">
        <Table<SecurityCapability>
          rowKey="id"
          columns={capabilityColumns}
          dataSource={posture.capabilities}
          pagination={false}
          size="small"
        />
      </Card>
    </div>
  );
}
