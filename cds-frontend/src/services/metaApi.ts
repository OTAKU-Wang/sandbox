import api from './api';

// PZ-13 (SS-11): 级别能力矩阵 — 单一权威源（后端 app/services/capability_matrix.py，
// GET /api/v1/meta/capability-matrix）。级别标签/定位/弃用状态一律消费本模块，
// 禁止在页面中第二处硬编码（SessionList / ContractCreate / Login 已切换至此）。

export type CapabilityState = 'real' | 'simulated' | 'unsupported' | 'planned';

export interface CapabilityCell {
  state: CapabilityState;
  note?: string;
  evidence?: string;
}

export interface LevelMetadata {
  label: string;
  positioning: string;
  description: string;
  deprecated: boolean;
}

/** SS-13 交付档声明：后端 `capability_matrix.dataplane_status()`（矩阵载荷的第三个键）的映射。
 * 键名/可空性取自后端源：`enabled` 是 `bool(...)`、`mode` 只有 `full`/`control_plane_only` 两个取值、
 * `reason` 仅控制面档时为 `"dataplane_disabled"`（完整档为 null）、`note` 恒有文案。 */
export interface DataplaneStatus {
  enabled: boolean;
  mode: 'full' | 'control_plane_only';
  reason: string | null;
  note: string;
}

export interface CapabilityMatrix {
  matrix: Record<string, Record<string, CapabilityCell>>;
  levels: Record<string, LevelMetadata>;
  /** 本部署的档位声明（唯一消费方：全局交付档横幅）。后端恒定返回该键，
   * 声明为可选只是「载荷可能不完整」的边界容错，不代表存在两种接口。 */
  dataplane?: DataplaneStatus;
}

/** 能力行标签（API 只返回能力 key）；未知 key 回退为原始 key，保证前向兼容。 */
export const CAPABILITY_LABELS: Record<string, string> = {
  sync_exec: '同步执行',
  stream_ws: '流式执行 (WS)',
  files_snapshot: '文件/快照',
  pause_resume: '暂停/恢复',
  tee_proof_gate: 'TEE 证明门禁',
  network_policy_hotswap: '网络策略热更新',
};

export function capabilityLabel(capability: string): string {
  return CAPABILITY_LABELS[capability] ?? capability;
}

export interface LevelSelectOption {
  value: string;
  label: string;
  positioning: string;
  deprecated: boolean;
  description: string;
}

/** 创建表单的级别选项（单一来源：矩阵 API）。
 * - 合约侧排除 L0（产品决策：L0 不可写入合约，spec §2.1）；
 * - 全 planned 的级别（当前为 l2c，数据面未上线）不进入创建选项，
 *   矩阵落地后（CB-01..CB-06）自动出现，无需再改前端。
 */
export function levelSelectOptions(
  matrix: CapabilityMatrix | undefined,
  opts: { contract?: boolean } = {},
): LevelSelectOption[] {
  if (!matrix) return [];
  return Object.entries(matrix.levels)
    .filter(([key]) => {
      if (opts.contract && key === 'L0') return false;
      const cells = matrix.matrix[key] ?? {};
      return Object.values(cells).some(
        (cell) => cell.state === 'real' || cell.state === 'simulated',
      );
    })
    .map(([key, meta]) => ({
      value: key,
      label: meta.positioning ? `${meta.label} · ${meta.positioning}` : meta.label,
      positioning: meta.positioning,
      deprecated: meta.deprecated,
      description: meta.description,
    }));
}

export const metaApi = {
  getCapabilityMatrix: (): Promise<CapabilityMatrix> =>
    api.get('/meta/capability-matrix'),
  /** 当前部署真实可用的档位 + 逐项披露（拍板②，后端 capability_matrix.sandbox_levels_payload）。 */
  getSandboxLevels: (): Promise<SandboxLevelsDisclosure> =>
    api.get('/meta/sandbox-levels'),
};

