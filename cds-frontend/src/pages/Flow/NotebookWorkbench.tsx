// SS-23 /notebooks = 原型图 1「2-3 我的开发 · 调试环境」的外框重建（唯一事实源：
// D:/workspace/.cds-e2e/img-specs/group-B.md §图 1）。
//
// 外框逐字对齐转写：标题卡（标题 + 交付场景副标题 + 仅 1 个「← 返回列表」）、
// 左栏「文件目录」（250 宽、44px 行距、勾选框+文件图标+名称、底部两行灰字统计）、
// 右栏上「编辑器卡」（卡头 / 五项纯文字工具条 / 代码区 / 底部状态条）、
// 右栏下「输出/日志卡」（输出·问题·终端三页签 + 执行态 + 两个灰蓝图标钮 + 底部会话行）。
//
// 槽位口径（项目铁律 honest degradation / 禁伪造，逐条在 notebookWorkbenchModel.ts 里实现）：
// · 绿点「● 沙箱已连接」只由 GET /sandbox-sessions/{id} 的 status=="running" 驱动；
//   上一次执行落在 cds_stateless 时改画橙点并写明降级原因，绝不画假绿点。
// · CPU / 内存已用（D-e2e-48）：接 GET /sandbox-sessions/{id}/metering 的 cgroup v2 真读数（5s 轮询）；
//   后端读不到 cgroup（l2c 在 VM 内等）就回 available:false → 状态条继续「未计量」，不编数字。
//   沙箱内 Python 版本 / 光标（开发环分支）：仍无接口 → 未计量·未接入。
// · 「执行成功 · 6.4s」取本会话审计流最后一条 sandbox.execute 的 exit_code 与 duration_ms。
// · 卡头「已自动保存 HH:MM:SS」（D-e2e-48）：自动保存 = 本页前端 2s 防抖真 PUT，时间取 PUT
//   返回的 updated_at；服务端没有自动保存任务，离开本页即不再触发，每次自动保存同样 +1 版本。
// · 日志正文只渲染 GET /sandbox-sessions/{id}/logs 的真实事件；原型那句「输出结果不落盘，
//   仅保留最近 24 小时日志」与本平台事实不符（persist 会把输出写回笔记、审计日志无 24h 清理），
//   故改成本页真实口径。
// · 代码区内部（真 JupyterLab iframe / 自研单元格编辑器）是 SS-19 已批准的偏离，本文件不动其内部实现。
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { Alert, Button, Checkbox, Dropdown, Empty, Form, Input, Modal, Select, Spin, Tooltip, Typography, message } from 'antd';
import type { MenuProps } from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleFilled,
  DownOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import Editor from '@monaco-editor/react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import './monacoSetup';
import './notebookWorkbench.css';
import {
  describeFlowError,
  UNKNOWN_TEXT,
  parseNotebookContent,
  serializeNotebookContent,
  canonicalizeNotebookContent,
  defaultNotebookPath,
  useCreateNotebook,
  useCreateWorkload,
  useDeleteNotebook,
  useExecuteCell,
  useJupyterStatus,
  useNotebook,
  useNotebooks,
  useOpenJupyterEnv,
  useRunNotebook,
  useStartDevSession,
  useUpdateNotebook,
  type CellExecuteResult,
  type JupyterEnvTicket,
  type Notebook,
} from '../../services/flow';
import { sandboxApi } from '../../services/sandboxApi';
import { useQuotaUsage } from '../../services/quotaApi';
import { fetchJupyterTree } from '../../services/jupyterContents';
import { CenteredLoading, QueryErrorAlert, getErrorMessage } from '../../components/Feedback/QueryFeedback';
import { backendFromCellResult, backendFromNotebook, toOutputPane } from './display';
import { RuntimeStatusBanner } from './RuntimeStatusBanner';
import { useContractOptions } from './useContractOptions';
import { FileDirectoryPanel, OutputLogCard, type OutputTabKey } from './notebookWorkbenchPanels';
import {
  DISK_QUOTA_SCOPE_HINT,
  NBW_BACK,
  NBW_MENUS,
  NBW_NOT_MEASURED,
  NBW_SUBTITLE,
  NBW_TITLE,
  buildLogLines,
  applyViewCleared,
  clockOf,
  connectionState,
  cursorText,
  detectEol,
  fileBasename,
  gibFromMb,
  isAliveSessionStatus,
  jupyterEntryToNode,
  lastExecutionFromLogs,
  runStatusView,
  sessionFilesToTree,
  summarizeFiles,
  type ConnectionState,
  type WorkbenchFileNode,
  type WorkbenchLogLine,
} from './notebookWorkbenchModel';

const { Text } = Typography;

interface CellState {
  running: boolean;
  result: CellExecuteResult | null;
}

// 单元格 id 只要在本页生命周期里唯一即可；仓里没有 nanoid 这个依赖，就不为一个计数器加包。
let cellSeq = 0;
const nextCellId = () => `cell-${(cellSeq += 1)}`;

/** 会话目录 = 后端下发的 notebook_path 去掉文件名；开发环反代只允许列这个前缀以内。 */
function sessionDirOf(notebookPath: string | null | undefined): string {
  const segments = String(notebookPath ?? '').split('/').filter(Boolean);
  segments.pop();
  return segments.join('/');
}

function countFiles(nodes: WorkbenchFileNode[]): number {
  return nodes.reduce(
    (acc, node) => acc + (node.isDirectory ? countFiles(node.children ?? []) : 1),
    0,
  );
}

/** 内存配额分母：优先会话真实限额，其次任务档位；两个都没有就写未记录。 */
/** 自动保存防抖窗口：导出给测试，避免用写死的 sleep 验时序。 */
export const AUTO_SAVE_DEBOUNCE_MS = 2000;

function memoryQuotaMb(sessionLimits: Record<string, unknown> | null, debugMemoryMb: number | null | undefined): number | null {
  const fromSession = sessionLimits && typeof sessionLimits.memory_mb === 'number' ? sessionLimits.memory_mb : null;
  return fromSession ?? (typeof debugMemoryMb === 'number' ? debugMemoryMb : null);
}

// ───────────────────────────── 页面（标题卡 + 选中的本子） ─────────────────────────────

