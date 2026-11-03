import api from './api';

// 团队管理与团队级配额隔离（2026-11-03 沙箱系统边界收敛 Task 4）。
// 后端 /api/v1/users/teams（human-only 门，挂在 users 之前，见 app/api/teams.py）。
// 列表走仓库惯例信封 {items,total,page,page_size}；成员是团队行的 JSON 快照，
// 所以增删成员的响应回的是「整支团队」，调用方直接用它覆盖缓存，不需要二次拉详情。

export type TeamRole = 'team_admin' | 'core_developer' | 'data_analyst' | 'viewer';

export type TeamStatus = 'active' | 'archived' | 'suspended';

export interface TeamMember {
  user_id: string;
  /** 后端 join users 下发；账号被删时为 null，由 UI 记「未知账号」而不是编一个名字。 */
  username: string | null;
  /** 团队内角色。后端按 TeamRole 校验，这里保留 string 兜底：出现未知值要显示出来而不是归类。 */
  role: TeamRole | string;
  permissions: string[];
  added_at: string | null;
  added_by: string | null;
}

export interface Team {
  id: string;
  name: string;
  org_id: string | null;
  description: string | null;
  status: TeamStatus | string;
  cpu_quota_cores: number;
  memory_quota_gb: number;
  storage_quota_gb: number;
  members: TeamMember[];
  member_count: number;
  role_permissions: Record<string, string[]>;
  created_by: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface TeamList {
  items: Team[];
  total: number;
  page: number;
  page_size: number;
}

export interface TeamListParams {
  q?: string;
  org_id?: string;
  status?: string;
  page?: number;
  page_size?: number;
}

export interface TeamCreatePayload {
  name: string;
  org_id?: string | null;
  description?: string | null;
  cpu_quota_cores?: number;
  memory_quota_gb?: number;
  storage_quota_gb?: number;
  role_permissions?: Record<string, string[]>;
}

export type TeamPatchPayload = Partial<TeamCreatePayload> & { status?: TeamStatus };

export interface TeamMemberPayload {
  user_id: string;
  role: TeamRole;
  permissions?: string[];
}

export const teamApi = {
  fetchTeams: (params?: TeamListParams): Promise<TeamList> => api.get('/users/teams', { params }),
  fetchTeam: (teamId: string): Promise<Team> => api.get(`/users/teams/${teamId}`),
  createTeam: (payload: TeamCreatePayload): Promise<Team> => api.post('/users/teams', payload),
  updateTeam: (teamId: string, payload: TeamPatchPayload): Promise<Team> =>
    api.patch(`/users/teams/${teamId}`, payload),
  addTeamMember: (teamId: string, payload: TeamMemberPayload): Promise<Team> =>
    api.post(`/users/teams/${teamId}/members`, payload),
  removeTeamMember: (teamId: string, userId: string): Promise<Team> =>
    api.delete(`/users/teams/${teamId}/members/${userId}`),
};
