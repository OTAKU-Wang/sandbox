import { Alert, Card, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import { capabilityLabel, metaApi, type CapabilityCell } from '../services/metaApi';
import { CenteredLoading, QueryErrorAlert } from '../components/Feedback/QueryFeedback';
import SimulationBadge from '../components/Feedback/SimulationBadge';

const { Title, Text } = Typography;

interface LevelRow {
  key: string;
}

export default function CapabilityMatrix() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['capability-matrix'],
    queryFn: () => metaApi.getCapabilityMatrix(),
    staleTime: 5 * 60 * 1000,
  });

  if (isError) {
    return (
      <QueryErrorAlert error={error} message="能力矩阵加载失败" onRetry={() => { void refetch(); }} />
    );
  }
  if (isLoading || !data) {
    return <CenteredLoading tip="加载能力矩阵" />;
  }

  const levelKeys = Object.keys(data.matrix);
  const capabilityKeys = Object.keys(data.matrix[levelKeys[0]] ?? {});
  const deprecatedLabels = Object.values(data.levels)
    .filter((meta) => meta.deprecated)
    .map((meta) => meta.label);

  const columns: ColumnsType<LevelRow> = [
    {
      title: '级别',
      key: 'level',
      width: 240,
      fixed: 'left',
      render: (_, record) => {
        const meta = data.levels[record.key];
        return (
          <Space direction="vertical" size={2}>
            <Space size={6}>
              <Text strong>{meta?.label ?? record.key}</Text>
              {meta?.deprecated && <Tag>弃用观察</Tag>}
            </Space>
            <Text type="secondary" style={{ fontSize: 12 }}>{meta?.positioning}</Text>
          </Space>
        );
      },
    },
    ...capabilityKeys.map((cap) => ({
      title: capabilityLabel(cap),
      key: cap,
      width: 170,
      render: (_: unknown, record: LevelRow) => {
        const cell: CapabilityCell | undefined = data.matrix[record.key]?.[cap];
        const state = cell?.state ?? 'unsupported';
        const detail = [cell?.note, cell?.evidence ? `依据: ${cell.evidence}` : null].filter(Boolean).join('\n');
        if (state === 'planned') {
          return (
            <Tooltip title={detail || undefined}>
              <Tag color="blue">规划中</Tag>
            </Tooltip>
          );
        }
        return <SimulationBadge state={state} detail={detail || undefined} />;
      },
    })),
  ];

  return (
    <div>
      <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>级别能力矩阵</Title>
        <Text type="secondary">
          单一权威源（SS-11 PZ-13）：创建会话/合约前，在此确认该级别能做什么、是真实隔离还是模拟。
        </Text>
      </Space>

      {deprecatedLabels.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`${deprecatedLabels.join('、')} 级别已弃用（SS-10 后观察期），由 L2' (l2c) 承接；存量会话继续服务，不建议在这些级别新建会话。`}
        />
      )}

      <Table<LevelRow>
        rowKey="key"
        columns={columns}
        dataSource={levelKeys.map((key) => ({ key }))}
        pagination={false}
        scroll={{ x: 'max-content' }}
        size="middle"
      />

      <Card title="级别元数据" size="small" style={{ marginTop: 16 }}>
        <Space wrap size={12}>
          {levelKeys.map((key) => {
            const meta = data.levels[key];
            return (
              <Card key={key} size="small" style={{ width: 320, opacity: meta?.deprecated ? 0.65 : 1 }}>
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space size={8}>
                    <Text strong>{meta?.label}</Text>
                    <Tag color={meta?.deprecated ? 'default' : 'blue'}>{meta?.positioning}</Tag>
                  </Space>
                  <Text type="secondary" style={{ fontSize: 12 }}>{meta?.description}</Text>
                </Space>
              </Card>
            );
          })}
        </Space>
      </Card>
    </div>
  );
}
