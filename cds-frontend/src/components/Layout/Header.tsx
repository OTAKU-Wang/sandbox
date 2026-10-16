import { Layout, Dropdown, Avatar, Space, Typography, Tag, Breadcrumb, Badge, Segmented } from 'antd';
import { UserOutlined, LogoutOutlined, SafetyCertificateOutlined, BellOutlined } from '@ant-design/icons';
import { useAuthStore } from '../../stores/authStore';
import { useLocation, useNavigate } from 'react-router-dom';
import { normalizeUserRole, ROLE_LABELS } from '../../utils/roles';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { canAccessAdminWorkspace, resolveWorkspace, type Workspace } from '../../utils/workspace';

const { Text } = Typography;

const routeMeta = [
  { prefix: '/data-products/create', title: '创建数据产品', trail: ['数据产品', '创建产品'] },
  { prefix: '/data-products', title: '数据产品', trail: ['数据产品'] },
  { prefix: '/data-resources', title: '数据资源', trail: ['数据资源'] },
  { prefix: '/contracts/create', title: '创建合约', trail: ['合约管理', '创建合约'] },
  { prefix: '/contracts', title: '合约管理', trail: ['合约管理'] },
  { prefix: '/notebooks', title: 'Notebook 开发', trail: ['数据流通', 'Notebook 开发'] },
  { prefix: '/workloads', title: '负载测试与发布', trail: ['数据流通', '负载测试与发布'] },
  { prefix: '/compute', title: '计算调度', trail: ['数据流通', '计算调度'] },
  { prefix: '/deliveries', title: '成果交付', trail: ['数据流通', '成果交付'] },
  { prefix: '/sandbox-sessions', title: '沙箱会话', trail: ['沙箱管理', '会话列表'] },
  { prefix: '/dev-sandbox', title: '开发沙箱', trail: ['沙箱管理', '开发沙箱'] },
  { prefix: '/output-control', title: '输出管控', trail: ['输出管控'] },
  { prefix: '/audit', title: '审计中心', trail: ['审计中心'] },
  { prefix: '/catalog', title: '数据目录', trail: ['数据目录'] },
  { prefix: '/monitoring', title: '监控中心', trail: ['监控中心'] },
  { prefix: '/identity/keys', title: '密钥管理', trail: ['身份管理', '密钥管理'] },
  { prefix: '/certificates', title: '证书管理', trail: ['身份管理', '证书管理'] },
  { prefix: '/connectors', title: '连接器管理', trail: ['连接器管理'] },
  { prefix: '/federation', title: '跨空间联邦', trail: ['跨空间联邦'] },
  { prefix: '/training', title: '训练管理', trail: ['训练管理'] },
];

function getRouteMeta(pathname: string) {
  return routeMeta.find((item) => pathname.startsWith(item.prefix)) ?? { title: '运营概览', trail: ['仪表盘'] };
}

export default function AppHeader() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();
  const meta = getRouteMeta(location.pathname);
  const role = normalizeUserRole(user?.role);
  const storedWorkspace = useWorkspaceStore((s) => s.workspace);
  const setWorkspace = useWorkspaceStore((s) => s.setWorkspace);
  const workspace = resolveWorkspace(role, storedWorkspace);

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  return (
    <Layout.Header
      className="cds-header"
    >
      <div className="cds-header-context">
        <Breadcrumb items={meta.trail.map((title) => ({ title }))} />
        <Text className="cds-page-title">{meta.title}</Text>
      </div>
      <div className="cds-header-actions">
        {canAccessAdminWorkspace(role) && (
          <Segmented
            size="small"
            value={workspace}
            onChange={(value) => setWorkspace(value as Workspace)}
            options={[
              { label: '开发者工作台', value: 'developer' },
              { label: '管理员工台', value: 'admin' },
            ]}
          />
        )}
        <Tag color="success" icon={<SafetyCertificateOutlined />}>安全在线</Tag>
        {role && <Tag color="processing">{ROLE_LABELS[role]}</Tag>}
        <Badge dot>
          <BellOutlined className="cds-header-icon" />
        </Badge>
      <Dropdown
        menu={{
          items: [
            { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: handleLogout },
          ],
        }}
      >
        <Space className="cds-user-menu">
          <Avatar icon={<UserOutlined />} size="small" />
          <Text>{user?.username || user?.email}</Text>
        </Space>
      </Dropdown>
      </div>
    </Layout.Header>
  );
}
