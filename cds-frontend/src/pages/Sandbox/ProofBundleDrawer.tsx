import { useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Drawer, Space, Spin, Tag, Typography, message,
} from 'antd';
import { DownloadOutlined, FileProtectOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { sandboxApi, type SessionProofBundle } from '../../services/sandboxApi';
import { getErrorMessage } from '../../components/Feedback/QueryFeedback';
import SimulationBadge, { simulationStateForValue } from '../../components/Feedback/SimulationBadge';

const { Text } = Typography;

const proofLevelLabels: Record<string, string> = {
  hardware_tee: '硬件 TEE',
  software_confidential: '软件密态',
  runtime_isolation: '运行时隔离',
};
const proofLevelColors: Record<string, string> = {
  hardware_tee: 'green',
  software_confidential: 'orange',
  runtime_isolation: 'blue',
};

/** 证明包抽屉（PZ-16 ⑤）：一键生成/下载 + 披露分组
 *  （bundle_meta 披露层 vs evidence 证据层，与 PZ-14 抽屉口径一致：
 *   bundle_meta 不参与 evidence_hash，evidence 字段参与）。 */
export default function ProofBundleDrawer({
  sessionId,
  open,
  onClose,
}: {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}) {
  const { data: bundle, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['sandbox-session-proof-bundle', sessionId],
    queryFn: () => sandboxApi.getProofBundle(sessionId),
    enabled: open,
  });
  const [downloading, setDownloading] = useState(false);

  const download = (b: SessionProofBundle) => {
    setDownloading(true);
    try {
      const blob = new Blob([JSON.stringify(b, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `cds-session-proof-${sessionId}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      message.success('证明包已下载');
    } finally {
      setDownloading(false);
    }
  };

  // 一键生成并下载：已加载直接下载，未加载先取再下（单一按钮完成 生成→下载）
  const handleOneClick = async () => {
    if (bundle) {
      download(bundle);
      return;
    }
    setDownloading(true);
    try {
      const data = await refetch();
      if (data.data) download(data.data);
    } catch (e) {
      message.error(getErrorMessage(e, '证明包生成失败'));
    } finally {
      setDownloading(false);
    }
  };

  const bundleMeta = (bundle as (SessionProofBundle & { bundle_meta?: Record<string, unknown> }) | undefined)?.bundle_meta;

  return (
    <Drawer
      title="会话证明包"
      open={open}
      onClose={onClose}
      width={760}
      extra={
        <Space>
          {bundle && (
            <Button icon={<DownloadOutlined />} loading={downloading} onClick={() => download(bundle)}>
              下载 JSON
            </Button>
          )}
          <Button
            type="primary"
            icon={<FileProtectOutlined />}
            loading={downloading || isLoading}
            onClick={() => void handleOneClick()}
          >
            生成并下载
          </Button>
        </Space>
      }
    >
      {isLoading && !bundle && (
        <Spin size="large" style={{ display: 'block', margin: '80px auto' }} />
      )}
      {isError && (
        <Alert
          type="error"
          showIcon
          message="证明包加载失败"
          description={getErrorMessage(error, '未知错误')}
          action={
            <Button size="small" onClick={() => void refetch()}>
              重试
            </Button>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      {bundle ? (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card size="small" title="披露元数据（bundle_meta · 不参与 evidence_hash）">
            {bundleMeta && Object.keys(bundleMeta).length > 0 ? (
              <Space wrap size={[8, 8]}>
                {Object.entries(bundleMeta).map(([key, value]) => {
                  const text = value === null ? 'none' : value === undefined ? '-' : String(value);
                  const state = value === null ? 'unsupported' : (simulationStateForValue(text) ?? 'real');
                  return (
                    <SimulationBadge
                      key={key}
                      state={state}
                      label={`${key}: ${text}`}
                      detail="证明包披露元数据（PZ-14）：仅随包披露真实运行状态，不参与 evidence_hash。"
                    />
                  );
                })}
              </Space>
            ) : (
              <Text type="secondary">本证明包未携带 bundle_meta 披露字段。</Text>
            )}
          </Card>
          <Card size="small" title="证据字段（evidence · 参与 evidence_hash）">
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="证明级别">
                <Tag color={proofLevelColors[bundle.runtime.proof_level] || 'default'}>
                  {proofLevelLabels[bundle.runtime.proof_level] || bundle.runtime.proof_level}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Bundle Hash">
                <Text code copyable>{bundle.integrity.bundle_hash}</Text>
              </Descriptions.Item>
              {bundle.integrity.evidence_hash && (
                <Descriptions.Item label="Evidence Hash">
                  <Text code copyable>{bundle.integrity.evidence_hash}</Text>
                </Descriptions.Item>
              )}
              <Descriptions.Item label="策略 Hash">
                <Text code copyable>{bundle.policy.combined_policy_hash || '-'}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Attestation">
                <Space direction="vertical" size={2}>
                  <Text>类型：{String(bundle.runtime.attestation.type || '-')}</Text>
                  <Text code copyable={!!bundle.runtime.attestation.quote_hash}>
                    quote_hash: {bundle.runtime.attestation.quote_hash || '-'}
                  </Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="输出审查">
                {bundle.output_security.available ? (
                  <Space direction="vertical" size={2}>
                    <Space wrap>
                      <Tag color={bundle.output_security.blocked ? 'red' : 'green'}>
                        {bundle.output_security.blocked ? '已阻断' : '已放行'}
                      </Tag>
                      <Tag>发现 {bundle.output_security.findings_count ?? 0} 项</Tag>
                    </Space>
                    {bundle.output_security.signature && (
                      <Text code copyable>{bundle.output_security.signature}</Text>
                    )}
                    {bundle.output_security.report_hash && (
                      <Text code copyable>report_hash: {bundle.output_security.report_hash}</Text>
                    )}
                  </Space>
                ) : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="审计事件">{bundle.audit.event_count ?? 0}</Descriptions.Item>
            </Descriptions>
          </Card>
          <Card size="small" title="JSON">
            <pre className="cds-json-block">{JSON.stringify(bundle, null, 2)}</pre>
          </Card>
        </Space>
      ) : (
        !isLoading && (
          <Alert type="info" showIcon message="暂无证明包" description="可点击右上「生成并下载」获取。" />
        )
      )}
    </Drawer>
  );
}
