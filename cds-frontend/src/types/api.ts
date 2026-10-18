export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
  /** PZ-10：自助注册只能省略（默认 buyer）或 admin（bootstrap，生产被门拒绝）；
   *  其余角色由管理员在用户管理页指派（PATCH /auth/users/{id}/role）。 */
  role?: string;
  organization?: string;
}

export interface ApiError {
  detail: string;
}

import type { User } from './models';