export default function NotebookWorkbench() {
  const [searchParams, setSearchParams] = useSearchParams();
  const taskParam = searchParams.get('task');
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const notebooksQuery = useNotebooks(null, 1, 50);
  const items = notebooksQuery.data?.items ?? [];

  // ?task=<id>：卡片跳进来的上下文。选中态是从「地址栏 + 后端列表」推导出来的，不走 effect：
  // 只有列表里真的有这个 id 才算选中，否则 /notebooks?task=<删掉的本子> 会对不存在的 id
  // 发详情请求，页面挂在 404 上而不是回到「还没选」。列表还在路上时先转圈，不闪一下空状态。
  const taskKnown = !!taskParam && items.some((item) => item.id === taskParam);
  const selectedId = pickedId ?? (taskKnown ? taskParam : null);
  const awaitingTaskParam = pickedId === null && !!taskParam && !notebooksQuery.data;
  const { data: detail, isLoading, isError, error, refetch } = useNotebook(selectedId);

  // 地址栏里的 task 参数刻意留着（刷新/收藏都回到同一个本子），只在手动换本子时清掉。
  const pick = useCallback(
    (id: string | null) => {
      setPickedId(id);
      if (searchParams.has('task')) setSearchParams({}, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  return (
    <div className="nbw-root">
      <div className="nbw-titlecard">
        <div>
          <div className="nbw-titlecard-title">{NBW_TITLE}</div>
          <div className="nbw-titlecard-sub">{NBW_SUBTITLE}</div>
        </div>
        <BackButton />
      </div>

      {/* 原型上没有告警条；但后端真报「没有可用 MicroVM 运行时」时必须挂出来（honest degradation），
          运行时健康时这个组件自己返回 null，画面回到原型样。 */}
      <RuntimeStatusBanner />

      {isError ? <QueryErrorAlert error={error} message="调试对象加载失败" onRetry={() => { void refetch(); }} /> : null}

      {!selectedId ? (
        awaitingTaskParam ? (
          <CenteredLoading />
        ) : (
          <PickNotebookCard
            items={items.map((item) => ({ id: item.id, title: item.title }))}
            onPick={pick}
            onCreate={() => setCreateOpen(true)}
          />
        )
      ) : isLoading ? (
        <CenteredLoading />
      ) : detail ? (
        <NotebookWorkspace
          key={detail.id}
          notebook={detail}
          siblings={items.map((item) => ({ id: item.id, title: item.title, task_no: item.task_no }))}
          onPick={pick}
          onCreate={() => setCreateOpen(true)}
        />
      ) : null}

      <CreateNotebookModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(nb) => {
          setCreateOpen(false);
          pick(nb.id);
        }}
      />
    </div>
  );
}

function BackButton() {
  const navigate = useNavigate();
  return (
    <Button icon={<ArrowLeftOutlined />} onClick={() => void navigate('/flow/tasks')}>
      {NBW_BACK}
    </Button>
  );
}

/** 没有选中对象时的落点：图 1 没有这一屏，但也不能凭空画文件目录/日志，只给两个真动作。 */
function PickNotebookCard({
  items,
  onPick,
  onCreate,
}: {
  items: { id: string; title: string }[];
  onPick: (id: string) => void;
  onCreate: () => void;
}) {
  const menu: MenuProps = {
    onClick: ({ key }) => onPick(key),
    items: items.length
      ? items.map((item) => ({ key: item.id, label: item.title }))
      : [{ key: 'empty', label: '后端没有返回任何开发任务', disabled: true }],
  };
  return (
    <section className="nbw-card" style={{ minHeight: 260 }}>
      <div className="nbw-card-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Empty
          description={
            <span style={{ fontSize: 12 }}>
              还没有选中的调试对象：GET /notebooks 里是你在后端真实存在的开发任务，选一个再进调试环境。
            </span>
          }
        >
          <Dropdown menu={menu} trigger={['click']}>
            <Button type="primary">选择调试对象</Button>
          </Dropdown>
          <Button onClick={onCreate} style={{ marginLeft: 8 }}>
            新建 Notebook
          </Button>
        </Empty>
      </div>
    </section>
  );
}

// ─────────────────────── 工作区（左栏 + 右栏两张卡） ───────────────────────

function NotebookWorkspace({
  notebook,
  siblings,
  onPick,
  onCreate,
}: {
  notebook: Notebook;
  siblings: { id: string; title: string; task_no?: string | null }[];
  onPick: (id: string | null) => void;
  onCreate: () => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [messageApi, contextHolder] = message.useMessage();
  // 高危确认框走 hook 版（进 contextHolder）：静态 Modal.confirm 拿不到主题，在测试里也不可靠。
  const [modalApi, modalHolder] = Modal.useModal();

  const parsed = useMemo(() => parseNotebookContent(notebook.content), [notebook.content]);
  const [savedContent, setSavedContent] = useState(() => serializeNotebookContent(parsed, parsed.cells.length ? parsed.cells : ['']));
  const [cells, setCells] = useState(() => parsed.cells.map((cell) => ({ id: nextCellId(), value: cell })));
  const [cellStates, setCellStates] = useState<Record<string, CellState>>({});
  const [persistOutput, setPersistOutput] = useState(true);
  const [lastBackend, setLastBackend] = useState<string | null>(null);
  const [lastDegraded, setLastDegraded] = useState<string | null>(null);
  const [cursor, setCursor] = useState<{ line: number; column: number } | null>(null);
  const [ticket, setTicket] = useState<JupyterEnvTicket | null>(null);
  const [showDevEnv, setShowDevEnv] = useState(false);
  const [labLoaded, setLabLoaded] = useState(false);
  const [showJupyter, setShowJupyter] = useState(false);
  const [tab, setTab] = useState<OutputTabKey>('output');
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [clearedAt, setClearedAt] = useState<string | null>(null);
  const [localLines, setLocalLines] = useState<WorkbenchLogLine[]>([]);
  const [localRun, setLocalRun] = useState<{
    ok: boolean | null;
    durationMs: number | null;
    label: string;
    at: string | null;
    blocked?: boolean;
  } | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [autoSave, setAutoSave] = useState(false);
  const localSeq = useRef(0);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  const update = useUpdateNotebook();
  const executeCell = useExecuteCell();
  const runAll = useRunNotebook();
  const startDevSession = useStartDevSession();
  const createWorkload = useCreateWorkload();
  const archiveNotebook = useDeleteNotebook();
  const openEnv = useOpenJupyterEnv();

  const sessionId = notebook.session_id;

  // ── 会话侧真实数据（全部只读 GET，可按 status 轮询）──
  const sessionQuery = useQuery({
    queryKey: ['sandbox-session', sessionId ?? null],
    queryFn: () => sandboxApi.get(sessionId as string),
    enabled: !!sessionId,
    refetchInterval: 20_000,
  });
  const sessionStatus = sessionQuery.data?.status ?? null;
  const alive = isAliveSessionStatus(sessionStatus);

  // D-e2e-48：状态条 CPU/内存——5s 轮询真端口 GET /sandbox-sessions/{id}/metering；
  // 后端读不到 cgroup（l2c 在 VM 内／会话非运行）就回 available:false，前端继续未计量。
  const meteringQuery = useQuery({
    queryKey: ['session-metering', sessionId ?? null],
    queryFn: () => sandboxApi.getMetering(sessionId as string),
    enabled: !!sessionId && alive,
    refetchInterval: 5_000,
  });
  const metering = meteringQuery.data;

  const filesQuery = useQuery({
    queryKey: ['session-files', sessionId ?? null],
    queryFn: () => sandboxApi.listFiles(sessionId as string),
    enabled: !!sessionId && alive,
  });
  const usageQuery = useQuery({
    queryKey: ['session-usage', sessionId ?? null],
    queryFn: () => sandboxApi.getUsage(sessionId as string),
    enabled: !!sessionId && alive,
  });
  const logsQuery = useQuery({
    queryKey: ['session-logs', sessionId ?? null],
    queryFn: () => sandboxApi.getLogs(sessionId as string, { limit: 200 }),
    enabled: !!sessionId,
    refetchInterval: 10_000,
  });
  const quotaQuery = useQuotaUsage();

  const sessionDir = sessionDirOf(ticket?.notebook_path);
  const treeQuery = useQuery({
    queryKey: ['jupyter-contents', ticket?.session_id ?? '', sessionDir],
    queryFn: async () => {
      const tree = await fetchJupyterTree(sessionDir);
      return tree;
    },
    enabled: !!(ticket && sessionDir && labLoaded),
  });

  const terminateSession = useMutation({
    mutationFn: (id: string) => sandboxApi.terminate(id),
    onSuccess: () => {
      messageApi.success('沙箱会话已终止');
      void queryClient.invalidateQueries({ queryKey: ['sandbox-session'] });
      void logsQuery.refetch();
    },
    onError: (err: unknown) => messageApi.error(`终止失败：${getErrorMessage(err, '后端未返回原因')}`),
  });

  // ── 文件目录数据源：开发环目录（递归、含字节）优先，否则会话上传文件清单 ──
  const jupyterRoot = treeQuery.data && showDevEnv && labLoaded ? treeQuery.data.root : null;
  const jupyterNodes: WorkbenchFileNode[] | null = jupyterRoot
    ? (jupyterRoot.children ?? []).map(jupyterEntryToNode)
    : null;
  const sessionNodes = Array.isArray(filesQuery.data?.files) ? sessionFilesToTree(filesQuery.data.files) : null;
  // 后端对 cube/l2c 会话会直说 files=null（工作区在 VM 内，宿主侧不可枚举），不是「空目录」。
  const workspaceNotEnumerable = alive && !!filesQuery.data && !Array.isArray(filesQuery.data.files);
  const treeNodes: WorkbenchFileNode[] | null = jupyterNodes ?? sessionNodes;
  const treeSummary = treeNodes ? summarizeFiles(treeNodes) : null;
  const listed = useMemo(() => treeNodes ?? [], [treeNodes]);
  const usageBytes = usageQuery.data?.workspace?.bytes;
  const panelSummary = treeSummary
    ? {
        count: countFiles(listed),
        bytes: typeof usageBytes === 'number' ? usageBytes : treeSummary.bytes,
        partial: !!treeSummary.partial,
      }
    : null;
  const treeError = jupyterNodes
    ? null
    : filesQuery.isError
      ? getErrorMessage(filesQuery.error, '后端未返回文件清单')
      : null;
  const quotaLimitMb = quotaQuery.data?.tenant_resources?.limits?.disk_mb;
  const quotaText = typeof quotaLimitMb === 'number' && quotaLimitMb > 0 ? gibFromMb(quotaLimitMb) : null;

  const panelEmptyHint = !sessionId
    ? '还没有沙箱会话：点「运行 → 启动调试沙箱」或直接执行单元格，后端会创建会话，那时才谈得上沙箱内目录。'
    : sessionQuery.isPending
      ? '正在向后端确认沙箱会话状态（GET /sandbox-sessions/{id}）。'
      : sessionQuery.isError
        ? `沙箱会话状态查询失败：${getErrorMessage(sessionQuery.error, '后端未返回原因')}`
        : !alive
          ? `沙箱会话已是终态（status=${sessionStatus ?? UNKNOWN_TEXT}）：工作区按后端口径回收，目录不再可读。`
          : workspaceNotEnumerable
            ? '后端说这个会话的工作区在虚机内，宿主侧不可枚举（GET /sandbox-sessions/{id}/files 返 files=null）：到 JupyterLab 开发环里看目录。'
            : '会话文件接口返回空清单（GET /sandbox-sessions/{id}/files 只列工作区 files/ 顶层）：确实一个文件都没有。';

  const refreshTree = () => {
    if (jupyterNodes) void treeQuery.refetch();
    else void filesQuery.refetch();
  };

  const chosenFiles = useMemo(() => {
    const walk = (nodes: WorkbenchFileNode[]): WorkbenchFileNode[] =>
      nodes.flatMap((node) => (node.isDirectory ? walk(node.children ?? []) : [node]));
    return walk(listed).filter((node) => selectedFiles.includes(node.key));
  }, [listed, selectedFiles]);

  const downloadSelected = async () => {
    if (!sessionId || !chosenFiles.length) return;
    setDownloading(true);
    for (const file of chosenFiles) {
      try {
        const { blob, review } = await sandboxApi.downloadFile(sessionId, file.name);
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = file.name;
        link.click();
        URL.revokeObjectURL(url);
        if (review?.status === 'redacted') messageApi.warning(`${file.name}：输出审查已脱敏后才放行`);
      } catch (err) {
        const detail = (err as { detail?: { findings_count?: number } }).detail;
        messageApi.error(
          `${file.name} 取回失败：${getErrorMessage(err, '后端未返回原因')}${
            detail?.findings_count ? `（命中 ${detail.findings_count} 项）` : ''
          }`,
        );
      }
    }
    setDownloading(false);
  };

  // ── 内容编辑与保存 ──
  const contentNow = serializeNotebookContent(parsed, cells.map((cell) => cell.value));
  const dirty = contentNow !== savedContent;
  // 「后端被别人改了」= 后端现在的内容 ≠ 本页上次同步的内容（与你在编辑区改了多少无关）。
  const serverChanged = !!savedContent && savedContent !== canonicalizeNotebookContent(notebook.content);

  const saveContent = async (nextCells = cells, silent = false) => {
    const sources = nextCells.map((cell) => cell.value);
    try {
      const doc = await update.mutateAsync({
        id: notebook.id,
        payload: { content: canonicalizeNotebookContent(serializeNotebookContent(parsed, sources)) },
      });
      setSavedContent(serializeNotebookContent(parsed, sources));
      setSavedAt(clockOf(doc.updated_at));
      if (!silent) messageApi.success(`已保存（v${doc.version}）`);
    } catch (err) {
      messageApi.error(`保存失败：${describeFlowError(err, '后端未返回原因')}`);
    }
  };

  // D-e2e-48：自动保存对齐原型卡头「已自动保存 HH:MM:SS」——实现 = 本页前端每次编辑
  // 重置一个 2s 计时器，到点静默 PUT（服务端仍无自动保存任务，离开本页即不再触发）；
  // 自动保存与手动保存同价：每次都是新版本（代价写在卡头 tooltip，不假装免费）。
  const autoSaveBridge = useRef(saveContent);
  autoSaveBridge.current = saveContent;
  // CR B-1：服务端已被别人改过时，自动保存不得盲 PUT 掉别人的版本——
  // 冲突横幅（reloadFromServer）必须活到用户亲手处置；手动保存是用户
  // 知情决策，仍照走。ref 而非闭包：2s 窗口内后端回流也要被看见。
  const serverChangedRef = useRef(false);
  serverChangedRef.current = serverChanged;
  useEffect(() => {
    if (!dirty) return undefined;
    const timer = window.setTimeout(() => {
      if (serverChangedRef.current) {
        setAutoSave(false);
        return;
      }
      if (!update.isPending) {
        setAutoSave(true);
        void autoSaveBridge.current(undefined, true);
      }
    }, AUTO_SAVE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [dirty, cells, update.isPending]);

  const reloadFromServer = () => {
    const fresh = parseNotebookContent(notebook.content);
    setSavedContent(serializeNotebookContent(fresh, fresh.cells.length ? fresh.cells : ['']));
    setCells(fresh.cells.map((cell) => ({ id: nextCellId(), value: cell })));
    setCellStates({});
  };

  const pushLocalLine = (line: Omit<WorkbenchLogLine, 'key' | 'clock'>) => {
    localSeq.current += 1;
    setLocalLines((prev) => [
      ...prev.slice(-49),
      { ...line, key: `local:${localSeq.current}`, clock: clockOf(line.at) },
    ]);
  };

  const markExecuted = (label: string, result: { ok: boolean; backend?: string | null; exit_code?: number; output_blocked?: boolean; degraded_reason?: string | null; kernel_error?: string | null; blocked_reason?: string | null } | null, failure: unknown) => {
    const at = new Date().toISOString();
    if (!result) {
      setLocalRun({ ok: null, durationMs: null, label, at });
      pushLocalLine({
        at,
        text: `本页发起 ${label} · 请求失败：${describeFlowError(failure, '后端未返回原因')}`,
        tone: 'error',
        problem: true,
      });
      return;
    }
    if (result.backend) setLastBackend(result.backend);
    setLastDegraded(result.degraded_reason ?? null);
    const ok = !!result.ok && !result.output_blocked;
    setLocalRun({ ok, durationMs: null, label, at, blocked: !!result.output_blocked });
    pushLocalLine({
      at,
      text: `本页发起 ${label} · 后端 ${result.backend ?? UNKNOWN_TEXT} · 退出码 ${result.exit_code ?? UNKNOWN_TEXT}${
        result.output_blocked ? ` · 输出被阻断（${result.blocked_reason ?? 'output_inspection_blocked'}）` : ''
      }${result.degraded_reason ? ` · 降级：${result.degraded_reason}` : ''}${
        result.kernel_error ? ` · 内核错误：${String(result.kernel_error).slice(0, 120)}` : ''
      }`,
      tone: ok ? 'ok' : 'error',
      problem: !ok,
    });
    // 审计流是权威来源：跑完就补拉一次，让状态与耗时随后由后端字段说话。
    void logsQuery.refetch();
    void sessionQuery.refetch();
  };

  const runCell = async (cellId: string, index: number) => {
    const cell = cells[index];
    if (!cell) return;
    setCellStates((prev) => ({ ...prev, [cellId]: { running: true, result: null } }));
    try {
      const result = await executeCell.mutateAsync({
        id: notebook.id,
        payload: {
          code: cell.value,
          cell_index: persistOutput ? index : undefined,
          persist: persistOutput,
        },
      });
      setCellStates((prev) => ({ ...prev, [cellId]: { running: false, result } }));
      markExecuted(`单元格 #${index + 1}`, result, null);
    } catch (err) {
      setCellStates((prev) => ({ ...prev, [cellId]: { running: false, result: null } }));
      markExecuted(`单元格 #${index + 1}`, null, err);
      messageApi.error(`执行失败：${describeFlowError(err, '单元格执行失败')}`);
    }
  };

  const runAllCells = async () => {
    try {
      const result = await runAll.mutateAsync(notebook.id);
      const list = result.results ?? [];
      list.forEach((item) => {
        const cell = cells[item.cell_index];
        if (cell) setCellStates((prev) => ({ ...prev, [cell.id]: { running: false, result: item } }));
      });
      const failed = list.find((item) => !item.ok || item.output_blocked);
      const summaryResult = list.length
        ? { ...(failed ?? list[list.length - 1]), ok: result.passed && !failed }
        : null;
      markExecuted(
        `全部 ${list.length} 个单元格${result.passed ? '（通过）' : '（有失败）'}`,
        summaryResult,
        null,
      );
    } catch (err) {
      markExecuted('全部单元格', null, err);
      messageApi.error(`运行失败：${describeFlowError(err, '运行全部失败')}`);
    }
  };

  const startSandbox = () => {
    startDevSession.mutate(notebook.id, {
      onSuccess: (created) => {
        messageApi.success(`调试沙箱已就绪：会话 ${created.session_id}（等级 ${created.sandbox_level}）`);
        void queryClient.invalidateQueries({ queryKey: ['sandbox-session'] });
        void queryClient.invalidateQueries({ queryKey: ['session-logs'] });
      },
      onError: (err: unknown) => messageApi.error(`启动调试沙箱失败：${describeFlowError(err, '后端未返回原因')}`),
    });
  };

  const openDevEnv = () => {
    openEnv.mutate(notebook.id, {
      onSuccess: (next) => {
        setLabLoaded(false);
        setTicket(next);
        setShowDevEnv(true);
      },
      onError: (err: unknown) => messageApi.error(`打开开发环失败：${describeFlowError(err, '后端未返回可用票据')}`),
    });
  };

  const promote = () => {
    createWorkload.mutate(
      {
        name: `${notebook.title} 负载`,
        notebook_id: notebook.id,
        contract_id: notebook.contract_id ?? undefined,
      },
      {
        onSuccess: (wl) =>
          messageApi.success(
            `已提升为负载：${wl.name}${wl.contract_id ? '' : '（未关联合约，发布前需在负载中心补齐）'}`,
          ),
        onError: (err: unknown) => messageApi.error(`提升失败：${describeFlowError(err, '提升为负载失败')}`),
      },
    );
  };

  const archive = () => {
    archiveNotebook.mutate(notebook.id, {
      onSuccess: () => {
        messageApi.success('已归档');
        onPick(null);
      },
      onError: (err: unknown) => messageApi.error(`归档失败：${describeFlowError(err, '归档失败')}`),
    });
  };

  const focusLab = () => {
    iframeRef.current?.contentWindow?.focus();
    messageApi.info('焦点已交给 JupyterLab：运行/中断都在开发环里按它自己的口径执行（本页无法替 iframe 里的内核跑单元格）');
  };

  // ── 日志与执行态 ──
  const logLines = applyViewCleared(
    buildLogLines(logsQuery.data?.logs ?? [], localLines),
    clearedAt,
  );
  const lastBackendExec = lastExecutionFromLogs(logsQuery.data?.logs ?? []);
  const status = runStatusView(lastBackendExec, localRun);
  const conn: ConnectionState = connectionState({
    sessionId,
    status: sessionStatus,
    loading: sessionQuery.isPending,
    error: sessionQuery.isError ? sessionQuery.error : null,
    degradedBackend: lastBackend ?? notebook.execution_backend,
    degradedReason: lastDegraded,
  });
  const memoryQuota = memoryQuotaMb(sessionQuery.data?.resource_limits ?? null, notebook.debug_memory_mb);
  const language = notebook.language?.trim() || UNKNOWN_TEXT;
  const fileName = fileBasename(notebook.path, notebook.title || UNKNOWN_TEXT);
  // 卡头那个灰色日期就是后端写的 updated_at（列 onupdate=now()），只取到日。
  const updatedDate = notebook.updated_at ? notebook.updated_at.slice(0, 10) : UNKNOWN_TEXT;
  const eol = detectEol(contentNow);
  const stopEnabled = alive && !terminateSession.isPending;
  const stopHint = !sessionId
    ? '还没有沙箱会话可终止'
    : alive
      ? '终止沙箱会话（POST /sandbox-sessions/{id}/terminate）：平台没有「中断单次执行」的接口，终止会话就是真实的停止手段'
      : `会话已是终态（status=${sessionStatus ?? UNKNOWN_TEXT}），没有可终止的会话`;

  // ── 工具条五项菜单（全部指向真实动作；没有真动作的项一律 disabled 并写明原因）──
  const cellBranch = !showDevEnv;
  // 卡头文件名下拉 = 开发任务切换器（真动作）；数据源是本页已载入的那一页任务，不是全量检索。
  const switcher: MenuProps = {
    onClick: ({ key }) => {
      if (key === '__create') onCreate();
      else onPick(key);
    },
    items: [
      ...(siblings.length
        ? siblings.map((item) => ({
            key: item.id,
            label: `${item.title}${item.task_no ? ` · ${item.task_no}` : ''}`,
            disabled: item.id === notebook.id,
          }))
        : [{ key: '__none', label: '后端没有返回其他开发任务', disabled: true }]),
      { type: 'divider' },
      { key: '__create', label: '新建 Notebook…' },
    ],
  };

  const fileMenu: MenuProps = {
    onClick: ({ key }) => {
      if (key === 'save') {
        setAutoSave(false);
        void saveContent();
      }
      if (key === 'reload') reloadFromServer();
      if (key === 'persist') setPersistOutput((prev) => !prev);
      if (key === 'promote') promote();
      if (key === 'archive') archive();
      if (key === 'create') onCreate();
    },
    items: [
      { key: 'create', label: '新建 Notebook…' },
      { key: 'save', label: '保存（内容写回后端，版本 +1）', disabled: !dirty || update.isPending },
      { key: 'reload', label: '载入后端最新内容', disabled: !serverChanged },
      { key: 'persist', label: `${persistOutput ? '☑' : '☐'} 输出写入笔记（persist）` },
      { type: 'divider' },
      { key: 'promote', label: '提升为负载' },
      { key: 'archive', label: '归档当前 Notebook', danger: true },
    ],
  };

  const editMenu: MenuProps = {
    onClick: ({ key }) => {
      if (key === 'add') setCells((prev) => [...prev, { id: nextCellId(), value: '' }]);
      if (key === 'remove') setCells((prev) => (prev.length > 1 ? prev.slice(0, -1) : prev));
    },
    items: cellBranch
      ? [
          { key: 'add', label: '新增单元格' },
          { key: 'remove', label: '删除末尾单元格', disabled: cells.length <= 1 },
        ]
      : [
          {
            key: 'lab',
            label: '开发环分支：单元格由 JupyterLab 自己编辑，本页不代理',
            disabled: true,
          },
        ],
  };

  const viewMenu: MenuProps = {
    onClick: ({ key }) => {
      if (key === 'lab') (showDevEnv ? () => setShowDevEnv(false) : openDevEnv)();
      if (key === 'jupyter') setShowJupyter((prev) => !prev);
      if (key === 'tree') refreshTree();
      if (key === 'sandbox') startSandbox();
      if (key === 'records') void navigate(`/flow/tasks/${notebook.id}`);
      if (key === 'quota') void navigate('/flow/quota');
    },
    items: [
      {
        key: 'lab',
        label: showDevEnv ? '返回单元格编辑器' : '打开 Jupyter 开发环（申请一次性票据）',
        disabled: openEnv.isPending,
      },
      { key: 'jupyter', label: `${showJupyter ? '☑' : '☐'} Jupyter 可用性探测` },
      { key: 'tree', label: '刷新文件目录' },
      { key: 'sandbox', label: '启动/复用调试沙箱' },
      { type: 'divider' },
      { key: 'records', label: '运行记录（任务详情）' },
      { key: 'quota', label: '配额用量' },
    ],
  };

  const languageMenu: MenuProps = {
    items: [
      {
        key: 'lang',
        label: `语言：${language} —— 创建时确定，PUT /notebooks 载荷里没有 language，故本页只读`,
        disabled: true,
      },
      { key: 'kernel', label: `内核：${notebook.kernel ?? UNKNOWN_TEXT}（NotebookOut.kernel）`, disabled: true },
    ],
  };

  const runMenu: MenuProps = {
    onClick: ({ key }) => {
      if (key === 'all') void runAllCells();
      if (key === 'sandbox') startSandbox();
      if (key === 'lab') focusLab();
    },
    items: cellBranch
      ? [
          { key: 'all', label: '运行全部单元格', disabled: runAll.isPending },
          { key: 'sandbox', label: '启动/复用调试沙箱' },
          {
            key: 'hint',
            label: '中断单次执行没有接口：要停止只能终止会话（输出卡右侧的停止图标）',
            disabled: true,
          },
        ]
      : [
          { key: 'lab', label: '把焦点交给 JupyterLab（运行在它里面执行）' },
          { key: 'sandbox', label: '启动/复用调试沙箱' },
        ],
  };

  const menuProps: Record<(typeof NBW_MENUS)[number], MenuProps> = {
    文件: fileMenu,
    编辑: editMenu,
    查看: viewMenu,
    语言: languageMenu,
    运行: runMenu,
  };

  return (
    <div className="nbw-columns">
      {contextHolder}
      {modalHolder}
      <FileDirectoryPanel
        nodes={listed}
        summary={panelSummary}
        loading={(filesQuery.isPending && alive) || (showDevEnv && treeQuery.isPending)}
        error={treeError}
        emptyHint={panelEmptyHint}
        quotaText={quotaText}
        quotaHint={
          quotaText
            ? `分子=本会话工作区实测字节（GET /sandbox-sessions/${sessionId ?? '-'}/usage → workspace.bytes；开发环分支=已读到的文件字节合计）。${DISK_QUOTA_SCOPE_HINT}`
            : '账号级磁盘额度未取到（GET /deliveries/quota-usage 未返回 tenant_resources.limits.disk_mb），故分母显示「配额未记录」。'
        }
        partialHint={treeQuery.data?.incomplete ? treeQuery.data.note ?? '目录清单不是全量' : null}
        refreshing={jupyterNodes ? treeQuery.isFetching : filesQuery.isFetching}
        onRefresh={refreshTree}
        refreshEnabled={!!sessionId && (alive || showDevEnv)}
        selected={selectedFiles}
        onToggle={(key, checked) =>
          setSelectedFiles((prev) => (checked ? [...prev, key] : prev.filter((item) => item !== key)))
        }
        onDownloadSelected={() => {
          void downloadSelected();
        }}
        downloading={downloading}
      />

      <div className="nbw-right">
        {/* ───── 编辑器卡片（图 1 §3.2）───── */}
        <section className="nbw-card nbw-card-grow" aria-label="代码编辑器">
          <div className="nbw-card-head">
            <div style={{ minWidth: 0 }}>
              <Dropdown menu={switcher} trigger={['click']}>
                <span className="nbw-editor-name">
                  <FileTextOutlined style={{ color: '#1b6ef3' }} />
                  <span style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {fileName}
                  </span>
                  <DownOutlined style={{ fontSize: 10, color: '#8c8c8c' }} />
                </span>
              </Dropdown>
              <div className="nbw-editor-meta">
                <span>{updatedDate}</span>
                <SaveState dirty={dirty} saving={update.isPending} savedAt={savedAt} auto={autoSave} />
                <Tooltip title="这份内容在本页改过但还没写回后端：文件 → 保存（PUT /notebooks/{id}）">
                  {dirty ? <InfoCircleOutlined style={{ color: '#ad6800' }} /> : null}
                </Tooltip>
              </div>
            </div>
            <Dropdown menu={languageMenu} trigger={['click']}>
              <span className="nbw-lang-pick">
                {language} <DownOutlined style={{ fontSize: 10 }} />
              </span>
            </Dropdown>
          </div>

          <div className="nbw-toolbar">
            <div className="nbw-toolbar-left">
              {NBW_MENUS.map((label) => (
                <Dropdown key={label} menu={menuProps[label]} trigger={['click']}>
                  <span className="nbw-menu-label">{label}</span>
                </Dropdown>
              ))}
            </div>
            <div className="nbw-toolbar-right">
              <Tooltip
                title={
                  cellBranch
                    ? '本页 Monaco 编辑器实例的 tabSize=4（写回后端时按源码原样保留，不做格式化）'
                    : '开发环是 JupyterLab iframe：缩进由它自己的编辑器决定，本页读不到，标未接入'
                }
              >
                <span className="nbw-tool-item">{cellBranch ? '缩进：4 空格' : '缩进：未接入'}</span>
              </Tooltip>
              <Tooltip
                title={
                  cellBranch
                    ? '浏览器与 Monaco 的文本都是 UTF-8（PUT /notebooks 的 JSON 体同理）'
                    : '开发环 iframe 内文件的编码本页读不到，标未接入'
                }
              >
                <span className="nbw-tool-item">{cellBranch ? '编码：UTF-8' : '编码：未接入'}</span>
              </Tooltip>
              <Tooltip title={conn.hint}>
                <span className={`nbw-chip nbw-chip-${conn.tone}`}>{conn.label}</span>
              </Tooltip>
            </div>
          </div>

          <div className="nbw-code-area">
            {serverChanged ? (
              <Alert
                type="warning"
                showIcon
                message="后端内容已被别处更新，与本页编辑器不一致"
                action={
                  <Button size="small" onClick={reloadFromServer}>
                    载入后端最新内容
                  </Button>
                }
                description="编辑区仍是你的版本，运行执行的是编辑区内容；保存会让后端版本 +1。"
              />
            ) : null}
            {openEnv.isError ? (
              <Alert type="error" showIcon message="打开开发环失败" description={describeFlowError(openEnv.error, '后端未返回可用票据')} />
            ) : null}

            {showDevEnv && ticket ? (
              <DevEnvFrame
                ticket={ticket}
                iframeRef={iframeRef}
                onLoaded={() => setLabLoaded(true)}
                treeError={treeQuery.isError ? getErrorMessage(treeQuery.error, '开发环目录读取失败') : null}
              />
            ) : (
              <>
                <BackendStrip
                  notebook={notebook}
                  cellStates={cellStates}
                  persistOutput={persistOutput}
                  onTogglePersist={() => setPersistOutput((prev) => !prev)}
                  lastBackend={lastBackend}
                />
                {cells.map((cell, index) => (
                  <div className="nbw-cell-block" key={cell.id}>
                    <div className="nbw-cell-bar">
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        #{index + 1} {notebook.kernel ?? 'cell'}
                      </Text>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        {cellStates[cell.id]?.running || executeCell.isPending ? <Spin size="small" /> : null}
                        <Button size="small" onClick={() => void runCell(cell.id, index)} disabled={executeCell.isPending}>
                          运行
                        </Button>
                        <Button size="small" type="text" disabled={cells.length === 1} onClick={() => setCells((prev) => prev.filter((item) => item.id !== cell.id))}>
                          删除
                        </Button>
                      </div>
                    </div>
                    <Editor
                      height="160px"
                      language={notebook.language ?? 'python'}
                      value={cell.value}
                      onChange={(value) => {
                        const next = cells.map((item) => (item.id === cell.id ? { ...item, value: value ?? '' } : item));
                        setCells(next);
                      }}
                      onMount={(editor) => {
                        editor.onDidChangeCursorPosition((event) =>
                          setCursor({ line: event.position.lineNumber, column: event.position.column }),
                        );
                      }}
                      options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, tabSize: 4 }}
                    />
                    {cellStates[cell.id]?.result ? (
                      <OutputView result={cellStates[cell.id]!.result!} />
                    ) : null}
                  </div>
                ))}
                <Button size="small" icon={<ReloadOutlined />} onClick={() => setCells((prev) => [...prev, { id: nextCellId(), value: '' }])}>
                  新增单元格
                </Button>
              </>
            )}
            {showJupyter ? <JupyterPanel notebookId={notebook.id} /> : null}
          </div>

          <div className="nbw-statusbar">
            <div className="nbw-statusbar-group">
              <Tooltip
                title={
                  cellBranch
                    ? '光标位置来自本页 Monaco 编辑器的 position 事件；还没点进编辑器时没有光标可报，显示「—」。'
                    : '未接入：开发环是 iframe，跨文档拿不到里面的光标位置'
                }
              >
                <span>{cursorText(cellBranch ? cursor : null)}</span>
              </Tooltip>
              <span>{cellBranch ? 'UTF-8' : '编码 未接入'}</span>
              <span>{eol}</span>
              <Tooltip title="语言取 NotebookOut.language（真字段）；沙箱内解释器版本没有任何接口下发（cells/execute 只回 ok/backend/stdout…，runtime-status 也不含版本），故版本号显示未计量。">
                <span>{`${language} · 版本${NBW_NOT_MEASURED}`}</span>
              </Tooltip>
            </div>
            <div className="nbw-statusbar-group">
              <Tooltip
                title={
                  metering?.available && metering.cpu_percent != null
                    ? `cgroup v2 cpu.stat 真采样（${metering.sampled_ms ?? 250}ms 窗口，单核百分比，5s 轮询；来源 ${metering.source ?? '-'}）。`
                    : `未计量：${metering?.reason ?? '无会话或尚未取到读数'}；画一个 CPU 百分比就是编数字。`
                }
              >
                <span>{metering?.available && metering.cpu_percent != null ? `CPU ${metering.cpu_percent}%` : `CPU ${NBW_NOT_MEASURED}`}</span>
              </Tooltip>
              <Tooltip
                title={
                  metering?.available && metering.memory_used_mb != null
                    ? `分子 = cgroup memory.current（5s 轮询）；分母优先 cgroup memory.max，无上限时回落会话配额。`
                    : `已用侧未计量：${metering?.reason ?? '无会话或尚未取到读数'}；分母是真配额：优先沙箱会话 resource_limits.memory_mb，没有则回落任务档位 debug_memory_mb。`
                }
              >
                <span>{`${metering?.available && metering.memory_used_mb != null ? `内存 ${gibFromMb(metering.memory_used_mb)}` : `内存 ${NBW_NOT_MEASURED}`} / ${(() => {
                  const limitMb = metering?.available ? metering.memory_limit_mb ?? memoryQuota : memoryQuota;
                  return limitMb ? `${gibFromMb(limitMb)} 配额` : '配额未记录';
                })()}`}</span>
              </Tooltip>
            </div>
          </div>
        </section>

        {/* ───── 输出 / 日志卡片（图 1 §3.3）───── */}
        <OutputLogCard
          lines={logLines}
          tab={tab}
          onTab={setTab}
          status={status}
          statusHint={status.hint}
          onStop={() => {
            if (!sessionId) return;
            // 高危动作：先二次确认再打下后端那条真接口（项目里高危动作的统一做法）。
            modalApi.confirm({
              title: '终止沙箱会话？',
              content: `沙箱里正在跑的东西一起结束，会话密钥随即销毁，工作区按后端口径回收（POST /sandbox-sessions/${sessionId}/terminate）。`,
              okText: '终止',
              okButtonProps: { danger: true },
              cancelText: '取消',
              onOk: () => terminateSession.mutate(sessionId),
            });
          }}
          stopEnabled={stopEnabled}
          stopHint={stopHint}
          onClear={() => {
            // 「清空视图」= 只看比这一刻更晚的事件；后端审计日志一行都不动。
            setClearedAt(logLines.length ? logLines[logLines.length - 1].at : new Date().toISOString());
            setLocalLines([]);
            setTab('output');
          }}
          clearEnabled={logLines.length > 0}
          sessionLine={
            sessionId
              ? `沙箱会话 ${sessionId} · 本页读的是后端会话审计流最近 ${logsQuery.data?.count ?? 0} 条`
              : '沙箱会话 未创建 · 执行时后端会为本任务创建会话，日志届时才有来源'
          }
          sessionHint={
            '原型那句「输出结果不落盘，仅保留最近 24 小时日志」在本平台不成立，故不照抄：'
            + 'persist=true 时单元格输出会写回笔记内容（落库），执行事件的保留期由审计日志保留策略决定（平台没有 24 小时清理）。'
          }
          fullLogTo={sessionId ? `/sandbox-sessions/${sessionId}` : null}
          onOpenFullLog={() => sessionId && void navigate(`/sandbox-sessions/${sessionId}`)}
          notices={
            showDevEnv && ticket ? (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  开发环会话 {ticket.session_id} · 等级 {ticket.sandbox_level ?? UNKNOWN_TEXT} · 票据 {ticket.ttl}s 一次性有效
                  {ticket.injected.length ? ` · 已注入 ${ticket.injected.length} 个供数文件到 input/` : ' · input/ 未注入供数'}
                </Text>
              </div>
            ) : null
          }
        />
      </div>
    </div>
  );
}

/** 卡头保存态：只在真保存成功后亮绿勾，并写明这个时间是保存动作写的，不是自动保存。 */
/** 卡头保存态：auto=true 时文案是「已自动保存」（D-e2e-48 前端防抖 PUT，非后端任务）。 */
function SaveState({ dirty, saving, savedAt, auto }: { dirty: boolean; saving: boolean; savedAt: string | null; auto: boolean }) {
  if (saving) {
    return (
      <span>
        <Spin size="small" /> 保存中
      </span>
    );
  }
  if (savedAt) {
    return (
      <Tooltip title="时间取 PUT /notebooks/{id} 返回的 updated_at（列 onupdate=now()）。自动保存 = 本页前端 2 秒防抖 PUT：服务端没有自动保存任务，离开本页即不再触发；每次自动保存同样 +1 版本。">
        <span className="nbw-saved-ok">
          <CheckCircleFilled style={{ marginRight: 4 }} />
          {auto ? '已自动保存' : '已保存'} {savedAt}
        </span>
      </Tooltip>
    );
  }
  return <span>{dirty ? '有未保存修改' : '无改动'}</span>;
}

/** 真 JupyterLab iframe（SS-19 已批准的代码区内偏离）；只在外框里换位置，不改它的行为。 */
function DevEnvFrame({
  ticket,
  iframeRef,
  onLoaded,
  treeError,
}: {
  ticket: JupyterEnvTicket;
  iframeRef: RefObject<HTMLIFrameElement | null>;
  onLoaded: () => void;
  treeError: string | null;
}) {
  const level = ticket.sandbox_level;
  return (
    <>
      <Alert
        type="warning"
        showIcon
        message="开发环环境说明（不掩饰）"
        description={
          <div style={{ lineHeight: 1.7 }}>
            <div>
              JupyterLab 跑在独立容器里（<code>cds-jupyter</code>），会话等级 <code>{level ?? '未下发'}</code>
              {level === 'l2c' ? '（CubeSandbox MicroVM）' : '，不是 CubeSandbox MicroVM'}。终端接口已在镜像内关闭，
              文件与 kernel 由后端按本会话目录前缀限制。
            </div>
            <div>
              当前目录就是本会话的产物目录：只有写在这里的文件会被成果包采集，并且逐个过 T5 出口网关审查与水印；
              空产物会被如实记为真空。
            </div>
            {ticket.inject_errors?.length ? (
              <div style={{ color: '#c0392b' }}>
                供数异常 {ticket.inject_errors.length} 项（后端原话）：{ticket.inject_errors.slice(0, 3).join('；')}
                {ticket.inject_errors.length > 3 ? '…' : ''}
              </div>
            ) : null}
            {treeError ? <div style={{ color: '#c0392b' }}>左栏目录读取失败：{treeError}</div> : null}
          </div>
        }
      />
      <iframe
        ref={iframeRef}
        title="Jupyter 开发环"
        src={ticket.url}
        onLoad={onLoaded}
        style={{
          width: '100%',
          flex: '1 1 auto',
          minHeight: 360,
          border: '1px solid rgba(128,128,128,0.35)',
          borderRadius: 6,
          background: '#fff',
        }}
      />
    </>
  );
}

function OutputView({ result }: { result: CellExecuteResult }) {
  const pane = toOutputPane(result);
  if (!pane) return null;
  return (
    <div style={{ padding: 8, borderTop: '1px solid #eef1f6', display: 'grid', gap: 6 }}>
      {typeof pane.exitCode === 'number' ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {`退出码 ${pane.exitCode}`}
        </Text>
      ) : null}
      {pane.blocked ? (
        <Alert
          type="error"
          showIcon
          message="输出被管控策略阻断"
          description={pane.blockedReason ?? '后端未返回阻断原因（blocked_reason 为空）'}
        />
      ) : null}
      {pane.repr ? <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{pane.repr}</pre> : null}
      {pane.stdout ? <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{pane.stdout}</pre> : null}
      {pane.stderr ? <pre style={{ margin: 0, color: '#c0392b', whiteSpace: 'pre-wrap' }}>{pane.stderr}</pre> : null}
      {!pane.hasContent ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          无输出（stdout/stderr/repr 都是空）
        </Text>
      ) : null}
      {result.degraded_reason ? (
        <Text type="warning" style={{ fontSize: 12 }}>
          {`执行路径降级：${result.degraded_reason}`}
        </Text>
      ) : null}
    </div>
  );
}

/** 执行后端披露条：只转述后端下发的 backend/stateful/degraded_reason，不推断未登记的后端。 */
function BackendStrip({
  notebook,
  cellStates,
  persistOutput,
  onTogglePersist,
  lastBackend,
}: {
  notebook: Notebook;
  cellStates: Record<string, CellState>;
  persistOutput: boolean;
  onTogglePersist: () => void;
  lastBackend: string | null;
}) {
  const views = Object.values(cellStates)
    .map((state) => state.result)
    .filter(Boolean)
    .map((result) => backendFromCellResult(result!));
  const latest = lastBackend ? views.find((view) => view.backend === lastBackend) ?? null : null;
  const view = latest ?? backendFromNotebook(notebook);
  const source = latest ? '本次执行结果' : '笔记记录（尚未在本页执行）';

  return (
    <Alert
      type={view?.degraded ? 'warning' : 'info'}
      showIcon
      message={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span>
            {view
              ? `执行后端 ${view.backend}：${view.label}（${view.stateful ? '有状态' : '无状态'}·${source}）`
              : '执行后端未知（尚未执行，后端未下发 execution_backend）'}
          </span>
          <Checkbox checked={persistOutput} onChange={onTogglePersist}>
            输出写入笔记（persist）
          </Checkbox>
          {notebook.last_executed_at ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              最近执行 {notebook.last_executed_at}
            </Text>
          ) : null}
        </span>
      }
      description={
        view?.degraded ? (
          <span style={{ fontSize: 12 }}>{view.reasons.join('；')}</span>
        ) : view && !view.known ? (
          <span style={{ fontSize: 12 }}>后端标识未登记，不推断成降级，也不推断成健康。</span>
        ) : null
      }
    />
  );
}

