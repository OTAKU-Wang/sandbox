import { Layout, Dropdown, Space, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { UserOutlined, LogoutOutlined, SwapOutlined, DownOutlined } from '@ant-design/icons';
import { useAuthStore } from '../../stores/authStore';
import { useNavigate } from 'react-router-dom';
import { normalizeUserRole, ROLE_LABELS } from '../../utils/roles';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import {
  canAccessAdminWorkspace,
  homeLandingPath,
  resolveGreetingName,
  resolveWorkspace,
  type Workspace,
} from '../../utils/workspace';

const { Text } = Typography;

export default function AppHeader() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const role = normalizeUserRole(user?.role);
  const storedWorkspace = useWorkspaceStore((s) => s.workspace);
  const setWorkspace = useWorkspaceStore((s) => s.setWorkspace);
  const workspace = resolveWorkspace(role, storedWorkspace);
  // 部署方示例机构名不写死：组织名为空时回落用户名，两者都缺时按「用户」口径。
  const greetingName = resolveGreetingName(user) || '用户';

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  // 顶栏腾出来的能力收进用户下拉：切工作台（只对能切的角色出现）+ 只读角色显示 + 退出登录。
  const menuItems: NonNullable<MenuProps['items']> = [];
  if (canAccessAdminWorkspace(role)) {
    const target: Workspace = workspace === 'admin' ? 'developer' : 'admin';
    menuItems.push({
      key: 'workspace',
      icon: <SwapOutlined />,
      label: target === 'admin' ? '切到管理员工台' : '切到开发者工作台',
      onClick: () => {
        setWorkspace(target);
        navigate(homeLandingPath(role, target));
      },
    });
  }
  if (role) {
    menuItems.push({ key: 'role', disabled: true, label: `当前角色：${ROLE_LABELS[role]}` });
  }
  menuItems.push({ key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: handleLogout });

  return (
    <Layout.Header className="cds-header">
      <div className="cds-brand">
        {/* 品牌块从 Sider 移到顶栏（原型 A-1：白字单行品牌，无英文副标题）。
            mark 真值 strict-1-home.md §8-1：19×20 的白色**描边**盾形（顶部圆、两侧下行、底部收尖）+
            中心 4×8 短竖笔，ink 范围 41,23,19,20；**没有方形底板**（上一版用 public/logo.svg
            的 30×30 圆角实心底板，与真值不符）。这里直接内联 SVG，形状一字一笔对着墨迹边界画的。
            不用 <SafetyOutlined/>：它会多一个 .anticon，而顶栏右侧只有人像+下拉两个（Header.test 钉住）。 */}
        <svg
          className="cds-brand-logo"
          viewBox="0 0 19 20"
          fill="none"
          stroke="#ffffff"
          strokeWidth="1.6"
          strokeLinejoin="round"
          aria-hidden="true"
          focusable="false"
        >
          <path d="M2.2 6.0C2.2 3.0 5.3 1.5 9.5 1.5C13.7 1.5 16.8 3.0 16.8 6.0V10.0C16.8 13.6 13.4 16.6 9.5 18.8C5.6 16.6 2.2 13.6 2.2 10.0Z" />
          <path d="M9.5 6.1V13.9" strokeWidth="3.6" />
        </svg>
        <div className="cds-brand-text">
          <span>星信可信数据空间 · 数据沙箱开发平台</span>
        </div>
      </div>
      <div className="cds-header-actions">
        <Dropdown menu={{ items: menuItems }} trigger={['click']}>
          <Space className="cds-user-menu">
            {/* 原型顶栏右侧是白色人像图标（无边框圆形底），不用 Avatar 的灰色默认底。 */}
            <UserOutlined className="cds-header-icon" />
            <Text>{`欢迎登录，${greetingName}`}</Text>
            {/* 原型的下拉箭头：antd 5 的 Dropdown 不再自动追加 caret，自己画一个。 */}
            <DownOutlined />
          </Space>
        </Dropdown>
      </div>
    </Layout.Header>
  );
}
