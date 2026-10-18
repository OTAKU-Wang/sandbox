// PZ-06 披露口径回归锁：未登记的否定族值（not_*/none/disabled）不得被 sim 启发式吞掉——
// not_simulated 是「未模拟/未启用」的披露，若先判 'sim' 会被标成「模拟」，真值方向正好相反。
import { describe, it, expect } from 'vitest';
import { simulationStateForValue } from '../SimulationBadge';

describe('simulationStateForValue 披露口径', () => {
  it('否定族优先：not_simulated → unsupported（曾误报 simulated）', () => {
    expect(simulationStateForValue('not_simulated')).toBe('unsupported');
  });

  it('登记值：simulated → simulated', () => {
    expect(simulationStateForValue('simulated')).toBe('simulated');
  });

  it('启发式兜底：含 sim 的未知值 → simulated；否定族 → unsupported', () => {
    expect(simulationStateForValue('sim-in-software')).toBe('simulated');
    expect(simulationStateForValue('not_enabled')).toBe('unsupported');
    expect(simulationStateForValue('feature_disabled')).toBe('unsupported');
  });

  it('值缺失返回 null（老响应无该字段则不渲染）', () => {
    expect(simulationStateForValue(null)).toBeNull();
    expect(simulationStateForValue('')).toBeNull();
  });
});
