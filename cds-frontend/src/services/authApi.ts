import api from './api';
import type { LoginRequest, LoginResponse, RegisterRequest } from '../types/api';
import type { User } from '../types/models';

export const authApi = {
  login: (data: LoginRequest): Promise<LoginResponse> =>
    api.post('/auth/login', data),

  register: (data: RegisterRequest): Promise<LoginResponse> =>
    api.post('/auth/register', data),

  refresh: (refreshToken: string): Promise<LoginResponse> =>
    api.post('/auth/refresh', { refresh_token: refreshToken }),

  getMe: (): Promise<User> => api.get('/auth/me'),

  generateSM2Keys: (): Promise<{ public_key: string; private_key: string; message: string }> =>
    api.post('/auth/generate-sm2-keys'),

  signData: (data: string, privateKey: string): Promise<{ signature: string }> =>
    api.post('/auth/sign-data', { data, private_key: privateKey }),
};
