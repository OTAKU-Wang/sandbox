// SS-18 沙箱目录契约：VM 内四个目录的口径，前端唯一文案来源。
// Guide.tsx（开发指引）与 NotebookWorkbench.tsx（在线调试）共用同一份，避免两处漂移。
//
// 权威来源 app/services/cube_runtime_adapter.py：GUEST_DATA_DIR / GUEST_OUTPUT_DIR /
// GUEST_WORK_DIR / GUEST_EXEC_DIR 与 HARVEST_DIRS（唯一执行路径 l2c）。
// 后端改这四个常量时必须同步改这里。
//
// 诚实口径（禁伪造铁律）：/sandbox/data 的「只读」= 挂载只读声明 + 采集面不取它，
// 不是虚拟化层强制；真正的出域约束由 T5 出口网关把行。文案不得写成更强的强度。

export interface SandboxDirectoryEntry {
  /** VM 内路径，与后端 GUEST_* 常量逐字一致 */
  path: string;
  /** 这个目录放什么，以及它在采集面上的位置 */
  purpose: string;
}

export const SANDBOX_DIRECTORY_ENTRIES: readonly SandboxDirectoryEntry[] = [
  { path: '/sandbox/data', purpose: '合约授权数据（只读声明，不采集）' },
  { path: '/sandbox/output', purpose: '产物目录（唯一正规采集面）' },
  { path: '/scratch', purpose: '工作区（兼容期内仍采集）' },
  { path: '/results/.cds-exec', purpose: 'exec 中转（永不进包）' },
];

/** 采集规则的唯一一句话表述：两个页面都直接渲染它，不得各自改写。 */
export const SANDBOX_HARVEST_RULE =
  '成果包只从 /sandbox/output（正规口径）与 /scratch（兼容期，逐文件标注来源目录）采集；'
  + '/sandbox/data 与 /results/.cds-exec 不在采集面内——前者的「只读」是只读声明加采集排除，'
  + '数据出域一律过出口网关审查。';

/** 指引页常驻公告块的标题。 */
export const SANDBOX_DIRECTORY_NOTICE_TITLE = '沙箱目录约定';
