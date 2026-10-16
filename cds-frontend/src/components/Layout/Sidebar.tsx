import { Menu } from 'antd';
import {
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  CloudServerOutlined,
  AuditOutlined,
  SafetyOutlined,
  MonitorOutlined,
  ShopOutlined,
  FolderOutlined,
  GlobalOutlined,
  ExperimentOutlined,
  ApiOutlined,
  KeyOutlined,
  ClusterOutlined,
  NodeIndexOutlined,
  TeamOutlined,
  PartitionOutlined,
  ProjectOutlined,
  BookOutlined,
  ReadOutlined,
  WalletOutlined,
  ThunderboltOutlined,
  ScheduleOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import type { MenuProps } from 'antd';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, normalizeUserRole, ROLE_GROUPS, type RoleLike } from '../../utils/roles';
import { filterMenuByWorkspace, resolveWorkspace } from '../../utils/workspace';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { UserRole } from '../../types/enums';

function getMenuItems(role?: RoleLike): MenuProps['items'] {
  const normalizedRole = normalizeUserRole(role);
  const items: MenuProps['items'] = [
    { key: '/', icon: <DashboardOutlined />, label: '首页' },
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

  items.push({ key: '/data-pipeline', icon: <NodeIndexOutlined />, label: '数据管线' });

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
        { key: '/capability-matrix', label: '能力矩阵' },
        ...(hasAnyRole(normalizedRole, ROLE_GROUPS.devSandboxUsers) ? [{ key: '/dev-sandbox', label: '开发沙箱' }] : []),
      ],
    });
  }

  const devChildren: NonNullable<MenuProps['items']> = [
    ...(hasAnyRole(normalizedRole, ROLE_GROUPS.flowDevUsers)
      ? [
          { key: '/flow/tasks', icon: <ProjectOutlined />, label: '任务列表' },
          { key: '/notebooks', icon: <BookOutlined />, label: '在线调试' },
          { key: '/workloads', icon: <ThunderboltOutlined />, label: '负载与发布' },
        ]
      : []),
    ...(hasAnyRole(normalizedRole, ROLE_GROUPS.sandboxReaders)
      ? [{ key: '/compute', icon: <ScheduleOutlined />, label: '计算调度' }]
      : []),
  ];
  if (devChildren.length > 0) {
    items.push({ key: 'dev', icon: <PartitionOutlined />, label: '我的开发', children: devChildren });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.contractReaders)) {
    items.push({
      key: 'results',
      icon: <SendOutlined />,
      label: '我的结果集',
      children: [{ key: '/deliveries', label: '成果中心' }],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.flowDevUsers)) {
    items.push({ key: '/flow/guide', icon: <ReadOutlined />, label: '开发指引' });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.sandboxReaders)) {
    items.push({ key: '/flow/quota', icon: <WalletOutlined />, label: '环境配额' });
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
      label: '安全管理',
      children: [
        { key: '/audit', label: '操作日志' },
        { key: '/compliance-disclosure', label: '合规披露' },
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.monitoringReaders)) {
    items.push({
      key: 'monitoring',
      icon: <MonitorOutlined />,
      label: '监控中心',
      children: [
        { key: '/monitoring', label: '监控大盘' },
        { key: '/system-status', label: '系统状态' },
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.k8sOpsReaders)) {
    items.push({
      key: 'k8s-ops',
      icon: <ClusterOutlined />,
      label: 'K8s 运营',
      children: [
        { key: '/ops/deployments', label: '部署' },
        { key: '/ops/network-policies', label: '网络策略' },
        { key: '/ops/pvcs', label: '存储卷' },
        { key: '/ops/logs', label: '日志' },
      ],
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.identityManagers)) {
    items.push({
      key: 'identity',
      icon: <KeyOutlined />,
      label: '身份管理',
      children: [
        { key: '/identity/keys', label: '密钥管理' },
        { key: '/certificates', label: '证书管理' },
        { key: '/mpc', label: 'MPC 密钥共享' },
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

  if (normalizedRole === UserRole.ADMIN) {
    items.push({ key: '/users', icon: <TeamOutlined />, label: '用户管理' });
    items.push({ key: '/api-keys', icon: <ApiOutlined />, label: 'API 密钥' });
  }

  return items;
}

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const storedWorkspace = useWorkspaceStore((s) => s.workspace);
  const workspace = resolveWorkspace(user?.role, storedWorkspace);
  const items = filterMenuByWorkspace(getMenuItems(user?.role) ?? [], workspace);

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
