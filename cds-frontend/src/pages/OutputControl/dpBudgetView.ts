// DP 预算三态文案映射（纯函数）——从 InspectionPipeline.tsx 拆出（P4-3 lint 清零）：
// 页面文件导出非组件函数会让 react-refresh 失效；此处逻辑本就要求可独立单测
// （A192e：「未核实」≠ 0，未知不许渲染成耗尽）。

import type { DPBudgetState } from '../../services/outputControlApi';

export interface DPBudgetView {
  /** 上屏的读数：可读时是两位小数，未知/未分配时是状态词，永远不是被造出来的 0。 */
  display: string;
  hint: string | null;
}

export function dpBudgetView(
  state: DPBudgetState | undefined,
  remaining: number | null,
  authoritative = true,
): DPBudgetView {
  if (remaining === null || remaining === undefined || state === 'unverified') {
    return {
      display: '未核实',
      hint: '预算后端读不到，剩余 ε 未知——未知不是 0。',
    };
  }
  if (state === 'unallocated') {
    return { display: '未分配', hint: '该会话没有 ε 预算记录，不代表已耗尽' };
  }
  if (state === 'exhausted') {
    return { display: remaining.toFixed(2), hint: '预算已耗尽（核实过的读数）' };
  }
  return {
    display: remaining.toFixed(2),
    hint: authoritative ? null : '本机进程内预算，未跨副本共享',
  };
}
