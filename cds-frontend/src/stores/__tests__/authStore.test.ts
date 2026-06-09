import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from '../authStore';

const mockUser = {
  id: 'u1',
  username: 'testuser',
  email: 'test@example.com',
  role: 'buyer' as const,
  organization: 'TestOrg',
};

describe('authStore', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      token: null,
      refreshToken: null,
      isAuthenticated: false,
    });
    localStorage.clear();
  });

  it('starts unauthenticated', () => {
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.user).toBeNull();
    expect(state.token).toBeNull();
  });

  it('login sets user and tokens', () => {
    useAuthStore.getState().login(mockUser, 'access-tok', 'refresh-tok');
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(true);
    expect(state.user).toEqual(mockUser);
    expect(state.token).toBe('access-tok');
    expect(state.refreshToken).toBe('refresh-tok');
  });

  it('logout clears everything', () => {
    useAuthStore.getState().login(mockUser, 'tok', 'rtok');
    useAuthStore.getState().logout();
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.user).toBeNull();
    expect(state.token).toBeNull();
    expect(state.refreshToken).toBeNull();
  });

  it('setTokens updates tokens only', () => {
    useAuthStore.getState().login(mockUser, 'old', 'oldrt');
    useAuthStore.getState().setTokens('new', 'newrt');
    const state = useAuthStore.getState();
    expect(state.token).toBe('new');
    expect(state.refreshToken).toBe('newrt');
    expect(state.user).toEqual(mockUser);
  });

  it('updateUser merges partial updates', () => {
    useAuthStore.getState().login(mockUser, 'tok', 'rtok');
    useAuthStore.getState().updateUser({ organization: 'NewOrg' });
    expect(useAuthStore.getState().user?.organization).toBe('NewOrg');
    expect(useAuthStore.getState().user?.username).toBe('testuser');
  });

  it('updateUser is no-op when no user', () => {
    useAuthStore.getState().updateUser({ organization: 'X' });
    expect(useAuthStore.getState().user).toBeNull();
  });
});
