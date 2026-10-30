/**
 * 前端超时口径的唯一来源（A311，2026-10-30）。
 *
 * 为什么不放在 `api.ts` 里：那个模块在测试里是被整体 mock 的最外层边界
 * （`services/__tests__/*.test.ts` 都拿 `{ default: { get, post, … } }` 顶掉它），
 * 把口径常量挂在一个会被换掉的模块上，测试里拿到的就是 `undefined`——
 * 「超时到底放宽了多少」这种判据一旦变成 undefined，钉它的用例也就哑了。
 */

/**
 * 上传类请求的超时。
 *
 * 全局默认是 30s（`api.ts`），而后端数据资源上传上限是 **500MB**
 * （`app/api/data_resources.py:MAX_UPLOAD_SIZE`；会话文件那侧 `sandbox_sessions.py`
 * 压根没有显式大小上限，30s 是唯一的护栏，也就是唯一拦错地方的那道）。
 * 300s 对齐网关 `/api/` 的 `proxy_read_timeout`（cds-frontend/nginx.conf）——
 * 客户端超时装得比网关还大只会自己先断，没有意义。
 */
export const UPLOAD_TIMEOUT_MS = 300_000;
