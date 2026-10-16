/**
 * PZ-15 W2 码 → 用户提示映射（镜像 docs/error-codes.md 用户提示/下一步列）。
 * 纯数据/纯函数，供 ErrorCard 与会话错误卡片消费；新增 W2 码时与
 * docs/error-codes.md 同步维护（两端同改）。
 */
export interface ErrorHint {
  hint: string;
  next: string;
  retryable: boolean;
  showCapability?: boolean;
}

const FALLBACK: ErrorHint = {
  hint: '请求失败',
  next: '稍后重试；持续失败请携带 request_id 报告。',
  retryable: true,
};

export const ERROR_HINTS: Record<string, ErrorHint> = {
  SESSION_TERMINATED: {
    hint: '会话已终止，数据不可恢复',
    next: '不要重试；如需数据请联系管理员（审计保留）。',
    retryable: false,
  },
  SESSION_STATE_CONFLICT: {
    hint: '会话当前状态不允许该操作',
    next: '先 resume/refresh 再操作（见服务端消息中的当前状态）。',
    retryable: false,
  },
  OPERATION_LOCKED: {
    hint: '另一操作进行中',
    next: '按 Retry-After 等待后自动重试。',
    retryable: true,
  },
  RATE_LIMITED: {
    hint: '请求过于频繁（或并发会话已达上限）',
    next: '按 Retry-After 退避；并发上限时先终止已有会话。',
    retryable: true,
  },
  QUOTA_EXCEEDED: {
    hint: '租户配额不足',
    next: '释放配额或申请提额。',
    retryable: false,
  },
  RESOURCE_EXHAUSTED: {
    hint: '资源耗尽，暂无可用节点',
    next: '不要立即重试；等待资源释放。',
    retryable: false,
  },
  REQUEST_TIMEOUT: {
    hint: '同步执行等待超时，操作可能仍在沙箱内完成',
    next: '先查询结果（会话状态/日志/输出）再决定重试。',
    retryable: true,
    showCapability: true,
  },
  STREAM_UNSUPPORTED: {
    hint: '该级别不支持流式执行',
    next: '用同步 execute（能力矩阵指引）。',
    retryable: false,
    showCapability: true,
  },
  SANDBOX_NOT_FOUND: {
    hint: '底层沙箱已不存在（节点侧）',
    next: '终止会话重建；报告 request_id。',
    retryable: false,
  },
  RUNTIME_UNAVAILABLE: {
    hint: "L2' 数据面未启用/节点未就绪",
    next: '联系管理员检查 cube 节点（FG-017）。',
    retryable: false,
    showCapability: true,
  },
  NO_NODE_TEMPLATE: {
    hint: '无节点具备所需模板',
    next: '等待模板分发完成或联系管理员。',
    retryable: true,
  },
  SESSION_NOT_FOUND: {
    hint: '会话不存在或已删除',
    next: '不要重试；核对会话 ID，必要时重新创建。',
    retryable: false,
  },
  INTERNAL_ERROR: {
    hint: '服务端内部错误',
    next: '携带 request_id 报告；可安全重试一次。',
    retryable: true,
  },
  VALIDATION_ERROR: {
    hint: '请求参数不合法',
    next: '修正请求，不要重试。',
    retryable: false,
  },
  AUTH_MFA_REQUIRED: {
    hint: '需要完成双因素验证',
    next: '打开身份验证器输入 TOTP，或使用恢复码；均不可用时联系管理员。',
    retryable: false,
  },
  TEE_NODE_UNAVAILABLE: {
    hint: '无可用 TEE 节点（诚实降级：不伪装 TEE）',
    next: '联系管理员确认 TEE 节点，或改用 tee_if_available。',
    retryable: false,
    showCapability: true,
  },
  LEGACY_LEVEL_RETIRED: {
    hint: '该沙箱级别已退役，仅支持 l2c',
    next: '使用默认级别 l2c；dev/test 可由管理员设 CDS_SANDBOX_LEGACY_ENABLED=true。',
    retryable: false,
    showCapability: true,
  },
  API_KEY_INVALID: {
    hint: '密钥格式不符 / 不存在 / 已过期',
    next: '检查密钥是否完整、是否已过期；联系平台管理员。',
    retryable: false,
  },
  API_KEY_INACTIVE: {
    hint: '密钥被禁用 / 吊销 / 失败冷却中',
    next: '冷却可稍后重试；被禁用或吊销请联系管理员。',
    retryable: false,
  },
  API_KEY_SCOPE_DENIED: {
    hint: '该 API 密钥无此功能域的权限',
    next: '不要重试；请管理员调整 scope 或改用其他密钥。',
    retryable: false,
  },
};

/** 按 W2 码查提示；未知码（含 HTTP_{status} 兼容码）走状态语义回退。 */
export function hintForCode(code: string | undefined, status?: number): ErrorHint {
  if (code && ERROR_HINTS[code]) {
    const h = ERROR_HINTS[code];
    // GONE 契约兜底：即使码名异常，410 也绝不重试
    if (status === 410) return { ...h, retryable: false };
    return h;
  }
  if (status === 410) return ERROR_HINTS.SESSION_TERMINATED;
  if (status === 404) return ERROR_HINTS.SESSION_NOT_FOUND;
  if (status === 503) return ERROR_HINTS.OPERATION_LOCKED;
  if (status === 500) return ERROR_HINTS.INTERNAL_ERROR;
  return FALLBACK;
}
