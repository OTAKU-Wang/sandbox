import { Menu } from 'antd';
import {
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  CloudServerOutlined,
  AuditOutlined,
  SafetyOutlined,
  MonitorOutlined,
  CodeOutlined,
  ShopOutlined,
  FolderOutlined,
  GlobalOutlined,
  ExperimentOutlined,
  ApiOutlined,
  KeyOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import type { MenuProps } from 'antd';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, normalizeUserRole, ROLE_GROUPS, type RoleLike } from '../../utils/roles';

function getMenuItems(role?: RoleLike): MenuProps['items'] {
  const normalizedRole = normalizeUserRole(role);
  const items: MenuProps['items'] = [
    { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  ];

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.productReaders)) {
    items.push({
      key: 'data-products',
      icon: <DatabaseOutlined />,
      label: '数据产品',
      children: [
        { key: '/data-products', label: '产品列表' },
        ...(hasAnyRole(normalizedRole, ROLE_GROUPS.productWriters) ? [{ key: '/data-products/create', label: '创建产品' }] : []),
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.dataResourceManagers)) {
    items.push({ key: '/data-resources', icon: <FolderOutlined />, label: '数据资源' });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.catalogReaders)) {
    items.push({ key: '/catalog', icon: <ShopOutlined />, label: '数据目录' });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.contractReaders)) {
    items.push({
      key: 'contracts',
      icon: <FileTextOutlined />,
      label: '合约管理',
      children: [
        { key: '/contracts', label: '合约列表' },
        ...(hasAnyRole(normalizedRole, ROLE_GROUPS.contractWriters) ? [{ key: '/contracts/create', label: '创建合约' }] : []),
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.sandboxReaders)) {
    items.push({
      key: 'sandbox',
      icon: <CloudServerOutlined />,
      label: '沙箱管理',
      children: [
        { key: '/sandbox-sessions', label: '会话列表' },
        ...(hasAnyRole(normalizedRole, ROLE_GROUPS.devSandboxUsers) ? [{ key: '/dev-sandbox', label: '开发沙箱' }] : []),
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.outputControlReaders)) {
    items.push({
      key: 'output-control',
      icon: <SafetyOutlined />,
      label: '输出管控',
      children: [
        { key: '/output-control', label: '审查管线' },
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.auditReaders)) {
    items.push({
      key: 'audit',
      icon: <AuditOutlined />,
      label: '审计中心',
      children: [
        { key: '/audit', label: '审计日志' },
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.monitoringReaders)) {
    items.push({ key: '/monitoring', icon: <MonitorOutlined />, label: '监控中心' });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.identityManagers)) {
    items.push({
      key: 'identity',
      icon: <KeyOutlined />,
      label: '身份管理',
      children: [
        { key: '/identity/keys', label: '密钥管理' },
        { key: '/certificates', label: '证书管理' },
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.connectorManagers)) {
    items.push({ key: '/connectors', icon: <ApiOutlined />, label: '连接器管理' });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.federationUsers)) {
    items.push({ key: '/federation', icon: <GlobalOutlined />, label: '跨空间联邦' });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.trainingUsers)) {
    items.push({ key: '/training', icon: <ExperimentOutlined />, label: '训练管理' });
  }

  return items;
}

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const items = getMenuItems(user?.role);

  const selectedKey = location.pathname;
  const openKeys = (items ?? [])
    .filter((item): item is NonNullable<typeof item> =>
      item != null && 'children' in item && !!item.children?.some((c) => selectedKey.startsWith(c?.key as string)))
    .map((item) => item!.key as string);

  return (
    <Menu
      mode="inline"
      selectedKeys={[selectedKey]}
      defaultOpenKeys={openKeys}
      items={items}
      onClick={({ key }) => navigate(key)}
      style={{ height: '100%', borderRight: 0 }}
    />
  );
}
