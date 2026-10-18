import { lazy } from 'react';
import { useAuthStore } from '../../stores/authStore';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { hasAnyRole, ROLE_GROUPS } from '../../utils/roles';
import { resolveWorkspace } from '../../utils/workspace';

// 原型 A-1「首页（平台总览）」是两个工台各一张：管理员工台落运营概览 Dashboard，
// 开发者工台落开发者首页（FlowDevHome，原 /home）。旧 Dashboard 对数商/买方只会渲染
// 一串被角色门隐藏掉的空卡片，所以 index 路由必须按工台分派，否则开发者点「首页」是空页。
const Dashboard = lazy(() => import('../../pages/Dashboard'));
const FlowDevHome = lazy(() => import('../../pages/Flow/DevHome'));

export default function WorkspaceHome() {
  const role = useAuthStore((s) => s.user?.role);
  const storedWorkspace = useWorkspaceStore((s) => s.workspace);
  const workspace = resolveWorkspace(role, storedWorkspace);

  // 门与 App.tsx 的 flowDevUsers 同一份（hasAnyRole 就是 RoleRoute 用的那个判定）。
  // 这里不能直接套 <RoleRoute>：它的拒止语义是 <Navigate to="/" replace/>，挂在 index 路由上
  // 等于让 '/' 跳转到 '/'——Navigate 的 effect 没有依赖数组，每次渲染都会再跳一次，会原地打转。
  // 所以够不到开发者首页的人回落管理员工台的运营概览（回落，不是 403，也不是自跳转）。
  if (workspace === 'developer' && hasAnyRole(role, ROLE_GROUPS.flowDevUsers)) {
    return <FlowDevHome />;
  }
  return <Dashboard />;
}
