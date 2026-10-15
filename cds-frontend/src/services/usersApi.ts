import api from './api';

export interface UserOption {
  id: string;
  username: string;
  role: string;
  organization: string | null;
}

export const usersApi = {
  options: (params?: { role?: string; q?: string; limit?: number }): Promise<UserOption[]> =>
    api.get('/users/options', { params }),
  // PZ-09③: 角色变更（ADMIN 专属，reason+ticket_id 高危留痕）
  // 后端路由在 auth router（/api/v1/auth/users/{id}/role，PZ-10）；
  // users router 仅有 GET /options —— 不要改为 /users/...（会 404）。
  changeRole: (
    userId: string,
    payload: { role: string; reason: string; ticket_id?: string | null },
  ): Promise<unknown> => api.patch(`/auth/users/${userId}/role`, payload),
};