// ---------------------------------------------------------------------------
// 2026-10-30 拍板②：合约表单的「允许沙箱级别」不再硬编码 l2c。
//
// 旧形态是 disabled 的固定 l2c，而 dev compose 把 CDS_DEV_SANDBOX_LEVEL 设成 L3
// （docker-compose.yml:213），本机一开调试环必 400（取证
// specs/ss-19-full-platform-docker-e2e.md §10.1-4）。现在选项与披露文案都取自
// GET /meta/sandbox-levels：后端说什么档位可写、当前能不能跑、为什么，前端就渲什么。
//
// 读不到披露时**不猜**：`contractLevelOptions` 返回空数组，页面渲染「未记录」，
// 表单不提交 allowed_sandbox_levels 这个键（= 后端按其声明的默认档落库），
// 而不是悄悄替用户选一个档位。
// ---------------------------------------------------------------------------

/** 单个档位在**当前部署**上的可用性披露行（后端 level_availability() 的映射）。 */
export interface SandboxLevelDisclosureRow {
  level: string;
  label: string;
  positioning: string;
  description: string;
  deprecated: boolean;
  /** 适配器是否注册（开关门控的真实结果）。 */
  registered: boolean;
  /** 当前部署此刻能不能真跑起来这个档位。 */
  runtime_available: boolean;
  /** 门禁镜像词（dataplane_disabled / no_ready_node / legacy_level_retired …）或 null。 */
  reason: string | null;
  /** 可直接渲染的人话，不可用时说明为什么不可用。 */
  disclosure: string;
  supply_data_eligible: boolean;
  /** 能不能写进合约条款（供数下限 ∩ 合约取值域）。 */
  contract_selectable: boolean;
  contract_reason: string | null;
  contract_note: string | null;
}

export interface SandboxLevelsDisclosure {
  levels: SandboxLevelDisclosureRow[];
  recommended_level: string;
  recommended_disclosure: string;
  supply_data_levels: string[];
  dev_sandbox_level: {
    level: string;
    known: boolean;
    runtime_available: boolean;
    disclosure: string;
  };
  ready_nodes: number;
  dataplane?: DataplaneStatus;
  note: string;
}

export interface ContractLevelOption {
  value: string;
  label: string;
  positioning: string;
  deprecated: boolean;
  runtime_available: boolean;
  disclosure: string;
}

/** 读不到披露时的唯一措辞——与全站 UNKNOWN_TEXT 同口径，绝不写成「无档位」。 */
export const SANDBOX_LEVELS_UNDISCLOSED = '未记录：未能读取本部署的沙箱档位能力披露';

/** 合约表单可选项：后端标了 contract_selectable 的档位，顺序与后端一致。 */
export function contractLevelOptions(
  disclosure: SandboxLevelsDisclosure | undefined | null,
): ContractLevelOption[] {
  if (!disclosure || !Array.isArray(disclosure.levels)) return [];
  return disclosure.levels
    .filter((row) => row.contract_selectable)
    .map((row) => ({
      value: row.level,
      label: row.positioning ? `${row.label} · ${row.positioning}` : row.label,
      positioning: row.positioning,
      deprecated: row.deprecated,
      runtime_available: row.runtime_available,
      disclosure: row.disclosure,
    }));
}

/** 选中档位下方要照抄的披露：一个档位一行，后端说什么写什么。 */
export function contractLevelFootnote(
  disclosure: SandboxLevelsDisclosure | undefined | null,
  selected: string[] | undefined | null,
): string[] {
  if (!disclosure || !Array.isArray(disclosure.levels)) return [SANDBOX_LEVELS_UNDISCLOSED];
  const picked = (selected ?? []).length
    ? disclosure.levels.filter((row) => (selected ?? []).includes(row.level))
    : disclosure.levels.filter((row) => row.level === disclosure.recommended_level);
  const lines = picked.map(
    (row) => `${row.label}：${row.runtime_available ? '' : '当前部署不可执行 —— '}${row.disclosure}`,
  );
  // 后端 note 自带「口径：」开头（capability_matrix.sandbox_levels_payload），前端原样照抄不再叠前缀
  if (disclosure.note) lines.push(disclosure.note);
  return lines;
}
