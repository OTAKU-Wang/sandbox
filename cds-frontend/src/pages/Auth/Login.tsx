import { useState } from 'react';
import { Form, Input, Button, Card, Typography, message, Space, Tag } from 'antd';
import { UserOutlined, LockOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { authApi } from '../../services/authApi';

const { Title, Text } = Typography;

export default function Login() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      const resp = await authApi.login(values);
      login(resp.user, resp.access_token, resp.refresh_token);
      message.success('登录成功');
      navigate('/');
    } catch (err: any) {
      const detail = err?.detail;
      const msg = Array.isArray(detail)
        ? detail.map((e: any) => e.msg || e.message || JSON.stringify(e)).join('; ')
        : detail || '登录失败';
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="cds-auth-shell">
      <section className="cds-auth-info">
        <div className="cds-auth-brandmark">CDS</div>
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
          <strong>L1 / L2 / L3</strong>
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
          <Form.Item name="password" rules={[{ required: true, min: 8, message: '密码至少8位' }]}>
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
