import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useAuthStore } from '../../stores/authStore';

describe('API client', () => {
  beforeEach(() => {
    useAuthStore.setState({ token: null, refreshToken: null });
    localStorage.clear();
  });

  it('authStore provides token for requests', () => {
    useAuthStore.getState().login(
      { id: 'u1', username: 'test', email: 't@t.com', role: 'buyer', organization: null },
      'my-token',
      'my-refresh',
    );
    expect(useAuthStore.getState().token).toBe('my-token');
  });

  it('authStore logout clears token', () => {
    useAuthStore.getState().login(
      { id: 'u1', username: 'test', email: 't@t.com', role: 'buyer', organization: null },
      'tok',
      'rtok',
    );
    useAuthStore.getState().logout();
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});
