import { useState } from 'react';
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
  HomeOutlined,
  CodeOutlined,
  SafetyCertificateOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import type { MenuProps } from 'antd';
import { useAuthStore } from '../../stores/authStore';
import { hasAnyRole, normalizeUserRole, ROLE_GROUPS, type RoleLike } from '../../utils/roles';
import { filterMenuByWorkspace, matchMenuSelection, resolveWorkspace } from '../../utils/workspace';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { UserRole } from '../../types/enums';

type Items = NonNullable<MenuProps['items']>;

/**
 * 管理员工台菜单（原型之外，口径保持不动）：SS-18 只删掉「开发者工作台」这一项——
 * 它指向的 /home 现在重定向回 '/'，在管理员工台 '/' 就是同一张运营概览，留着等于自己指自己。
 */
function buildAdminMenu(role?: RoleLike): Items {
  const normalizedRole = normalizeUserRole(role);
  const items: Items = [{ key: '/', icon: <DashboardOutlined />, label: '首页' }];

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

  const devChildren: Items = [
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
      children: [
        { key: '/deliveries', label: 'OpenAPI导出' },
        { key: '/deliveries/dataset', label: '数据集打包' },
        { key: '/deliveries/push', label: '结果推送' },
      ],
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
      children: [{ key: '/output-control', label: '审查管线' }],
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

/**
 * 开发者工作台菜单 = 原型 4+2（逐字文案，图标同原型）：
 * 首页 / 我的开发（折叠子菜单）/ 我的结果集 / 安全管理（折叠子菜单）—— 分隔线 —— 开发指引文档 / 环境与配额说明。
 * 角色门不因原型改名而放宽：任何单一角色都凑不满 6 项（安全管理要 auditReaders，我的开发与
 * 开发指引文档要 flowDevUsers），门与 App.tsx 的路由门保持一份。
 */
function buildDeveloperMenu(role?: RoleLike): Items {
  const normalizedRole = normalizeUserRole(role);
  const items: Items = [{ key: '/', icon: <HomeOutlined />, label: '首页' }];

  const devChildren: Items = hasAnyRole(normalizedRole, ROLE_GROUPS.flowDevUsers)
    ? [
        { key: '/flow/tasks', label: '任务列表' },
        { key: '/notebooks', label: '在线调试' },
        { key: '/workloads', label: '负载与发布' },
      ]
    : [];
  if (devChildren.length > 0) {
    items.push({ key: 'dev', icon: <CodeOutlined />, label: '我的开发', children: devChildren });
  }

  // 「我的结果集」下挂三种交付方式（3-1/3-2/3-3 各对应一条路由，落地页 = OpenAPI导出）；
  // PNG 上是单行，展开为子菜单是「全面包屑+通用导航」定稿（2026-10-17）下的可达性补偿，已登记 Δ。
  if (hasAnyRole(normalizedRole, ROLE_GROUPS.contractReaders)) {
    items.push({
      key: 'results',
      icon: <DatabaseOutlined />,
      label: '我的结果集',
      children: [
        { key: '/deliveries', label: 'OpenAPI导出' },
        { key: '/deliveries/dataset', label: '数据集打包' },
        { key: '/deliveries/push', label: '结果推送' },
      ],
    });
  }

  const secChildren: Items = [
    ...(hasAnyRole(normalizedRole, ROLE_GROUPS.auditReaders) ? [{ key: '/audit', label: '操作日志' }] : []),
    ...(hasAnyRole(normalizedRole, ROLE_GROUPS.monitoringReaders)
      ? [{ key: '/compliance-disclosure', label: '合规披露' }]
      : []),
  ];
  if (secChildren.length > 0) {
    items.push({ key: 'sec', icon: <SafetyCertificateOutlined />, label: '安全管理', children: secChildren });
  }

  // 分隔线位于「安全管理」与「开发指引文档」之间（原型实测）；不给 key，
  // filterMenuByWorkspace 把无 key 的结构节点原样放过（路由白名单管不到它）。
  items.push({ type: 'divider' });

  // 灰字弱化的两项仍可点击；文案逐字照原型（不是「开发指引」「环境配额」）。
  if (hasAnyRole(normalizedRole, ROLE_GROUPS.flowDevUsers)) {
    items.push({
      key: '/flow/guide',
      icon: <ReadOutlined />,
      label: <span className="cds-menu-muted">开发指引文档</span>,
    });
  }

  if (hasAnyRole(normalizedRole, ROLE_GROUPS.sandboxReaders)) {
    items.push({
      key: '/flow/quota',
      icon: <QuestionCircleOutlined />,
      label: <span className="cds-menu-muted">环境与配额说明</span>,
    });
  }

  return items;
}

export default function Sidebar() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((s) => s.user);
  const storedWorkspace = useWorkspaceStore((s) => s.workspace);
  const workspace = resolveWorkspace(user?.role, storedWorkspace);
  // 开发者工台走原型的 4+2，管理员工台保持完整菜单；两端都再过一遍路由白名单，
  // 保证菜单不会指到不属于该工台的路由（filterMenuByWorkspace 与 WorkspaceRoute 同一口径）。
  const items =
    filterMenuByWorkspace(
      workspace === 'admin' ? buildAdminMenu(user?.role) : buildDeveloperMenu(user?.role),
      workspace,
    ) ?? [];

  // 选中态：子路由（/flow/tasks/new）按最长前缀命中，父项一起点亮。
  const { selectedKey, groupKey } = matchMenuSelection(items, location.pathname);
  // 展开态：进入子路由时自动展开所属分组（原型首页是全部折叠 = 恰好 6 行），
  // 人手动展开的分组额外记在本地 state 里，因此默认就是折叠的。
  const [toggledOpenKeys, setToggledOpenKeys] = useState<string[]>([]);
  const autoOpenKeys = groupKey && groupKey !== selectedKey ? [groupKey] : [];
  const openKeys = Array.from(new Set([...autoOpenKeys, ...toggledOpenKeys]));

  return (
    <Menu
      mode="inline"
      selectedKeys={selectedKey ? [selectedKey] : []}
      openKeys={openKeys}
      onOpenChange={(keys) => setToggledOpenKeys(keys.filter((key) => !autoOpenKeys.includes(String(key))))}
      items={items}
      onClick={({ key }) => navigate(key)}
      style={{ height: '100%', borderRight: 0 }}
    />
  );
}
