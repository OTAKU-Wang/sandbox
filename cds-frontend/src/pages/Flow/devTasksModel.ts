import dayjs from 'dayjs';
import type { ComputeRun, Notebook, Workload } from '../../services/flow';
import { TASK_DEBUG_METHOD_OPTIONS, TASK_EXPORT_MODE_OPTIONS, UNKNOWN_TEXT, WorkloadStatus, formatMemoryGib, taskEnumLabel } from '../../services/flow';
import { COMPUTE_RUN_STATUS_META, WORKLOAD_STATUS_META } from './display';
import type { StatusMeta } from './display';

export type DevTaskKind = 'notebook' | 'workload' | 'run';

export interface DevTaskRow {
  key: string;
  kind: DevTaskKind;
  name: string;
  stage: StatusMeta;
  contract_id: string | null;
  at: string | null;
  target: string;
}

const KIND_TARGET: Record<DevTaskKind, string> = {
  notebook: '/notebooks',
  workload: '/workloads',
  run: '/compute',
};

const NOTEBOOK_STAGES = {
  published: { color: 'purple', label: '已发布生产' },
  debugging: { color: 'green', label: '调试中' },
  draft: { color: 'default', label: '草稿' },
} satisfies Record<string, StatusMeta>;

function passThrough(table: Record<string, StatusMeta>, value: string): StatusMeta {
  return table[value] ?? { color: 'default', label: value };
}

function publishedNotebookIds(workloads: Workload[]): Set<string> {
  return new Set(
    workloads
      .filter((workload) => workload.status === WorkloadStatus.Published && workload.source_notebook_id)
      .map((workload) => workload.source_notebook_id as string),
  );
}

function notebookStage(notebook: Notebook, published: ReadonlySet<string>): StatusMeta {
  return published.has(notebook.id)
    ? NOTEBOOK_STAGES.published
    : notebook.session_id
      ? NOTEBOOK_STAGES.debugging
      : NOTEBOOK_STAGES.draft;
}

export function buildDevTaskRows(input: {
  notebooks: Notebook[];
  workloads: Workload[];
  runs: ComputeRun[];
}): DevTaskRow[] {
  const publishedByNotebook = publishedNotebookIds(input.workloads);

  const rows: DevTaskRow[] = [
    ...input.notebooks.map((notebook): DevTaskRow => ({
      key: `notebook:${notebook.id}`,
      kind: 'notebook',
      name: notebook.title,
      stage: notebookStage(notebook, publishedByNotebook),
      contract_id: notebook.contract_id,
      at: notebook.last_executed_at ?? notebook.updated_at,
      target: KIND_TARGET.notebook,
    })),
    ...input.workloads.map((workload): DevTaskRow => ({
      key: `workload:${workload.id}`,
      kind: 'workload',
      name: `${workload.name} v${workload.version}`,
      stage: passThrough(WORKLOAD_STATUS_META, workload.status),
      contract_id: workload.contract_id,
      at: workload.published_at ?? workload.created_at,
      target: KIND_TARGET.workload,
    })),
    ...input.runs.map((run): DevTaskRow => ({
      key: `run:${run.id}`,
      kind: 'run',
      name: run.error_message ? `${run.status}：${run.error_message}` : `执行 ${run.scheduled_for ?? run.started_at ?? run.id}`,
      stage: passThrough(COMPUTE_RUN_STATUS_META, run.status),
      contract_id: run.contract_id,
      at: run.finished_at ?? run.started_at ?? run.scheduled_for,
      target: KIND_TARGET.run,
    })),
  ];

  return rows.sort((left, right) => {
    if (!left.at && !right.at) return left.key.localeCompare(right.key);
    if (!left.at) return 1;
    if (!right.at) return -1;
    return right.at.localeCompare(left.at);
  });
}

// ---------------------------------------------------------------------------
// M2 开发任务卡片（SS-18 §4）：/flow/tasks 的主视图 = notebook 的任务读视图。
// 诚实口径（项目铁律）：后端未下发的字段一律落成「未记录」，不补示例值、不拿 0 充数；
// 存量行（task_no/debug_memory_mb 为 null）仍是列表里正常的一张卡，不得被默认筛掉。
// ---------------------------------------------------------------------------

