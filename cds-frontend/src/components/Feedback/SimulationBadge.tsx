import { Tag, Tooltip } from 'antd';
import type { CSSProperties, ReactNode } from 'react';

/** PZ-14（SS-11 §4.3）诚实降级统一呈现：真实/模拟/未启用 三态。 */
export type SimulationBadgeState = 'real' | 'simulated' | 'unsupported';

export interface SimulationBadgeProps {
  state: SimulationBadgeState;
  /** 展示文本；缺省按 state 显示 真实/模拟/未启用 */
  label?: ReactNode;
  /** tooltip 说明：什么被模拟、影响、何时变真实（指向 FG 项 / 前序任务） */
  detail?: ReactNode;
  style?: CSSProperties;
}

const stateColors: Record<SimulationBadgeState, string> = {
  real: 'green',
  simulated: 'gold',
  unsupported: 'default',
};

const stateLabels: Record<SimulationBadgeState, string> = {
  real: '真实',
  simulated: '模拟',
  unsupported: '未启用',
};

export default function SimulationBadge({ state, label, detail, style }: SimulationBadgeProps) {
  const tag = (
    <Tag color={stateColors[state]} style={style}>
      {label || stateLabels[state]}
    </Tag>
  );
  if (detail === undefined || detail === null || detail === '') return tag;
  return <Tooltip title={detail}>{tag}</Tooltip>;
}

/** 后端披露字段值 → 徽标状态（PZ-06 disclosure 口径）；未登记值由 simulationStateForValue 启发式兜底。 */
// eslint-disable-next-line react-refresh/only-export-components
export const DISCLOSURE_VALUE_STATES: Record<string, SimulationBadgeState> = {
  real: 'real',
  hardware_tee: 'real',
  tongsuo_sm4: 'real',
  hsm: 'real',
  external: 'real',
  'pg-log': 'real',
  runtime_isolation: 'real',
  lac: 'real',
  simulated: 'simulated',
  software_confidential: 'simulated',
  aes_fallback: 'simulated',
  'fisco-sim': 'simulated',
  'ant-sim': 'simulated',
  software: 'simulated',
  'local-simulated': 'simulated',
  rule: 'simulated',
  unsupported: 'unsupported',
  not_configured: 'unsupported',
  none: 'unsupported',
};

/** 由披露字段值推导徽标状态；值缺失时返回 null（老响应无该字段则不渲染，防御式）。 */
// eslint-disable-next-line react-refresh/only-export-components
export function simulationStateForValue(value: string | null | undefined): SimulationBadgeState | null {
  if (value === null || value === undefined || value === '') return null;
  const key = String(value).trim().toLowerCase();
  const known = DISCLOSURE_VALUE_STATES[key];
  if (known) return known;
  // 否定/禁用族先判：not_simulated 这类披露值含 'sim' 子串，先判 sim 会把「未模拟」标成「模拟」。
  if (key.includes('not_') || key.includes('none') || key.includes('disabled')) return 'unsupported';
  if (key.includes('sim')) return 'simulated';
  return 'real';
}
