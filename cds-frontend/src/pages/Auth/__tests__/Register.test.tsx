// 注册页本地可验证契约（与登录页同一族，全部离线跑）：
// ① 空密码提示「请输入密码」，长度不足才提示「密码至少8位」——一条规则共用一条 message 会串台；
// ② 注册失败的提示只取 W2 信封的 code + message，detail 是结构化内部信息，对未认证访客不外泄，
//    也绝不 JSON.stringify 整个错误对象（422 校验数组里的 msg 例外，那本就是给用户看的文案）。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { message } from 'antd';
import { renderWithProviders } from '../../../test/test-utils';
import Register from '../Register';
import { authApi } from '../../../services/authApi';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

vi.mock('../../../services/authApi', () => ({
  authApi: { register: vi.fn(), login: vi.fn() },
}));
// antd v5 的 static message 在 React 19 + jsdom 下不往 body 里渲染，只能接管调用参数断言。
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return { ...actual, message: { ...actual.message, error: vi.fn(), success: vi.fn() } };
});

const mockedRegister = vi.mocked(authApi.register);

function visibleFormErrors(): string[] {
  return Array.from(document.querySelectorAll('.ant-form-item-explain-error'))
    .filter((node) => !node.className.includes('-leave'))
    .map((node) => (node.textContent ?? '').trim());
}

function passwordInput(): HTMLInputElement {
  const input = document.querySelector('input#password') as HTMLInputElement | null;
  if (!input) throw new Error('未找到密码输入框');
  return input;
}

async function submitForm() {
  fireEvent.click(screen.getByRole('button', { name: /注\s*册|注册/ }));
  await waitFor(() => expect(visibleFormErrors().length).toBeGreaterThan(0));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('注册页密码校验文案', () => {
  it('空密码提示「请输入密码」，不得串成「密码至少8位」', async () => {
    renderWithProviders(<Register />);
    await submitForm();
    const errors = visibleFormErrors();
    expect(errors.join(' ')).toContain('请输入密码');
    expect(errors.join(' ')).not.toContain('密码至少8位');
  });

  it('密码不足 8 位才提示「密码至少8位」', async () => {
    renderWithProviders(<Register />);
    fireEvent.change(passwordInput(), { target: { value: 'abc' } });
    await submitForm();
    expect(visibleFormErrors().join(' ')).toContain('密码至少8位');
  });
});

describe('注册失败提示不外泄内部结构', () => {
  it('detail 是结构化对象时，提示只含 code 与 message', async () => {
    mockedRegister.mockRejectedValue({
      code: 'USERNAME_TAKEN',
      message: '用户名已存在',
      detail: { username: 'alice', existing_by_email: 'alice@example.com' },
      request_id: 'req-1',
    });
    renderWithProviders(<Register />);
    fireEvent.change(document.querySelector('input#username') as HTMLInputElement, { target: { value: 'alice' } });
    fireEvent.change(document.querySelector('input#email') as HTMLInputElement, { target: { value: 'alice@example.com' } });
    fireEvent.change(passwordInput(), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: /注\s*册|注册/ }));
    await waitFor(() => expect(message.error).toHaveBeenCalled());
    const text = String(vi.mocked(message.error).mock.calls[0][0]);
    expect(text).toContain('用户名已存在');
    expect(text).toContain('USERNAME_TAKEN');
    expect(text).not.toContain('alice');
    expect(text).not.toContain('alice@example.com');
    expect(text).not.toContain('{');
  });

  it('422 校验数组取 msg，不落到 JSON 序列化', async () => {
    mockedRegister.mockRejectedValue({
      code: 'VALIDATION_ERROR',
      message: '请求参数校验失败',
      detail: [{ loc: ['body', 'password'], msg: '密码至少8位', type: 'string_too_short' }],
      request_id: 'req-2',
    });
    renderWithProviders(<Register />);
    fireEvent.change(document.querySelector('input#username') as HTMLInputElement, { target: { value: 'alice' } });
    fireEvent.change(document.querySelector('input#email') as HTMLInputElement, { target: { value: 'alice@example.com' } });
    fireEvent.change(passwordInput(), { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: /注\s*册|注册/ }));
    await waitFor(() => expect(message.error).toHaveBeenCalled());
    const text = String(vi.mocked(message.error).mock.calls[0][0]);
    expect(text).toContain('密码至少8位');
    expect(text).not.toContain('string_too_short');
    expect(text).not.toContain('loc');
  });
});
