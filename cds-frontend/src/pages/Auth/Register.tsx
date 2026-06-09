import { useState } from 'react';
import { Form, Input, Button, Card, Typography, Select, message, Space, Tag } from 'antd';
import { UserOutlined, LockOutlined, MailOutlined, BankOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import { authApi } from '../../services/authApi';
import { SELF_REGISTER_ROLE_OPTIONS } from '../../utils/roles';

const { Title, Text } = Typography;

export default function Register() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: { username: string; email: string; password: string; role: string; organization?: string }) => {
    setLoading(true);
    try {
      const resp = await authApi.register(values);
      login(resp.user, resp.access_token, resp.refresh_token);
      message.success('注册成功');
      navigate('/');
    } catch (err: any) {
      const detail = err?.detail;
      const msg = Array.isArray(detail)
        ? detail.map((e: any) => e.msg || e.message || JSON.stringify(e)).join('; ')
        : detail || '注册失败';
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
          <Title level={1}>可信数据空间</Title>
          <Text>Confidential Data Sandbox</Text>
        </div>
        <Space wrap className="cds-auth-tags">
          <Tag color="cyan">沙箱隔离</Tag>
          <Tag color="blue">合约控制</Tag>
          <Tag color="green">审计存证</Tag>
        </Space>
        <div className="cds-auth-status">
          <span>自助角色</span>
          <strong>数商 / 买方</strong>
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
            <Form.Item name="password" label="密码" rules={[{ required: true, min: 8, message: '密码至少8位' }]}>
              <Input.Password prefix={<LockOutlined />} placeholder="密码" />
            </Form.Item>
            <Form.Item name="role" label="角色" rules={[{ required: true, message: '请选择角色' }]}>
              <Select
                placeholder="选择角色"
                options={SELF_REGISTER_ROLE_OPTIONS}
              />
            </Form.Item>
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
