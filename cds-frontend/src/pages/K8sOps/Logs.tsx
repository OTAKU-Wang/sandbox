import { useState } from 'react';
import { Card, Input, Button, Tag, Alert, Space, Typography } from 'antd';
import { SearchOutlined, FileTextOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { getOpsLogs, type LogsResponse } from '../../services/k8sOpsApi';
import { QueryErrorAlert, EmptyState } from '../../components/Feedback/QueryFeedback';

const { Text, Paragraph } = Typography;

export default function Logs() {
  const [sessionId, setSessionId] = useState('');
  const [submittedId, setSubmittedId] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery<LogsResponse>({
    queryKey: ['ops', 'logs', submittedId],
    queryFn: () => getOpsLogs(submittedId as string, 200),
    enabled: Boolean(submittedId),
  });

  const handleSearch = () => {
    const trimmed = sessionId.trim();
    if (trimmed) setSubmittedId(trimmed);
  };

  const sourceTag = data ? (
    data.source === 'k8s' ? <Tag color="cyan">K8s 实时</Tag> : <Tag>审计轨迹</Tag>
  ) : null;

  return (
    <Card
      title={
        <Space>
          <FileTextOutlined />
          <span>日志查看（Logs）</span>
        </Space>
      }
      extra={
        <Space>
          <Input.Search
            placeholder="输入会话 ID"
            allowClear
            value={sessionId}
            style={{ width: 320 }}
            enterButton={<Button icon={<SearchOutlined />}>拉取</Button>}
            onChange={(e) => setSessionId(e.target.value)}
            onSearch={handleSearch}
          />
        </Space>
      }
    >
      {error ? <QueryErrorAlert error={error} onRetry={handleSearch} /> : null}
      {data && !data.cluster_available && data.source === 'audit' ? (
        <Alert
          type="info"
          showIcon
          message="审计轨迹"
          description="K8s 集群不可用或无实时 Pod 日志，展示该会话的审计日志（非实时 stdout）。"
          style={{ marginBottom: 16 }}
        />
      ) : null}
      {!submittedId ? (
        <EmptyState description="输入会话 ID 以拉取日志（K8s 会话走实时 Pod 日志；其余回退审计轨迹）" />
      ) : (
        <div>
          <Space style={{ marginBottom: 12 }}>
            {sourceTag}
            {data?.pod_name ? <Text type="secondary">Pod: {data.pod_name}</Text> : null}
            <Text type="secondary">共 {data?.count || 0} 行</Text>
          </Space>
          <Paragraph
            style={{
              background: '#0d1117',
              color: '#c9d1d9',
              borderRadius: 8,
              padding: 16,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: 12,
              maxHeight: 560,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}
          >
            {isLoading
              ? '加载中…'
              : (data?.logs?.map((ln) => ln.line).join('\n') || '(无日志)')}
          </Paragraph>
        </div>
      )}
    </Card>
  );
}
