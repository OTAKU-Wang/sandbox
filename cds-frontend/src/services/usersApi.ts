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
};