export interface DevTaskCard {
  key: string;
  id: string;
  task_no: string | null;
  /** 筛选栏与卡片共用的展示文本：null/空串 → 「未记录」。 */
  task_no_text: string;
  title: string;
  path: string;
  contract_id: string | null;
  debug_method: string | null;
  debug_method_text: string;
  export_mode: string | null;
  export_mode_text: string;
  debug_memory_mb: number | null;
  exec_memory_mb: number | null;
  debug_memory_text: string;
  exec_memory_text: string;
  /** 任务态（SS-18 §3：归档=已结束，否则进行中）；未登记取值原样透出。 */
  state: StatusMeta;
  /** 任务状态筛选桶：与上面的状态 Tag 同口径，未登记状态落 other（既非进行中也非已结束）。 */
  state_bucket: DevTaskStateBucket;
  /** 开发阶段（草稿/调试中/已发布生产），与 rows 视图同一口径。 */
  stage: StatusMeta;
  version: number;
  version_text: string;
  created_at: string | null;
  created_text: string;
  updated_at: string | null;
  updated_text: string;
  /** 沙箱引擎：任务载荷里没有这个字段，恒为「未记录」（理由见 DEV_TASK_ENGINE_HINT）。 */
  engine_text: string;
  /** 生产执行入口：预置到计算调度页的这条合约 + 这条任务已发布的生产版本。 */
  exec_path: string;
  /** 不可执行的原因（后端/既有文案原话）；null = 可执行。 */
  exec_disabled_reason: string | null;
  /** 进入在线调试工作台（带任务上下文）的站内路径。 */
  workbench_path: string;
  /** 任务详情页（原型 2-4）的站内路径。 */
  detail_path: string;
}

export interface DevTaskFilters {
  keyword: string;
  /** PRD §3.2.1 任务状态（全部/进行中/已结束）；null = 全部。 */
  state: DevTaskStateFilter | null;
  debug_method: string | null;
  export_mode: string | null;
  contract_id: string | null;
  /** 只隐藏「没有任务编号」的存量行；默认关，关掉后也不得隐藏其他行。 */
  only_numbered: boolean;
}

/** 新建入口的落地地址：带着当前合约筛选进整页，否则整页得重选一次合约。 */
export function devTaskCreateHref(contractId?: string | null): string {
  return contractId ? `/flow/tasks/new?contract_id=${contractId}` : '/flow/tasks/new';
}

export const EMPTY_DEV_TASK_FILTERS: DevTaskFilters = {
  keyword: '',
  state: null,
  debug_method: null,
  export_mode: null,
  contract_id: null,
  only_numbered: false,
};

const TASK_STATE_META: Record<string, StatusMeta> = {
  draft: { color: 'green', label: '进行中' },
  archived: { color: 'default', label: '已结束' },
};

export function notebookStateMeta(notebook: Notebook): StatusMeta {
  const status = (notebook.status ?? '').trim();
  if (!status) return { color: 'default', label: UNKNOWN_TEXT };
  return TASK_STATE_META[status] ?? { color: 'default', label: status };
}

/**
 * 状态色（antd 预设名）→ 图 2-4「浅底 + 同色系深字」标签的色调类（`.t24-tag.is-*`，真值见
 * D:/workspace/.cds-e2e/img-specs/2-4-measured.md §2/§8：绿 #e8f7ee/#00a870、红 #fdecee/#f53f3f、
 * 蓝 #e8f1ff/#1b6ef3，其余灰 #f2f3f5/#4e5969）。只换皮不换字：label 仍取后端状态映射。
 */
export function statusTagTone(color: string): string {
  if (color === 'green' || color === 'success') return 'is-ok';
  if (color === 'red' || color === 'error') return 'is-err';
  if (color === 'blue' || color === 'processing') return 'is-run';
  return 'is-idle';
}

// 任务状态筛选（PRD §3.2.1）：桶只认后端 NotebookStatus 的两个取值
// （app/models/notebook.py: draft / archived），标签文字与卡片状态 Tag 同一份来源。
// 未登记的状态既不归「进行中」也不归「已结束」，只在「全部」下可见——不靠猜把它们塞进某一桶。
export type DevTaskStateFilter = 'active' | 'ended';
export type DevTaskStateBucket = DevTaskStateFilter | 'other';

const STATE_BUCKET_BY_STATUS: Record<string, DevTaskStateBucket> = {
  draft: 'active',
  archived: 'ended',
};

