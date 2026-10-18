import { Button } from 'antd';
import { useAuthStore } from '../../stores/authStore';
import { useWorkspaceStore } from '../../stores/workspaceStore';
import { EmptyState } from '../Feedback/QueryFeedback';
import { canAccessAdminWorkspace, resolveWorkspace, type Workspace } from '../../utils/workspace';

// D-16（docs/defect-ledger-2026-10-16-portal-e2e.md）：双模式工作台过去只裁菜单不裁路由。
// 角色路由门（RoleRoute）本来就在，所以这不是越权洞；真实缺口是「已具备管理态角色、但人已经
// 切到开发者工作台」的账号手输 /ops/deployments 仍进得去，与原型「开发者只看 4+2」的口径不一致。
// 这一层按 resolveWorkspace(role, stored) 判定，让路由与 filterMenuByWorkspace 的菜单口径一致。
//
// 层级：RoleRoute 在外、WorkspaceRoute 在内 —— 角色不达标仍是原来的回首页语义，只有角色够格
// 而工作台不对的人才看到无权访问态；角色门本身不因这一层放宽。
//
// 工台口径：管理员工台不裁剪菜单，即开发者工台的页面在两个工台都在（admin ⊇ developer）。
// 所以 workspace="developer" 的路由今天不会拦下任何人，它钉的是归属口径（两端共用的详情子页
// 同样标 developer）；会真正 deny 的只有 workspace="admin" 的管理态专属页。

const WORKSPACE_RANK: Record<Workspace, number> = { developer: 0, admin: 1 };

const WORKSPACE_LABELS: Record<Workspace, string> = {
  developer: '开发者工作台',
  admin: '管理员工台',
};

/** 管理员工台是开发者工台的超集：需要 developer 的路由两个工台都可达。 */
function canReachWorkspaceRoute(required: Workspace, current: Workspace): boolean {
  return WORKSPACE_RANK[current] >= WORKSPACE_RANK[required];
}

interface Props {
  children: React.ReactNode;
  /** 这条路由属于哪个工作台；口径同 Sidebar 的 filterMenuByWorkspace */
  workspace: Workspace;
}

export default function WorkspaceRoute({ children, workspace }: Props) {
  const role = useAuthStore((s) => s.user?.role);
  const storedWorkspace = useWorkspaceStore((s) => s.workspace);
  const setWorkspace = useWorkspaceStore((s) => s.setWorkspace);
  const current = resolveWorkspace(role, storedWorkspace);

  if (canReachWorkspaceRoute(workspace, current)) return <>{children}</>;

  const target = WORKSPACE_LABELS[workspace];
  return (
    <EmptyState
      description={`当前工作台不含此页面：${target}专属，当前在${WORKSPACE_LABELS[current]}。`}
      action={
        canAccessAdminWorkspace(role) ? (
          <Button size="small" onClick={() => setWorkspace(workspace)}>
            切到{target}
          </Button>
        ) : undefined
      }
    />
  );
}
