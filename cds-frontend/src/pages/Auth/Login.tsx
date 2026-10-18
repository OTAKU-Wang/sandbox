import { useState } from 'react';
import { Form, Input, Button, Card, Typography, message, Space, Tag } from 'antd';
import { UserOutlined, LockOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/authStore';
import { authApi } from '../../services/authApi';
import api from '../../services/api';
import { describeAuthError } from './loginError';

const { Title, Text } = Typography;


export default function Login() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);
  // 登录前无 JWT：消费公开矩阵面（/health 能力区，SS-11 PZ-13）；/api/v1/meta 需认证。
  const { data: health } = useQuery({
    queryKey: ['health-capability'],
    queryFn: async () => {
      const resp = (await api.get('/health', { baseURL: '' })) as { capability?: { levels?: string[] } };
      if (!Array.isArray(resp?.capability?.levels)) {
        // 诚实降级：chip 区继续静默（登录前不打扰访客），但字段缺失/改名必须在控制台留痕，
        // 否则后端重命名 capability.levels 会让安全级 chip 静悄悄消失、无人发现。
        console.warn('[cds] /health 未下发 capability.levels，安全域 chip 已隐藏（能力字段缺失或已改名）');
      }
      return resp;
    },
    staleTime: 60 * 1000,
  });
  const levels = health?.capability?.levels;
  const capabilityLevels = Array.isArray(levels) ? levels : [];

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const resp = await authApi.login(values);
      login(resp.user, resp.access_token, resp.refresh_token);
      message.success('登录成功');
      navigate('/');
    } catch (err) {
      message.error(describeAuthError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="cds-auth-shell">
      <section className="cds-auth-info">
        <img className="cds-auth-logo" src="/logo.svg" alt="密态沙箱系统" />
        <div>
          <Title level={1}>密态沙箱系统</Title>
          <Text>Confidential Data Sandbox</Text>
        </div>
        <Space wrap className="cds-auth-tags">
          <Tag color="cyan">TEE</Tag>
          <Tag color="blue">DLP</Tag>
          <Tag color="green">DP</Tag>
          <Tag color="gold">SM2</Tag>
        </Space>
        <div className="cds-auth-status">
          <span>安全域</span>
          {capabilityLevels.length > 0 && (
            <strong>{capabilityLevels.map((level) => (level === 'l2c' ? "L2'" : level)).join(' / ')}</strong>
          )}
        </div>
      </section>

      <main className="cds-auth-main">
        <Card className="cds-auth-card">
          <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 24 }}>
            <SafetyCertificateOutlined className="cds-auth-icon" />
            <Title level={3} style={{ margin: 0 }}>账户登录</Title>
            <Text type="secondary">进入密态沙箱工作台</Text>
          </Space>
          <Form onFinish={onFinish} size="large" layout="vertical">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }, { min: 8, message: '密码至少8位' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>登录</Button>
          </Form.Item>
          <div style={{ textAlign: 'center' }}>
            <a onClick={() => navigate('/register')}>注册新账户</a>
          </div>
        </Form>
        </Card>
      </main>
    </div>
  );
}