export const DEV_TASK_STATE_OPTIONS: { label: string; value: DevTaskStateFilter }[] = [
  { label: TASK_STATE_META.draft.label, value: 'active' },
  { label: TASK_STATE_META.archived.label, value: 'ended' },
];

export function devTaskStateLabel(value: DevTaskStateFilter): string {
  return DEV_TASK_STATE_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

export function notebookStateBucket(notebook: Notebook): DevTaskStateBucket {
  return STATE_BUCKET_BY_STATUS[(notebook.status ?? '').trim()] ?? 'other';
}

function unknownTo(value: string | null | undefined, fallback: string): string {
  const text = value?.trim();
  return text ? text : fallback;
}

const NO_PUBLISHED: ReadonlySet<string> = new Set<string>();
const NO_PRODUCTION: ReadonlyMap<string, Workload> = new Map<string, Workload>();

// ---------- 生产执行入口（PRD §3.2.1 卡片操作 + §3.2.6 执行弹窗）----------

export const DEV_TASK_EXEC_BASE = '/compute';

/**
 * 无已发布生产版本的原因：与计算调度页执行弹窗里那句权威文案逐字同一份
 * （ComputeSchedule.tsx 生产版本下拉的 notFoundContent），不另编一套说法。
 */
export const NO_PRODUCTION_WORKLOAD_REASON = '该合约没有 published 的生产版本，需先在负载中心发布';

/** 生产执行按合约调度（POST /compute/runs 只收 contract_id），没有合约就无从定位执行范围。 */
export const NO_CONTRACT_REASON = '任务未关联合约，生产执行按合约调度';

/** 沙箱引擎：notebook 载荷（app/schemas/flow.py::NotebookOut）没有引擎字段，恒「未记录」。 */
export const DEV_TASK_ENGINE_TEXT = UNKNOWN_TEXT;

/**
 * 执行面固定为 CubeSandbox / l2c 单一路径（SS-13，级别不可选，见 SessionList/Guide 同一口径），
 * 但它既不是任务字段也不随任务下发；按禁伪造铁律不照原型示例值往卡上写。
 */
export const DEV_TASK_ENGINE_HINT = '任务字段里没有沙箱引擎项：执行面固定为 CubeSandbox / l2c 单一路径，级别不可选，此处不填示例值';

/**
 * 这条任务已发布进生产的那一版负载——与执行弹窗的查询同口径（env=production 且 status=published，
 * 见 workflow_service.publish 落的生产行与 ComputeSchedule 的 useWorkloads 参数），
 * 同一条任务多版时取最新版本（弹窗默认也是最新已发布版）。
 */
function publishedProductionByTask(workloads: Workload[]): ReadonlyMap<string, Workload> {
  const picked = new Map<string, Workload>();
  for (const workload of workloads) {
    const taskId = workload.source_notebook_id;
    if (!taskId) continue;
    if (workload.env !== 'production' || workload.status !== WorkloadStatus.Published) continue;
    const current = picked.get(taskId);
    if (!current || workload.version > current.version) picked.set(taskId, workload);
  }
  return picked;
}

function buildExecTarget(contractId: string | null, workloadId: string | null): { exec_path: string; exec_disabled_reason: string | null } {
  if (!contractId) return { exec_path: DEV_TASK_EXEC_BASE, exec_disabled_reason: NO_CONTRACT_REASON };
  if (!workloadId) return { exec_path: DEV_TASK_EXEC_BASE, exec_disabled_reason: NO_PRODUCTION_WORKLOAD_REASON };
  return {
    exec_path: `${DEV_TASK_EXEC_BASE}?contract=${encodeURIComponent(contractId)}&workload=${encodeURIComponent(workloadId)}`,
    exec_disabled_reason: null,
  };
}

export function buildDevTaskCard(
  notebook: Notebook,
  published: ReadonlySet<string> = NO_PUBLISHED,
  production: ReadonlyMap<string, Workload> = NO_PRODUCTION,
): DevTaskCard {
  return {
    key: `notebook:${notebook.id}`,
    id: notebook.id,
    task_no: notebook.task_no ?? null,
    task_no_text: unknownTo(notebook.task_no, UNKNOWN_TEXT),
    title: notebook.title,
    path: notebook.path,
    contract_id: notebook.contract_id,
    debug_method: notebook.debug_method ?? null,
    debug_method_text: taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, notebook.debug_method),
    export_mode: notebook.export_mode ?? null,
    export_mode_text: taskEnumLabel(TASK_EXPORT_MODE_OPTIONS, notebook.export_mode),
    debug_memory_mb: notebook.debug_memory_mb ?? null,
    exec_memory_mb: notebook.exec_memory_mb ?? null,
    debug_memory_text: formatMemoryGib(notebook.debug_memory_mb),
    exec_memory_text: formatMemoryGib(notebook.exec_memory_mb),
    state: notebookStateMeta(notebook),
    state_bucket: notebookStateBucket(notebook),
    stage: notebookStage(notebook, published),
    version: notebook.version,
    version_text: `v${notebook.version}`,
    created_at: notebook.created_at ?? null,
    created_text: unknownTo(formatDevTaskTime(notebook.created_at), UNKNOWN_TEXT),
    updated_at: notebook.updated_at ?? null,
    updated_text: unknownTo(formatDevTaskTime(notebook.updated_at), UNKNOWN_TEXT),
    engine_text: DEV_TASK_ENGINE_TEXT,
    ...buildExecTarget(notebook.contract_id ?? null, production.get(notebook.id)?.id ?? null),
    workbench_path: `/notebooks?task=${encodeURIComponent(notebook.id)}`,
    detail_path: `/flow/tasks/${encodeURIComponent(notebook.id)}`,
  };
}

