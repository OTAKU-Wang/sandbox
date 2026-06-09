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
  role: string;
  organization?: string;
}

export interface ApiError {
  detail: string;
}

import type { User } from './models';
