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
};
