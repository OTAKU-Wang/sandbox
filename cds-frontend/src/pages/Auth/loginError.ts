// W2 错误信封 → 面向用户的文案（docs/error-codes.md：{code, message, detail, request_id}）。
// 登录页与注册页共用：两页都在认证之前，看到的是同一族信封；只有兜底文案不同。
// 只取 code + message：detail 是给程序看的结构化内部信息（如 AUTH_MFA_REQUIRED 的 {username}、
// API_KEY_SCOPE_DENIED 的 {group,verb,path}），对尚未通过认证的访客一律不外泄，也绝不
// JSON.stringify 整个错误对象。例外：422 的 detail 是 FastAPI 校验错误数组，其中的
// msg/message 本就是后端写给用户看的文案。
import { getErrorMessage } from '../../components/Feedback/QueryFeedback';

export function describeAuthError(err: unknown, fallback = '登录失败'): string {
  const envelope = err && typeof err === 'object' ? (err as Record<string, unknown>) : {};
  const code = typeof envelope.code === 'string' && envelope.code ? envelope.code : null;
  const messageText = typeof envelope.message === 'string' && envelope.message.trim()
    ? envelope.message.trim()
    : null;
  const suffix = code ? `（${code}）` : '';

  if (Array.isArray(envelope.detail)) {
    const texts = envelope.detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null;
        const record = item as Record<string, unknown>;
        const text = typeof record.msg === 'string'
          ? record.msg
          : typeof record.message === 'string' ? record.message : null;
        return text && text.trim() ? text.trim() : null;
      })
      .filter((text): text is string => text !== null);
    if (texts.length > 0) return `${texts.join('; ')}${suffix}`;
  }
  if (messageText) return `${messageText}${suffix}`;
  if (code) return `${fallback}${suffix}`;
  // 非信封形态（网络失败、超时等）沿用全站共用的取文案兜底，不自己拆错误对象。
  return getErrorMessage(err, fallback);
}
