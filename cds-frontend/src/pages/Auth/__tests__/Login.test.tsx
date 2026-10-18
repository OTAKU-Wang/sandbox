// 登录页本地可验证契约（四条，全部离线跑）：
// ① 错误文案只取 W2 信封的 code + message（docs/error-codes.md）：detail 是给程序看的结构化
//    内部信息（如 AUTH_MFA_REQUIRED 的 {username}、API_KEY_SCOPE_DENIED 的 {group,verb,path}），
//    面向未认证访客一律不外泄，也绝不 JSON.stringify 整个错误对象；
// ② 空密码提示「请输入密码」，长度不足才提示「密码至少8位」——一条规则共用一条 message 会串台；
// ③ /health 能力区少了 capability.levels 字段时 UI 继续静默，但必须留一条 console.warn
//    （诚实降级 ≠ 静默：后端字段改名不能悄悄把安全级 chip 变没）。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { message } from 'antd';
import { renderWithProviders } from '../../../test/test-utils';
import Login from '../Login';
import { describeAuthError } from '../loginError';
import api from '../../../services/api';
import { authApi } from '../../../services/authApi';

// antd 表单/按钮的响应式断点依赖 window.matchMedia（jsdom 未实现）
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

vi.mock('../../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}));
vi.mock('../../../services/authApi', () => ({
  authApi: { login: vi.fn(), register: vi.fn() },
}));
// antd v5 的 static message 在 React 19 下根本不往 body 里渲染（实测 document.body.innerHTML === ''），
// 所以 toast 只能接管它的调用参数来断言：只换掉这个 UI 旁道，页面里的映射逻辑仍是真代码。
vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  return {
    ...actual,
    message: { ...actual.message, error: vi.fn(), success: vi.fn() } as typeof actual.message,
  };
});

/** W2 信封（api.ts 拦截器已把 error.response.data 原样 reject 出来，页面拿到的就是这个体）。 */
function envelope(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    code: 'HTTP_401',
    message: 'Invalid credentials',
    request_id: 'req-9f3a1b2c',
    detail: 'Invalid credentials',
    ...overrides,
  };
}

/** antd 会在两字按钮里插全角空格，按去空白的 textContent 比对才稳。 */
function submitLogin(username: string, password: string) {
  fireEvent.change(screen.getByPlaceholderText('用户名'), { target: { value: username } });
  fireEvent.change(screen.getByPlaceholderText('密码'), { target: { value: password } });
  const button = Array.from(document.querySelectorAll('button'))
    .find((node) => node.textContent?.replace(/\s/g, '') === '登录');
  if (!button) throw new Error('登录按钮没渲染');
  fireEvent.click(button);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ status: 'ok', capability: { levels: ['l0', 'tee', 'l2c'] } });
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** 当前真正生效的表单错词：antd 离场动画会把上一条错误留在 DOM 里（height:0 的 -leave 节点），必须排掉。 */
function visibleFormErrors(): string[] {
  return Array.from(document.querySelectorAll('.ant-form-item-explain-error'))
    .filter((node) => !node.className.includes('-leave'))
    .map((node) => (node.textContent ?? '').trim());
}

describe('describeAuthError（W2 信封 → 访客可见文案）', () => {
  it('detail 是结构化对象时：透出后端 message 与 code，detail 里的内部字段一个都不露', () => {
    // 真实形态：app/api/auth.py 的 CDSError(code=AUTH_MFA_REQUIRED, detail={"username": ...})
    const err = envelope({
      code: 'AUTH_MFA_REQUIRED',
      message: '该账户已启用二次验证，请输入 MFA 验证码',
      detail: { username: 'zhangsan', mfa_method: 'totp' },
    });

    const text = describeAuthError(err);

    expect(text).toContain('该账户已启用二次验证');
    expect(text).toContain('AUTH_MFA_REQUIRED');
    // detail 属于内部信息：字段名与取值都不得出现在访客可见文案里
    expect(text).not.toContain('zhangsan');
    expect(text).not.toContain('mfa_method');
    expect(text).not.toContain('username');
    expect(text).not.toMatch(/[{}]/);
  });

  it('作用域拒绝类信封：detail 的 group/verb/path 与 request_id 不外泄', () => {
    // app/core/scopes.py L309-315 的 API_KEY_SCOPE_DENIED detail 形态
    const text = describeAuthError(envelope({
      code: 'API_KEY_SCOPE_DENIED',
      message: '当前凭证无权访问该资源',
      detail: { group: 'kms', verb: 'write', path: '/api/v1/kms/keys/k-1' },
      request_id: 'req-secretid',
    }));

    expect(text).toContain('当前凭证无权访问该资源');
    expect(text).toContain('API_KEY_SCOPE_DENIED');
    expect(text).not.toContain('/api/v1/kms/keys');
    expect(text).not.toContain('req-secretid');
  });

  it('detail 为数组且元素不含用户可读文案时：绝不回落到 JSON.stringify', () => {
    const text = describeAuthError(envelope({
      code: 'VALIDATION_ERROR',
      message: 'Request validation failed',
      detail: [{ loc: ['body', 'password'], type: 'string_too_short', input: 'raw-request-bytes' }],
    }));

    expect(text).toContain('VALIDATION_ERROR');
    expect(text).not.toMatch(/[{}[\]]/);
    expect(text).not.toContain('raw-request-bytes');
    expect(text).not.toContain('string_too_short');
    expect(text).not.toContain('body');
  });

  it('422 校验明细的 msg 是后端写给用户看的文案，保留并可拼接', () => {
    const text = describeAuthError(envelope({
      code: 'VALIDATION_ERROR',
      message: 'Request validation failed',
      detail: [
        { loc: ['body', 'username'], msg: 'Field required', type: 'missing' },
        { loc: ['body', 'password'], msg: 'String should have at least 8 characters', type: 'string_too_short' },
      ],
    }));

    expect(text).toContain('Field required');
    expect(text).toContain('at least 8 characters');
    expect(text).not.toContain('body');
  });

  it('非信封错误回落到共用 getErrorMessage（字符串直出，其余中文兜底）', () => {
    expect(describeAuthError('登录已过期，请重新登录')).toBe('登录已过期，请重新登录');
    expect(describeAuthError(undefined)).toBe('登录失败');
    expect(describeAuthError({})).toBe('登录失败');
  });
});

