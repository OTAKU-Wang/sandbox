/**
 * A251：注册接口回 200 不等于「拿到了登录态」。
 *
 * 旧写法 `login(resp.user, resp.access_token, resp.refresh_token)` 不校验凭证是否存在：
 * 后端哪天改成「注册后需管理员激活 / 需邮箱验证」而不下发 token 时，store 里会写进一个
 * undefined token，用户被当成已登录送进受保护路由 ⇒ 接下来每一发都是 401（401 刷新队列
 * 也跟着空转），而屏幕上只写着「注册成功」。这一条钉的就是那个分支：凭证缺失 = 失败。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { message } from 'antd';
import { renderWithProviders } from '../../../test/test-utils';
import Register from '../Register';
import { authApi } from '../../../services/authApi';
import { useAuthStore } from '../../../stores/authStore';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
  }),
});

vi.mock('../../../services/authApi', () => ({
  authApi: { register: vi.fn(), login: vi.fn() },
}));
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return { ...actual, message: { ...actual.message, error: vi.fn(), success: vi.fn() } };
});

const mockedRegister = vi.mocked(authApi.register);

const USER = { id: 'u9', username: 'alice', email: 'a@e.com', role: 'buyer' as const, organization: null };

function fillAndSubmit() {
  fireEvent.change(document.querySelector('input#username') as HTMLInputElement, { target: { value: 'alice' } });
  fireEvent.change(document.querySelector('input#email') as HTMLInputElement, { target: { value: 'a@e.com' } });
  fireEvent.change(document.querySelector('input#password') as HTMLInputElement, { target: { value: 'password123' } });
  fireEvent.click(screen.getByRole('button', { name: /注\s*册|注册/ }));
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.getState().logout();
});

afterEach(() => {
  useAuthStore.getState().logout();
});

describe('A251：access_token 缺失时不进登录态', () => {
  it('200 但没有 access_token ⇒ 不写 store、不说成功，并说明该怎么办', async () => {
    mockedRegister.mockResolvedValue({ user: USER, refresh_token: 'r' } as never);
    renderWithProviders(<Register />);
    fillAndSubmit();

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    expect(message.success).not.toHaveBeenCalled();
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.token).toBeNull();
    const text = String(vi.mocked(message.error).mock.calls[0][0]);
    // 文案要点：说清楚「注册这件事本身成功了」+「缺的是登录凭证」+ 下一步（去登录页）。
    expect(text).toContain('注册已提交');
    expect(text).toContain('登录');
  });

  it('access_token 是空串同样算缺失（falsy 的口径一致，别只挡 undefined）', async () => {
    mockedRegister.mockResolvedValue({ user: USER, access_token: '', refresh_token: 'r' } as never);
    renderWithProviders(<Register />);
    fillAndSubmit();

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it('正常带回凭证时照旧直接进控制台（收紧这条不能把主路径卡死）', async () => {
    mockedRegister.mockResolvedValue({ user: USER, access_token: 'tok', refresh_token: 'r' } as never);
    renderWithProviders(<Register />);
    fillAndSubmit();

    await waitFor(() => expect(message.success).toHaveBeenCalled());
    expect(message.error).not.toHaveBeenCalled();
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(true);
    expect(state.token).toBe('tok');
  });
});
