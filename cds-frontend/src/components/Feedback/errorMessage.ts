// 错误文案提取（纯函数，无 JSX）——从 QueryFeedback.tsx 拆出（P4-3 lint 清零）：
// 组件文件混导出非组件符号会让 react-refresh 整族失效（HMR 退化为整页刷新）。
// 组件留 QueryFeedback.tsx，本模块只放可单测的纯逻辑。

export function getErrorMessage(error: unknown, fallback = '请求失败，请稍后重试') {
  if (!error) return fallback;
  if (typeof error === 'string') return error;
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'error' in detail) {
      const nested = (detail as { error?: unknown }).error;
      if (typeof nested === 'string') return nested;
    }
  }
  return fallback;
}