describe('Login 页面', () => {
  it('登录失败：递给 message.error 的是后端 message + code，不含 detail 内部字段', async () => {
    vi.mocked(authApi.login).mockRejectedValue(envelope({
      code: 'AUTH_MFA_REQUIRED',
      message: '该账户已启用二次验证，请输入 MFA 验证码',
      detail: { username: 'zhangsan', mfa_method: 'totp' },
    }));
    renderWithProviders(<Login />, { route: '/login' });

    submitLogin('zhangsan', 'Passw0rd!23');

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    const shown = String(vi.mocked(message.error).mock.calls[0][0]);
    expect(shown).toContain('该账户已启用二次验证');
    expect(shown).toContain('AUTH_MFA_REQUIRED');
    expect(shown).not.toContain('zhangsan');
    expect(shown).not.toContain('mfa_method');
  });

  it('登录失败（数组型 detail）：递给 message.error 的不得是 JSON 化的内部字段', async () => {
    vi.mocked(authApi.login).mockRejectedValue(envelope({
      code: 'VALIDATION_ERROR',
      message: 'Request validation failed',
      detail: [{ loc: ['body', 'password'], type: 'string_too_short', input: 'raw-request-bytes' }],
    }));
    renderWithProviders(<Login />, { route: '/login' });

    submitLogin('zhangsan', 'abc12345');

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    const shown = String(vi.mocked(message.error).mock.calls[0][0]);
    expect(shown).toContain('VALIDATION_ERROR');
    expect(shown).not.toMatch(/[{}[\]]/);
    expect(shown).not.toContain('raw-request-bytes');
    expect(shown).not.toContain('string_too_short');
  });

  it('空密码提交提示「请输入密码」，长度不足才提示「密码至少8位」', async () => {
    renderWithProviders(<Login />, { route: '/login' });

    submitLogin('zhangsan', '');
    await waitFor(() => expect(visibleFormErrors()).toContain('请输入密码'));
    expect(visibleFormErrors()).not.toContain('密码至少8位');
    expect(authApi.login).not.toHaveBeenCalled();

    submitLogin('zhangsan', 'abc');
    await waitFor(() => expect(visibleFormErrors()).toContain('密码至少8位'));
    expect(visibleFormErrors()).not.toContain('请输入密码');
    expect(authApi.login).not.toHaveBeenCalled();
  });
  it('/health 没有 capability.levels 时 UI 静默，但留一条 console.warn（诚实降级留痕）', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(api.get).mockResolvedValue({ status: 'ok', checks: { database: 'ok' } });
    renderWithProviders(<Login />, { route: '/login' });

    await waitFor(() => {
      const ours = warnSpy.mock.calls.filter((call) => String(call[0]).includes('capability.levels'));
      expect(ours).toHaveLength(1);
    });
    // chip 区仍不渲染（UI 静默），字段补齐后才有内容
    expect(document.querySelector('.cds-auth-status strong')).toBeNull();
  });

  it('/health 正常下发 levels 时不告警，chip 渲染安全级（l2c 显示 L2\'）', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    renderWithProviders(<Login />, { route: '/login' });

    await screen.findByText(/l0 \/ tee \/ L2'/);
    expect(warnSpy.mock.calls.filter((call) => String(call[0]).includes('capability.levels'))).toHaveLength(0);
  });
});