export function buildDevTaskCards(notebooks: Notebook[], workloads: Workload[]): DevTaskCard[] {
  const published = publishedNotebookIds(workloads);
  const production = publishedProductionByTask(workloads);
  return notebooks.map((notebook) => buildDevTaskCard(notebook, published, production));
}

/** 时间只在能解析时格式化；解析不了就原样透出，编不了时间也不装「刚刚」。 */
export function formatDevTaskTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value;
}

/** 关键词只比名称/编号/路径；空关键词不参与过滤。 */
function matchesKeyword(card: DevTaskCard, keyword: string): boolean {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return true;
  return [card.title, card.task_no ?? '', card.path].some((field) => field.toLowerCase().includes(needle));
}

/**
 * 筛选只影响「看到哪些卡」，不改写数据：没有任何筛选时逐字返回入参。
 * 唯一能掉行的是 only_numbered，且它只命中 task_no 为空的存量行。
 */
export function filterDevTaskCards(cards: DevTaskCard[], filters: DevTaskFilters): DevTaskCard[] {
  const untouched
    = !filters.keyword.trim()
      && !filters.state
      && !filters.debug_method
      && !filters.export_mode
      && !filters.contract_id
      && !filters.only_numbered;
  if (untouched) return cards;

  return cards.filter((card) =>
    matchesKeyword(card, filters.keyword)
      && (!filters.state || card.state_bucket === filters.state)
      && (!filters.debug_method || card.debug_method === filters.debug_method)
      && (!filters.export_mode || card.export_mode === filters.export_mode)
      && (!filters.contract_id || card.contract_id === filters.contract_id)
      && (!filters.only_numbered || !!card.task_no),
  );
}

/** 当前生效的筛选（人读），用于零结果时说明“是哪个筛选筛空的”。 */
export function activeDevTaskFilterLabels(filters: DevTaskFilters): string[] {
  const labels: string[] = [];
  if (filters.keyword.trim()) labels.push(`关键词“${filters.keyword.trim()}”`);
  if (filters.state) labels.push(`任务状态=${devTaskStateLabel(filters.state)}`);
  if (filters.contract_id) labels.push('已选合约');
  if (filters.debug_method) labels.push(`调试方法=${taskEnumLabel(TASK_DEBUG_METHOD_OPTIONS, filters.debug_method, filters.debug_method)}`);
  if (filters.export_mode) labels.push(`导出方式=${taskEnumLabel(TASK_EXPORT_MODE_OPTIONS, filters.export_mode, filters.export_mode)}`);
  if (filters.only_numbered) labels.push('只看已编号任务');
  return labels;
}

// ---------------------------------------------------------------------------
// 原型 2-4「任务详情」固定文案与纯口径（图规格 = 静态文案 1:1；数字一律后端下发）。
// ---------------------------------------------------------------------------

