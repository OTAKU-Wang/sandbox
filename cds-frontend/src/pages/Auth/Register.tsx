import { useState } from 'react';
import { Form, Input, Button, Card, Typography, message, Space, Tag } from 'antd';
import { UserOutlined, LockOutlined, MailOutlined, BankOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { authApi } from '../../services/authApi';
import { describeAuthError } from './loginError';

const { Title, Text } = Typography;

export default function Register() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; email: string; password: string; organization?: string }) => {
    setLoading(true);
    try {
      const resp = await authApi.register(values);
      login(resp.user, resp.access_token, resp.refresh_token);
      message.success('注册成功');
      navigate('/');
    } catch (err) {
      // 只取 W2 信封的 code + message（与登录页同一映射），不把 detail 或整个错误对象吐给未认证访客。
      message.error(describeAuthError(err, '注册失败'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="cds-auth-shell">
      <section className="cds-auth-info">
        <img className="cds-auth-logo" src="/logo.svg" alt="密态沙箱系统" />
        <div>
          <Title level={1}>可信数据空间</Title>
          <Text>Confidential Data Sandbox</Text>
        </div>
        <Space wrap className="cds-auth-tags">
          <Tag color="cyan">沙箱隔离</Tag>
          <Tag color="blue">合约控制</Tag>
          <Tag color="green">审计存证</Tag>
        </Space>
        <div className="cds-auth-status">
          <span>注册角色</span>
          <strong>默认买方（数商等角色由管理员指派）</strong>
        </div>
      </section>

      <main className="cds-auth-main">
        <Card className="cds-auth-card cds-auth-card-wide">
          <Space direction="vertical" size={4} style={{ width: '100%', marginBottom: 24 }}>
            <SafetyCertificateOutlined className="cds-auth-icon" />
            <Title level={3} style={{ margin: 0 }}>注册账户</Title>
            <Text type="secondary">创建 CDS 工作台身份</Text>
          </Space>
          <Form onFinish={onFinish} size="large" layout="vertical">
            <Form.Item name="username" label="用户名" rules={[
              { required: true, message: '请输入用户名' },
              { min: 3, message: '至少3个字符' },
              { pattern: /^[a-zA-Z0-9_]+$/, message: '仅限字母、数字、下划线' },
            ]}>
              <Input prefix={<UserOutlined />} placeholder="用户名" />
            </Form.Item>
            <Form.Item name="email" label="邮箱" rules={[{ required: true, type: 'email', message: '请输入有效邮箱' }]}>
              <Input prefix={<MailOutlined />} placeholder="邮箱地址" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }, { min: 8, message: '密码至少8位' }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="密码" />
            </Form.Item>
            {/* PZ-10：角色不再自助选择（后端只收省略/admin，选数商或买方必 422）——
                注册后默认买方，其余角色由管理员在用户管理页高风险指派。 */}
            <Form.Item name="organization" label="组织名称">
              <Input prefix={<BankOutlined />} placeholder="组织名称（可选）" />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" block loading={loading}>注册</Button>
            </Form.Item>
            <div style={{ textAlign: 'center' }}>
              <a onClick={() => navigate('/login')}>已有账户？去登录</a>
            </div>
          </Form>
        </Card>
      </main>
    </div>
  );
}
