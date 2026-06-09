import { useParams, useNavigate } from 'react-router-dom';
import { Descriptions, Tag, Button, Typography, Spin, Space, message } from 'antd';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { sandboxApi } from '../../services/sandboxApi';

const { Title } = Typography;

const statusColors: Record<string, string> = {
  creating: 'blue', running: 'green', paused: 'orange', terminated: 'default', error: 'red',
};

export default function SessionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: session, isLoading } = useQuery({
    queryKey: ['sandbox-session', id],
    queryFn: () => sandboxApi.get(id!),
    enabled: !!id,
  });

  const terminateMutation = useMutation({
    mutationFn: () => sandboxApi.terminate(id!),
    onSuccess: () => { message.success('已终止'); queryClient.invalidateQueries({ queryKey: ['sandbox-session', id] }); },
    onError: () => message.error('终止失败'),
  });

  if (isLoading) return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  if (!session) return <div>会话不存在</div>;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <Title level={4} style={{ margin: 0 }}>沙箱会话</Title>
          <Tag color={statusColors[session.status]}>{session.status}</Tag>
          <Tag>{session.sandbox_level}</Tag>
        </Space>
        <Space>
          {session.status === 'running' && <Button danger onClick={() => terminateMutation.mutate()}>终止</Button>}
          <Button onClick={() => navigate('/sandbox-sessions')}>返回列表</Button>
        </Space>
      </div>
      <Descriptions bordered column={2}>
        <Descriptions.Item label="会话ID">{session.id}</Descriptions.Item>
        <Descriptions.Item label="关联合约">{session.contract_id}</Descriptions.Item>
        <Descriptions.Item label="沙箱级别"><Tag>{session.sandbox_level}</Tag></Descriptions.Item>
        <Descriptions.Item label="状态"><Tag color={statusColors[session.status]}>{session.status}</Tag></Descriptions.Item>
        <Descriptions.Item label="容器ID">{session.container_id || '-'}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{new Date(session.created_at).toLocaleString()}</Descriptions.Item>
      </Descriptions>
    </div>
  );
}