/**
 * 表格里「这一格后端给不出」的占位（图 2-4 自己就用破折号：运行记录「运行中」那行的耗时就是 `—`）。
 * 与 UNKNOWN_TEXT（未记录 = 后端字段存在但未下发）分两层：这一档是「整个来源就没有这一项」。
 */
export const NO_SOURCE_TEXT = '—';

/** 图 2-4「基本信息」里「所属节点」：取当前登录主体的组织（登录档案 user.organization，与顶栏「欢迎登录，X」同源）。 */
export const DEV_TASK_NODE_HINT = '所属节点取当前登录主体的组织（登录下发的 user.organization，与顶栏同一字段）；平台没有逐任务的节点归属字段，登录档案里没有组织时只能空着';

export function taskNodeText(organization: string | null | undefined): string {
  const text = organization?.trim();
  return text ? text : NO_SOURCE_TEXT;
}

/** 图 2-4「结果集」的「导出状态」列：交付包行没有导出状态字段，整列置破折号。 */
export const BUNDLE_EXPORT_STATUS_HINT = '交付包行（GET /deliveries/dataset/bundles）只下发文件数、数据行数、大小、T5 审查与水印状态、生成时间与下载地址，没有导出状态字段；原型上的「已导出」不拿来当默认值';

/** 图 2-4「结果集名称」：后端只下发文件名与成果包编号，没有“结果集名称”这个概念。 */
export const BUNDLE_NAME_HINT = '这一列取后端下发的 file_name（交付包在对象存储里的文件名）；没有 file_name 的旧包退到 bundle_id，两者都是后端给的真值，不是结果集名称';

/** 成果包的归集口径：挂在 tooltip 上，不往卡题里再塞一行灰字（图上卡题只有那一句）。 */
export const RESULT_SET_SCOPE_HINT = '本页成果包按关联合约归集：交付包表（delivery_bundles）只有合约与会话两个维度，没有任务维度外键，不假装这一条任务独产';

/** 图 2-4「关联数据」的「组织名称」：产品载荷只有 provider_id，组名靠账号选项解析。 */
export const PRODUCT_ORG_HINT = '组织名称按数据产品的 provider_id 到 GET /users/options 里解析 organization；解析不到（该账号不在可读名单里）就置破折号，不从 id 反推名字';

/** 图 2-4「运行记录」卡固定说明（保留期来源见 RUN_RECORD_RETENTION_SOURCE，不与它混写）。 */
export const RUN_RECORD_RETENTION_NOTE = '记录调试与正式执行的历史，最多保留 90 天';

/** 上面那句「90 天」的来源披露：后端没有运行记录保留期字段，按图内原文写。 */
export const RUN_RECORD_RETENTION_SOURCE = '「最多保留 90 天」按原型固定文案原样写：后端只披露审计日志/告警的保留期配置（retention janitor 清理 audit_logs、alert_records、merkle_leaves），运行记录既无保留期字段也无清理任务';

/**
 * 运行记录状态（图 2-4 只有 执行成功 / 执行失败 / 运行中 三态，色按图：绿 / 红 / 蓝）。
 * 表里没有的后端取值一律灰底原样透出：不静默把它们归进上面三态（尤其不得归成成功）。
 */
const RUN_RECORD_STATE_META: Record<string, StatusMeta> = {
  succeeded: { color: 'green', label: '执行成功' },
  failed: { color: 'red', label: '执行失败' },
  running: { color: 'blue', label: '运行中' },
};

export function runRecordStatusMeta(status: string | null | undefined): StatusMeta {
  const key = (status ?? '').trim();
  if (!key) return { color: 'default', label: UNKNOWN_TEXT };
  const mapped = RUN_RECORD_STATE_META[key];
  if (mapped) return mapped;
  // 三态之外的取值（pending / skipped_overlap / cancelled …）：文字沿用后端状态的人读名，颜色一律灰。
  return { color: 'default', label: COMPUTE_RUN_STATUS_META[key]?.label ?? key };
}

