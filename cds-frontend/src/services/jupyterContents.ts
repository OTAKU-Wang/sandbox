import api from './api';

// SS-19 开发环「文件目录」的唯一取数口：Jupyter Contents API（经 /api/jupyter 反代）。
//
// 反代实测口径（app/api/jupyter_proxy.py::_check）：
// - 裸形 `api/contents?path=...` 一律 403（上游镜像会吐掉 ?path= 并回根目录，会漏出别人的会话目录名），
//   列目录只认 `api/contents/<本会话前缀>/...`，因此这里的 path 永远拼在 URL 段里。
// - 鉴权是 `/api/jupyter/login?ticket=` 换来的会话 Cookie（同源请求自动携带），上游 token 不下发浏览器；
//   反代还会把 query 里的 `token` 丢掉再转发，带它没有任何作用。
// - 返回体两种形状都要能吃：新版把子项放在 `children`，经典 Contents API 放在 `content` 数组里。

export const JUPYTER_PUBLIC_PATH = '/api/jupyter';

export interface JupyterContentEntry {
  name: string;
  path: string;
  type: string;
  isDirectory: boolean;
  /** 文件字节数；目录的 size 在 Jupyter 里是子项个数，不能当占用算 */
  size: number | null;
  lastModified: string | null;
  /** 目录子项；null = 这次返回里没有清单，需要再取一层 */
  children: JupyterContentEntry[] | null;
  /** 该目录单独取数失败时的后端原话（父层仍如实列出，不假装它是空目录） */
  error?: string;
}

export interface JupyterTree {
  root: JupyterContentEntry;
  /** 已读到的文件数（目录不计入） */
  fileCount: number;
  /** 已读到的文件字节合计 */
  totalBytes: number;
  /** 受目录请求预算或子目录读取失败影响，清单不是全量 */
  incomplete: boolean;
  note: string | null;
}

interface RawContent {
  name?: unknown;
  path?: unknown;
  type?: unknown;
  size?: unknown;
  content?: unknown;
  children?: unknown;
  last_modified?: unknown;
  mtime?: unknown;
}

const DEFAULT_MAX_DIRS = 24;
const DEFAULT_MAX_DEPTH = 4;

function backendReason(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  if (error && typeof error === 'object') {
    const payload = error as { message?: unknown; detail?: unknown; error?: unknown };
    for (const key of ['message', 'detail', 'error'] as const) {
      const value = payload[key];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
  }
  return fallback;
}

/** 目录清单的 URL：path 逐段转义后拼在 URL 里（裸形 ?path= 被反代 403）。 */
export function jupyterContentsUrl(dirPath: string): string {
  const segments = dirPath.replace(/^\/+/, '').split('/').filter(Boolean).map(encodeURIComponent);
  return `${JUPYTER_PUBLIC_PATH}/api/contents/${segments.join('/')}`;
}

function toEntry(raw: RawContent, fallbackPath: string): JupyterContentEntry {
  const type = typeof raw.type === 'string' ? raw.type : 'file';
  const listing = Array.isArray(raw.children) ? raw.children : Array.isArray(raw.content) ? raw.content : null;
  const path = typeof raw.path === 'string' && raw.path ? raw.path : fallbackPath;
  return {
    name: typeof raw.name === 'string' && raw.name ? raw.name : path.split('/').filter(Boolean).pop() || path,
    path,
    type,
    isDirectory: type === 'directory',
    size: typeof raw.size === 'number' && Number.isFinite(raw.size) ? raw.size : null,
    lastModified:
      typeof raw.last_modified === 'string' ? raw.last_modified : typeof raw.mtime === 'string' ? raw.mtime : null,
    children: type === 'directory' && listing ? listing.map((child) => toEntry((child || {}) as RawContent, path)) : null,
  };
}

/** 取一层目录（或单文件元信息）。失败抛出可直接交给 getErrorMessage 的后端原因。 */
export async function fetchJupyterDirectory(dirPath: string): Promise<JupyterContentEntry> {
  let raw: unknown;
  try {
    raw = await api.request({ baseURL: '/', url: jupyterContentsUrl(dirPath), method: 'GET' });
  } catch (error) {
    throw new Error(backendReason(error, `开发环目录读取失败：${dirPath}`), { cause: error });
  }
  if (!raw || typeof raw !== 'object') {
    throw new Error(`开发环目录返回格式异常：${dirPath}`);
  }
  return toEntry(raw as RawContent, dirPath);
}

function needsFetch(entry: JupyterContentEntry): boolean {
  return entry.isDirectory && entry.children === null && !entry.error;
}

function collect(entry: JupyterContentEntry, into: JupyterContentEntry[]): void {
  into.push(entry);
  (entry.children ?? []).forEach((child) => collect(child, into));
}

/**
 * 递归取回整个会话目录树：只有上一级没带清单的目录才再发一次请求，
 * 目录请求数与深度都有预算（超预算如实标 incomplete，不把半棵树当全量）。
 */
export async function fetchJupyterTree(
  rootPath: string,
  options: { maxDirs?: number; maxDepth?: number } = {},
): Promise<JupyterTree> {
  const maxDirs = options.maxDirs ?? DEFAULT_MAX_DIRS;
  const maxDepth = options.maxDepth ?? DEFAULT_MAX_DEPTH;

  const root = await fetchJupyterDirectory(rootPath);
  if (!root.isDirectory) {
    throw new Error(`开发环目录类型异常：${rootPath} 不是目录（后端返回 ${root.type}）`);
  }

  let requests = 1;
  let budgetHit = false;
  let failedDirs = 0;
  let depth = 0;
  let frontier = needsFetch(root) ? [root] : (root.children ?? []).filter(needsFetch);

  while (frontier.length > 0 && depth < maxDepth) {
    const pending = frontier.filter(() => {
      if (requests < maxDirs) return true;
      budgetHit = true;
      return false;
    });
    const loaded = await Promise.all(
      pending.map(async (dir) => {
        requests += 1;
        try {
          dir.children = (await fetchJupyterDirectory(dir.path)).children;
        } catch (error) {
          failedDirs += 1;
          dir.error = backendReason(error, '目录读取失败');
        }
        return dir;
      }),
    );
    frontier = loaded.flatMap((dir) => (dir.children ?? []).filter(needsFetch));
    depth += 1;
  }
  if (frontier.length > 0) budgetHit = true;

  const all: JupyterContentEntry[] = [];
  collect(root, all);
  const files = all.filter((entry) => !entry.isDirectory);
  const totalBytes = files.reduce((sum, entry) => sum + (entry.size ?? 0), 0);
  const incomplete = budgetHit || failedDirs > 0;
  const notes: string[] = [];
  if (budgetHit) notes.push(`目录层级/数量超出单次读取预算（${maxDirs} 次请求、${maxDepth} 层），清单只到已读到的层级`);
  if (failedDirs) notes.push(`${failedDirs} 个子目录读取失败，其内容未计入`);

  return { root, fileCount: files.length, totalBytes, incomplete, note: notes.length ? notes.join('；') : null };
}
