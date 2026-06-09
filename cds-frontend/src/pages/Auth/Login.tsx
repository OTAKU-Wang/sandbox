import { useState } from 'react';
import { Form, Input, Button, Card, Typography, message } from 'antd';
import { UserOutlined, LockOutlined } from '@ant-design/icons';
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
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#f0f2f5' }}>
      <Card style={{ width: 420 }}>
        <Title level={3} style={{ textAlign: 'center' }}>密态沙箱系统</Title>
        <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginBottom: 24 }}>
          可信数据空间 · 安全数据流通
        </Text>
        <Form onFinish={onFinish} size="large">
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
    </div>
  );
}