/** 一次调试执行（本页内存里的记录）落到三态：被拦截与退出码非零都是执行失败。 */
export function debugRunStatusMeta(input: {
  ok: boolean | null;
  blocked?: string | null;
  exit_code?: number | null;
}): { meta: StatusMeta; note: string | null } {
  if (input.blocked) return { meta: { color: 'red', label: '执行失败' }, note: `输出被拦截：${input.blocked}` };
  if (input.ok === null || input.ok === undefined) return { meta: { color: 'default', label: UNKNOWN_TEXT }, note: null };
  if (input.ok) return { meta: { color: 'green', label: '执行成功' }, note: null };
  return {
    meta: { color: 'red', label: '执行失败' },
    note: input.exit_code === null || input.exit_code === undefined ? null : `退出码 ${input.exit_code}`,
  };
}

/** 耗时：只有起止两个后端时间戳都在且能解析时才计算，否则置破折号（不拿 0 秒充数）。 */
export function formatRunDuration(
  startedAt: string | null | undefined,
  finishedAt: string | null | undefined,
): string {
  if (!startedAt || !finishedAt) return NO_SOURCE_TEXT;
  const start = Date.parse(startedAt);
  const end = Date.parse(finishedAt);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return NO_SOURCE_TEXT;
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  if (minutes < 60) return restSeconds ? `${minutes} 分 ${restSeconds} 秒` : `${minutes} 分`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes ? `${hours} 小时 ${restMinutes} 分` : `${hours} 小时`;
}

/** 零结果文案：说清是哪个筛选筛空的，不报不存在的总数。 */
export function devTaskEmptyDescription(filters: DevTaskFilters, loadedCount: number): string {
  const labels = activeDevTaskFilterLabels(filters);
  if (labels.length) {
    return `已载入 ${loadedCount} 个开发任务，在筛选（${labels.join('、')}）下没有命中；清空筛选可回到全部任务。`;
  }
  return '还没有开发任务，点右上角新建一个';
}

// ---------------------------------------------------------------------------
// 图 2-4「资源配置 / 关联数据」的展示口径（固定文案逐字照图；数字一律后端下发）。
// ---------------------------------------------------------------------------

/**
 * 图 2-4 的内存单位写作「G」（卡题已带（G），值写 `8 G`）。
 * 与全站共用的 formatMemoryGib（`8 GiB`，列表页/表单提示靠它）并存：这一页照图，其它页不改口径。
 */
export function formatPrototypeMemory(mib: number | null | undefined): string {
  if (typeof mib !== 'number' || !Number.isFinite(mib) || mib < 0) return UNKNOWN_TEXT;
  const gib = mib / 1024;
  return `${Number.isInteger(gib) ? gib : Number(gib.toFixed(1))} G`;
}

/** 图 2-4「资源使用情况」右侧数值：无采样源时写「未计量」，不写 0，也不写分母的一半。 */
export const MEMORY_USAGE_UNMEASURED = '未计量';

/** 后端拿不到实测内存时的兜底说明；后端 own 的披露（home.not_collected）优先于它。 */
export const MEMORY_UNMEASURED_FALLBACK = '后端没有任何实测内存来源（无 cgroup/psutil 读取），因此这一项没有分子';

/**
 * 从 GET /flow/dashboard/home 的 not_collected 里取「内存实测未采集」那句后端原话作为 tooltip：
 * 后端没给这一项时退回固定文案，不自己编一套理由。
 */
export function memoryUnmeasuredReason(
  notCollected: { key: string; reason: string }[] | undefined,
): string {
  const hit = (notCollected ?? []).find((item) => item.key === 'memory.measured');
  return hit?.reason?.trim() ? hit.reason : MEMORY_UNMEASURED_FALLBACK;
}

/** 图 2-4「资源使用情况」底部两句：有活跃开发会话才说「运行中」，没有就直说没有。 */
export const SANDBOX_MEMORY_RUNNING_NOTE = '沙箱环境运行中，退出调试后自动释放内存';
export const NO_ACTIVE_DEBUG_SESSION_NOTE = '当前无活跃调试会话，没有待释放的内存';

/**
 * 数据产品类型的人读名（与数据产品页同一组词）；表里没有的取值原样透出，不替它归类。
 */
const PRODUCT_TYPE_LABELS: Record<string, string> = {
  structured: '结构化数据',
  unstructured: '非结构化数据',
  'semi-structured': '半结构化数据',
  dataset: '数据集',
};

export function productTypeLabel(value: string | null | undefined): string {
  const key = (value ?? '').trim();
  if (!key) return NO_SOURCE_TEXT;
  return PRODUCT_TYPE_LABELS[key] ?? key;
}
