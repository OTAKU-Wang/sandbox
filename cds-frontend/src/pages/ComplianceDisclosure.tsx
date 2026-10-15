import { Button, Card, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { complianceApi } from '../services/complianceApi';
import { monitoringApi } from '../services/monitoringApi';
import { CenteredLoading, QueryErrorAlert } from '../components/Feedback/QueryFeedback';
import SimulationBadge, { simulationStateForValue } from '../components/Feedback/SimulationBadge';

const { Title, Text } = Typography;

const DISCLOSURE_FIELDS: Array<{
  key: 'signing_backend' | 'aead_algorithm' | 'trust_root' | 'chain_anchoring';
  label: string;
  description: string;
}> = [
  {
    key: 'signing_backend',
    label: '签名后端',
    description: '审计 SM2 签名密钥来源：hsm = 真实 HSM 密钥；software = 内存软件密钥对（降级路径）。',
  },
  {
    key: 'aead_algorithm',
    label: 'AEAD 算法',
    description: 'Tongsuo helper 接线前，SM4-GCM 标签实际为 AES-256-GCM（aes_fallback）——诚实披露。',
  },
  {
    key: 'trust_root',
    label: '信任根',
    description: '远程认证签名所用信任根：external = 外部信任根；local-simulated = 本地静态根。',
  },
  {
    key: 'chain_anchoring',
    label: '链锚定',
    description: 'pg-log = PG 追加式防篡改日志（真实，非上链）；fisco-sim/ant-sim = 进程内模拟；not_configured = 未配置。',
  },
];

type DisclosureDef = (typeof DISCLOSURE_FIELDS)[number];

export default function ComplianceDisclosure() {
  const { data: posture, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['security-posture'],
    queryFn: () => monitoringApi.getSecurityPosture(),
  });
  const {
    data: report,
    isLoading: reportLoading,
    isError: reportIsError,
    error: reportError,
    refetch: refetchReport,
  } = useQuery({
    queryKey: ['compliance-quick-report', 30],
    queryFn: () => complianceApi.quickReport(30),
  });

  const disclosure: Record<DisclosureDef['key'], string | undefined> = {
    signing_backend: posture?.signing_backend ?? report?.signing_backend,
    aead_algorithm: posture?.aead_algorithm ?? report?.aead_algorithm,
    trust_root: posture?.trust_root ?? report?.trust_root,
    chain_anchoring: posture?.chain_anchoring ?? report?.chain_anchoring,
  };

  const handleExport = () => {
    const payload = {
      exported_at: new Date().toISOString(),
      source: 'CDS 合规披露页（SS-11 PZ-09）',
      disclosure,
      anchoring: report?.anchoring ?? null,
      compliance_status: report?.compliance_status ?? null,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `cds-disclosure-${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    message.success('披露数据已导出');
  };

  if (isError) {
    return (
      <QueryErrorAlert error={error} message="安全披露加载失败" onRetry={() => { void refetch(); }} />
    );
  }
  if (isLoading || reportLoading) {
    return <CenteredLoading tip="加载合规披露" />;
  }

  const disclosureColumns: ColumnsType<DisclosureDef> = [
    { title: '字段', key: 'field', width: 140, render: (_, def) => def.label },
    {
      title: '当前值',
      key: 'value',
      width: 180,
      render: (_, def) => <Text code>{disclosure[def.key] ?? '—'}</Text>,
    },
    {
      title: '状态',
      key: 'state',
      width: 120,
      render: (_, def) => {
        const state = simulationStateForValue(disclosure[def.key]);
        return state ? <SimulationBadge state={state} /> : <Tag>未返回</Tag>;
      },
    },
    {
      title: '说明',
      key: 'description',
      render: (_, def) => <Text type="secondary" style={{ fontSize: 12 }}>{def.description}</Text>,
    },
  ];

  return (
    <div>
      <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <div>
            <Title level={4} style={{ margin: 0 }}>合规披露</Title>
            <Text type="secondary">
              系统当前真实运行状态的诚实披露（PZ-06 实时计算，非硬编码）；“模拟/降级”值必须显式可见。
            </Text>
          </div>
          <Button icon={<DownloadOutlined />} onClick={handleExport}>导出披露数据</Button>
        </Space>
      </Space>

      <Card size="small" title="安全披露字段（实时状态）" style={{ marginBottom: 16 }}>
        <Table<DisclosureDef>
          rowKey="key"
          columns={disclosureColumns}
          dataSource={DISCLOSURE_FIELDS}
          pagination={false}
          size="small"
        />
      </Card>

      {reportIsError ? (
        <QueryErrorAlert error={reportError} message="合规报告加载失败" onRetry={() => { void refetchReport(); }} />
      ) : report ? (
        <>
          <Card size="small" title="链锚定披露" style={{ marginBottom: 16 }}>
            <Space size={12} wrap>
              <Text>{report.anchoring.backend_label}</Text>
              {report.anchoring.is_consortium_chain ? (
                <Tag color="green">联盟链锚定</Tag>
              ) : (
                <Tag>本地防篡改日志（非上链）</Tag>
              )}
              <Text type="secondary">后端: {report.anchoring.backend}</Text>
            </Space>
          </Card>

          <Card size="small" title="合规状态（近 30 天 · TC609）">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Space wrap size={12}>
                {Object.entries(report.compliance_status).map(([key, value]) => (
                  <Tooltip key={key} title={typeof value === 'object' ? JSON.stringify(value) : undefined}>
                    <Tag color={String(value).includes('pass') || String(value) === 'true' ? 'green' : 'gold'}>
                      {key}: {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </Tag>
                  </Tooltip>
                ))}
              </Space>
              {(report.recommendations ?? []).length > 0 && (
                <div>
                  <Text type="secondary">建议：</Text>
                  <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                    {(report.recommendations ?? []).map((item, index) => (
                      <li key={index}><Text type="secondary">{String(item)}</Text></li>
                    ))}
                  </ul>
                </div>
              )}
            </Space>
          </Card>
        </>
      ) : null}
    </div>
  );
}