function JupyterPanel({ notebookId }: { notebookId: string }) {
  const { data, isPending, isError, error } = useJupyterStatus(notebookId);
  if (isPending) return <Spin size="small" />;
  if (isError) {
    return (
      <Alert
        type="warning"
        showIcon
        message="Jupyter 可用性探测失败"
        description={<span style={{ fontSize: 12 }}>{getErrorMessage(error, '后端未返回原因')}</span>}
      />
    );
  }
  const dev = data?.dev_env ?? null;
  return (
    <div style={{ marginTop: 8 }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {`Jupyter: ${data?.jupyter_available ? '可用' : '不可用'} · 代理: ${data?.proxy_enabled ? '已启用' : '未启用'} · 执行后端: ${data?.notebook_execution_backend ?? UNKNOWN_TEXT}`}
      </Text>
      {data?.jupyter_path ? (
        <div>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {`Jupyter 路径: ${data.jupyter_path}`}
          </Text>
        </div>
      ) : null}
      {data?.probe_error ? (
        <div>
          <Text type="warning" style={{ fontSize: 12 }}>
            {`探测链本身失败（不等于未安装）: ${data.probe_error}`}
          </Text>
        </div>
      ) : null}
      {dev ? (
        <div>
          <Text type={dev.available ? 'secondary' : 'warning'} style={{ fontSize: 12 }}>
            {dev.available
              ? `在线开发环: 可用（等级 ${dev.sandbox_level}）`
              : `在线开发环: 不可用 —— ${dev.reason ?? '后端未说明原因'}`}
          </Text>
        </div>
      ) : null}
    </div>
  );
}

function CreateNotebookModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (notebook: Notebook) => void;
}) {
  const [form] = Form.useForm();
  const create = useCreateNotebook();
  const { data: contractOptions } = useContractOptions();
  const [messageApi, contextHolder] = message.useMessage();
  const title = Form.useWatch('title', form);
  const path = Form.useWatch('path', form);
  // 路径不唯一（后端不拦重名）：把已存在的本子提个醒，但不弹确认框——新建框架里没有这个口径。
  const { data: existing } = useNotebooks(null, 1, 50);
  const duplicated = !!path && (existing?.items ?? []).some((item) => item.path === path);

  useEffect(() => {
    if (!open) return;
    form.setFieldsValue({ title: '未命名分析', path: defaultNotebookPath('未命名分析'), contract_id: undefined });
  }, [open, form]);

  const submit = async () => {
    const values = await form.validateFields();
    try {
      const nb = await create.mutateAsync({
        title: values.title,
        path: values.path?.trim() || defaultNotebookPath(values.title),
        contract_id: values.contract_id ?? null,
      });
      messageApi.success('已创建');
      onCreated(nb);
    } catch (err) {
      messageApi.error(describeFlowError(err, '创建 Notebook 失败'));
    }
  };

  return (
    <Modal
      title="新建 Notebook"
      open={open}
      onCancel={onClose}
      onOk={() => {
        void submit();
      }}
      confirmLoading={create.isPending}
    >
      {contextHolder}
      <Form form={form} layout="vertical">
        <Form.Item name="title" label="标题" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="path" label="路径" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        {duplicated ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`已有 Notebook 用这个路径：${title ?? ''}`}
            description="后端不要求路径唯一（Notebook.path 无唯一约束），两个本子会共用这个名字，建议换一个。"
          />
        ) : null}
        <Form.Item name="contract_id" label="关联合约">
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="可选：关联合约后即可在线执行"
            options={(contractOptions ?? []).map((contract) => ({
              value: contract.id,
              label: `${contract.title}（${contract.contract_no}）`,
            }))}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
